"""Manual overrides are explicit attestations, not stage executions."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import override_snapshot, stage_completion as completion, stage_override as override, validation


def tree(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.project, self.workflow = root / "project", root / "workflow"
        self.project.mkdir()
        self.workflow.mkdir()
        self.stages = validation.load_stages()
        paths = {Path(path) for path in override_snapshot.CRITICAL_FILES}
        paths.add(Path("scripts/append-log.py"))
        for definition in self.stages.values():
            folder = Path(".agents/skills/studio") / definition["folder"]
            paths.update((folder / "SKILL.md", folder / "dependencies.json"))
            paths.update(Path(path) for path in definition.get("workflow_dependencies", []))
            paths.update(path.relative_to(validation.ROOT) for path in (validation.ROOT / folder / "scripts").glob("*.py")
                         if not path.name.startswith("test_"))
            paths.update(path.relative_to(validation.ROOT) for path in
                         (validation.ROOT / folder / "scripts").glob("requirements*.txt"))
        for path in paths:
            target = self.workflow / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(validation.ROOT / path, target)
        self.artifacts = []
        names = {
            "1.1": ["Approved/A.docx"], "1.2": ["A.pptx"],
            "1.3": ["APPROVED/Short-Form Excerpt Shortlist.md"],
            "2.1": ["A__C01__camA.mov", "A__C01__camB.mp4", "A__C01__lav.wav"],
            "3.1": ["A__C01.json"], "3.2": ["A__C01__lav.json"],
            "3.3": ["A__Takes.v1.json"], "3.4": ["A__Scores.v1.json"],
            "3.5": ["Face Paths.json"], "3.6": ["APPROVED/A.mov"],
        }
        for step, files in names.items():
            folder = self.folder(step)
            folder.mkdir(parents=True, exist_ok=True)
            outputs = []
            for name in files:
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"Existing content accepted by user, not semantically verified")
                self.artifacts.append(path)
                outputs.append(completion.fingerprint(path, self.project, "project"))
            self.marker(step).write_text(json.dumps({"step": step, "inputs": [], "outputs": outputs,
                                                    "dependencies": []}))

    def folder(self, step):
        return self.project / self.stages[step]["folder"]

    def marker(self, step):
        return self.folder(step) / "DONE"

    def plan(self, stage="all"):
        return override.preview(self.project, self.workflow, stage=stage, reason="Workflow maintenance; accept existing outputs")

    def apply(self, plan):
        return override.apply(self.project, self.workflow, stage=plan["requested"], reason=plan["reason"],
                              confirm=plan["plan_id"], actor="Explicit test-user confirmation")

    def test_preview_is_read_only_and_reports_scope_warning_and_impacts(self):
        before = tree(self.project)
        plan = self.plan()
        self.assertTrue(plan["can_apply"], plan["blocked"])
        self.assertEqual(plan["stages"], validation.order_stages(self.stages))
        self.assertIn("without rerunning", plan["warning"])
        self.assertEqual(len(plan["impacts"]), 10)
        self.assertEqual(tree(self.project), before)
        self.assertEqual(plan["plan_id"], self.plan()["plan_id"])

    def test_all_waives_stage_checks_and_subsequent_status_imports_no_stage_plugins(self):
        original = importlib.util.spec_from_file_location

        def no_stage_plugins(name, location, *args, **kwargs):
            if Path(location).name == "validation.py" and ".agents/skills/studio/" in str(location):
                raise AssertionError("Stage validator must not be imported for manual acceptance")
            return original(name, location, *args, **kwargs)

        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.artifacts}
        with patch.object(importlib.util, "spec_from_file_location", side_effect=no_stage_plugins):
            result = self.apply(self.plan())
            report = validation.audit(self.project, self.workflow)
        self.assertEqual(len(result["applied"]), 10)
        self.assertTrue(all(item["status"] == "current" for item in report.values()), report)
        for step in self.stages:
            manifest = json.loads(self.marker(step).read_text())
            self.assertEqual(manifest["completion_mode"], "manual-override")
            self.assertTrue(manifest["override"]["confirmed"])
            self.assertNotIn("conditions", manifest)
            self.assertTrue((self.folder(step) / "STALE/1/DONE").exists())
            self.assertIn("Manual override accepted", (self.folder(step) / "LOG.md").read_text())
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.artifacts}, before)

    def test_override_of_current_stage_still_archives_active_marker(self):
        first = self.plan("1.1")
        self.apply(first)
        old = self.marker("1.1").read_bytes()
        second = self.plan("1.1")
        self.assertTrue(second["can_apply"])
        self.apply(second)
        self.assertEqual((self.folder("1.1") / "STALE/2/DONE").read_bytes(), old)
        self.assertNotEqual(self.marker("1.1").read_bytes(), old)

    def test_scoped_override_invalidates_dependents_but_not_their_outputs(self):
        before = {path: path.read_bytes() for path in self.artifacts}
        result = self.apply(self.plan("1.1"))
        self.assertEqual(result["applied"], ["1.1"])
        self.assertFalse(self.marker("1.2").exists())
        self.assertTrue((self.folder("1.2") / "STALE/1/DONE").exists())
        self.assertEqual({path: path.read_bytes() for path in self.artifacts}, before)

    def test_scoped_override_requires_nonselected_prerequisites_current(self):
        before = tree(self.project)
        plan = self.plan("3.2")
        self.assertFalse(plan["can_apply"])
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.apply(plan)
        self.assertEqual(tree(self.project), before)

    def test_never_completed_stages_are_skipped_for_all_and_block_explicit_selection(self):
        self.marker("3.6").unlink()
        plan = self.plan()
        self.assertTrue(plan["can_apply"], plan["blocked"])
        self.assertIn("3.6", plan["skipped"])
        self.apply(plan)
        self.assertFalse(self.marker("3.6").exists())
        explicit = self.plan("3.6")
        self.assertFalse(explicit["can_apply"])

    def test_archived_completion_is_eligible_without_empty_extra_archive(self):
        original = self.marker("1.1").read_bytes()
        saved = completion.archive_done(self.folder("1.1"))
        self.apply(self.plan("1.1"))
        self.assertEqual(saved.read_bytes(), original)
        self.assertFalse((self.folder("1.1") / "STALE/2").exists())

    def test_prior_completion_reference_points_to_real_archive_and_survives_cleanup(self):
        self.apply(self.plan("1.1"))
        manifest = json.loads(self.marker("1.1").read_text())
        reference = manifest["override"]["prior_done"]
        archived = self.project / reference["path"]
        self.assertNotEqual(archived, self.marker("1.1"))
        self.assertEqual(override.fingerprints.raw_sha256(archived), reference["sha256"])
        archived.unlink()  # Finished archives are user-managed, not live dependencies.
        self.assertEqual(validation.audit(self.project, self.workflow, "1.1")["1.1"]["status"], "current")

    def test_malformed_or_unrelated_history_does_not_prove_prior_completion(self):
        valid = json.loads(self.marker("1.1").read_text())["outputs"][0]
        for output in ({"path": "unrelated/nonexistent", "method": "nonsense"},
                       dict(valid, sha256="invalid hash"), dict(valid, scope="workflow"),
                       {"scope": "project", "path": valid["path"], "method": "media-stat", "size_bytes": 1, "mtime_s": 1}):
            with self.subTest(output=output):
                self.marker("1.1").write_text(json.dumps({"outputs": [output]}))
                before = tree(self.project)
                plan = self.plan("1.1")
                self.assertFalse(plan["can_apply"])
                with self.assertRaisesRegex(ValueError, "blocked"):
                    self.apply(plan)
                self.assertEqual(tree(self.project), before)

    def test_missing_last_stage_outputs_block_the_whole_batch(self):
        (self.folder("3.6") / "APPROVED/A.mov").unlink()
        before = tree(self.project)
        plan = self.plan()
        self.assertFalse(plan["can_apply"])
        self.assertIn("3.6", plan["blocked"])
        with self.assertRaises(ValueError):
            self.apply(plan)
        self.assertEqual(tree(self.project), before)

    def test_confirmation_is_bound_to_reason_scope_and_current_files(self):
        plan = self.plan()
        before = tree(self.project)
        with self.assertRaisesRegex(ValueError, "Confirmation"):
            override.apply(self.project, self.workflow, stage="all", reason=plan["reason"],
                           confirm="0" * 64, actor="user")
        self.assertEqual(tree(self.project), before)
        (self.folder("3.2") / "A__C01__lav.json").write_bytes(b"new transcript")
        changed = tree(self.project)
        with self.assertRaisesRegex(ValueError, "Confirmation"):
            self.apply(plan)
        self.assertEqual(tree(self.project), changed)

    def test_source_output_helpers_and_new_takes_invalidate_override(self):
        self.apply(self.plan())
        take = self.folder("3.3") / "A__Takes.v2.json"
        take.write_bytes(b"new review version")
        outcome = validation.audit(self.project, self.workflow)
        self.assertNotEqual(outcome["3.3"]["status"], "current")
        self.assertEqual(outcome["3.1"]["status"], "current")
        take.unlink()
        self.assertEqual(validation.audit(self.project, self.workflow)["3.3"]["status"], "current")
        helper = self.workflow / "scripts/new_flow_helper.py"
        helper.write_text("# new helper\n")
        self.assertNotEqual(validation.audit(self.project, self.workflow)["1.1"]["status"], "current")

    def test_guard_file_presence_is_frozen_without_enforcing_its_contents(self):
        guard = self.folder("3.2") / "Stale Transcripts.json"
        guard.write_text('{"assumed": "valid despite guard"}')
        self.apply(self.plan())
        self.assertEqual(validation.audit(self.project, self.workflow)["3.2"]["status"], "current")
        guard.unlink()
        self.assertNotEqual(validation.audit(self.project, self.workflow)["3.2"]["status"], "current")

    def test_normal_recording_cannot_publish_override_evidence(self):
        self.apply(self.plan("1.1"))
        manual = json.loads(self.marker("1.1").read_text())
        before = tree(self.project)
        with self.assertRaisesRegex(ValueError, "explicit preview"):
            completion.record_done(self.project, self.workflow, "1.1", manual)
        self.assertEqual(tree(self.project), before)

    def test_normal_render_validation_can_replace_override_without_false_input_changes(self):
        self.apply(self.plan())
        movie = self.folder("3.6") / "APPROVED/A.mov"
        before = movie.read_bytes(), movie.stat().st_mtime_ns
        ordinary = completion.build_manifest(self.project, self.workflow, "3.6")
        completion.record_done(self.project, self.workflow, "3.6", ordinary)
        self.assertNotIn("completion_mode", json.loads(self.marker("3.6").read_text()))
        self.assertEqual(validation.audit(self.project, self.workflow, "3.6")["3.6"]["status"], "current")
        self.assertEqual((movie.read_bytes(), movie.stat().st_mtime_ns), before)

    def test_later_io_failure_returns_durable_partial_progress(self):
        plan = self.plan()
        original = completion._record_manual_override

        def fail_second(project, workflow, step, manifest):
            if step == "1.2":
                raise OSError("simulated publication failure")
            return original(project, workflow, step, manifest)

        with patch.object(completion, "_record_manual_override", side_effect=fail_second):
            with self.assertRaises(override.OverrideApplyError) as caught:
                self.apply(plan)
        self.assertEqual(caught.exception.report["status"], "partial")
        self.assertEqual(caught.exception.report["applied"], ["1.1"])
        self.assertEqual(caught.exception.report["remaining"][0], "1.2")
        self.assertTrue(json.loads(self.marker("1.1").read_text())["override"]["confirmed"])
        self.assertFalse(self.marker("1.2").exists())
        self.assertTrue((self.folder("1.2") / "STALE/1/DONE").exists())

    def test_post_publication_cleanup_failure_reports_applied_marker(self):
        from scripts import stage_invalidation
        plan = self.plan("1.1")
        with patch.object(stage_invalidation, "clear_pending", side_effect=OSError("cleanup failed after publish")):
            with self.assertRaises(override.OverrideApplyError) as caught:
                self.apply(plan)
        report = caught.exception.report
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["applied"], ["1.1"])
        self.assertTrue(report["marker_published"])
        self.assertEqual(report["remaining"], [])
        self.assertEqual(json.loads(self.marker("1.1").read_text())["completion_mode"], "manual-override")

    def test_changed_active_marker_during_recording_is_preserved(self):
        plan = self.plan("1.1")
        original = completion._record_manual_override
        replacement = json.loads(self.marker("1.1").read_text())
        replacement["changed_by_another_operator"] = True

        def replace_active(project, workflow, step, manifest):
            self.marker(step).write_text(json.dumps(replacement))
            return original(project, workflow, step, manifest)

        with patch.object(completion, "_record_manual_override", side_effect=replace_active):
            with self.assertRaises(override.OverrideApplyError) as caught:
                self.apply(plan)
        self.assertFalse(caught.exception.report["marker_published"])
        self.assertEqual(caught.exception.report["applied"], [])
        self.assertEqual(json.loads(self.marker("1.1").read_text()), replacement)

    def test_incomplete_attestation_cannot_be_honored_as_current(self):
        self.apply(self.plan("1.1"))
        manifest = json.loads(self.marker("1.1").read_text())
        manifest["override"]["confirmed"] = False
        self.marker("1.1").write_text(json.dumps(manifest))
        self.assertEqual(validation.check_marker(self.project, self.workflow, self.stages["1.1"])[0], "unverifiable")

    def test_locked_later_stage_blocks_preview_without_any_mutation(self):
        (self.folder("3.6") / ".DONE.lock").mkdir()
        before = tree(self.project)
        plan = self.plan()
        self.assertFalse(plan["can_apply"])
        with self.assertRaises(ValueError):
            self.apply(plan)
        self.assertEqual(tree(self.project), before)
        self.assertTrue((self.folder("3.6") / ".DONE.lock").exists())

    def test_cli_requires_confirmation_and_preview_is_compact_read_only(self):
        command = [sys.executable, "-B", str(validation.ROOT / "scripts/stage_override.py")]
        common = ["--project-root", str(self.project), "--stage", "1.1", "--reason", "manual maintenance"]
        before = tree(self.project)
        refused = subprocess.run([*command, "apply", *common], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        preview = subprocess.run([*command, "preview", *common], capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        result = json.loads(preview.stdout)
        self.assertTrue(result["can_apply"], result["blocked"])
        self.assertIn("accepted_files", result)
        self.assertNotIn("snapshots", result)
        self.assertEqual(tree(self.project), before)
        applied = subprocess.run([*command, "apply", *common, "--confirm", result["plan_id"],
                                  "--actor", "explicit user confirmation"], capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["applied"], ["1.1"])


if __name__ == "__main__":
    unittest.main()
