"""Find Takes completion checks use only temporary, already persisted artifacts."""
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("_test_validate_33", Path(__file__).with_name("validation.py"))
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)
find = validation.load_helpers()


def snapshot(project):
    return {p.relative_to(project).as_posix(): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in (project, *project.rglob("*"))}


class ValidationTests(unittest.TestCase):
    def fixture(self, project):
        script = project / find.APPROVED / "A.txt"
        script.parent.mkdir(parents=True)
        script.write_text("Amber birch cedar dogwood.")
        transcript = project / find.TRANSCRIBED / "A__C01__lav.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"words": [{"text": text, "start_ms": None, "end_ms": None}
                                                   for text in script.read_text().split()]}))
        source = find.sources.load_script(project, script)
        spoken = find.sources.load_transcript(project, transcript)
        group = {"word_start": 0, "word_end": 3}
        document = {"schema": find.SCHEMA, "schema_version": 1, "script": source,
                    "script_groupings": [group], "recordings": [{"transcript": spoken,
                    "groupings": [group], "links": [{"script": group, "transcript": group,
                    "classification": "exact"}], "discarded": []}]}
        path = project / find.OUTPUT / "A__Takes.v1.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(document))
        result = {"outputs": [{"path": str(path), "script": source["source"]["path"], "version": 1}]}
        checker = validation.Validation(project, find.ROOT, {})
        checker.manifest = find.completion_manifest(project, result)
        return checker, path, transcript

    def check_read_only(self, checker, error=None):
        before = snapshot(checker.project)
        with patch.object(validation, "load_helpers", return_value=find), \
                patch.object(find, "generate", side_effect=AssertionError("generation")), \
                patch.object(find, "append_log", side_effect=AssertionError("logging")):
            if error:
                with self.assertRaises(error):
                    checker.validate_stage()
            else:
                self.assertIsNone(checker.validate_stage())
        self.assertEqual(snapshot(checker.project), before)

    def test_success_without_generation_or_new_approval_gate(self):
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            self.check_read_only(checker)

    def test_latest_version_and_ambiguous_version_are_stale(self):
        with TemporaryDirectory() as directory:
            checker, path, _ = self.fixture(Path(directory).resolve())
            newer = path.with_name("A__Takes.v2.json")
            newer.write_bytes(path.read_bytes())
            self.check_read_only(checker, ValueError)
            newer.with_name("a__takes.v2.json").write_bytes(newer.read_bytes())
            self.check_read_only(checker, ValueError)

    def test_new_transcript_or_changed_source_is_stale(self):
        with TemporaryDirectory() as directory:
            checker, _, transcript = self.fixture(Path(directory).resolve())
            transcript.with_name("A__C02__lav.json").write_bytes(transcript.read_bytes())
            self.check_read_only(checker, ValueError)
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            (checker.project / find.APPROVED / "A.txt").write_text("Changed approved script.")
            self.check_read_only(checker, ValueError)

    def test_missing_coverage_and_extra_recorded_output_are_stale(self):
        with TemporaryDirectory() as directory:
            checker, path, _ = self.fixture(Path(directory).resolve())
            document = json.loads(path.read_text())
            document["recordings"][0]["links"] = []
            document["recordings"][0]["discarded"] = document["recordings"][0]["groupings"]
            path.write_text(json.dumps(document))
            self.check_read_only(checker, ValueError)
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            checker.manifest["outputs"].append({"scope": "project", "path": "obsolete.json", "sha256": "0" * 64})
            self.check_read_only(checker, ValueError)

    def test_missing_evidence_is_unverifiable(self):
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            checker.manifest.pop("conditions")
            self.check_read_only(checker, NotImplementedError)

    def test_helper_import_restores_process_state(self):
        before, bytecode = sys.path[:], sys.dont_write_bytecode
        validation.load_helpers()
        self.assertEqual(sys.path, before)
        self.assertEqual(sys.dont_write_bytecode, bytecode)

    def test_common_loader_accepts_persisted_completion_read_only(self):
        from scripts.validation import check_marker
        with TemporaryDirectory() as directory:
            checker, path, _ = self.fixture(Path(directory).resolve())
            definition = json.loads(Path(__file__).parents[1].joinpath("dependencies.json").read_text())
            (path.parent / "DONE").write_text(json.dumps(checker.manifest))
            before = snapshot(checker.project)
            self.assertEqual(check_marker(checker.project, find.ROOT, definition)[0], "current")
            self.assertEqual(snapshot(checker.project), before)


if __name__ == "__main__":
    unittest.main()
