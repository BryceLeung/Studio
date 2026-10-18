"""Verify existing JSON adoption, unchanged reuse, and stale/missing input handling."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import check_transcripts as check


def project_files(project):
    intake = project / check.INTAKE
    intake.mkdir(parents=True)
    (intake / "DONE").write_text("{}")
    audio = intake / "A__C01__lav.wav"
    audio.write_bytes(b"original audio")
    directory = project / check.STAGE
    directory.mkdir(parents=True)
    output = directory / "A__C01__lav.json"
    output.write_text(json.dumps({"segments": [{"text": "hello"}]}))
    return audio, output


class TranscriptionCompletionTests(unittest.TestCase):
    def setUp(self):
        # Unit fixtures isolate transcript lifecycle; common graph tests cover ancestors.
        for target in ("check_transcripts.require_prerequisites", "scripts.validation.require_prerequisites"):
            guard = patch(target, return_value={})
            guard.start()
            self.addCleanup(guard.stop)

    def test_normal_path_uses_shared_preflight_and_writer(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            project_files(project)
            with patch.object(check, "require_prerequisites", return_value={}) as preflight, \
                    patch.object(check.completion, "record_done", wraps=check.completion.record_done) as writer:
                check.run(project)
                preflight.assert_called_once_with(project, check.ROOT, "3.2")
                self.assertEqual(writer.call_count, 1)

    def test_rejected_prerequisite_does_not_mutate_project(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            project_files(project)
            before = {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()}
            with patch.object(check, "require_prerequisites", side_effect=ValueError("ancestor blocked")):
                with self.assertRaisesRegex(ValueError, "ancestor blocked"):
                    check.run(project)
            self.assertEqual({path.relative_to(project): path.read_bytes()
                              for path in project.rglob("*") if path.is_file()}, before)

    def test_first_regeneration_output_only_archive_can_complete_without_old_done(self):
        from scripts.stage_invalidation import prepare_regeneration
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            _, output = project_files(project)
            subtitle = output.with_suffix(".srt")
            subtitle.write_text("old subtitles")
            reference = output.parent / "Human Reference.txt"
            reference.write_text("preserve benchmark correction")
            previous = output.read_bytes()
            slot = prepare_regeneration(project, check.ROOT, "3.2", [output, subtitle], "replace selected engine outputs")
            self.assertEqual((slot / output.name).read_bytes(), previous)
            self.assertFalse((slot / "DONE").exists())
            output.write_text(json.dumps({"words": [], "regenerated": True}))
            self.assertEqual(check.run(project)["status"], "adopted")
            self.assertFalse((output.parent / "STALE/PENDING.json").exists())
            self.assertEqual((slot / subtitle.name).read_text(), "old subtitles")
            self.assertEqual(reference.read_text(), "preserve benchmark correction")

    def test_existing_json_is_adopted_and_unchanged_rerun_reuses_done(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            audio, output = project_files(project)
            output_bytes = output.read_bytes()
            first = check.run(project)
            marker = project / check.STAGE / "DONE"
            completed = marker.read_bytes()
            self.assertEqual(first["status"], "adopted")
            self.assertEqual(len(first["manifest"]["inputs"]), 1)
            self.assertEqual(len(first["manifest"]["outputs"]), 1)
            self.assertEqual(check.run(project)["status"], "reused")
            self.assertEqual(marker.read_bytes(), completed)
            self.assertEqual(output.read_bytes(), output_bytes)
            self.assertEqual(first["manifest"]["inputs"][0]["method"], "media-stat")
            self.assertEqual(first["manifest"]["inputs"][0]["size_bytes"], audio.stat().st_size)

    def test_changed_audio_cannot_reuse_unchanged_json(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            audio, output = project_files(project)
            check.run(project)
            downstream = project / "3-Post Production/3-Find Takes"
            downstream.mkdir()
            (downstream / "DONE").write_text("{}")
            previous = output.read_bytes()
            audio.write_bytes(b"changed audio")
            with self.assertRaisesRegex(ValueError, "not regenerated"):
                check.run(project)
            self.assertFalse((project / check.STAGE / "DONE").exists())
            self.assertFalse((downstream / "DONE").exists())
            self.assertTrue((project / check.STAGE / "STALE/1/DONE").is_file())
            self.assertTrue((downstream / "STALE/1/DONE").is_file())
            self.assertEqual(output.read_bytes(), previous)
            with self.assertRaisesRegex(ValueError, "regenerate unchanged transcript"):
                check.run(project)
            output.write_text(json.dumps({"segments": [{"text": "new audio transcription"}]}))
            self.assertEqual(check.run(project)["status"], "adopted")
            self.assertFalse((project / check.STAGE / "Stale Transcripts.json").exists())

    def test_missing_or_invalid_json_never_marks_completion(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            _, output = project_files(project)
            output.unlink()
            with self.assertRaisesRegex(ValueError, "missing="):
                check.run(project)
            self.assertFalse((project / check.STAGE / "DONE").exists())
            output.write_text("not JSON")
            with self.assertRaisesRegex(ValueError, "Invalid transcript JSON"):
                check.run(project)
            self.assertFalse((project / check.STAGE / "DONE").exists())

    def test_changed_transcript_updates_manifest_without_whisperx(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            _, output = project_files(project)
            first = check.run(project)["manifest"]
            output.write_text(json.dumps({"segments": [{"text": "corrected"}]}))
            second = check.run(project)["manifest"]
            self.assertNotEqual(first["outputs"], second["outputs"])
            self.assertEqual(first["inputs"], second["inputs"])
            self.assertTrue((project / check.STAGE / "STALE/1/DONE").is_file())

    def test_archived_done_prevents_readopting_unchanged_json_after_audio_change(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            audio, output = project_files(project)
            check.run(project)
            check.completion.archive_done(project / check.STAGE)
            audio.write_bytes(b"new audio with different size")
            with self.assertRaisesRegex(ValueError, "not regenerated"):
                check.run(project)
            self.assertFalse((project / check.STAGE / "DONE").exists())
            self.assertTrue(output.is_file())

    def test_existing_archive_is_not_replaced_and_old_hash_marker_is_archived(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            audio, output = project_files(project)
            marker = project / check.STAGE / "DONE"
            archive = marker.parent / "STALE/1"
            archive.mkdir(parents=True)
            (archive / "DONE").write_text("older archive")
            current = check.manifest(project, [audio], [output])
            current["inputs"][0] = {"scope": "project", "path": current["inputs"][0]["path"],
                                     "sha256": check.sha256(audio)}
            marker.write_text(json.dumps(current))
            self.assertEqual(check.run(project)["status"], "adopted")
            self.assertEqual((archive / "DONE").read_text(), "older archive")
            self.assertEqual(json.loads((marker.parent / "STALE/2/DONE").read_text())["inputs"][0], current["inputs"][0])
            self.assertEqual(check.run(project)["status"], "reused")

    def test_symlinked_stage_folder_is_rejected_before_logging(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            intake = project / check.INTAKE
            intake.mkdir(parents=True)
            (intake / "DONE").write_text("{}")
            outside = root / "outside"
            outside.mkdir()
            (project / "3-Post Production").mkdir()
            (project / check.STAGE).symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "outside project"):
                check.run(project)
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
