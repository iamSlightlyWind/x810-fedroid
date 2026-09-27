#!/usr/bin/env python3
"""Regression tests for capability preservation in the legacy rootfs installers."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RootfsCapabilityInstallerTests(unittest.TestCase):
    def test_internal_storage_repairs_capabilities_after_twrp_toybox_extract(self):
        script = (ROOT / "rootfs/mk-internal-storage.sh").read_text()
        extract = script.index("tar xzf /tmp/gts9-rootfs.tar.gz -C /rmnt")
        repair = script.index("chroot /rmnt /usr/bin/bash /tmp/gts9-restore-filecaps.sh")
        self.assertLess(extract, repair)
        self.assertIn(r"cat > /rmnt/tmp/gts9-restore-filecaps.sh <<\CAPS", script)
        self.assertIn("rpm --noplugins -qa --qf", script)
        self.assertIn("%{FILECAPS}", script)
        self.assertIn("%{FILENAMES}", script)
        self.assertIn('/usr/bin/setcap "$caps" "$path"', script)
        self.assertIn("capability-bearing file is missing", script)

    def test_sd_card_uses_gnu_tar_xattr_extraction(self):
        script = (ROOT / "rootfs/mk-sd-card.sh").read_text()
        self.assertIn(
            'tar --xattrs --xattrs-include=security.capability -xzf "$rootfs_tar" -C "$m_root"',
            script,
        )

    def test_installer_shell_syntax(self):
        for script in ("rootfs/mk-internal-storage.sh", "rootfs/mk-sd-card.sh"):
            with self.subTest(script=script):
                subprocess.run(["bash", "-n", str(ROOT / script)], check=True)

    def test_gnu_tar_xattr_round_trip(self):
        needed = ("unshare", "setcap", "getcap", "tar")
        if any(shutil.which(command) is None for command in needed):
            self.skipTest("unshare/setcap/getcap/tar are required for xattr integration test")
        probe = subprocess.run(
            ["unshare", "-Ur", "true"], capture_output=True, text=True, check=False
        )
        if probe.returncode != 0:
            self.skipTest("user namespaces are unavailable for unprivileged setcap testing")

        with tempfile.TemporaryDirectory(prefix="x810-capability-tar-") as temp:
            source = Path(temp) / "source"
            extracted = Path(temp) / "extracted"
            archive = Path(temp) / "rootfs.tar.gz"
            source.mkdir()
            extracted.mkdir()
            script = r"""
set -euo pipefail
source=$1 archive=$2 extracted=$3
printf '#!/bin/sh\nexit 0\n' > "$source/probe"
chmod 755 "$source/probe"
setcap cap_net_raw=ep "$source/probe"
tar --format=pax --xattrs --xattrs-include=security.capability \
    -czf "$archive" -C "$source" .
tar --xattrs --xattrs-include=security.capability \
    -xzf "$archive" -C "$extracted"
before=$(getcap "$source/probe" | sed "s|^$source/probe ||")
after=$(getcap "$extracted/probe" | sed "s|^$extracted/probe ||")
test "$before" = cap_net_raw=ep
test "$after" = "$before"
"""
            result = subprocess.run(
                ["unshare", "-Ur", "bash", "-c", script, "cap-test",
                 str(source), str(archive), str(extracted)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
