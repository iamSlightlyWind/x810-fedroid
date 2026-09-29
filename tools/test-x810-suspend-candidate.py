#!/usr/bin/env python3
"""Check opt-in, board scope, and pinned Linux IPCC/IRQ suspend semantics."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PREPARE = REPO / "kernel/prepare.sh"
PATCH = REPO / "kernel/experimental/suspend/ipcc-mask-summary-x810.patch"
DEFAULT_PATCHES = REPO / "kernel/patches"
DTS = REPO / "kernel/files/sm8550-samsung-gts9wifi.dts"


def check_static_contract() -> None:
    prepare = PREPARE.read_text()
    patch = PATCH.read_text()
    dts = DTS.read_text()
    assert '"${X810_SUSPEND_EXPERIMENTAL:-0}"' in prepare
    assert '"$here"/experimental/suspend/*.patch' in prepare
    assert 'X810_SUSPEND_EXPERIMENTAL must be 0 or 1' in prepare
    assert not (DEFAULT_PATCHES / PATCH.name).exists(), "candidate patch leaked into default patch directory"
    assert 'of_machine_is_compatible("samsung,gts9pwifi")' in patch
    assert "samsung,gts9uwifi" not in patch
    assert "IRQF_NO_SUSPEND" in patch
    assert "IRQF_TRIGGER_HIGH | IRQF_NO_THREAD" in patch
    assert "qmp-ufs" not in patch.lower()
    assert '"samsung,gts9pwifi", "samsung,gts9wifi"' in dts


def check_patch_applies(kernel_tree: Path) -> None:
    makefile = (kernel_tree / "Makefile").read_text()
    assert (
        "VERSION = 7" in makefile
        and "PATCHLEVEL = 2" in makefile
        and "SUBLEVEL = 0" in makefile
    )
    rel = Path("drivers/mailbox/qcom-ipcc.c")
    original = kernel_tree / rel
    assert original.is_file(), f"not a kernel source tree: {kernel_tree}"
    original_source = original.read_text()
    of_header = (kernel_tree / "include/linux/of.h").read_text()
    of_base = (kernel_tree / "drivers/of/base.c").read_text()
    irq_pm = (kernel_tree / "kernel/irq/pm.c").read_text()
    sm8550_dtsi = (kernel_tree / "arch/arm64/boot/dts/qcom/sm8550.dtsi").read_text()
    assert "IPCC_REG_RECV_ID" in original_source
    assert "IPCC_REG_RECV_SIGNAL_CLEAR" in original_source
    assert "IPCC_CLEAR_ON_RECV_RD" in original_source
    assert "IRQF_TRIGGER_HIGH | IRQF_NO_SUSPEND |" in original_source
    handler = original_source.split("static irqreturn_t qcom_ipcc_irq_fn", 1)[1].split(
        "static void qcom_ipcc_mask_irq", 1
    )[0]
    assert "for (;;)" in handler
    assert "readl(ipcc->base + IPCC_REG_RECV_ID)" in handler
    assert "writel(hwirq, ipcc->base + IPCC_REG_RECV_SIGNAL_CLEAR)" in handler
    assert "generic_handle_irq(virq)" in handler
    # The machine-compatible helper searches the root DT's compatible list,
    # not only its first string; X810 includes a specific plus fallback IDs.
    assert "Test root of device tree for a given compatible value" in of_header
    assert "return of_machine_compatible_match(compats);" in of_header
    assert "root = of_find_node_by_path(\"/\");" in of_base
    assert "rc = of_device_compatible_match(root, compats);" in of_base
    assert "return rc != 0;" in of_base
    ipcc_node = sm8550_dtsi.split("ipcc: mailbox@408000 {", 1)[1].split("};", 1)[0]
    assert "GIC_SPI 229 IRQ_TYPE_LEVEL_HIGH" in ipcc_node
    assert "wakeup-source" not in ipcc_node
    suspend_irq = irq_pm.split("static bool suspend_device_irq", 1)[1].split(
        "void suspend_device_irqs", 1
    )[0]
    resume_irq = irq_pm.split("static void resume_irq", 1)[1].split(
        "static void resume_irqs", 1
    )[0]
    assert "desc->no_suspend_depth" in suspend_irq
    assert "desc->istate |= IRQS_SUSPENDED;" in suspend_irq
    assert "__disable_irq(desc);" in suspend_irq
    assert "__enable_irq(desc);" in resume_irq
    with tempfile.TemporaryDirectory(prefix="x810-ipcc-patch-test-") as temp:
        tree = Path(temp)
        target = tree / rel
        target.parent.mkdir(parents=True)
        shutil.copy2(original, target)
        subprocess.run(
            ["git", "apply", "--check", str(PATCH)],
            cwd=tree,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        subprocess.run(
            ["git", "apply", str(PATCH)],
            cwd=tree,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        patched = target.read_text()
        assert 'static irqreturn_t qcom_ipcc_irq_fn' in patched
        assert '#include <linux/of.h>' in patched
        assert 'of_machine_is_compatible("samsung,gts9pwifi")' in patched
        assert "irqflags |= IRQF_NO_SUSPEND;" in patched
        assert "qcom_ipcc_irq_fn,\n\t\t\t       irqflags, name, ipcc);" in patched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kernel-tree",
        type=Path,
        help="optional exact Linux 7.2.0 source tree for a zero-fuzz patch application check",
    )
    args = parser.parse_args()
    check_static_contract()
    if args.kernel_tree:
        check_patch_applies(args.kernel_tree.resolve())
        print("PASS: opt-in and board-scope contracts; patch applies to Linux 7.2.0 with zero fuzz")
    else:
        print("PASS: opt-in and board-scope contracts (patch-application check skipped)")


if __name__ == "__main__":
    main()
