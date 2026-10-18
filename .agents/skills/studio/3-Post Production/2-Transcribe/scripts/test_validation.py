"""Transcript validation never changes temporary project evidence."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import stage_completion as completion
from scripts.validation import validate_evidence
from studio.fingerprints import sha256


SOURCE = Path(__file__).with_name("validation.py")
SPEC = importlib.util.spec_from_file_location("test_transcribe_stage_validation", SOURCE)
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)
ROOT = next(path for path in SOURCE.resolve().parents if (path / "studio/fingerprints.py").is_file())


def snapshot(root):
    return {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None) for path in root.rglob("*")}


class TranscribeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.definition = json.loads(SOURCE.parents[1].joinpath("dependencies.json").read_text())
        self.intake = self.project / self.definition["inputs"][0]["folder"]
        self.destination = self.project / self.definition["folder"]
        self.intake.mkdir(parents=True)
        self.destination.mkdir(parents=True)
        self.audio = self.intake / "A__C01__lav.wav"
        self.audio.write_bytes(b"audio")
        self.output = self.destination / "A__C01__lav.json"
        self.output.write_text(json.dumps({"segments": [{"text": "hello"}]}))
        self.validator = validation.Validation(self.project, ROOT, self.definition)
        self.record()

    def record(self):
        self.validator.manifest = {
            "inputs": [completion.fingerprint(self.audio, self.project, "project")],
            "outputs": [completion.fingerprint(self.output, self.project, "project")],
        }

    def check_unchanged(self, error=None):
        before = snapshot(self.project)
        if error:
            with self.assertRaises(error):
                self.validator.validate_stage()
        else:
            self.assertIsNone(self.validator.validate_stage())
        self.assertEqual(snapshot(self.project), before)

    def test_matching_json_success_is_read_only(self):
        self.check_unchanged()

    def test_common_validation_accepts_complete_manifest_without_writes(self):
        (self.destination / "DONE").write_text(json.dumps(self.validator.manifest))
        before = snapshot(self.project)
        self.assertEqual(self.validator.validate()[0], "current")
        self.assertEqual(snapshot(self.project), before)

    def test_missing_extra_empty_malformed_and_nonobject_json_fail(self):
        self.output.unlink()
        self.check_unchanged(ValueError)
        for contents in ("", "not JSON", "[]", "null"):
            with self.subTest(contents=contents):
                self.output.write_text(contents)
                self.check_unchanged(ValueError)
        self.output.write_text("{}")
        (self.destination / "Other.json").write_text("{}")
        self.check_unchanged(ValueError)

    def test_empty_or_new_unrecorded_audio_fails(self):
        self.audio.write_bytes(b"")
        self.check_unchanged(ValueError)
        self.audio.write_bytes(b"audio")
        (self.intake / "B__C01__lav.wav").write_bytes(b"new audio")
        (self.destination / "B__C01__lav.json").write_text("{}")
        self.check_unchanged(ValueError)

    def test_stale_guard_blocks_unchanged_but_preserves_changed_output(self):
        stale = self.destination / "Stale Transcripts.json"
        stale.write_text(json.dumps({self.output.relative_to(self.project).as_posix(): sha256(self.output)}))
        self.check_unchanged(ValueError)
        self.output.write_text('{"segments": []}')
        self.record()
        self.check_unchanged()
        self.assertTrue(stale.is_file())

    def test_malformed_guard_fails_read_only(self):
        stale = self.destination / "Stale Transcripts.json"
        for contents in ("[]", "not JSON", '{"path": 12}'):
            with self.subTest(contents=contents):
                stale.write_text(contents)
                self.check_unchanged(ValueError)

    def test_changed_audio_is_caught_by_common_fingerprints_without_writes(self):
        (self.destination / "DONE").write_text(json.dumps(self.validator.manifest))
        self.audio.write_bytes(b"changed audio")
        before = snapshot(self.project)
        status, _ = self.validator.validate()
        self.assertEqual(status, "stale")
        self.assertEqual(snapshot(self.project), before)

    def save_history(self, archived=False):
        marker = self.destination / ("STALE/1/DONE" if archived else "DONE")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(self.validator.manifest))
        return marker

    def check_candidate(self, expected):
        before = snapshot(self.project)
        status, reason = validate_evidence(self.project, ROOT, self.definition, self.validator.manifest)
        self.assertEqual(status, expected, reason)
        self.assertEqual(snapshot(self.project), before)
        return reason

    def test_candidate_without_history_permits_first_adoption(self):
        self.check_candidate("current")

    def test_candidate_rejects_changed_audio_unchanged_json_without_guard(self):
        for archived in (False, True):
            with self.subTest(archived=archived):
                marker = self.save_history(archived)
                self.audio.write_bytes(b"different longer audio")
                self.record()
                self.assertFalse((self.destination / "Stale Transcripts.json").exists())
                self.assertIn("regenerate unchanged transcript", self.check_candidate("stale"))
                marker.unlink()
                self.audio.write_bytes(b"audio")
                self.record()

    def test_changed_json_allows_candidate_with_active_or_archived_history(self):
        for archived in (False, True):
            with self.subTest(archived=archived):
                marker = self.save_history(archived)
                self.audio.write_bytes(self.audio.read_bytes() + b"different")
                self.output.write_text(self.output.read_text() + " ")
                self.record()
                self.check_candidate("current")
                marker.unlink()

    def test_new_audio_path_cannot_reuse_historical_json(self):
        self.save_history()
        renamed = self.audio.with_suffix(".WAV")
        self.audio.rename(renamed)
        self.audio = renamed
        self.record()
        self.assertIn("regenerate unchanged transcript", self.check_candidate("stale"))

    def test_equal_active_candidate_does_not_compare_older_archive(self):
        self.save_history(archived=True)
        self.audio.write_bytes(b"different audio")
        self.record()
        self.save_history()
        self.check_candidate("current")

    def test_latest_numbered_archive_is_baseline(self):
        first = self.save_history(archived=True)
        self.output.write_text('{"new": true}')
        self.record()
        latest = first.parent.parent / "10/DONE"
        latest.parent.mkdir()
        latest.write_text(json.dumps(self.validator.manifest))
        self.audio.write_bytes(b"changed audio")
        self.record()
        self.check_candidate("stale")

    def test_malformed_history_fails_closed_read_only(self):
        baseline = json.loads(json.dumps(self.validator.manifest))
        malformed = [[], {}, {"inputs": [], "outputs": []},
                     dict(baseline, step="3.1"), dict(baseline, inputs=[None]),
                     dict(baseline, outputs=[dict(baseline["outputs"][0], sha256="invalid")]),
                     dict(baseline, inputs=[dict(baseline["inputs"][0], path="../outside.wav")]),
                     dict(baseline, outputs=[dict(baseline["outputs"][0], scope="workflow")]),
                     dict(baseline, inputs=[dict(baseline["inputs"][0], size_bytes="5")]),
                     dict(baseline, outputs=baseline["outputs"] * 2)]
        for archived in (False, True):
            marker = self.save_history(archived)
            for document in malformed:
                with self.subTest(archived=archived, document=document):
                    marker.write_text(json.dumps(document))
                    self.check_candidate("stale")
            marker.write_text("not JSON")
            self.check_candidate("stale")
            marker.unlink()

    def test_archive_and_entry_escapes_fail_read_only(self):
        with TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory)
            (outside / "DONE").write_text(json.dumps(self.validator.manifest))
            archive = self.destination / "STALE"
            archive.symlink_to(outside, target_is_directory=True)
            before = snapshot(outside)
            self.check_candidate("stale")
            archive.unlink()
            archive.mkdir()
            slot = archive / "1"
            slot.symlink_to(outside, target_is_directory=True)
            self.check_candidate("stale")
            slot.unlink()
            slot.mkdir()
            (slot / "DONE").symlink_to(outside / "DONE")
            self.check_candidate("stale")
            self.assertEqual(snapshot(outside), before)

    def test_historical_entry_symlink_escape_fails_read_only(self):
        marker = self.save_history()
        baseline = json.loads(marker.read_text())
        with TemporaryDirectory() as directory:
            outside = Path(directory) / "old.wav"
            outside.write_bytes(b"old audio")
            link = self.intake / "Old__C01__lav.wav"
            link.symlink_to(outside)
            baseline["inputs"][0]["path"] = link.relative_to(self.project).as_posix()
            marker.write_text(json.dumps(baseline))
            before = snapshot(Path(directory))
            project_before = snapshot(self.project)
            with self.assertRaisesRegex(ValueError, "escapes project"):
                self.validator.historical_pairing([self.audio], [self.output])
            self.assertEqual(snapshot(self.project), project_before)
            self.assertEqual(snapshot(Path(directory)), before)

    def test_incomplete_latest_archive_fails_closed(self):
        self.save_history(archived=True)
        (self.destination / "STALE/2").mkdir()
        self.check_candidate("stale")


if __name__ == "__main__":
    unittest.main()
