"""Behavior checks for range matching, input inventory, and safe stage reruns."""
from copy import deepcopy
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
            target = output / "A__Takes.json"
            document = json.loads(target.read_text())
            self.assertEqual(len(document["recordings"]), 2)
            self.assertEqual(len(document["recordings"][0]["links"]), 1)
            document["recordings"][0]["links"][0]["classification"] = "partial"
            target.write_text(json.dumps(document), encoding="utf-8")
            edited = target.read_bytes()
            self.assertEqual(find_takes.generate(root, output)["outputs"][0]["status"], "reused")
            self.assertEqual(target.read_bytes(), edited)
            source = root / find_takes.TRANSCRIBED / "A__C02__lav.json"
            source.write_text(source.read_text() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Preserved stale/invalid"):
                find_takes.generate(root, output)
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
            target = output / "A__Takes.json"
            document = json.loads(target.read_text())
            self.assertEqual(document["algorithm"]["boundary_refinement"], find_takes.boundary_provenance())
            self.assertTrue(all(recording["boundary_analysis"]["selected_count"] == 0 for recording in document["recordings"]))
            self.assertEqual(find_takes.generate(root, output)["outputs"][0]["status"], "reused")
            document["algorithm"] = find_takes.alignment_provenance()
            target.write_text(json.dumps(document), encoding="utf-8")
            original = target.read_bytes()
            with self.assertRaisesRegex(ValueError, "Preserved stale/invalid"):
                find_takes.generate(root, output)
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
                find_takes.generate(root, root / find_takes.TRANSCRIBED, overwrite=True)


if __name__ == "__main__":
    unittest.main()
