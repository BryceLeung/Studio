"""Verify existing JSON adoption, unchanged reuse, and stale/missing input handling."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

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
            self.assertEqual(first["manifest"]["inputs"][0]["sha256"], check.sha256(audio))

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


if __name__ == "__main__":
    unittest.main()
