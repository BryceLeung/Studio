"""Verify continuous source boundaries, camera flips, auditions, and XML wiring."""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
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
            resources = {r.attrib["id"]: r for r in tree.find("resources")}
            first_compound = resources[auditions[0][0].attrib["ref"]]
            self.assertIn("score=40", first_compound.attrib["name"])
            for compound in tree.findall("resources/media/sequence"):
                clip = compound.find("spine/mc-clip")
                sources = clip.findall("mc-source")
                self.assertEqual(sources[0].attrib["srcEnable"], "audio")
                self.assertTrue(sources[0].attrib["angleID"].endswith("-lav"))
                self.assertEqual(len(clip.findall("marker")), 2)
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
