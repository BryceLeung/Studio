"""Normal completion uses candidate validation and a single safe marker writer."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import stage_completion as completion
from scripts import validation


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.stages = validation.load_stages()
        approved = self.project / self.stages["1.1"]["outputs"][0]["folder"]
        approved.mkdir(parents=True)
        self.script = approved / "A.docx"
        self.script.write_bytes(b"approved script")

    def manifest(self, step):
        return completion.build_manifest(self.project, validation.ROOT, step)

    def marker(self, step):
        return self.project / self.stages[step]["folder"] / "DONE"

    def record(self, step):
        return completion.record_done(self.project, validation.ROOT, step, self.manifest(step))

    def test_candidate_is_validated_without_writing_a_marker(self):
        candidate = self.manifest("1.1")
        self.assertEqual(validation.validate_evidence(self.project, validation.ROOT, self.stages["1.1"], candidate)[0], "current")
        self.assertFalse(self.marker("1.1").exists())
        candidate["outputs"] = []
        self.assertNotEqual(validation.validate_evidence(self.project, validation.ROOT, self.stages["1.1"], candidate)[0], "current")
        with self.assertRaisesRegex(ValueError, "Cannot complete"):
            completion.record_done(self.project, validation.ROOT, "1.1", candidate)
        self.assertFalse(self.marker("1.1").exists())

    def test_agent_owned_approval_steps_share_the_writer_and_graph(self):
        self.record("1.1")
        deck = self.project / self.stages["1.2"]["outputs"][0]["folder"] / "A.pptx"
        deck.parent.mkdir(parents=True)
        deck.write_bytes(b"deck")
        self.record("1.2")
        shortlist = self.project / self.stages["1.3"]["outputs"][0]["folder"] / "Short-Form Excerpt Shortlist.md"
        shortlist.parent.mkdir(parents=True)
        shortlist.write_text("# No extracts\n")
        saved = self.record("1.3")
        self.assertTrue(any(item["path"].endswith("EXCERPT-PREFERENCES.md") for item in saved["dependencies"]))
        outcomes = validation.audit(self.project, target="1.3")
        self.assertTrue(all(item["status"] == "current" for item in outcomes.values()))
        self.script.write_bytes(b"changed input")
        before = self.marker("1.3").read_bytes()
        with self.assertRaises(validation.PrerequisiteError):
            self.record("1.3")
        self.assertEqual(self.marker("1.3").read_bytes(), before)

    def test_prerequisites_do_not_require_the_target_marker(self):
        self.record("1.1")
        result = validation.require_prerequisites(self.project, validation.ROOT, "1.2")
        self.assertEqual(list(result), ["1.1"])
        self.assertFalse(self.marker("1.2").exists())
        self.assertEqual(validation.require_prerequisites(self.project, validation.ROOT, "1.1"), {})

    def test_unchanged_record_reuses_original_bytes_and_time(self):
        saved = self.record("1.1")
        before, stamp = self.marker("1.1").read_bytes(), self.marker("1.1").stat().st_mtime_ns
        again = self.record("1.1")
        self.assertEqual(saved, again)
        self.assertEqual(self.marker("1.1").read_bytes(), before)
        self.assertEqual(self.marker("1.1").stat().st_mtime_ns, stamp)
        self.assertFalse((self.marker("1.1").parent / "STALE").exists())

    def test_compatible_legacy_hash_entries_are_not_rewritten_on_reuse(self):
        legacy = self.manifest("1.1")
        for section in ("inputs", "outputs", "dependencies"):
            for entry in legacy[section]:
                entry.pop("method", None)
        self.marker("1.1").write_text(json.dumps(legacy))
        before = self.marker("1.1").read_bytes()
        self.record("1.1")
        self.assertEqual(self.marker("1.1").read_bytes(), before)
        self.assertFalse((self.marker("1.1").parent / "STALE").exists())

    def test_reuse_comparison_does_not_discard_extra_saved_dependencies(self):
        current = self.manifest("1.1")
        previous = dict(current, dependencies=[*current["dependencies"], current["dependencies"][0]])
        self.assertFalse(completion.same_manifest(previous, current, self.project, validation.ROOT))

    def test_replaced_record_preserves_previous_marker(self):
        self.record("1.1")
        before = self.marker("1.1").read_bytes()
        self.script.write_bytes(b"revised approved script")
        self.record("1.1")
        archive = self.marker("1.1").parent / "STALE/1/DONE"
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual(validation.check_marker(self.project, validation.ROOT, self.stages["1.1"])[0], "current")

    def test_failed_atomic_replace_restores_previous_active_marker(self):
        self.record("1.1")
        before = self.marker("1.1").read_bytes()
        self.script.write_bytes(b"revised approved script")
        with patch.object(completion.os, "replace", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(OSError, "write failed"):
                self.record("1.1")
        self.assertEqual(self.marker("1.1").read_bytes(), before)
        self.assertEqual(list(self.marker("1.1").parent.glob(".DONE-*")), [])

    def test_malformed_and_wrong_stage_evidence_cannot_write(self):
        candidate = self.manifest("1.1")
        candidate["step"] = "1.2"
        with self.assertRaises(ValueError):
            completion.record_done(self.project, validation.ROOT, "1.1", candidate)
        with self.assertRaises(ValueError):
            completion.record_done(self.project, validation.ROOT, "1.1", [])
        self.assertFalse(self.marker("1.1").exists())

    def test_completion_cannot_omit_shared_flow_and_instruction_fingerprints(self):
        candidate = self.manifest("1.1")
        candidate["dependencies"] = []
        with self.assertRaisesRegex(ValueError, "lacks workflow dependencies"):
            completion.record_done(self.project, validation.ROOT, "1.1", candidate)
        self.assertFalse(self.marker("1.1").exists())

    def test_stage_implementation_dependency_cannot_be_omitted_from_supplied_evidence(self):
        definition = self.stages["3.3"]
        stage = Path(".agents/skills/studio") / definition["folder"]
        paths = {stage / "SKILL.md", stage / "dependencies.json", stage / "scripts/validation.py",
                 Path("scripts/validation.py"), Path("scripts/stage_completion.py"),
                 Path("scripts/stage_invalidation.py"), Path("studio/fingerprints.py")}
        paths.update(Path(path) for path in definition["workflow_dependencies"])
        omitted = stage / "scripts/find_takes.py"
        candidate = {"step": "3.3", "inputs": [], "outputs": [], "dependencies": [
            completion.fingerprint(validation.ROOT / path, validation.ROOT, "workflow")
            for path in sorted(paths) if path != omitted]}
        with patch("scripts.validation.require_prerequisites", return_value={}):
            with self.assertRaisesRegex(ValueError, "find_takes.py"):
                completion.record_done(self.project, validation.ROOT, "3.3", candidate)
        self.assertFalse(self.marker("3.3").exists())

    def test_inventory_only_recording_cannot_bypass_rich_stage_evidence(self):
        for step in ("3.2", "3.3", "3.4", "3.5"):
            with self.subTest(step=step), self.assertRaisesRegex(ValueError, "normal checker/generator"):
                completion.build_manifest(self.project, validation.ROOT, step)

    def test_atomic_publication_keeps_active_marker_visible_and_serializes_recorders(self):
        self.record("1.1")
        before = self.marker("1.1").read_bytes()
        self.script.write_bytes(b"revised approved script")
        replace = completion.os.replace

        def publish(source, destination):
            self.assertEqual(self.marker("1.1").read_bytes(), before)
            self.assertEqual((self.marker("1.1").parent / "STALE/1/DONE").read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "already active or interrupted"):
                self.record("1.1")
            return replace(source, destination)

        with patch.object(completion.os, "replace", side_effect=publish):
            self.record("1.1")
        self.assertNotEqual(self.marker("1.1").read_bytes(), before)
        self.assertFalse((self.marker("1.1").parent / ".DONE.lock").exists())

    def test_existing_recording_lock_is_not_removed_or_overridden(self):
        lock = self.marker("1.1").parent / ".DONE.lock"
        lock.mkdir()
        with self.assertRaisesRegex(ValueError, "already active or interrupted"):
            self.record("1.1")
        self.assertTrue(lock.is_dir())
        self.assertFalse(self.marker("1.1").exists())

    def test_project_mutation_guard_queues_another_process_and_is_reentrant(self):
        command = (
            "import sys; from scripts.stage_completion import mutation_guard; "
            "print('waiting', flush=True); "
            "\nwith mutation_guard(sys.argv[1]): print('acquired', flush=True)"
        )
        with completion.mutation_guard(self.project):
            with completion.mutation_guard(self.project):
                child = subprocess.Popen([sys.executable, "-c", command, str(self.project)],
                                         cwd=validation.ROOT, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), "waiting")
                    self.assertIsNone(child.poll())
                except BaseException:
                    child.kill()
                    child.communicate()
                    raise
        try:
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), "acquired")
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()

    def test_completion_clears_all_job_pointers_but_preserves_archives(self):
        from scripts.stage_invalidation import invalidate, read_pending
        self.record("1.1")
        directory = self.marker("1.1").parent
        for job in ("A__C01", "A__C02"):
            invalidate(self.project, validation.ROOT, "1.1", "job attempt", job_id=job)
        slots = [read_pending(directory, "1.1", job_id=job) for job in ("A__C01", "A__C02")]
        self.record("1.1")
        self.assertIsNone(read_pending(directory, "1.1"))
        for job, slot in zip(("A__C01", "A__C02"), slots):
            self.assertIsNone(read_pending(directory, "1.1", job_id=job))
            self.assertTrue((slot / "EVENT.json").is_file())

    def test_invalid_job_pointer_prevents_completion_without_clearing_other_pointers(self):
        from scripts.stage_invalidation import invalidate
        directory = self.marker("1.1").parent
        invalidate(self.project, validation.ROOT, "1.1", "job attempt", job_id="A__C01")
        pointers = list((directory / "STALE/PENDING").glob("*.json"))
        self.assertEqual(len(pointers), 1)
        pointers[0].write_text("invalid JSON")
        with self.assertRaises(ValueError):
            self.record("1.1")
        self.assertFalse(self.marker("1.1").exists())
        self.assertTrue((directory / "STALE/PENDING.json").exists())

    def test_manual_recording_cannot_bypass_job_lifecycle_gate(self):
        candidate = self.manifest("1.1")
        candidate["completion_mode"] = "manual-override"
        jobs = self.marker("1.1").parent / ".SYNC-JOBS"
        jobs.mkdir()
        job = jobs / "interrupted.json"
        job.write_text("{}")
        with self.assertRaisesRegex(ValueError, "active or interrupted"):
            completion._record_manual_override(self.project, validation.ROOT, "1.1", candidate)
        self.assertFalse(self.marker("1.1").exists())
        self.assertTrue(job.exists())

    def test_existing_override_is_not_current_while_a_job_is_active(self):
        from scripts import stage_override
        self.record("1.1")
        plan = stage_override.preview(self.project, validation.ROOT, stage="1.1", reason="fixture acceptance")
        stage_override.apply(self.project, validation.ROOT, stage="1.1", reason="fixture acceptance",
                             confirm=plan["plan_id"], actor="fixture")
        self.assertEqual(validation.check_marker(self.project, validation.ROOT, self.stages["1.1"])[0], "current")
        jobs = self.marker("1.1").parent / ".SYNC-JOBS"
        jobs.mkdir()
        (jobs / "active.json").write_text("{}")
        status, reason = validation.check_marker(self.project, validation.ROOT, self.stages["1.1"])
        self.assertNotEqual(status, "current")
        self.assertIn("active or interrupted", reason)

    def test_replaced_recording_lock_is_not_removed_and_prevents_publication(self):
        self.record("1.1")
        before = self.marker("1.1").read_bytes()
        self.script.write_bytes(b"changed approved source")
        archive = completion.archive_done
        lock = self.marker("1.1").parent / ".DONE.lock"

        def archive_and_replace_lock(directory, **options):
            saved = archive(directory, **options)
            lock.rename(lock.with_name("retired-lock"))
            lock.mkdir()
            return saved

        with patch.object(completion, "archive_done", side_effect=archive_and_replace_lock):
            with self.assertRaisesRegex(ValueError, "ownership changed"):
                self.record("1.1")
        self.assertTrue(lock.is_dir())
        self.assertEqual(self.marker("1.1").read_bytes(), before)

    def test_cli_record_and_prerequisite_status_share_the_normal_graph(self):
        recorded = subprocess.run([sys.executable, "-B", str(validation.ROOT / "scripts/stage_completion.py"),
                                   "record", "--project-root", str(self.project), "--stage", "1.1"],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["status"], "complete")
        before = self.marker("1.1").read_bytes()
        status = subprocess.run([sys.executable, "-B", str(validation.ROOT / "scripts/validation.py"),
                                 "--project-root", str(self.project), "--stage", "1.2", "--prerequisites"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(list(json.loads(status.stdout)), ["1.1"])
        self.assertFalse(self.marker("1.2").exists())
        self.assertEqual(self.marker("1.1").read_bytes(), before)

    def test_changed_completion_archives_descendants_without_moving_outputs(self):
        self.record("1.1")
        deck = self.project / self.stages["1.2"]["outputs"][0]["folder"] / "A.pptx"
        deck.parent.mkdir(parents=True)
        deck.write_bytes(b"human edited deck")
        self.record("1.2")
        before = self.marker("1.2").read_bytes()
        self.script.write_bytes(b"revised source")
        self.record("1.1")
        self.assertFalse(self.marker("1.2").exists())
        self.assertEqual((self.marker("1.2").parent / "STALE/1/DONE").read_bytes(), before)
        self.assertEqual(deck.read_bytes(), b"human edited deck")

    def test_new_completion_also_invalidates_orphaned_descendant_markers(self):
        downstream = self.marker("1.2")
        downstream.parent.mkdir(parents=True)
        downstream.write_text("historical orphan")
        self.record("1.1")
        self.assertFalse(downstream.exists())
        self.assertEqual((downstream.parent / "STALE/1/DONE").read_text(), "historical orphan")

    def test_unchanged_completion_does_not_invalidate_descendants(self):
        self.record("1.1")
        downstream = self.marker("1.2")
        downstream.parent.mkdir(parents=True)
        downstream.write_text("preserve")
        self.record("1.1")
        self.assertEqual(downstream.read_text(), "preserve")
        self.assertFalse((downstream.parent / "STALE").exists())

    def test_regeneration_cli_preserves_deck_and_completion_clears_pending_pointer(self):
        self.record("1.1")
        deck = self.project / self.stages["1.2"]["outputs"][0]["folder"] / "A.pptx"
        deck.parent.mkdir(parents=True)
        deck.write_bytes(b"previous deck")
        prepared = subprocess.run([sys.executable, "-B", str(validation.ROOT / "scripts/stage_completion.py"),
                                   "prepare-regeneration", "--project-root", str(self.project), "--stage", "1.2",
                                   "--reason", "approved replacement", "--file", deck.relative_to(self.project).as_posix()],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        slot = self.project / json.loads(prepared.stdout)["archive"]
        self.assertEqual((slot / "A.pptx").read_bytes(), b"previous deck")
        self.assertFalse(deck.exists())
        self.assertTrue((deck.parent / "STALE/PENDING.json").is_file())
        deck.write_bytes(b"new deck")
        self.record("1.2")
        self.assertFalse((deck.parent / "STALE/PENDING.json").exists())
        self.assertEqual((slot / "A.pptx").read_bytes(), b"previous deck")


if __name__ == "__main__":
    unittest.main()
