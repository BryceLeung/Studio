"""Verify continuous source boundaries, camera flips, auditions, and XML wiring."""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import json

import first_cut as cut


def span(i):
    return {"word_start": i, "word_end": i}


def fixture(root):
    source = "3-Post Production/2-Transcribe/A__C01__lav.json"
    words = [{"text": str(i), "start_ms": start, "end_ms": end}
             for i, (start, end) in enumerate(((1000, 1500), (1700, 2200), (3000, 3500), (4000, 4500)))]
    groups = [span(i) for i in range(3)]
    document = {"script_groupings": groups, "script": {"words": [{"text": str(i)} for i in range(3)]},
                "recordings": [{"transcript": {"source": {"path": source}, "words": words}}]}
    takes = [{"recording_number": 1, "transcript_source": {"path": source}, "script": span(s),
              "transcript": span(t), "take_key": f"take{i}", "score": score, "classification": quality}
             for i, (s, t, score, quality) in enumerate(((0, 0, 40, "exact"), (1, 1, 41, "partial"),
                                                        (0, 2, 5, "flub"), (2, 3, 20, "exact")))]
    clips = {}
    for role in cut.ROLES:
        path = cut.INTAKE / f"A__C01__{role}.{'wav' if role == 'lav' else 'mov'}"
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"test media placeholder")
        clips[role] = {"path": path.as_posix(), "offset_ms": 0}
    syncs = {1: {"clips": clips}}
    video = {"r_frame_rate": "30/1", "width": 3840, "height": 2160,
             "color_primaries": "bt709", "color_transfer": "bt709"}
    audio = [{"channels": 2, "sample_rate": "48000"}]
    probes = {1: {role: {"duration": Fraction(10), "video": video if role != "lav" else None,
                        "audio": audio} for role in cut.ROLES}}
    return document, {"takes": takes}, syncs, probes


