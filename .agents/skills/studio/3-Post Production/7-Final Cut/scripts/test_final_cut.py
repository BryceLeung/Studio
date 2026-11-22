from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import final_cut as cut
from studio.fingerprints import raw_sha256


XML = b'''<fcpxml version="1.10"><resources>
  <format id="r1" frameDuration="1/25s"/>
  <asset id="r2"><media-rep src="../Media%20with%20space.mov"/></asset>
  <effect id="r3" name="Basic Title"/>
  <media id="r4"><multicam/></media>
  <media id="r5"><sequence><spine><mc-clip ref="r4" offset="3s" start="20s" duration="4s" name="S001 C02 T002">
    <mc-source angleID="lav" srcEnable="audio"/><mc-source angleID="camB" srcEnable="video"/>
    <title ref="r3" lane="1" offset="20s" start="3600s" duration="2s"><text><text-style ref="ts1">Picked </text-style><text-style>take</text-style></text><text-style-def id="ts1"><text-style/></text-style-def></title>
    <title ref="r3" lane="1" offset="22s" duration="2s"><text>Second highlight</text></title>
    <marker start="20s" value="S001 C02 T002 IN"/><marker start="22s" value="An edit marker"/>
  </mc-clip></spine></sequence></media>
  <media id="r6"><sequence><spine><mc-clip ref="r4" start="10s" duration="4s" name="S001 C01 T001"/></spine></sequence></media>
</resources><library location="file:///Source.fcpbundle"><event name="Original" uid="event"><project name="A" uid="original">
  <sequence format="r1" duration="10s"><spine>
    <gap offset="0s" duration="8s"/>
    <audition offset="8s"><ref-clip ref="r5" start="4s" duration="2s"><asset-clip ref="r2" lane="1" offset="9/2s" duration="1s"/></ref-clip><ref-clip ref="r6" duration="4s"/></audition>
  </spine></sequence>
</project></event></library></fcpxml>'''

DOCUMENT = {"script": {"source": {"path": "Approved/A.docx"}, "words": [{"text": "Hello"}]},
            "script_groupings": [{"word_start": 0, "word_end": 0}]}


class FinalCutTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        self.source = self.project / cut.FIRST_CUT / "APPROVED/A.fcpxml"
        self.output = self.project / cut.STAGE / self.source.name

    def convert(self, xml=XML, entries=(), extract=None, document=DOCUMENT):
        payload, report = cut.convert(xml, self.project, self.source, self.output, document, extract, entries)
        root = ET.fromstring(payload)
        project = root.find("library/event/project")
        return root, project.find("sequence/spine"), report

    def asset(self):
        media = self.project / cut.animations.APPROVALS / "A01/APPROVED/v001 animation #1.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"approved animation")
        return {"asset_id": "A01", "path": media.relative_to(self.project).as_posix(), "sha256": raw_sha256(media),
                "registry": {"ID": "A01", "Brief": "Animate an example", "Script": "Approved/A.docx",
                             "Start": "0", "End": "0", "Passage": "Hello", "Extracts": '["EXTRACT-1"]'},
                "width": 1920, "height": 1080, "duration": Fraction(5), "frame": Fraction(1, 25)}

    def test_uses_active_pick_and_keeps_timing_camera_and_subtitle_text(self):
        root, spine, report = self.convert()
        gap, clip = spine
        self.assertEqual((gap.tag, gap.get("duration")), ("gap", "8s"))
        self.assertEqual((clip.tag, clip.get("offset"), clip.get("start"), clip.get("duration")),
                         ("mc-clip", "8s", "21s", "2s"))
        self.assertEqual(report["selected"][0]["clip"], "S001 C02 T002")
        self.assertEqual([angle.get("angleID") for angle in clip.findall("mc-source")], ["lav", "camB"])
        self.assertEqual([(t.get("offset"), t.get("duration")) for t in clip.findall("title")],
                         [("21s", "1s"), ("22s", "1s")])
        title = clip.find("title")
        self.assertEqual(title.get("start"), "3601s")
        self.assertEqual("".join(title.find("text").itertext()), "Picked take")
        self.assertEqual(title.find("text/text-style").get("ref"), title.find("text-style-def").get("id"))
        self.assertEqual(clip.find("asset-clip").get("offset"), "43/2s")
        self.assertEqual(clip.find("marker").get("value"), "An edit marker")
        self.assertFalse(root.findall("resources/media/sequence"))
        self.assertFalse(list(spine.iter("audition")))
        self.assertFalse(list(spine.iter("ref-clip")))
        self.assertEqual(root.find("resources/asset/media-rep").get("src"), "../6-First%20Cut/Media%20with%20space.mov")
        self.assertIsNone(root.find("library/event/project").get("uid"))

    def test_stages_disabled_video_below_subtitles_on_finalized_compound(self):
        xml = XML.replace(b'<audition offset="8s"><ref-clip ref="r5"', b'<ref-clip offset="8s" ref="r5"')
        xml = xml.replace(b'<ref-clip ref="r6" duration="4s"/></audition>', b'')
        root, spine, report = self.convert(xml, [self.asset()], 1)
        clip = spine.find("mc-clip")
        cutaway = clip.find("asset-clip[@enabled='0']")
        self.assertEqual((cutaway.get("lane"), cutaway.get("offset"), cutaway.get("duration"), cutaway.get("srcEnable")),
                         ("1", "21s", "2s", "video"))
        self.assertGreater(int(clip.find("title").get("lane")), int(cutaway.get("lane")))
        self.assertLess(list(clip).index(cutaway), list(clip).index(clip.find("marker")))
        self.assertEqual(report["placements"][0]["offset"], "8s")
        self.assertIsNotNone(root.find("library/event[@name='Animations']/asset-clip"))

    def test_automatic_animation_follows_anchor_into_extracts(self):
        entry = self.asset()
        entry["registry"]["Extracts"] = "[]"
        for extract in (None, 1, 2):
            with self.subTest(extract=extract):
                _, spine, report = self.convert(entries=[entry], extract=extract)
                self.assertEqual(len(report["placements"]), 1)
                self.assertIsNotNone(spine.find("mc-clip/asset-clip[@enabled='0']"))
        document = {"script": dict(DOCUMENT["script"], words=[{"text": "Hello"}, {"text": "Elsewhere"}]),
                    "script_groupings": [*DOCUMENT["script_groupings"], {"word_start": 1, "word_end": 1}]}
        entry["registry"].update(Start="1", End="1", Passage="Elsewhere")
        root, _, report = self.convert(entries=[entry], extract=2, document=document)
        self.assertEqual(report["placements"], [])
        self.assertIsNotNone(root.find("library/event[@name='Animations']/asset-clip"))

    def test_explicit_extract_ids_restrict_placement(self):
        entry = self.asset()
        root, _, report = self.convert(entries=[entry], extract=2)
        self.assertEqual(report["placements"], [])
        self.assertIsNotNone(root.find("library/event[@name='Animations']/asset-clip"))
        _, _, report = self.convert(entries=[entry])
        self.assertEqual(len(report["placements"]), 1)

    def test_rejects_unsafe_edits(self):
        with self.assertRaisesRegex(ValueError, "Cannot transfer adjust-transform"):
            self.convert(XML.replace(b'tart="4s" duration="2s">', b'tart="4s" duration="2s"><adjust-transform/>'))

    def test_generation_reads_bundle_and_uses_its_stem_and_xml_location(self):
        source = self.source.with_suffix(".fcpxmld") / "Info.fcpxml"
        source.parent.mkdir(parents=True)
        source.write_bytes(XML)
        scores = self.project / cut.SCORES / "A__Scores.v1.json"
        scores.parent.mkdir(parents=True)
        scores.write_text(json.dumps({"source_mapping": {"path": "mapping.json"}}))
        (self.project / "mapping.json").write_text(json.dumps(DOCUMENT))
        with patch.object(cut, "require_prerequisites"), patch.object(cut.animations, "load", return_value=([], [])), \
                patch.object(cut, "invalidate"):
            report = cut.run(self.project)
        row, = report["projects"]
        self.assertEqual(row["input"], source.relative_to(self.project).as_posix())
        self.assertEqual(row["output"], "A.FCPXML")
        media = ET.parse(self.output.with_suffix(".FCPXML")).find("resources/asset/media-rep")
        self.assertEqual(media.get("src"), "../6-First%20Cut/APPROVED/Media%20with%20space.mov")
        self.assertEqual(source.read_bytes(), XML)

    def test_generation_does_not_overwrite_existing_edit(self):
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(XML)
        scores = self.project / cut.SCORES / "A__Scores.v10.json"
        scores.parent.mkdir(parents=True)
        scores.with_name("A__Scores.v9.json").write_text("historical scores")
        scores.write_text(json.dumps({"source_mapping": {"path": "mapping.json"}}))
        (self.project / "mapping.json").write_text(json.dumps(DOCUMENT))
        with patch.object(cut, "require_prerequisites") as prerequisites, patch.object(cut.animations, "load", return_value=([], [])), \
                patch.object(cut, "invalidate"):
            report = cut.run(self.project)
            original = self.output.read_bytes()
            self.assertEqual(cut.run(self.project), report)
            self.assertEqual(self.output.read_bytes(), original)
            self.output.write_bytes(b"human edit")
            with self.assertRaisesRegex(ValueError, "Existing Final Cut differs"):
                cut.run(self.project)
            self.assertEqual(self.output.read_bytes(), b"human edit")
            self.assertEqual(self.source.read_bytes(), XML)
            prerequisites.assert_called_with(self.project, cut.ROOT, "3.7")


if __name__ == "__main__":
    unittest.main()
