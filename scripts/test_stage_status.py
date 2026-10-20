"""Read-only stage graph and completion evidence checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import stage_completion as completion
from scripts import stage_status


class StageStatusTests(unittest.TestCase):
    def test_all_definitions_resolve_and_preserve_independent_branches(self):
        stages = stage_status.load_stages()
        self.assertEqual(len(stages), 11)
        self.assertEqual(set(stages["3.2"]["prerequisites"][0].values()),
                         {"2.1", "2-Production/1-Footage Intake"})
        self.assertNotIn("3.1", [item["step"] for item in stages["3.2"]["prerequisites"]])
        self.assertNotIn("3.4", [item["step"] for item in stages["3.5"]["prerequisites"]])
        order = stage_status.order_stages(stages, "3.6")
        self.assertEqual(set(order), set(stages) - {"1.4"})
        self.assertEqual({item["step"] for item in stages["1.4"]["prerequisites"]}, {"1.1", "1.3"})
        self.assertLess(order.index("3.3"), order.index("3.5"))
        self.assertLess(order.index("3.4"), order.index("3.6"))

    def test_audit_uses_saved_evidence_without_mutating_markers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            workflow = root / "workflow"
            workflow.mkdir()
            (project / "review").mkdir()
            (project / "cue").mkdir()
            source = project / "review/source.docx"
            source.write_bytes(b"approved")
            output = project / "cue/deck.pptx"
            output.write_bytes(b"deck")
            stages = {
                "1.1": {"step": "1.1", "folder": "review", "prerequisites": [],
                        "inputs": [], "outputs": [{"folder": "review", "filename_regex": "^.+\\.docx$", "minimum": 1,
                                                 "complete_set": True}]},
                "1.2": {"step": "1.2", "folder": "cue", "prerequisites": [{"step": "1.1", "folder": "review"}],
                        "inputs": [], "outputs": [{"folder": "cue", "filename_regex": "^deck\\.pptx$", "minimum": 1}]},
            }
            for step, file in (("1.1", source), ("1.2", output)):
                folder = project / stages[step]["folder"]
                marker = {"step": step, "inputs": [], "dependencies": [],
                          "outputs": [completion.fingerprint(file, project, "project")]}
                (folder / "DONE").write_text(json.dumps(marker))
            before = {item: (project / item).read_bytes() for item in ("review/DONE", "cue/DONE")}
            self.assertEqual([item["status"] for item in stage_status.audit(project, workflow, stages=stages).values()],
                             ["current", "current"])
            added = project / "review/new.docx"
            added.write_bytes(b"new approved script")
            self.assertIn("added=", stage_status.audit(project, workflow, stages=stages)["1.1"]["reason"])
            added.unlink()
            source.write_bytes(b"changed")
            results = stage_status.audit(project, workflow, target="1.2", stages=stages)
            self.assertEqual(results["1.1"]["status"], "stale")
            self.assertEqual(results["1.2"]["status"], "blocked")
            self.assertEqual({item: (project / item).read_bytes() for item in before}, before)
            self.assertFalse((project / "review/STALE").exists())

    def test_missing_and_unverifiable_markers_are_not_current(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "review"
            stage.mkdir()
            definition = {"step": "1.1", "folder": "review", "inputs": [], "outputs": []}
            self.assertEqual(stage_status.check_marker(root, root, definition)[0], "missing")
            (stage / "DONE").write_text("{}")
            self.assertEqual(stage_status.check_marker(root, root, definition)[0], "unverifiable")
            self.assertEqual((stage / "DONE").read_text(), "{}")

    def test_unrelated_fingerprint_cannot_prove_required_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "review"
            stage.mkdir()
            (stage / "approved.docx").write_bytes(b"approved")
            unrelated = root / "unrelated.txt"
            unrelated.write_text("other")
            (stage / "DONE").write_text(json.dumps({
                "outputs": [], "inputs": [],
                "dependencies": [completion.fingerprint(unrelated, root, "project")]}))
            definition = {"step": "1.1", "folder": "review", "inputs": [], "outputs": [
                {"folder": "review", "filename_regex": "^.+\\.docx$", "minimum": 1}]}
            status, reason = stage_status.check_marker(root, root, definition)
            self.assertEqual(status, "unverifiable")
            self.assertIn("lacks evidence", reason)

    def test_stage_specific_check_is_not_inferred_from_file_presence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "review"
            stage.mkdir()
            output = stage / "approved.docx"
            output.write_bytes(b"approved")
            (stage / "DONE").write_text(json.dumps({"outputs": [completion.fingerprint(output, root, "project")]}))
            definition = {"step": "1.1", "folder": "review", "inputs": [], "requires_stage_check": True,
                          "outputs": [{"folder": "review", "filename_regex": "^.+\\.docx$", "minimum": 1}]}
            self.assertEqual(stage_status.check_marker(root, root, definition)[0], "unverifiable")

    def test_cycle_and_unknown_stage_are_rejected(self):
        stages = {"1.1": {"prerequisites": [{"step": "1.2"}]},
                  "1.2": {"prerequisites": [{"step": "1.1"}]}}
        with self.assertRaisesRegex(ValueError, "Cycle"):
            stage_status.order_stages(stages)
        with self.assertRaisesRegex(ValueError, "Unknown stage"):
            stage_status.order_stages(stages, "9.9")


if __name__ == "__main__":
    unittest.main()