class FirstCutTests(unittest.TestCase):
    def test_stage_path_and_failure_invalidation_use_3_6(self):
        self.assertEqual(cut.STAGE, Path("3-Post Production/6-First Cut"))
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            score = project / cut.scoring.STAGE / "A__Scores.v1.json"
            score.parent.mkdir(parents=True)
            score.write_text("{}")
            with patch.object(cut.find, "append_log"), patch.object(cut, "require_prerequisites") as prerequisites, \
                    patch.object(cut.find, "discover", return_value=[(project / "A.docx", [])]), \
                    patch.object(cut, "read_json", side_effect=TypeError("invalid input")):
                with patch.object(cut.find, "invalidate") as invalidate:
                    with self.assertRaisesRegex(TypeError, "invalid input"):
                        cut.run(project, [score])
                    self.assertEqual(invalidate.call_args.args[:2], (project, (3, 6)))
                prerequisites.assert_called_once_with(project, cut.ROOT, "3.6")

    def test_bad_arguments_do_not_invalidate_or_log_current_completion(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            with patch.object(cut, "require_prerequisites"), \
                    patch.object(cut.find, "discover", return_value=[(project / "A.docx", [])]), \
                    patch.object(cut.find, "invalidate") as invalidate, patch.object(cut.find, "append_log") as log:
                with self.assertRaisesRegex(ValueError, "score file"):
                    cut.run(project, [])
                with self.assertRaisesRegex(ValueError, "nonnegative"):
                    cut.run(project, [], padding_ms=-1)
                invalidate.assert_not_called()
                log.assert_not_called()

    def test_prerequisite_failure_is_read_only(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            with patch.object(cut, "require_prerequisites", side_effect=ValueError("stale ancestor")) as prerequisites, \
                    patch.object(cut.find, "invalidate") as invalidate, \
                    patch.object(cut.find, "append_log") as log, \
                    patch.object(cut, "validate_done") as legacy:
                with self.assertRaisesRegex(ValueError, "stale ancestor"):
                    cut.run(project, [])
                prerequisites.assert_called_once_with(project, cut.ROOT, "3.6")
                invalidate.assert_not_called()
                log.assert_not_called()
                legacy.assert_not_called()
            self.assertEqual(list(project.iterdir()), [])

    def test_legacy_nested_manifest_fingerprints_are_validated(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            folder = project / "legacy"
            folder.mkdir()
            source = project / "input.txt"
            source.write_text("unchanged")
            output = folder / "output.txt"
            output.write_text("approved")
            manifest = {"inputs": {"approved_scripts": [cut.scoring.fingerprint(source, project, "project")]},
                        "outputs": {"shortlist": cut.scoring.fingerprint(output, project, "project")}}
            (folder / "DONE").write_text(json.dumps(manifest))
            cut.validate_done(project, Path("legacy"))
            source.write_text("changed")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                cut.validate_done(project, Path("legacy"))
            self.assertTrue((folder / "DONE").exists())

    def test_regeneration_archives_whole_selected_set_and_unchanged_run_keeps_done(self):
        from scripts.stage_invalidation import prepare_regeneration
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            script = project / "A.txt"
            script.write_text("Example.")
            mapping = project / "A__Takes.v1.json"
            mapping.write_text("{}")
            score = project / cut.scoring.STAGE / "A__Scores.v1.json"
            score.parent.mkdir(parents=True)
            scores = {"source_mapping": cut.scoring.fingerprint(mapping, project, "project")}
            score.write_text(json.dumps(scores))
            (project / cut.EXTRACTS / "APPROVED").mkdir(parents=True)
            output = project / cut.STAGE / "A.FCPXML"
            output.parent.mkdir(parents=True)
            approved = output.parent / "APPROVED/A.mov"
            approved.parent.mkdir()
            approved.write_bytes(b"approved movie")
            with patch.object(cut, "require_prerequisites"), patch.object(cut.find, "append_log"), \
                    patch.object(cut.find, "discover", return_value=[(script, [])]), \
                    patch.object(cut.find.mapping, "takes_versions", return_value=[(1, mapping)]), \
                    patch.object(cut.scoring, "load_mapping", return_value={"recordings": []}), \
                    patch.object(cut.scoring, "score_document", return_value=scores), \
                    patch.object(cut.find, "check_mapping", return_value={"coverage": {"complete": True}}), \
                    patch.object(cut, "build_xml", return_value=(b"<fcpxml/>", {"name": "A"})), \
                    patch.object(cut, "validate_xml"):
                cut.run(project, [score])
                marker = output.parent / "DONE"
                marker.write_text("approved completion")
                before = marker.read_bytes(), marker.stat().st_mtime_ns
                with patch.object(cut.find, "invalidate") as invalidate:
                    cut.run(project, [score])
                    invalidate.assert_not_called()
                self.assertEqual((marker.read_bytes(), marker.stat().st_mtime_ns), before)
                review = output.parent / "REVIEW.json"
                review_bytes = review.read_bytes()
                output.write_bytes(b"human edited XML")
                with self.assertRaisesRegex(ValueError, "inspect before replacing"):
                    cut.run(project, [score])
                self.assertEqual(output.read_bytes(), b"human edited XML")
                self.assertEqual(review.read_bytes(), review_bytes)
                with patch("scripts.stage_invalidation.prepare_regeneration", wraps=prepare_regeneration) as prepare:
                    cut.run(project, [score], regenerate=True)
                    prepare.assert_called_once()
                    self.assertEqual(set(prepare.call_args.args[3]), {output, review})
                archived = list(output.parent.glob("STALE/*/A.FCPXML"))
                self.assertEqual(len(archived), 1)
                self.assertEqual(archived[0].read_bytes(), b"human edited XML")
                self.assertEqual(archived[0].with_name("REVIEW.json").read_bytes(), review_bytes)
                self.assertEqual(archived[0].with_name("DONE").read_bytes(), before[0])
                self.assertEqual(output.read_bytes(), b"<fcpxml/>")
                self.assertEqual(approved.read_bytes(), b"approved movie")
                self.assertTrue(mapping.is_file())

    def test_midpoints_keep_auditions_separate_and_flip_only_at_jump(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc, scores, syncs, probes = fixture(root)
            untouched = deepcopy((doc, scores, syncs))
            takes, auditions = cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 50)
            self.assertEqual(len(takes), 4)
            self.assertEqual(len(auditions), 3)
            a, b, c = [item["candidates"][0] for item in auditions]
            self.assertEqual(a["cut_out"], Fraction(8, 5))
            self.assertEqual(a["cut_out"], b["cut_in"])
            self.assertEqual([a["angle"], b["angle"], c["angle"]], ["camA", "camA", "camB"])
            self.assertEqual([item["continuous"] for item in auditions], [False, True, False])
            self.assertEqual((doc, scores, syncs), untouched)

    def test_alternatives_compounds_and_active_choices_survive_xml(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc, scores, syncs, probes = fixture(root)
            output = root / cut.STAGE / "A.FCPXML"
            xml, report = cut.build_xml(root, output, "A", doc, scores, syncs, probes, 50)
            tree = ET.fromstring(xml)
            auditions = tree.findall("library/event/project/sequence/spine/audition")
            self.assertEqual(len(auditions), 3)
            self.assertEqual([len(a) for a in auditions], [2, 1, 1])
            self.assertEqual(len(tree.findall("resources/media/sequence")), 4)
            self.assertEqual(report["camera_flips"], 1)
            self.assertEqual(report["subtitle_titles"], 4)
            self.assertEqual(tree.find("resources/effect").attrib["uid"], cut.subtitles.TITLE_UID)
            resources = {r.attrib["id"]: r for r in tree.find("resources")}
            first_compound = resources[auditions[0][0].attrib["ref"]]
            self.assertIn("score=40", first_compound.attrib["name"])
            for compound in tree.findall("resources/media/sequence"):
                clip = compound.find("spine/mc-clip")
                sources = clip.findall("mc-source")
                self.assertEqual(sources[0].attrib["srcEnable"], "audio")
                self.assertTrue(sources[0].attrib["angleID"].endswith("-lav"))
                self.assertEqual(len(clip.findall("marker")), 2)
                title = clip.find("title")
                self.assertIsNotNone(title)
                self.assertGreater(cut.parse_time(title.attrib["offset"]), cut.parse_time(clip.attrib["start"]))
                self.assertEqual(title.attrib["lane"], "1")
            ids = [node.attrib["id"] for node in tree.iter() if "id" in node.attrib]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue(all(m.attrib["src"].startswith("../../2-Production/")
                                for m in tree.findall("resources/asset/media-rep")))

    def test_padding_is_configurable_and_cannot_override_shared_cut(self):
        with TemporaryDirectory() as directory:
            doc, scores, syncs, probes = fixture(Path(directory))
            small, _ = cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 50)
            large, _ = cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 200)
            self.assertLess(large[0]["cut_in"], small[0]["cut_in"])
            self.assertEqual(large[0]["cut_out"], small[0]["cut_out"])
            self.assertEqual(large[1]["cut_in"], small[1]["cut_in"])
            self.assertGreater(large[-1]["cut_out"], small[-1]["cut_out"])

    def test_sync_lav_offset_is_added_and_negative_offsets_shift_whole_group(self):
        with TemporaryDirectory() as directory:
            doc, scores, syncs, probes = fixture(Path(directory))
            syncs[1]["clips"]["lav"]["offset_ms"] = -200
            syncs[1]["clips"]["camB"]["offset_ms"] = -100
            takes, _ = cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 50)
            self.assertEqual(takes[0]["origins"]["camA"], Fraction(1, 5))
            self.assertEqual(takes[0]["origins"]["lav"], 0)
            self.assertEqual(takes[0]["word_in"], 1)
            syncs[1]["clips"]["lav"]["offset_ms"] = 200
            syncs[1]["clips"]["camB"]["offset_ms"] = 0
            takes, _ = cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 50)
            self.assertEqual(takes[0]["word_in"], Fraction(6, 5))

    def test_missing_timestamps_and_unavailable_video_fail(self):
        with TemporaryDirectory() as directory:
            doc, scores, syncs, probes = fixture(Path(directory))
            doc["recordings"][0]["transcript"]["words"][0]["start_ms"] = None
            with self.assertRaisesRegex(ValueError, "timestamp"):
                cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 50)
            doc, scores, syncs, probes = fixture(Path(directory))
            probes[1]["camB"]["duration"] = Fraction(2)
            with self.assertRaisesRegex(ValueError, "coverage"):
                cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 50)

    def test_word_highlighting_uses_each_alternative_and_lav_sync(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc, scores, syncs, probes = fixture(root)
            doc["recordings"][0]["transcript"]["words"][0]["text"] = "hello"
            doc["recordings"][0]["transcript"]["words"][1]["text"] = "world"
            scores["takes"][0]["transcript"]["word_end"] = 1
            scores["takes"][1]["transcript"]["word_start"] = 1
            # Test two words in the first take independently of take-planning adjacency.
            scores["takes"] = scores["takes"][:1]
            doc["script_groupings"] = doc["script_groupings"][:1]
            scores["takes"][0]["script"] = span(0)
            syncs[1]["clips"]["lav"]["offset_ms"] = 200
            xml, report = cut.build_xml(root, root / cut.STAGE / "Extracts/Preview.FCPXML", "Preview",
                                        doc, scores, syncs, probes, 50)
            tree = ET.fromstring(xml)
            titles = tree.findall("resources/media/sequence/spine/mc-clip/title")
            self.assertEqual(report["subtitle_titles"], 2)
            self.assertEqual([t.attrib["name"] for t in titles], ["hello world", "hello world"])
            self.assertEqual([t.attrib["offset"] for t in titles], ["6/5s", "19/10s"])
            self.assertEqual([t.find("text/text-style").attrib["ref"].endswith("h") for t in titles], [True, False])
            self.assertEqual("".join(titles[0].find("text").itertext()), "hello world")
            self.assertEqual([s.text for s in titles[0].find("text")], ["hello ", "world"])
            self.assertEqual([s.attrib["fontColor"] for s in titles[0].find("text")],
                             [cut.subtitles.HIGHLIGHT_COLOR, "1 1 1 1"])
            self.assertEqual([style.attrib["font"] for style in titles[0].findall("text-style-def/text-style")],
                             ["Arial Black", "Arial Black"])
            self.assertEqual([style.attrib["fontSize"] for style in titles[0].findall("text-style-def/text-style")],
                             ["140", "140"])
            self.assertEqual({p.attrib["name"]: p.attrib["value"] for p in titles[0].findall("param")},
                             {"Layout Method": "1 (Paragraph)", "Left Margin": "-1500", "Right Margin": "1500"})
            self.assertFalse(titles[0].findall(".//text-style[@strokeWidth]"))

    def test_long_phrase_wraps_at_word_boundary_without_losing_highlight(self):
        clip = ET.Element("mc-clip")
        take = {"take_key": "example", "transcript": {"word_start": 0, "word_end": 2},
                "origins": {"lav": Fraction(0)}, "cut_in": Fraction(0), "cut_out": Fraction(3)}
        words = [{"text": word, "start_ms": i * 1000, "end_ms": (i + 1) * 1000}
                 for i, word in enumerate(("Wonderful", "birthday", "party"))]
        self.assertEqual(cut.subtitles.add_titles(clip, take, words, Fraction(1, 30), "r1"), 3)
        for index, title in enumerate(clip.findall("title")):
            styles = title.findall("text/text-style")
            self.assertEqual("".join(style.text for style in styles),
                             "Wonderful\nbirthday party")
            self.assertEqual([style.attrib["fontColor"] == cut.subtitles.HIGHLIGHT_COLOR for style in styles],
                             [i == index for i in range(3)])
        clip = ET.Element("mc-clip")
        take["transcript"]["word_end"] = 0
        cut.subtitles.add_titles(clip, take, [{"text": "Uncharacteristically", "start_ms": 0,
                                               "end_ms": 1000}], Fraction(1, 30), "r1")
        self.assertLess(int(clip.find("title/text-style-def/text-style").attrib["fontSize"]),
                        cut.subtitles.FONT_SIZE)

    def test_explicit_script_discard_omits_sentence_but_retains_take_compound(self):
        with TemporaryDirectory() as directory:
            doc, scores, syncs, probes = fixture(Path(directory))
            doc["script_discarded"] = [span(1)]
            takes, auditions = cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 50)
            self.assertEqual(len(takes), 4)
            self.assertEqual([a["sentence"] for a in auditions], [1, 3])
            self.assertEqual(auditions[-1]["candidates"][0]["angle"], "camB")


if __name__ == "__main__":
    unittest.main()
