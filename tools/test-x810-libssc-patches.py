#!/usr/bin/env python3
"""Check the pinned libssc source has the no-spin SSC wait fix applied."""

from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} PATCHED_LIBSSC_SOURCE_DIR")

    source_root = Path(sys.argv[1])
    common = (source_root / "src/libssc-common.c").read_text(encoding="utf-8")
    start = common.index("ssc_common_wait_sync_context (SyncContext *ctx)")
    end = common.index("\n}\n", start)
    wait = common[start:end]

    for required in (
        "g_main_context_ref_thread_default ()",
        "g_main_context_acquire (context)",
        "g_main_context_iteration (context, TRUE)",
        "g_cond_wait (&ctx->condition, &ctx->mutex)",
        "g_main_context_release (context)",
        "g_main_context_unref (context)",
    ):
        if required not in wait:
            raise SystemExit(f"libssc wait fix missing: {required}")
    if "g_main_context_iteration (g_main_context_default (), FALSE)" in wait:
        raise SystemExit("libssc still busy-spins while awaiting SSC")
    if "g_main_context_default ()" in wait:
        raise SystemExit("libssc wait ignores the GTask caller's thread-default context")

    print("PASS: libssc SSC waits block on the owning context/condition instead of spinning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
