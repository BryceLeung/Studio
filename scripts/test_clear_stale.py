import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from scripts.clear_stale import main


class ClearStaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()

    def file(self, relative):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep")
        return path

    def run_cleanup(self, scope, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = main(["--project-root", str(self.project), "--stage", scope, *extra])
        return result, output.getvalue()

    def test_substage_preserves_other_files_and_is_idempotent(self):
        stale = self.file("3-Post/2-Transcribe/STALE/1/DONE")
        self.file("3-Post/2-Transcribe/STALE/PENDING.json")
        keep = [self.file("3-Post/2-Transcribe/" + name)
                for name in ("DONE", "LOG.md", "output.json", "Stale Transcripts.json")]
        keep.append(self.file("3-Post/1-Sync/STALE/1/DONE"))
        self.assertEqual(self.run_cleanup("3.2")[0], 0)
        self.assertFalse(stale.parents[1].exists())
        self.assertTrue(all(p.exists() for p in keep))
        self.assertEqual(self.run_cleanup("3.2")[0], 0)

    def test_whole_stage_and_all_include_undefined_substages(self):
        stage = self.file("3-Post/STALE/1/DONE")
        sub = self.file("3-Post/99-Other/STALE/1/DONE")
        other = self.file("1-Pre/1-Scripts/STALE/1/DONE")
        scratch = self.file("0-Tmp/STALE/keep")
        nested = self.file("3-Post/99-Other/working/STALE/keep")
        self.assertEqual(self.run_cleanup("3")[0], 0)
        self.assertFalse(stage.exists())
        self.assertFalse(sub.exists())
        self.assertTrue(other.exists())
        self.assertEqual(self.run_cleanup("all")[0], 0)
        self.assertFalse(other.exists())
        self.assertTrue(scratch.exists())
        self.assertTrue(nested.exists())

    def test_preview_and_invalid_selection_do_not_delete(self):
        stale = self.file("3-Post/1-Sync/STALE/1/DONE")
        for scope, extra in (("all", ("--dry-run",)), ("../3", ()), ("3.2", ())):
            self.run_cleanup(scope, *extra)
            self.assertTrue(stale.exists())

    def test_preflight_refuses_linked_archives_before_any_deletion(self):
        stale = self.file("1-Pre/1-Scripts/STALE/1/DONE")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "precious").write_text("keep")
        linked = self.project / "3-Post/1-Sync/STALE"
        linked.parent.mkdir(parents=True)
        linked.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.run_cleanup("all")[0], 1)
        self.assertTrue(stale.exists())
        self.assertTrue((outside / "precious").exists())

    def test_links_inside_archive_do_not_delete_their_targets(self):
        outside = Path(self.tmp.name) / "precious"
        outside.write_text("keep")
        stale = self.file("3-Post/1-Sync/STALE/1/DONE").parents[1]
        (stale / "link").symlink_to(outside)
        self.assertEqual(self.run_cleanup("3.1")[0], 0)
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
