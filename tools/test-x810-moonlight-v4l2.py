#!/usr/bin/env python3
"""Test the opt-in Moonlight V4L2 launcher without launching Moonlight."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "rootfs/overlay/usr/bin/x810-moonlight-v4l2"
DESKTOP = ROOT / "rootfs/overlay/usr/share/applications/x810-moonlight-v4l2.desktop"
INSTALLER = ROOT / "rootfs/overlay/usr/bin/x810-moonlight-install"
APP_ID = "com.moonlight_stream.Moonlight"
ROOTFS_BUILDER = ROOT / "rootfs/build-rootfs.sh"
RPM_BUILDER = ROOT / "tools/build-port-support-rpm.sh"


class MoonlightV4L2LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="x810-moonlight-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "flatpak.jsonl"
        self.fake_flatpak = self.bin / "flatpak"
        self.fake_flatpak.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            "with open(os.environ['FAKE_FLATPAK_LOG'], 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:3] == ['--system', 'info'] and os.environ.get('FAKE_FLATPAK_INFO_FAIL') == '1':\n"
            "    raise SystemExit(1)\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        self.fake_flatpak.chmod(0o755)

    def invoke(self, *args, info_fail=False):
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_FLATPAK_LOG"] = str(self.log)
        if info_fail:
            env["FAKE_FLATPAK_INFO_FAIL"] = "1"
        return subprocess.run(
            [str(LAUNCHER), *args],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_launcher_passes_only_per_run_hints_and_forwards_arguments(self):
        result = self.invoke("--windowed", "moonlight://example/host")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                ["--system", "info", "--show-ref", APP_ID],
                [
                    "--system",
                    "run",
                    "--env=H264_DECODER_HINT=h264_v4l2m2m",
                    "--env=HEVC_DECODER_HINT=hevc_v4l2m2m",
                    APP_ID,
                    "--windowed",
                    "moonlight://example/host",
                ],
            ],
        )

    def test_launcher_refuses_if_system_flatpak_app_is_missing(self):
        result = self.invoke(info_fail=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("system-installed Moonlight Flatpak was not found", result.stderr)
        self.assertEqual(self.calls(), [["--system", "info", "--show-ref", APP_ID]])

    def test_launcher_does_not_write_persistent_overrides_or_force_direct_drm(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("flatpak --system run", text)
        self.assertNotIn("flatpak override", text)
        self.assertNotIn("DRM_FORCE_DIRECT", text.split("exec flatpak", 1)[1])

    def test_launcher_uses_v4l2m2m_not_vaapi(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("H264_DECODER_HINT=h264_v4l2m2m", text)
        self.assertIn("HEVC_DECODER_HINT=hevc_v4l2m2m", text)
        self.assertNotIn("LIBVA_DRIVER_NAME", text)
        self.assertNotIn("vaapi", text.lower())
        self.assertNotIn("--device=all", text)
        self.assertIn("do not bind /dev/video17", text)

    def test_experiment_does_not_claim_decoder_hints_prove_hardware_decode(self):
        experiment = (ROOT / "docs/x810-research/MOONLIGHT-V4L2-EXPERIMENT.md").read_text(encoding="utf-8")
        normalized_experiment = " ".join(experiment.split())
        self.assertIn("does not bind a specific `/dev/videoN` node", normalized_experiment)
        self.assertIn("Keep **Automatic** for the initial live-stream test", normalized_experiment)
        self.assertIn("sets `AV_CODEC_CAP_HARDWARE` on its V4L2 M2M decoder definitions", normalized_experiment)
        self.assertIn("should be reported as hardware", normalized_experiment)
        self.assertIn("earlier claim that Moonlight would generically classify these wrappers as software was incorrect", normalized_experiment)
        self.assertIn("diagnose decoder initialization", normalized_experiment)
        self.assertIn("H264_DECODER_HINT)", normalized_experiment)
        self.assertIn("Moonlight streaming decode has not been tested on the tablet", normalized_experiment)

    def test_docs_distinguish_decoder_encoder_and_vaapi(self):
        notes = (ROOT / "docs/Hardware-Notes.md").read_text(encoding="utf-8")
        experiment = (ROOT / "docs/x810-research/MOONLIGHT-V4L2-EXPERIMENT.md").read_text(encoding="utf-8")
        self.assertIn("`/dev/video17` — stateful V4L2 M2M MPLANE", notes)
        self.assertIn("`/dev/video18` — untested", notes)
        self.assertIn("there is no\nVA-API driver for it", notes)
        self.assertIn("not VA-API", experiment)
        self.assertIn("streaming decode has not been", experiment)
        self.assertIn("1dd6cdb567d9c79bcbd8caee13d999a447a8b413", experiment)

    def test_optional_installer_adds_flathub_and_installs_or_updates_system_app(self):
        fake_id = self.bin / "id"
        fake_id.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
        fake_id.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_FLATPAK_LOG"] = str(self.log)
        result = subprocess.run(
            [str(INSTALLER)], env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                ["remote-add", "--if-not-exists", "--system", "--from", "flathub", "https://dl.flathub.org/repo/flathub.flatpakrepo"],
                ["install", "--system", "--noninteractive", "--or-update", "flathub", APP_ID],
            ],
        )

    def test_installer_refuses_non_root_and_does_not_touch_flatpak(self):
        # Mock id as non-root, regardless of the uid running this test.
        fake_id = self.bin / "id"
        fake_id.write_text("#!/bin/sh\necho 1000\n", encoding="utf-8")
        fake_id.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_FLATPAK_LOG"] = str(self.log)
        result = subprocess.run(
            [str(INSTALLER)], env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("run with sudo", result.stderr)
        self.assertFalse(self.log.exists())

    def test_installer_is_explicit_and_never_invoked_by_build_or_update(self):
        self.assertTrue(INSTALLER.stat().st_mode & 0o111)
        self.assertIn("never run by rootfs build/upgrade scripts", INSTALLER.read_text(encoding="utf-8"))
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("--system", text)
        self.assertIn("--or-update", text)
        for path in (ROOTFS_BUILDER, RPM_BUILDER):
            self.assertNotIn("x810-moonlight-install", path.read_text(encoding="utf-8"))

    def test_desktop_entry_is_an_opt_in_launcher_not_the_default_flatpak_entry(self):
        text = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("Name=Moonlight (X810 V4L2 decode)", text)
        self.assertIn("Exec=/usr/bin/x810-moonlight-v4l2 %U", text)
        self.assertIn("TryExec=/usr/bin/x810-moonlight-v4l2", text)
        self.assertTrue(LAUNCHER.stat().st_mode & 0o111)

    def test_both_install_paths_copy_the_launcher_and_desktop_entry(self):
        # Fresh rootfs and support-RPM builds both copy the complete overlay.
        # The latter's file list / RPM check prevents silently dropping either
        # launcher file from the updater package.
        rootfs_builder = ROOTFS_BUILDER.read_text(encoding="utf-8")
        rpm_builder = RPM_BUILDER.read_text(encoding="utf-8")
        self.assertIn(
            'cp -a "$repo_dir/rootfs/overlay/." "$rootfs/"', rootfs_builder
        )
        self.assertIn(
            'cp -a "$repo_dir/rootfs/overlay/." "$stage/"', rpm_builder
        )
        self.assertIn('find . \\( -type f -o -type l \\)', rpm_builder)
        self.assertIn('comm -23 "$top/SOURCES/port-overlay.filelist"', rpm_builder)
        self.assertTrue(LAUNCHER.is_file())
        self.assertTrue(DESKTOP.is_file())
        self.assertTrue(INSTALLER.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
