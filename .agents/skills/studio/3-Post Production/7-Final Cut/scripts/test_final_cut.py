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

    def asset(self, asset_id="A01"):
        media = self.project / cut.animations.APPROVALS / asset_id / "APPROVED/v001 animation #1.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"approved animation")
        return {"asset_id": asset_id, "path": media.relative_to(self.project).as_posix(), "sha256": raw_sha256(media),
                "registry": {"ID": asset_id, "Brief": "Animate an example", "Script": "Approved/A.docx",
                             "Start": "0", "End": "0", "Passage": "Hello", "Extracts": '["EXTRACT-1"]'},
                "width": 1920, "height": 1080, "duration": Fraction(5), "frame": Fraction(1, 25)}

    def test_uses_active_pick_and_keeps_timing_camera_and_subtitle_text(self):
        root, spine, report = self.convert()
        gap, clip = spine
        self.assertEqual((gap.tag, gap.get("duration")), ("gap", "8s"))
        self.assertEqual((clip.tag, clip.get("offset"), clip.get("start"), clip.get("duration")),
                         ("ref-clip", "8s", "4s", "2s"))
        self.assertEqual(report["selected"][0]["clip"], "S001 C02 T002")
        multicam = root.find("resources/media[@id='r5']/sequence/spine/mc-clip")
        self.assertEqual([angle.get("angleID") for angle in multicam.findall("mc-source")], ["lav", "camB"])
        self.assertEqual([(t.get("offset"), t.get("duration")) for t in clip.findall("title")],
                         [("4s", "1s"), ("5s", "1s")])
        title = clip.find("title")
        self.assertEqual(title.get("start"), "3601s")
        self.assertEqual("".join(title.find("text").itertext()), "Picked take")
        self.assertEqual(title.find("text/text-style").get("ref"), title.find("text-style-def").get("id"))
        self.assertEqual(clip.find("asset-clip").get("offset"), "9/2s")
        self.assertIsNotNone(multicam.find("marker[@value='An edit marker']"))
        self.assertIsNone(multicam.find("title"))
        self.assertIsNone(root.find("resources/media[@id='r6']"))
        self.assertFalse(list(spine.iter("audition")))
        self.assertEqual(root.find("resources/asset/media-rep").get("src"), "../6-First%20Cut/Media%20with%20space.mov")
        self.assertIsNone(root.find("library/event/project").get("uid"))

    def test_stages_disabled_video_below_subtitles_on_finalized_compound(self):
        xml = XML.replace(b'<audition offset="8s"><ref-clip ref="r5"', b'<ref-clip offset="8s" ref="r5"')
        xml = xml.replace(b'<ref-clip ref="r6" duration="4s"/></audition>', b'')
        root, spine, report = self.convert(xml, [self.asset()], 1)
        clip = spine.find("ref-clip")
        cutaway = clip.find("asset-clip[@enabled='0']")
        self.assertEqual((cutaway.get("lane"), cutaway.get("offset"), cutaway.get("duration"), cutaway.get("srcEnable")),
                         ("1", "4s", "5s", "video"))
        self.assertGreater(int(clip.find("title").get("lane")), int(cutaway.get("lane")))
        self.assertEqual(report["placements"][0]["offset"], "8s")
        self.assertEqual(root.find("library/event/project/sequence").get("duration"), "13s")
        self.assertIsNotNone(root.find("library/event[@name='Animations']/asset-clip"))

    def test_full_length_cutaways_cross_cuts_below_subtitles_and_use_separate_lanes(self):
        xml = XML.replace(b'</audition>', b'''</audition>
            <ref-clip ref="r6" name="S002" offset="10s" duration="4s">
                <title ref="r3" lane="1" offset="0s" duration="4s"/>
            </ref-clip>''').replace(b'<sequence format="r1" duration="10s">',
                                     b'<sequence format="r1" duration="14s">').replace(b'S001 C01 T001', b'S002 C01 T001')
        document = {"script": dict(DOCUMENT["script"], words=[{"text": "Hello"}, {"text": "Next"}]),
                    "script_groupings": [*DOCUMENT["script_groupings"], {"word_start": 1, "word_end": 1}]}
        following = self.asset("A02")
        following["registry"].update(Start="1", End="1", Passage="Next")
        following["duration"] = Fraction(3)
        root, spine, report = self.convert(xml, [self.asset(), following], document=document)
        first, second = spine.findall("ref-clip")
        self.assertEqual([(clip.get("offset"), clip.get("duration")) for clip in (first, second)],
                         [("8s", "2s"), ("10s", "4s")])
        self.assertEqual([(row["offset"], row["duration"]) for row in report["placements"]],
                         [("8s", "5s"), ("10s", "3s")])
        self.assertEqual([clip.find("asset-clip[@enabled='0']").get("lane") for clip in (first, second)],
                         ["1", "2"])
        self.assertTrue(all(int(title.get("lane")) > 2 for title in spine.iter("title")))
        self.assertEqual(root.find("library/event/project/sequence").get("duration"), "14s")

    def test_automatic_animation_follows_anchor_into_extracts(self):
        entry = self.asset()
        entry["registry"]["Extracts"] = "[]"
        for extract in (None, 1, 2):
            with self.subTest(extract=extract):
                _, spine, report = self.convert(entries=[entry], extract=extract)
                self.assertEqual(len(report["placements"]), 1)
                self.assertIsNotNone(spine.find("ref-clip/asset-clip[@enabled='0']"))
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

    def test_preserves_compound_effect_and_rejects_unsafe_title_retime(self):
        def with_adjustment(node):
            return XML.replace(b'tart="4s" duration="2s">', b'tart="4s" duration="2s">' + node)

        _, spine, _ = self.convert(with_adjustment(b"<adjust-colorConform/>"))
        clip = spine.find("ref-clip")
        self.assertIs(clip[0], clip.find("adjust-colorConform"))
        with self.assertRaisesRegex(ValueError, "Cannot safely lift subtitles through retiming"):
            self.convert(with_adjustment(b"<timeMap/>"))

    def test_repeated_compound_lifts_titles_for_each_trim_with_unique_styles(self):
        xml = XML.replace(b"</audition>", b'</audition><ref-clip ref="r5" start="3s" duration="1s" offset="10s"/>')
        _, spine, _ = self.convert(xml)
        first, second = spine.findall("ref-clip")
        self.assertEqual(second.find("title").get("offset"), "3s")
        self.assertNotEqual(first.find("title/text-style-def").get("id"), second.find("title/text-style-def").get("id"))

    def test_preview_does_not_change_stage_or_approved_xml(self):
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(XML)
        with patch.object(cut, "require_prerequisites") as prerequisites, patch.object(cut, "invalidate") as invalidate:
            report = cut.preview(self.project, "A")
            self.assertEqual(cut.preview(self.project, "A"), report)
        prerequisites.assert_not_called()
        invalidate.assert_not_called()
        self.assertFalse(self.output.parent.exists())
        self.assertEqual(self.source.read_bytes(), XML)
        self.assertEqual(report["selected_takes"], 1)
        self.assertIsNotNone(ET.parse(report["output"]).find("library/event/project/sequence/spine/ref-clip/title"))

    def test_standard_generation_finalizes_bundle_and_preserves_multicam_compounds(self):
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
        output = ET.parse(self.output.with_suffix(".FCPXML"))
        media = output.find("resources/asset/media-rep")
        self.assertEqual(media.get("src"), "../6-First%20Cut/APPROVED/Media%20with%20space.mov")
        spine = output.find("library/event/project/sequence/spine")
        self.assertFalse(list(spine.iter("audition")))
        compound, = spine.findall("ref-clip")
        self.assertEqual(compound.get("ref"), "r5")
        multicam = output.find("resources/media[@id='r5']/sequence/spine/mc-clip")
        self.assertEqual(multicam.get("ref"), "r4")
        self.assertIsNotNone(output.find("resources/media[@id='r4']/multicam"))
        self.assertIsNone(output.find("resources/media[@id='r6']"))
        self.assertIsNone(multicam.find("title"))
        self.assertEqual(len(compound.findall("title")), 2)
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
