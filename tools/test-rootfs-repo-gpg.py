#!/usr/bin/env python3
"""Guard the temporary Fedora rootfs repos against missing RPM trust config."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rootfs/build-rootfs.sh"
SOURCE = SCRIPT.read_text(encoding="utf-8")


class RootfsRepoGpgTests(unittest.TestCase):
    def test_fedora_key_is_derived_from_release_and_arm64(self):
        self.assertRegex(
            SOURCE,
            re.compile(
                r'fedora_gpgkey_file="/etc/pki/rpm-gpg/'
                r'RPM-GPG-KEY-fedora-\$\{fedora_release\}-aarch64"'
            ),
        )
        self.assertIn('fedora_gpgkey_url="file://${fedora_gpgkey_file}"', SOURCE)

    def test_key_preflight_happens_before_any_dnf_transaction(self):
        preflight = SOURCE.index('if [ ! -s "$fedora_gpgkey_file" ]')
        repo_args = SOURCE.index("dnf_repo_args=(")
        first_dnf = SOURCE.index("dnf -y --installroot=")
        self.assertLess(preflight, repo_args)
        self.assertLess(preflight, first_dnf)
        self.assertIn("exit 2", SOURCE[preflight:repo_args])

    def test_both_dynamic_repos_keep_package_signature_checks_and_key(self):
        for repo_id in ("fedora_repo_id", "updates_repo_id"):
            self.assertIn(f'--setopt="${repo_id}.gpgcheck=1"', SOURCE)
            self.assertIn(
                f'--setopt="${repo_id}.gpgkey=$fedora_gpgkey_url"', SOURCE
            )

    def test_dnf5_cache_is_external_and_packages_are_retained_for_ci_cache(self):
        self.assertIn(
            'dnf_cache_dir="${X810_DNF_CACHE_DIR:-/tmp/x810-dnf-cache}"', SOURCE
        )
        self.assertIn('--setopt=cachedir="$dnf_cache_dir"', SOURCE)
        # DNF5 uses system_cachedir instead of cachedir for root transactions.
        self.assertIn('--setopt=system_cachedir="$dnf_cache_dir"', SOURCE)
        self.assertIn("--setopt=keepcache=True", SOURCE)
        self.assertIn('"$dnf_cache_real" == "$rootfs_real/"*', SOURCE)
        self.assertIn('"$rootfs/var/cache/libdnf5"', SOURCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
