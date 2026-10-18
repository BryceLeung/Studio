"""Validate saved camera paths without a detector, model, or project writes."""
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("_test_validate_35", Path(__file__).with_name("validation.py"))
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)
track = validation.load_helpers()
find = track.find


def snapshot(project):
    return {p.relative_to(project).as_posix(): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in (project, *project.rglob("*"))}


class ValidationTests(unittest.TestCase):
    def fixture(self, project, *, empty=False):
        if not empty:
            script = project / find.APPROVED / "A.txt"
            script.parent.mkdir(parents=True)
            script.write_text("Amber birch cedar dogwood.")
            clips = {}
            for role, suffix in (("camA", "mov"), ("camB", "mov"), ("lav", "wav")):
                media = project / track.INTAKE / f"A__C01__{role}.{suffix}"
                media.parent.mkdir(parents=True, exist_ok=True)
                media.write_bytes(b"temporary media")
                stat = media.stat()
                clips[role] = {"path": media.relative_to(project).as_posix(), "offset_ms": 0,
                               "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            transcript = project / find.TRANSCRIBED / "A__C01__lav.json"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(json.dumps({"source_audio": clips["lav"]["path"], "words": [
                {"text": text, "start_ms": 1000 + 100 * i, "end_ms": 1100 + 100 * i}
                for i, text in enumerate(script.read_text().split())]}))
            group = {"word_start": 0, "word_end": 3}
            document = {"schema": find.SCHEMA, "schema_version": 1,
                        "script": find.sources.load_script(project, script), "script_groupings": [group],
                        "recordings": [{"transcript": find.sources.load_transcript(project, transcript),
                        "groupings": [group], "links": [{"script": group, "transcript": group,
                        "classification": "exact"}], "discarded": []}]}
            mapping = project / find.OUTPUT / "A__Takes.v1.json"
            mapping.parent.mkdir(parents=True)
            mapping.write_text(json.dumps(document))
            (mapping.parent / "DONE").write_text(json.dumps({"conditions": {
                "latest_versions": [{"path": mapping.relative_to(project).as_posix()}]}}))
            sync = project / track.SYNC / "A__C01.json"
            sync.parent.mkdir(parents=True)
            sync.write_text(json.dumps({"reference_role": "camA", "clips": clips,
                "offset_convention": "positive_means_file_starts_later_than_camA"}))
            shortlist = project / track.SHORTLIST
            shortlist.parent.mkdir(parents=True)
            shortlist.write_text("## 1. Example\n\n- **Review status:** Approved\n- **Source:** A.txt\n\n"
                                 "### Provisional spoken script\n\nAmber birch cedar dogwood.\n")
        shortlist = project / track.SHORTLIST
        if not shortlist.exists():
            shortlist.parent.mkdir(parents=True, exist_ok=True)
            shortlist.write_text("# No viable extracts\n")
        extracts = track.approved_passages(shortlist.read_text()) if shortlist.exists() else []
        ranges, references = track.required_ranges(project, extracts)
        inputs = [track.fingerprint(shortlist, project)] if shortlist.exists() else []
        inputs.extend(json.loads(item) for item in references)
        dependencies = [Path(track.__file__), Path(track.__file__).parents[1] / "SKILL.md",
                        Path(track.__file__).parents[1] / "requirements.txt",
                        track.FIND_SCRIPT, track.ROOT / "studio/tools/line/mapping.py",
                        track.ROOT / "scripts/append-log.py", track.ROOT / "studio/fingerprints.py"]
        identity = {"schema": "studio.face-path", "schema_version": 1, "sample_fps": track.SAMPLE_FPS,
                    "padding_ms": track.PADDING_MS, "model_sha256": track.MODEL_SHA256, "inputs": inputs,
                    "dependencies": [{"scope": "workflow", "path": p.relative_to(track.ROOT).as_posix(),
                                      "sha256": find.sha256(p)} for p in dependencies]}
        directory = project / track.STAGE
        directory.mkdir(parents=True)
        paths = []
        for (media, role), spans in sorted(ranges.items(), key=lambda item: (str(item[0][0]), item[0][1])):
            intervals = [[round(float(a * 1000)), round(float(b * 1000))] for a, b in spans]
            result = dict(identity, media=track.media_fingerprint(media, project), role=role,
                          ranges_ms=intervals, width=640, height=360, warnings=[], frames=[
                          {"time_ms": timestamp, "status": "centered", "face": None}
                          for start, end in intervals for timestamp in (start, end)])
            output = directory / (media.stem + "__FacePath.v1.json")
            output.write_text(json.dumps(result))
            paths.append(track.fingerprint(output, project))
        index = directory / "Face Paths.json"
        index.write_text(json.dumps({"schema": "studio.face-path-index", "schema_version": 1,
                                     "inputs": inputs, "dependencies": identity["dependencies"],
                                     "paths": paths, "no_extracts": not extracts}))
        checker = validation.Validation(project, track.ROOT, {})
        checker.manifest = {"inputs": inputs, "outputs": [*paths, track.fingerprint(index, project)],
                            "conditions": {"no_extracts": not extracts,
                                           "face_paths": [p["path"] for p in paths]}}
        return checker, [project / p["path"] for p in paths], index

    def check_read_only(self, checker, error=None):
        before = snapshot(checker.project)
        with patch.object(validation, "load_helpers", return_value=track), \
                patch.object(track, "run", side_effect=AssertionError("run")), \
                patch.object(track, "model_path", side_effect=AssertionError("model")), \
                patch.object(track, "analyze_video", side_effect=AssertionError("detector")), \
                patch.object(track, "urlopen", side_effect=AssertionError("network")), \
                patch.object(find, "append_log", side_effect=AssertionError("logging")):
            if error:
                with self.assertRaises(error):
                    checker.validate_stage()
            else:
                self.assertIsNone(checker.validate_stage())
        self.assertEqual(snapshot(checker.project), before)

    def test_both_camera_paths_and_empty_extract_index_are_read_only(self):
        for empty in (False, True):
            with self.subTest(empty=empty), TemporaryDirectory() as directory:
                checker, paths, _ = self.fixture(Path(directory).resolve(), empty=empty)
                self.assertEqual(len(paths), 0 if empty else 2)
                self.check_read_only(checker)

    def test_changed_ranges_role_box_model_and_missing_samples_are_stale(self):
        for field in ("ranges_ms", "role", "face", "model_sha256", "frames"):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                checker, paths, _ = self.fixture(Path(directory).resolve())
                result = json.loads(paths[0].read_text())
                if field == "ranges_ms":
                    result[field][0][1] += 1
                elif field == "role":
                    result[field] = "camB"
                elif field == "face":
                    result["frames"][0]["face"] = [2, 0, 0, 0]
                elif field == "frames":
                    result[field] = []
                else:
                    result[field] = "wrong"
                paths[0].write_text(json.dumps(result))
                self.check_read_only(checker, ValueError)

    def test_index_must_include_exact_required_cameras(self):
        with TemporaryDirectory() as directory:
            checker, _, index = self.fixture(Path(directory).resolve())
            result = json.loads(index.read_text())
            result["paths"].pop()
            index.write_text(json.dumps(result))
            self.check_read_only(checker, ValueError)

    def test_new_latest_mapping_and_changed_sync_are_stale(self):
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            mapping = checker.project / find.OUTPUT / "A__Takes.v1.json"
            mapping.with_name("A__Takes.v2.json").write_bytes(mapping.read_bytes())
            self.check_read_only(checker, ValueError)
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            sync = checker.project / track.SYNC / "A__C01.json"
            result = json.loads(sync.read_text())
            result["clips"]["camB"]["offset_ms"] = 100
            sync.write_text(json.dumps(result))
            self.check_read_only(checker, ValueError)

    def test_sync_reference_cannot_escape_project(self):
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve())
            sync = checker.project / track.SYNC / "A__C01.json"
            result = json.loads(sync.read_text())
            result["clips"]["camA"]["path"] = "../outside.mov"
            sync.write_text(json.dumps(result))
            self.check_read_only(checker, ValueError)

    def test_missing_completion_evidence_is_unverifiable(self):
        with TemporaryDirectory() as directory:
            checker, _, _ = self.fixture(Path(directory).resolve(), empty=True)
            checker.manifest.pop("conditions")
            self.check_read_only(checker, NotImplementedError)

    def test_helper_import_restores_process_state(self):
        before, bytecode = sys.path[:], sys.dont_write_bytecode
        validation.load_helpers()
        self.assertEqual(sys.path, before)
        self.assertEqual(sys.dont_write_bytecode, bytecode)

    def test_common_loader_accepts_persisted_completion_read_only(self):
        from scripts.validation import check_marker
        for empty in (False, True):
            with self.subTest(empty=empty), TemporaryDirectory() as directory:
                checker, _, index = self.fixture(Path(directory).resolve(), empty=empty)
                definition = json.loads(Path(__file__).parents[1].joinpath("dependencies.json").read_text())
                (index.parent / "DONE").write_text(json.dumps(checker.manifest))
                before = snapshot(checker.project)
                self.assertEqual(check_marker(checker.project, track.ROOT, definition)[0], "current")
                self.assertEqual(snapshot(checker.project), before)


if __name__ == "__main__":
    unittest.main()
