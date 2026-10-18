"""Behavior checks for range matching, input inventory, and safe stage reruns."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import find_takes


def script(texts):
    return find_takes.sources.script_words({"rows": [
        {"line": i + 1, "text": text} for i, text in enumerate(texts)
    ]})


def transcript(texts):
    words, rows = [], []
    for text in texts:
        start = len(words)
        for token in text.split():
            words.append({"index": len(words), "text": token, "start_ms": None, "end_ms": None})
        rows.append({"word_start": start, "word_end": len(words) - 1})
    return {"words": words, "rows": rows}


class MatcherTests(unittest.TestCase):
    def test_false_period_merge_and_missing_period_split_use_word_spans(self):
        expected = script(["Alpha beta gamma delta.", "Echo foxtrot golf hotel."])
        spoken = transcript(["Alpha beta.", "Gamma delta echo foxtrot golf hotel."])
        untouched = deepcopy(spoken)
        result = find_takes.match_recording(expected, spoken)
        self.assertEqual([link["transcript"] for link in result["links"]], [
            {"word_start": 0, "word_end": 3}, {"word_start": 4, "word_end": 7}])
        self.assertEqual(result["discarded"], [])
        self.assertEqual(spoken, untouched)

    def test_script_cursor_can_return_for_a_repeated_passage(self):
        lines = ["Amber birch cedar dogwood.", "Elm fir ginkgo hazel.", "Iris juniper kelp larch."]
        result = find_takes.match_recording(script(lines), transcript(lines[:2] + lines))
        self.assertEqual([link["script"]["word_start"] for link in result["links"]], [0, 4, 0, 4, 8])
        self.assertTrue(all(word["start_ms"] is None for word in result["transcript"]["words"]))


class GenerationTests(unittest.TestCase):
    def setUp(self):
        # Lifecycle fixtures intentionally omit the full prerequisite graph.
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.prerequisites = stack.enter_context(patch.object(find_takes, "require_prerequisites"))
        self.recheck = stack.enter_context(patch("scripts.validation.require_prerequisites"))
        self.current = stack.enter_context(patch.object(find_takes, "require_current"))

    def test_coverage_requires_non_flub_or_saved_script_discard(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            script_path = root / find_takes.APPROVED / "A.txt"
            script_path.write_text("Amber birch cedar dogwood. Missing filmed line.")
            output = root / find_takes.OUTPUT
            result = find_takes.generate(root, output)
            self.assertFalse(result["complete"])
            target = output / "A__Takes.v1.json"
            state = find_takes.mapping.read_mapping(root, target)
            state["selected"]["script"] = 1
            from studio.tools.line import actions
            actions.discard(None, state, panel="script")
            saved = find_takes.mapping.save_version(root, state, "A__Takes")
            self.assertEqual(Path(saved).name, "A__Takes.v2.json")
            self.assertEqual(Path(saved).parent, find_takes.OUTPUT)
            self.assertTrue(find_takes.check_coverage(root, [Path(saved)])["complete"])
            with patch.object(find_takes, "match_recording", side_effect=AssertionError("Must skip generation")):
                result = find_takes.generate(root, output)
            self.assertTrue(result["complete"])
            self.assertEqual(result["outputs"][0]["version"], 2)
            reopened = find_takes.mapping.read_mapping(root, saved)
            self.assertEqual(reopened["script_discarded"], state["script_discarded"])
            reopened["selected"]["script"] = 1
            actions.restore(None, reopened, panel="script")
            self.assertFalse(find_takes.mapping.script_completion(reopened)["complete"])
            self.assertFalse(find_takes.check_coverage(root, [target])["complete"])

    def test_word_coverage_unions_readings_and_ignores_transcript_discards(self):
        state = find_takes.mapping.new_state()
        state["script"] = script(["One two three four."])
        state["links"] = [
            {"script": {"word_start": 0, "word_end": 1}, "classification": "exact"},
            {"script": {"word_start": 2, "word_end": 3}, "classification": "flub"},
        ]
        state["discarded"] = [{"word_start": 2, "word_end": 3}]
        self.assertEqual(find_takes.mapping.script_completion(state)["uncovered_words"], [2, 3])
        state["links"].append({"script": {"word_start": 2, "word_end": 3}, "classification": "partial"})
        self.assertTrue(find_takes.mapping.script_completion(state)["complete"])
        self.assertFalse(find_takes.mapping.script_completion(find_takes.mapping.new_state())["complete"])

    def fixture(self, root):
        approved = root / find_takes.APPROVED
        transcribed = root / find_takes.TRANSCRIBED
        approved.mkdir(parents=True)
        transcribed.mkdir(parents=True)
        (approved / "A.txt").write_text("Amber birch cedar dogwood.", encoding="utf-8")
        for clip in ("C01", "C02"):
            (transcribed / f"A__{clip}__lav.json").write_text(json.dumps({"words": [
                {"text": text, "start_ms": None, "end_ms": None}
                for text in "Amber birch cedar dogwood.".split()
            ]}), encoding="utf-8")

    def test_multiple_transcripts_reuse_and_stale_file_preservation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            output = root / find_takes.OUTPUT
            self.assertEqual(find_takes.generate(root, output)["transcripts"], 2)
            target = output / "A__Takes.v1.json"
            document = json.loads(target.read_text())
            self.assertEqual(len(document["recordings"]), 2)
            self.assertEqual(len(document["recordings"][0]["links"]), 1)
            document["recordings"][0]["links"][0]["classification"] = "partial"
            target.write_text(json.dumps(document), encoding="utf-8")
            edited = target.read_bytes()
            self.assertEqual(find_takes.generate(root, output)["outputs"][0]["status"], "checked")
            self.assertEqual(target.read_bytes(), edited)
            source = root / find_takes.TRANSCRIBED / "A__C02__lav.json"
            source.write_text(source.read_text() + "\n", encoding="utf-8")
            with patch.object(find_takes, "match_recording", side_effect=AssertionError("Must skip generation")):
                result = find_takes.generate(root, output)
            self.assertFalse(result["complete"])
            self.assertIn("sources or inventory have changed", result["outputs"][0]["error"])
            self.assertEqual(target.read_bytes(), edited)

    def test_base_algorithm_refines_boundaries_and_preserves_legacy_outputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            output = root / find_takes.OUTPUT
            with patch.object(find_takes.boundaries, "propose", wraps=find_takes.boundaries.propose) as propose:
                result = find_takes.generate(root, output)
                self.assertEqual(propose.call_count, 2)
            self.assertEqual(result["transcripts"], 2)
            target = output / "A__Takes.v1.json"
            document = json.loads(target.read_text())
            self.assertEqual(document["algorithm"]["boundary_refinement"], find_takes.boundary_provenance())
            self.assertTrue(all(recording["boundary_analysis"]["selected_count"] == 0 for recording in document["recordings"]))
            self.assertEqual(find_takes.generate(root, output)["outputs"][0]["status"], "checked")
            document["algorithm"] = find_takes.alignment_provenance()
            target.write_text(json.dumps(document), encoding="utf-8")
            original = target.read_bytes()
            with patch.object(find_takes, "match_recording", side_effect=AssertionError("Must skip generation")):
                self.assertTrue(find_takes.generate(root, output)["complete"])
            self.assertEqual(target.read_bytes(), original)

    def test_missing_transcript_is_not_generation_success(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            (root / find_takes.APPROVED / "B.txt").write_text("Other script.", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No 3.2 transcript"):
                find_takes.generate(root, root / find_takes.OUTPUT)
            self.assertFalse((root / find_takes.OUTPUT).exists())

    def test_upstream_input_directory_cannot_be_an_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            with self.assertRaisesRegex(ValueError, "upstream input"):
                find_takes.generate(root, root / find_takes.TRANSCRIBED)

    def test_numeric_latest_version_skips_generation_even_without_v1(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            output = root / find_takes.OUTPUT
            find_takes.generate(root, output)
            first = output / "A__Takes.v1.json"
            good = first.read_bytes()
            (output / "A__Takes.v2.json").write_bytes(good)
            latest = output / "A__Takes.v10.json"
            latest.write_text("invalid json")
            first.unlink()
            with patch.object(find_takes, "match_recording", side_effect=AssertionError("Must skip generation")):
                result = find_takes.generate(root, output)
            self.assertFalse(result["complete"])
            self.assertEqual(result["outputs"][0]["version"], 10)
            self.assertEqual(result["outputs"][0]["path"], str(latest))
            self.assertFalse(first.exists())
            self.assertEqual(latest.read_text(), "invalid json")

    def test_only_unpadded_versions_in_stage_folder_count(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            output = root / find_takes.OUTPUT
            find_takes.generate(root, output)
            first = output / "A__Takes.v1.json"
            legacy = root / "studio/line/A__Takes.v10.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(first.read_bytes())
            first.rename(output / "A__Takes.v001.json")
            result = find_takes.generate(root, output)
            self.assertTrue(result["complete"])
            self.assertEqual(result["outputs"][0]["version"], 1)
            self.assertEqual(result["outputs"][0]["status"], "generated")
            self.assertTrue(first.exists())
            state = find_takes.mapping.read_mapping(root, first)
            saved = find_takes.mapping.save_version(root, state, state["mapping_name"])
            self.assertEqual(Path(saved).name, "A__Takes.v2.json")
            self.assertEqual(Path(saved).parent, find_takes.OUTPUT)

    def test_failed_check_reports_blocking_line_and_cli_exits_one(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            (root / find_takes.APPROVED / "A.txt").write_text("Amber birch cedar dogwood. Missing filmed line.")
            output = root / find_takes.OUTPUT
            result = find_takes.generate(root, output)
            self.assertEqual(result["outputs"][0]["blocking_lines"][0]["text"], "Missing filmed line.")
            self.assertEqual(result["outputs"][0]["blocking_lines"][0]["reason"], "missing")
            self.assertIn("right-click", result["review_instructions"])
            with patch("sys.argv", ["find_takes.py", str(root)]), patch("builtins.print"):
                self.approve(root)
                self.assertEqual(find_takes.main(), 1)

    def approve(self, root):
        review = root / find_takes.APPROVED.parent
        scripts = sorted((root / find_takes.APPROVED).iterdir())
        (review / "DONE").write_text(json.dumps({
            "conditions": {"approved_docx_names": [path.name for path in scripts], "root_candidate_docx_count": 0},
            "outputs": [{"path": str(path.relative_to(root)), "sha256": find_takes.sha256(path)} for path in scripts],
        }))

    def test_one_command_initializes_log_and_writes_verified_done(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            self.approve(root)
            with patch("sys.argv", ["find_takes.py", str(root)]), patch("builtins.print"), \
                    patch.object(find_takes, "record_done", wraps=find_takes.record_done) as record:
                self.assertEqual(find_takes.main(), 0)
            self.prerequisites.assert_called_once_with(root, find_takes.ROOT, "3.3")
            self.current.assert_not_called()
            record.assert_called_once()
            self.assertEqual(record.call_args.args[:3], (root, find_takes.ROOT, "3.3"))
            self.recheck.assert_called_once_with(root, find_takes.ROOT, "3.3")
            stage = root / find_takes.OUTPUT
            done = json.loads((stage / "DONE").read_text())
            self.assertEqual(done["conditions"]["latest_versions"][0]["version"], 1)
            self.assertTrue(all(find_takes.sha256(root / entry["path"]) == entry["sha256"] for entry in done["inputs"] + done["outputs"]))
            original_log = (stage / "LOG.md").read_bytes()
            original_mapping = (stage / "A__Takes.v1.json").read_bytes()
            original_done = (stage / "DONE").read_bytes()
            original_mtime = (stage / "DONE").stat().st_mtime_ns
            with patch.object(find_takes, "match_recording", side_effect=AssertionError("Must skip generation")):
                self.assertTrue(find_takes.run_stage(root)["complete"])
            self.assertTrue((stage / "LOG.md").read_bytes().startswith(original_log))
            self.assertEqual((stage / "A__Takes.v1.json").read_bytes(), original_mapping)
            self.assertEqual((stage / "DONE").read_bytes(), original_done)
            self.assertEqual((stage / "DONE").stat().st_mtime_ns, original_mtime)

    def test_failed_coverage_archives_own_containing_and_future_markers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            (root / find_takes.APPROVED / "A.txt").write_text("Amber birch cedar dogwood. Missing filmed line.")
            self.approve(root)
            folders = [find_takes.OUTPUT, Path("3-Post Production"), Path("3-Post Production/4-Take Selection"), Path("4-Publish/1-Export")]
            for folder in folders:
                (root / folder).mkdir(parents=True, exist_ok=True)
                (root / folder / "DONE").write_text("{}")
                (root / folder / "work.txt").write_text("keep")
            with patch.object(find_takes, "record_done") as record:
                result = find_takes.run_stage(root)
            record.assert_not_called()
            self.assertFalse(result["complete"])
            self.assertFalse(result["done_written"])
            self.assertEqual(len(result["invalidated_markers"]), 4)
            for folder in folders:
                self.assertFalse((root / folder / "DONE").exists())
                self.assertEqual((root / folder / "STALE/1/DONE").read_text(), "{}")
                self.assertEqual((root / folder / "work.txt").read_text(), "keep")
                self.assertTrue((root / folder / "LOG.md").exists())
            self.assertEqual(list(root.rglob("DONE.invalidated*")), [])

    def test_prerequisite_failure_preserves_markers_and_never_generates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            self.approve(root)
            (root / find_takes.APPROVED / "A.txt").write_text("Changed approved script.")
            marker = root / find_takes.APPROVED.parent / "DONE"
            before = marker.read_bytes()
            self.prerequisites.side_effect = ValueError("Approved script fingerprint differs")
            with patch.object(find_takes, "match_recording", side_effect=AssertionError("Must not generate")):
                with self.assertRaisesRegex(ValueError, "fingerprint"):
                    find_takes.run_stage(root)
            self.assertEqual(marker.read_bytes(), before)
            self.assertFalse((root / find_takes.OUTPUT / "LOG.md").exists())
            self.assertEqual(list((root / find_takes.OUTPUT).glob("*__Takes*.json")), [])

    def test_legacy_crlf_fingerprint_reuse_does_not_invalidate_descendants(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            source = root / find_takes.APPROVED / "A.txt"
            source.write_bytes(b"Amber birch cedar dogwood.\r\n")
            self.approve(root)
            self.assertTrue(find_takes.run_stage(root)["complete"])
            marker = root / find_takes.OUTPUT / "DONE"
            legacy = json.loads(marker.read_text())
            for item in legacy["inputs"]:
                if item["path"] == source.relative_to(root).as_posix():
                    item["sha256"] = find_takes.fingerprints.raw_sha256(source)
            marker.write_text(json.dumps(legacy))
            before = marker.read_bytes()
            downstream = root / "3-Post Production/4-Take Selection/DONE"
            downstream.parent.mkdir(parents=True)
            downstream.write_text("preserve independent existing evidence")
            result = find_takes.run_stage(root)
            self.assertTrue(result["complete"])
            self.assertEqual(result["invalidated_markers"], [])
            self.assertEqual(marker.read_bytes(), before)
            self.assertTrue(downstream.is_file())

    def test_invalidation_uses_definitions_not_legacy_manifest_dependencies(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for folder, inputs in ((find_takes.OUTPUT, []),
                                   (Path("3-Post Production/4-Take Selection"), []),
                                   (Path("3-Post Production/6-First Cut"), ["3-Post Production/1-Sync/offsets.json"]),
                                   (Path("3-Post Production/1-Sync"), []),
                                   (Path("3-Post Production/2-Transcribe"), [])):
                target = root / folder
                target.mkdir(parents=True)
                (target / "DONE").write_text(json.dumps({"inputs": [{"scope": "project", "path": path} for path in inputs]}))
            archived = find_takes.invalidate(root, (3, 3), "coverage failed")
            self.assertEqual(len(archived), 3)
            self.assertTrue(all((root / path).is_file() and "/STALE/1/DONE" in path for path in archived))
            self.assertTrue((root / "3-Post Production/1-Sync/DONE").exists())
            self.assertTrue((root / "3-Post Production/2-Transcribe/DONE").exists())

    def test_isolated_run_does_not_modify_project_completion_or_log(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            self.approve(root)
            stage = root / find_takes.OUTPUT
            stage.mkdir(parents=True)
            (stage / "DONE").write_text("keep marker")
            output = root / "trial"
            with patch.object(find_takes, "record_done") as record:
                result = find_takes.run_stage(root, output)
            self.current.assert_called_once_with(root, find_takes.ROOT, "1.1")
            self.prerequisites.assert_not_called()
            self.recheck.assert_not_called()
            record.assert_not_called()
            self.assertTrue(result["complete"])
            self.assertFalse(result["done_written"])
            self.assertEqual((stage / "DONE").read_text(), "keep marker")
            self.assertFalse((stage / "LOG.md").exists())
            self.assertFalse((output / "DONE").exists())

    def test_new_latest_version_invalidates_future_done_before_completing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            self.approve(root)
            find_takes.run_stage(root)
            stage = root / find_takes.OUTPUT
            state = find_takes.mapping.read_mapping(root, stage / "A__Takes.v1.json")
            find_takes.mapping.save_version(root, state, "A__Takes")
            future = root / "4-Publish/1-Export"
            future.mkdir(parents=True)
            (future / "DONE").write_text("{}")
            result = find_takes.run_stage(root)
            self.assertTrue(result["done_written"])
            self.assertFalse((future / "DONE").exists())
            self.assertEqual(json.loads((stage / "DONE").read_text())["conditions"]["latest_versions"][0]["version"], 2)

    def test_flub_only_latest_version_reports_reason_without_regenerating(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            for path in (root / find_takes.TRANSCRIBED).iterdir():
                path.write_text(json.dumps({"words": [{"text": word, "start_ms": None, "end_ms": None}
                                                     for word in "Amber birch.".split()]}))
            output = root / find_takes.OUTPUT
            find_takes.generate(root, output)
            target = output / "A__Takes.v1.json"
            document = json.loads(target.read_text())
            for recording in document["recordings"]:
                recording["links"] = [{"script": document["script_groupings"][0],
                                       "transcript": recording["groupings"][0], "classification": "exact"}]
                recording["discarded"] = []
            target.write_text(json.dumps(document))
            with patch.object(find_takes, "match_recording", side_effect=AssertionError("Must skip generation")):
                result = find_takes.generate(root, output)
            self.assertFalse(result["complete"])
            self.assertEqual(result["outputs"][0]["blocking_lines"][0]["reason"], "flub_only")

    def test_duplicate_latest_numbers_fail_without_falling_back(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            output = root / find_takes.OUTPUT
            find_takes.generate(root, output)
            target = output / "A__Takes.v1.json"
            with patch.object(find_takes.mapping, "takes_versions", return_value=[(1, target), (1, target)]), patch.object(find_takes, "match_recording", side_effect=AssertionError("Must skip generation")):
                result = find_takes.generate(root, output)
            self.assertFalse(result["complete"])
            self.assertIn("Multiple Takes files", result["outputs"][0]["error"])


if __name__ == "__main__":
    unittest.main()
