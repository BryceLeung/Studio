"""Verify editorial ranking, source identity, complete scoring, and safe reruns."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import score_takes as scoring


def span(start, end=None):
    return {"word_start": start, "word_end": start if end is None else end}


def link(script, transcript, classification="exact"):
    return {"script": span(script), "transcript": span(transcript), "classification": classification}


def recording(number, links):
    return {"transcript": {"source": {"path": f"A__C{number:02d}__lav.json"}}, "links": links}


def document(*recordings):
    return {"script": {"source": {"path": "A.txt"}},
            "script_groupings": [span(i) for i in range(10)], "recordings": list(recordings)}


def score(doc):
    return scoring.score_document(doc, {"path": "A__Takes.v2.json", "sha256": "a" * 64})


class RankingTests(unittest.TestCase):
    def test_longer_run_beats_latest_and_every_member_gets_whole_run_length(self):
        result = score(document(recording(1, [link(i, i) for i in range(8)]),
                                recording(2, [link(i, i) for i in range(7)])))
        first, later = result["takes"][:8], result["takes"][8:]
        self.assertEqual({t["continuity_sentences"] for t in first}, {8})
        self.assertEqual({t["continuity_sentences"] for t in later}, {7})
        self.assertGreater(min(t["score"] for t in first), max(t["score"] for t in later))

    def test_recency_uses_numeric_recording_then_local_word_order(self):
        result = score(document(recording(10, [link(0, 0)]), recording(2, [link(0, 9), link(0, 99)])))
        self.assertEqual([(t["recording_number"], t["transcript"]["word_start"])
                          for t in result["takes"]], [(2, 9), (2, 99), (10, 0)])
        self.assertEqual([t["score"] for t in result["takes"]], sorted(t["score"] for t in result["takes"]))

    def test_partial_has_same_weight_and_flub_breaks_run_but_is_scored(self):
        doc = document(recording(1, [link(0, 0), link(1, 1, "partial"), link(2, 2, "flub"), link(3, 3)]))
        untouched = deepcopy(doc)
        result = score(doc)
        self.assertEqual(doc, untouched)
        self.assertEqual([t["continuity_sentences"] for t in result["takes"]], [2, 2, 0, 1])
        changed = deepcopy(doc)
        changed["recordings"][0]["links"][1]["classification"] = "exact"
        self.assertEqual([t["score"] for t in result["takes"]], [t["score"] for t in score(changed)["takes"]])
        self.assertTrue(all(type(t["score"]) is int for t in result["takes"]))

    def test_unmapped_gap_repeat_rewind_skip_and_recording_boundary_break_runs(self):
        result = score(document(recording(1, [link(0, 0), link(1, 2), link(1, 3),
                                               link(0, 4), link(2, 5)]), recording(2, [link(3, 0)])))
        self.assertEqual([t["continuity_sentences"] for t in result["takes"]], [1] * 6)

    def test_empty_input_and_ambiguous_chronology_fail(self):
        for doc in (document(recording(1, [])), document(recording(1, [link(0, 0)]), recording(1, [link(0, 1)]))):
            with self.assertRaises(ValueError):
                score(doc)

    def test_identity_and_coverage_preserve_recording_and_both_ranges(self):
        doc = document(recording(1, [link(0, 0)]), recording(2, [link(0, 0)]))
        result = score(doc)
        self.assertEqual(len({t["take_key"] for t in result["takes"]}), 2)
        result["takes"].pop()
        with self.assertRaises(ValueError):
            scoring.validate_scores(doc, result)
        result = score(doc)
        result["takes"][0]["score"] = float("nan")
        with self.assertRaises(ValueError):
            scoring.validate_scores(doc, result)


class StageTests(unittest.TestCase):
    def fixture(self, project):
        approved, transcribed = project / scoring.find.APPROVED, project / scoring.find.TRANSCRIBED
        approved.mkdir(parents=True)
        transcribed.mkdir(parents=True)
        script = approved / "A.txt"
        script.write_text("Amber birch cedar dogwood. Elm fir ginkgo hazel.", encoding="utf-8")
        transcript = transcribed / "A__C01__lav.json"
        transcript.write_text(json.dumps({"words": [
            {"text": text, "start_ms": None, "end_ms": None}
            for text in script.read_text().split()]}), encoding="utf-8")
        result = scoring.find.generate(project, project / scoring.find.OUTPUT)
        self.assertTrue(result["complete"])
        return Path(result["outputs"][0]["path"])

    def test_find_takes_v1_and_line_v2_v3_produce_identical_rankings(self):
        with TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            path = self.fixture(project)
            original = json.loads(path.read_text())
            recording = original["recordings"][0]
            v2 = {"schema": scoring.find.mapping.SCHEMA, "schema_version": 2,
                  "mapping_name": "A__Takes", "version": 2,
                  "sources": {"script": original["script"], "transcript": recording["transcript"]},
                  "groupings": {"script": original["script_groupings"], "transcript": recording["groupings"]},
                  "links": recording["links"], "discarded": recording["discarded"]}
            v3 = dict(original, schema=scoring.find.mapping.SCHEMA, schema_version=3,
                      mapping_name="A__Takes", version=3)
            results = []
            for doc in (original, v2, v3):
                path.write_text(json.dumps(doc), encoding="utf-8")
                result = score(scoring.load_mapping(project, path))
                results.append([(t["script"], t["transcript"], t["classification"], t["score"])
                                for t in result["takes"]])
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0], results[2])

    def test_isolated_stage_writes_all_scores_preserves_matching_outputs_and_edits(self):
        with TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            path = self.fixture(project)
            original = path.read_bytes()
            output = project / "trial"
            report = scoring.run_stage(project, [path], output)
            self.assertTrue(report["complete"])
            self.assertFalse(report["done_written"])
            target = Path(report["outputs"][0])
            first = target.read_bytes()
            self.assertEqual(report["take_count"], 2)
            scoring.run_stage(project, [path], output)
            self.assertEqual(target.read_bytes(), first)
            self.assertEqual(path.read_bytes(), original)
            edited = json.loads(first)
            edited["takes"][0]["score"] = -99
            target.write_text(json.dumps(edited), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inspect before replacing"):
                scoring.run_stage(project, [path], output)
            self.assertEqual(json.loads(target.read_text()), edited)
            self.assertFalse((output / "DONE").exists())

    def test_wrong_latest_version_and_changed_sources_fail(self):
        with TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            path = self.fixture(project)
            newer = path.with_name("A__Takes.v2.json")
            newer.write_bytes(path.read_bytes())
            with self.assertRaisesRegex(ValueError, "latest numbered"):
                scoring.run_stage(project, [path], project / "trial")
            (project / scoring.find.APPROVED / "A.txt").write_text("Changed script.")
            with self.assertRaisesRegex(ValueError, "current script"):
                scoring.run_stage(project, [newer], project / "trial")

    def test_project_done_lifecycle_and_failed_run_invalidates_only_markers(self):
        with TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            path = self.fixture(project)
            script = project / scoring.find.APPROVED / "A.txt"
            scoring.find.write_json(project / scoring.find.APPROVED.parent / "DONE", {
                "conditions": {"approved_docx_names": ["A.txt"], "root_candidate_docx_count": 0},
                "outputs": [{"path": script.relative_to(project).as_posix(), "sha256": scoring.find.sha256(script)}]})
            generated = scoring.find.generate(project, project / scoring.find.OUTPUT)
            scoring.find.write_json(project / scoring.find.OUTPUT / "DONE",
                                    scoring.find.completion_manifest(project, generated))
            report = scoring.run_stage(project, [path])
            marker = project / scoring.STAGE / "DONE"
            self.assertTrue(report["done_written"])
            self.assertTrue(marker.is_file())
            scoring.run_stage(project, [path])
            target = Path(report["outputs"][0])
            edited = json.loads(target.read_text())
            edited["takes"][0]["score"] = -1
            target.write_text(json.dumps(edited), encoding="utf-8")
            with self.assertRaises(ValueError):
                scoring.run_stage(project, [path])
            self.assertFalse(marker.exists())
            self.assertTrue((project / scoring.find.OUTPUT / "DONE").exists())
            self.assertEqual(json.loads(target.read_text()), edited)


if __name__ == "__main__":
    unittest.main()
