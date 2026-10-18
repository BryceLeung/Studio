"""Read-only checks reject edited scores and superseded mappings."""
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("_test_validate_34", Path(__file__).with_name("validation.py"))
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)
scoring = validation.load_helpers()
find = scoring.find


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
        group = {"word_start": 0, "word_end": 3}
        document = {"schema": find.SCHEMA, "schema_version": 1,
                    "script": find.sources.load_script(project, script), "script_groupings": [group],
                    "recordings": [{"transcript": find.sources.load_transcript(project, transcript),
                    "groupings": [group], "links": [{"script": group, "transcript": group,
                    "classification": "exact"}], "discarded": []}]}
        mapping = project / find.OUTPUT / "A__Takes.v1.json"
        mapping.parent.mkdir(parents=True)
        mapping.write_text(json.dumps(document))
        result = scoring.score_document(scoring.load_mapping(project, mapping),
                                        scoring.fingerprint(mapping, project, "project"))
        output = project / scoring.STAGE / "A__Scores.v1.json"
        output.parent.mkdir(parents=True)
        output.write_text(json.dumps(result))
        checker = validation.Validation(project, scoring.ROOT, {})
        checker.manifest = {"inputs": [scoring.fingerprint(p, project, "project")
                                       for p in (mapping, script, transcript)],
                            "outputs": [scoring.fingerprint(output, project, "project")],
                            "conditions": {"latest_mapping_paths": [mapping.relative_to(project).as_posix()],
                                           "every_take_scored": True, "take_count": 1}}
        return checker, mapping, output

    def check_read_only(self, checker, error=None):
        before = snapshot(checker.project)
        with patch.object(validation, "load_helpers", return_value=scoring), \
                patch.object(scoring, "run_stage", side_effect=AssertionError("run")), \
                patch.object(find, "generate", side_effect=AssertionError("generation")), \
                patch.object(find, "append_log", side_effect=AssertionError("logging")):
            if error:
                with self.assertRaises(error):
                    checker.validate_stage()
            else:
                self.assertIsNone(checker.validate_stage())
        self.assertEqual(snapshot(checker.project), before)

    def test_success_and_exact_recomputation_are_read_only(self):
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            self.check_read_only(checker)

    def test_edited_score_identity_algorithm_and_missing_take_are_stale(self):
        for field in ("score", "take_key", "algorithm", "takes"):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                checker, _, output = self.fixture(Path(directory).resolve())
                document = json.loads(output.read_text())
                if field == "algorithm":
                    document[field]["version"] = 99
                elif field == "takes":
                    document[field] = []
                else:
                    document["takes"][0][field] = -1 if field == "score" else "wrong"
                output.write_text(json.dumps(document))
                self.check_read_only(checker, ValueError)

    def test_new_latest_mapping_and_transcript_inventory_are_stale(self):
        with TemporaryDirectory() as directory:
            checker, mapping, _ = self.fixture(Path(directory).resolve())
            mapping.with_name("A__Takes.v2.json").write_bytes(mapping.read_bytes())
            self.check_read_only(checker, ValueError)
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            transcript = checker.project / find.TRANSCRIBED / "A__C01__lav.json"
            transcript.with_name("A__C02__lav.json").write_bytes(transcript.read_bytes())
            self.check_read_only(checker, ValueError)

    def test_missing_manifest_evidence_is_unverifiable(self):
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
            checker, _, output = self.fixture(Path(directory).resolve())
            definition = json.loads(Path(__file__).parents[1].joinpath("dependencies.json").read_text())
            (output.parent / "DONE").write_text(json.dumps(checker.manifest))
            before = snapshot(checker.project)
            self.assertEqual(check_marker(checker.project, scoring.ROOT, definition)[0], "current")
            self.assertEqual(snapshot(checker.project), before)


if __name__ == "__main__":
    unittest.main()
