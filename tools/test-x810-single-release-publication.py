#!/usr/bin/env python3
"""Guard against publishing an update-only GitHub release during a build."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/x810-fedora.yml"
PUBLISHER = ROOT / "tools/publish-x810-full-set.sh"


def section(text: str, start: str, end: str) -> str:
    first = text.index(start)
    last = text.index(end, first + len(start))
    return text[first:last]


class SingleReleasePublicationTests(unittest.TestCase):
    def test_update_job_only_uploads_private_workflow_artifact(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        job = section(workflow, "  build_port_update:\n", "  plan:\n")
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", job)
        self.assertIn("name: x810-port-update", job)
        self.assertIn("path: _ci/release/update.zip", job)
        self.assertIn("retention-days: 1", job)
        self.assertNotIn("gh release create", job)
        self.assertNotIn("gh release upload", job)

    def test_only_final_aggregate_job_downloads_update_artifact(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        job = section(workflow, "  publish_full_set:\n", "  prune_old_releases:\n")
        self.assertIn("actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", job)
        self.assertIn("name: x810-port-update", job)
        self.assertIn("path: _ci/release", job)
        self.assertIn("bash tools/publish-x810-full-set.sh", job)

    def test_aggregate_uses_local_artifact_not_partial_public_release(self):
        publisher = PUBLISHER.read_text(encoding="utf-8")
        self.assertIn('update_zip="${PORT_UPDATE_ZIP:-_ci/release/update.zip}"', publisher)
        self.assertIn('cp -- "$update_zip" update/update.zip', publisher)
        self.assertNotIn('download "$REL" --dir update', publisher)
        self.assertIn('gh release -R "$GITHUB_REPOSITORY" upload "$REL" --clobber', publisher)


if __name__ == "__main__":
    unittest.main()
