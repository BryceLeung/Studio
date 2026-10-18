"""Portrait framing geometry, source time, safety checks, and XML integration."""
from fractions import Fraction
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET

import first_cut as cut
import portrait
from test_first_cut import fixture


def face_path(center=0.7):
    return {"schema": "studio.face-path", "schema_version": 1,
            "width": 3840, "height": 2160, "role": "camA", "ranges_ms": [[800, 4800]],
            "frames": [{"time_ms": t, "face": [center, 0.3, 0.1, 0.2], "status": "tracked"}
                       for t in range(800, 4800, 250)]}


class PortraitTests(unittest.TestCase):
    def test_gap_holds_until_next_range_and_preserves_tracked_motion(self):
        data = face_path()
        data["ranges_ms"] = [[1000, 2000], [3000, 4000]]
        data["frames"] = [{"time_ms": t, "face": [center, 0.3, 0.1, 0.2], "status": "tracked"}
                          for start, center in ((1000, 0.3), (3000, 0.7))
                          for t in range(start, start + 1001, 250)]
        data["frames"][-4]["face"][0] = 0.8
        original = json.dumps(data, sort_keys=True)
        takes = [{"take_key": str(start), "cut_in": Fraction(start, 1000),
                  "cut_out": Fraction(end, 1000), "origins": {"camA": 0}}
                 for start, end in ((1000, 2300), (3000, 4000))]
        clip = ET.Element("asset-clip")
        warnings = portrait.add_framing(clip, data, {"width": 3840, "height": 2160},
                                        takes, "camA", Fraction(1, 30))
        points = {cut.parse_time(k.attrib["time"]): k.attrib["value"]
                  for k in clip.findall("adjust-transform/param/keyframeAnimation/keyframe")}
        self.assertEqual(points[Fraction(2)], points[Fraction(89, 30)])
        self.assertNotEqual(points[Fraction(89, 30)], points[Fraction(3)])
        self.assertNotEqual(points[Fraction(3)], points[cut.snap(Fraction(13, 4), Fraction(1, 30))])
        self.assertEqual(warnings[0]["statuses"], ["gap_hold"])
        self.assertEqual(warnings[0]["held_intervals_ms"],
                         [{"start_ms": 2000, "end_ms": 2300, "kind": "gap_hold"}])
        takes[1]["cut_in"] = Fraction(27, 10)
        takes[0]["cut_out"] = Fraction(33, 10)
        # Overlapping audition trims share one source-time path, not conflicting holds.
        overlapping = ET.Element("asset-clip")
        portrait.add_framing(overlapping, data, {"width": 3840, "height": 2160}, takes, "camA", Fraction(1, 30))
        self.assertEqual(ET.tostring(clip), ET.tostring(overlapping))
        self.assertEqual(json.dumps(data, sort_keys=True), original)

    def test_latest_observation_wins_when_range_starts_snap_to_one_frame(self):
        data = face_path()
        data["ranges_ms"] = [[0, 1], [2, 3]]
        data["frames"] = [{"time_ms": t, "face": [center, 0.3, 0.1, 0.2], "status": "tracked"}
                          for t, center in ((0, 0.3), (2, 0.7))]
        take = {"take_key": "t", "cut_in": Fraction(0), "cut_out": Fraction(1), "origins": {"camA": 0}}
        clip = ET.Element("asset-clip")
        portrait.add_framing(clip, data, {"width": 3840, "height": 2160}, [take], "camA", Fraction(1, 30))
        keys = clip.findall("adjust-transform/param/keyframeAnimation/keyframe")
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0].attrib["time"], "0s")
        self.assertTrue(all(k.attrib["value"] == "-35.55555556 0" for k in keys))

    def test_fractional_frame_rate_keeps_flat_gap_and_grid_aligned_keys(self):
        data = face_path()
        data["ranges_ms"] = [[1000, 2000], [10000, 11000]]
        data["frames"] = [{"time_ms": t, "face": [center, 0.3, 0.1, 0.2], "status": "tracked"}
                          for start, center in ((1000, 0.3), (10000, 0.7))
                          for t in range(start, start + 1001, 250)]
        frame = Fraction(1001, 30000)
        take = {"take_key": "t", "cut_in": Fraction(0), "cut_out": Fraction(12), "origins": {"camA": 0}}
        clip = ET.Element("asset-clip")
        portrait.add_framing(clip, data, {"width": 3840, "height": 2160}, [take], "camA", frame)
        points = {cut.parse_time(k.attrib["time"]): k.attrib["value"]
                  for k in clip.findall("adjust-transform/param/keyframeAnimation/keyframe")}
        self.assertTrue(all((t / frame).denominator == 1 for t in points))
        previous, resumed = cut.snap(Fraction(2), frame), cut.snap(Fraction(10), frame)
        self.assertEqual(points[previous], points[resumed - frame])
        self.assertNotEqual(points[resumed - frame], points[resumed])
        self.assertFalse(any(previous < t < resumed - frame for t in points))

    def test_long_midpoint_gap_holds_across_both_take_trims(self):
        data = face_path()
        data["ranges_ms"] = [[1000, 2000], [10000, 11000]]
        data["frames"] = [{"time_ms": t, "face": [center, 0.3, 0.1, 0.2], "status": "tracked"}
                          for start, center in ((1000, 0.3), (10000, 0.7))
                          for t in range(start, start + 1001, 250)]
        takes = [{"take_key": str(start), "cut_in": Fraction(start), "cut_out": Fraction(end),
                  "origins": {"camA": 0}} for start, end in ((1, 6), (6, 11))]
        clip = ET.Element("asset-clip")
        warnings = portrait.add_framing(clip, data, {"width": 3840, "height": 2160}, takes, "camA", Fraction(1, 30))
        keys = clip.findall("adjust-transform/param/keyframeAnimation/keyframe")
        points = {cut.parse_time(k.attrib["time"]): k.attrib["value"] for k in keys}
        self.assertEqual(points[Fraction(2)], points[Fraction(299, 30)])
        self.assertNotEqual(points[Fraction(299, 30)], points[Fraction(10)])
        self.assertFalse(any(Fraction(2) < t < Fraction(299, 30) for t in points))
        self.assertEqual(warnings[0]["held_intervals_ms"],
                         [{"start_ms": 2000, "end_ms": 6000, "kind": "gap_hold"}])
        self.assertEqual(warnings[1]["held_intervals_ms"],
                         [{"start_ms": 6000, "end_ms": 10000, "kind": "gap_hold"}])

    def test_leading_and_trailing_holds_are_source_local_and_reported_in_camera_time(self):
        for role, center, origin in (("camA", 0.3, 0), ("camA", 0.7, 5), ("camB", 0.8, 2)):
            with self.subTest(role=role):
                data = face_path(center)
                data["role"] = role
                takes = [{"take_key": str(start), "cut_in": origin + Fraction(start, 1000),
                          "cut_out": origin + Fraction(end, 1000), "origins": {role: origin}}
                         for start, end in ((100, 700), (6000, 9000))]
                clip = ET.Element("asset-clip")
                warnings = portrait.add_framing(clip, data, {"width": 3840, "height": 2160}, takes, role, Fraction(1, 30))
                keys = clip.findall("adjust-transform/param/keyframeAnimation/keyframe")
                self.assertEqual(cut.parse_time(keys[0].attrib["time"]), Fraction(1, 10))
                self.assertEqual(cut.parse_time(keys[-1].attrib["time"]), Fraction(9))
                expected = (0.5 - center) * 3840 / 2160 * 100
                self.assertTrue(all(abs(float(k.attrib["value"].split()[0]) - expected) < 1e-7 for k in keys))
                self.assertEqual(warnings[0]["held_intervals_ms"],
                                 [{"start_ms": 100, "end_ms": 700, "kind": "leading_hold"}])
                self.assertEqual(warnings[1]["held_intervals_ms"],
                                 [{"start_ms": 6000, "end_ms": 9000, "kind": "trailing_hold"}])
                self.assertEqual(warnings[1]["camera"], role)

    def test_centered_fallback_status_propagates_into_trailing_hold(self):
        data = face_path()
        for sample in data["frames"]:
            sample.update(face=None, status="centered")
        take = {"take_key": "gap", "cut_in": Fraction(6), "cut_out": Fraction(7), "origins": {"camA": 0}}
        clip = ET.Element("asset-clip")
        warnings = portrait.add_framing(clip, data, {"width": 3840, "height": 2160}, [take], "camA", Fraction(1, 30))
        self.assertEqual(warnings[0]["statuses"], ["centered", "trailing_hold"])
        self.assertTrue(all(k.attrib["value"] == "0.00000000 0" for k in
                            clip.findall("adjust-transform/param/keyframeAnimation/keyframe")))

    def test_both_angles_all_alternatives_and_source_formats(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            doc, scores, syncs, probes = fixture(project)
            syncs[1]["clips"]["lav"]["offset_ms"] = 100
            syncs[1]["clips"]["camB"]["offset_ms"] = -100
            paths = {}
            for role, center in (("camA", 0.7), ("camB", 0.3)):
                data = face_path(center)
                data["role"] = role
                paths[((project / syncs[1]["clips"][role]["path"]).resolve(), role)] = data
            xml, report = cut.build_xml(project, project / cut.STAGE / "Extracts/Portrait/A.FCPXML",
                                        "A Portrait", doc, scores, syncs, probes, 50, face_paths=paths)
            root = ET.fromstring(xml)
            formats = {f.attrib["id"]: f.attrib for f in root.findall("resources/format")}
            sequence = root.find("library/event/project/sequence")
            self.assertEqual((formats[sequence.attrib["format"]]["width"],
                              formats[sequence.attrib["format"]]["height"]), ("1080", "1920"))
            for asset in root.findall("resources/asset[@hasVideo='1']"):
                self.assertEqual(formats[asset.attrib["format"]]["width"], "3840")
            angles = root.findall("resources/media/multicam/mc-angle")
            for angle, sign in zip(angles[:2], (-1, 1)):
                clip = angle.find("asset-clip")
                self.assertEqual(clip.find("adjust-conform").attrib["type"], "fill")
                keys = clip.findall("adjust-transform/param/keyframeAnimation/keyframe")
                self.assertAlmostEqual(float(keys[0].attrib["value"].split()[0]), sign * 35.55555556)
                # Keyframes are camera SOURCE time, unaffected by lav/camera sync shifts.
                self.assertEqual(keys[0].attrib["time"], "4/5s")
            self.assertIsNone(angles[2].find("asset-clip/adjust-transform"))
            self.assertEqual(report["takes"], 4)
            self.assertEqual([(w["camera"], w["take_key"], w["statuses"]) for w in report["portrait"]["warnings"]],
                             [("camA", "take3", ["trailing_hold"]), ("camB", "take3", ["trailing_hold"])])
            self.assertEqual(len(root.findall("resources/media/sequence")), 4)
            for title in root.findall(".//title"):
                self.assertEqual(title.find("adjust-transform").attrib["position"], "0 -28")
                self.assertEqual(title.find("text-style-def/text-style").attrib["fontSize"], "70")

    def test_crop_clamps_and_centered_fallback_is_reported(self):
        data = face_path(1)
        video = {"width": 3840, "height": 2160}
        take = {"take_key": "t", "cut_in": Fraction(1), "cut_out": Fraction(2), "origins": {"camA": 0}}
        clip = ET.Element("asset-clip")
        portrait.add_framing(clip, data, video, [take], "camA", Fraction(1, 30))
        x = float(clip.find("adjust-transform").attrib["position"].split()[0])
        self.assertAlmostEqual(x, -(3840 / 2160 - 1080 / 1920) * 50)
        for sample in data["frames"]:
            sample.update(face=None, status="centered")
        clip = ET.Element("asset-clip")
        warnings = portrait.add_framing(clip, data, video, [take], "camA", Fraction(1, 30))
        self.assertEqual(clip.find("adjust-transform").attrib["position"], "0.00000000 0")
        self.assertEqual(warnings[0]["statuses"], ["centered"])

    def test_invalid_samples_sparse_coverage_and_missing_paths_fail(self):
        for mutation in (lambda d: d["frames"][0].update(face=[float("nan"), 0, 0, 0]),
                         lambda d: d.update(frames=d["frames"][::4]),
                         lambda d: d["frames"][1].update(time_ms=800)):
            data = face_path()
            mutation(data)
            with self.assertRaises(ValueError):
                portrait.validate_path(data)
        for field in ("frames", "ranges_ms"):
            data = face_path()
            data[field] = []
            with self.assertRaisesRegex(ValueError, "Empty face path"):
                portrait.validate_path(data)
        with self.assertRaisesRegex(ValueError, "at least one used take"):
            portrait.add_framing(ET.Element("asset-clip"), face_path(), {"width": 3840, "height": 2160},
                                 [], "camA", Fraction(1, 30))
        with self.assertRaisesRegex(ValueError, "unrotated"):
            portrait.add_framing(ET.Element("asset-clip"), face_path(),
                                 {"width": 3840, "height": 2160, "tags": {"rotate": "90"}},
                                 [], "camA", Fraction(1, 30))

    def test_index_and_media_identity_are_verified(self):
        import os
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            _, _, syncs, _ = fixture(project)
            stage = project / cut.FACE
            stage.mkdir(parents=True)
            source = project / syncs[1]["clips"]["camA"]["path"]
            data = face_path()
            data["media"] = cut.media_fingerprint(source, project)
            path = stage / "A__C01__camA__FacePath.v1.json"
            path.write_text(json.dumps(data))
            entry = cut.scoring.fingerprint(path, project, "project")
            index = stage / "Face Paths.json"
            index.write_text(json.dumps({"schema": "studio.face-path-index", "schema_version": 1, "paths": [entry]}))
            outputs = [entry, cut.scoring.fingerprint(index, project, "project")]
            (stage / "DONE").write_text(json.dumps({"outputs": outputs}))
            paths, inputs = portrait.load_paths(cut, project)
            self.assertEqual(len(paths), 1)
            self.assertEqual(len(inputs), 2)
            # Shared completion records declare hash methods; legacy indexes may not.
            outputs = [dict(item, method="sha256") for item in outputs]
            (stage / "DONE").write_text(json.dumps({"outputs": outputs}))
            self.assertEqual(len(portrait.load_paths(cut, project)[0]), 1)
            # Legacy path headers retain ns fields but follow the shared seconds policy.
            data["media"]["mtime_ns"] = data["media"].pop("mtime_s") * 1_000_000_000 + 999_999_999
            path.write_text(json.dumps(data))
            entry = cut.scoring.fingerprint(path, project, "project")
            index.write_text(json.dumps({"schema": "studio.face-path-index", "schema_version": 1, "paths": [entry]}))
            outputs = [entry, cut.scoring.fingerprint(index, project, "project")]
            (stage / "DONE").write_text(json.dumps({"outputs": outputs}))
            self.assertEqual(len(portrait.load_paths(cut, project)[0]), 1)
            stamp = source.stat().st_mtime_ns
            os.utime(source, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))
            with self.assertRaisesRegex(ValueError, "Stale face-path media"):
                portrait.load_paths(cut, project)
            os.utime(source, ns=(stamp, stamp))
            source.write_bytes(b"changed source")
            with self.assertRaisesRegex(ValueError, "Stale face-path media"):
                portrait.load_paths(cut, project)

    def test_missing_angle_never_silently_centers(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            with self.assertRaisesRegex(ValueError, "Missing stage 3.5"):
                cut.build_xml(project, project / "Portrait.FCPXML", "P", *fixture(project), 50, face_paths={})


if __name__ == "__main__":
    unittest.main()
