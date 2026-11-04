"""DOCX dialogue and comment-anchor parity across downstream script readers."""

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

from studio.script_docx import W, document_xml, spoken_paragraphs, spoken_text
from studio.tools.line import sources


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents/skills/studio"
CUE = SKILLS / "1-Pre Production/2-Cue Cards/scripts/make-plans.py"
SPEC = importlib.util.spec_from_file_location(
    "script_consumer_assets", SKILLS / "1-Pre Production/4-Asset Breakdown/scripts/assets.py")
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)


def run(text):
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph(text):
    return "<w:p>" + run(text) + "</w:p>"


def marked(text, key="7"):
    return f'<w:commentRangeStart w:id="{key}"/>' + run(text) + f'<w:commentRangeEnd w:id="{key}"/>'


def table(rows):
    return "<w:tbl>" + "".join("<w:tr>" + "".join(
        "<w:tc>" + cell + "</w:tc>" for cell in row) + "</w:tr>" for row in rows) + "</w:tbl>"


HEADERS = [paragraph(value) for value in ("ID", "Spoken Dialog", "Stage Directions")]


class ScriptConsumerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        self.script = self.project / "1-Pre Production/1-Script Review/Approved/Example.docx"
        self.script.parent.mkdir(parents=True)

    def write_docx(self, body, comments=()):
        with ZipFile(self.script, "w") as archive:
            archive.writestr("word/document.xml", f'<w:document xmlns:w="{W[1:-1]}">'
                             "<w:body>" + body + "</w:body></w:document>")
            if comments:
                archive.writestr("word/comments.xml", f'<w:comments xmlns:w="{W[1:-1]}">' + "".join(
                    f'<w:comment w:id="{key}" w:author="Human" w:date="2026-11-04T12:00:00Z">'
                    + paragraph(text) + "</w:comment>" for key, text in comments) + "</w:comments>")

    def cue_plan(self):
        with patch("scripts.validation.require_prerequisites"), \
                patch.object(sys, "argv", [str(CUE), str(self.project), "--overwrite"]), \
                redirect_stdout(StringIO()):
            runpy.run_path(str(CUE), run_name="__main__")
        return json.loads((self.project / "1-Pre Production/2-Cue Cards/working/Example.json").read_text())

    def assert_spoken_words(self, expected):
        document = document_xml(self.script)
        shared = " ".join(spoken_text(p) for p in spoken_paragraphs(document)).split()
        line = sources.load_script(self.project, self.script)
        cue = self.cue_plan()
        words, comments = assets.read_docx(self.script)
        self.assertEqual(shared, expected)
        self.assertEqual([word["text"] for word in line["words"]], expected)
        self.assertEqual(" ".join(cue["slides"]).split(), expected)
        self.assertEqual(cue["spoken_word_count"], len(expected))
        self.assertEqual(words, expected)
        self.assertEqual(line["title"], "Example title")
        self.assertEqual(cue["title"], "Example title")
        return line, cue, comments

    def test_table_dialogue_parity_and_overlapping_comments_across_cells(self):
        first = ('<w:p>' + run("Before ") + '<w:commentRangeStart w:id="7"/>' + run("One ")
                 + '<w:hyperlink>' + run("two.") + '</w:hyperlink>'
                 + '<w:r><w:br/></w:r><w:commentRangeStart w:id="8"/>' + run("Three")
                 + '<w:r><w:tab/></w:r>' + run("four.")
                 + '<w:del><w:r><w:t>deleted</w:t></w:r></w:del>'
                 + '<w:moveFrom><w:r><w:t>moved</w:t></w:r></w:moveFrom>'
                 + '<w:r><w:instrText>FIELD INSTRUCTION</w:instrText><w:br w:type="page"/></w:r></w:p>')
        second = ('<w:p>' + run("Five") + '<w:commentRangeEnd w:id="7"/>' + run(" six.")
                  + '<w:commentRangeEnd w:id="8"/></w:p>' + paragraph("Seven."))
        metadata = table([[paragraph("Front matter"), paragraph("Not dialogue")]])
        body = paragraph("Example title") + metadata + paragraph("Spoken script") + table([
            HEADERS,
            [paragraph("L01"), first, paragraph("[Show diagram]")],
            [paragraph("L02"), second, paragraph("Camera B")],
            [paragraph("L03"), paragraph(""), paragraph("[Pause]")],
        ])
        self.write_docx(body, [("7", "Asset: Diagram"), ("8", "Asset: Detail")])
        line, _, comments = self.assert_spoken_words(
            ["Before", "One", "two.", "Three", "four.", "Five", "six.", "Seven."])
        self.assertEqual([row["text"] for row in line["rows"]],
                         ["Before One two.", "Three\tfour.", "Five six.", "Seven."])
        self.assertEqual(sources.SEGMENTATION_VERSION, "line-sentences-v1")
        self.assertEqual([row["id"] for row in line["rows"]],
                         [sources.row_id(line["source"], number) for number in range(1, 5)])
        self.assertTrue(all(row["id"] not in ("L01", "L02", "L03") for row in line["rows"]))
        self.assertEqual([(c["comment_id"], c["start"], c["end"], c["passage"]) for c in comments],
                         [("7", 1, 5, "One two. Three four. Five"),
                          ("8", 3, 6, "Three four. Five six.")])
        self.assertEqual(comments[0]["highlighted_text"], "One two. Three four. \nFive")
        self.assertEqual(comments[0]["author"], "Human")

    def test_bracket_line_filter_preserves_visible_comment_spans(self):
        dialogue = ('<w:p><w:commentRangeStart w:id="7"/>' + run("One.")
                    + '<w:r><w:br/></w:r>' + run("[Look aside]")
                    + '<w:r><w:br/></w:r>' + run("Two.")
                    + '<w:commentRangeEnd w:id="7"/></w:p>')
        self.write_docx(paragraph("Example title") + paragraph("Spoken script") + table([
            HEADERS, [paragraph("L01"), dialogue, paragraph("Direction")],
            [paragraph("L02"), paragraph("Three."), paragraph("")],
        ]), [("7", "Asset: Show both")])
        _, _, comments = self.assert_spoken_words(["One.", "Two.", "Three."])
        self.assertEqual((comments[0]["start"], comments[0]["end"]), (0, 1))
        self.assertEqual(comments[0]["passage"], "One. Two.")

    def test_excluded_only_asset_comments_fail_with_actionable_location(self):
        locations = ("title", "metadata", "header", "id", "directions", "bracket-line")
        for location in locations:
            with self.subTest(location=location):
                headers = list(HEADERS)
                title = paragraph("Example title")
                metadata = paragraph("Front matter")
                identifier, dialogue, directions = paragraph("L01"), paragraph("Spoken words."), paragraph("[Hold]")
                if location == "title":
                    title = "<w:p>" + marked("Example title") + "</w:p>"
                elif location == "metadata":
                    metadata = table([["<w:p>" + marked("Front matter") + "</w:p>"]])
                elif location == "header":
                    headers[0] = "<w:p>" + marked("ID") + "</w:p>"
                elif location == "id":
                    identifier = "<w:p>" + marked("L01") + "</w:p>"
                elif location == "directions":
                    directions = "<w:p>" + marked("[Hold]") + "</w:p>"
                else:
                    dialogue = '<w:p>' + run("Spoken words.") + '<w:r><w:br/></w:r>' + marked("[Hold]") + '</w:p>'
                self.write_docx(title + metadata + paragraph("Spoken script")
                                + table([headers, [identifier, dialogue, directions]]), [("7", "Asset: Visual")])
                with self.assertRaisesRegex(ValueError, r"Asset: comment 7.*Example.docx.*excluded content.*Move.*Spoken Dialog"):
                    assets.read_docx(self.script)

    def test_ordinary_comments_on_excluded_content_are_ignored(self):
        self.write_docx("<w:p>" + marked("Example title") + "</w:p>" + paragraph("Spoken script")
                        + table([HEADERS, [paragraph("L01"), paragraph("Words."), paragraph("[Hold]")]]),
                        [("7", "Please revise this title")])
        self.assertEqual(assets.read_docx(self.script), (["Words."], []))

    def test_asset_range_only_on_deleted_text_requires_a_visible_anchor(self):
        dialogue = ('<w:p>' + run("Words.") + '<w:commentRangeStart w:id="7"/>'
                    '<w:del><w:r><w:delText>Old words</w:delText></w:r></w:del>'
                    '<w:commentRangeEnd w:id="7"/></w:p>')
        self.write_docx(paragraph("Example title") + paragraph("Spoken script")
                        + table([HEADERS, [paragraph("L01"), dialogue, paragraph("")]]),
                        [("7", "Asset: Preserve intent")])
        with self.assertRaisesRegex(ValueError, "excluded content.*Move its highlight to spoken dialogue"):
            assets.read_docx(self.script)

    def test_headed_legacy_script_keeps_cue_blank_breaks(self):
        self.write_docx(paragraph("Example title") + paragraph("Metadata") + paragraph("Spoken script")
                        + paragraph("One.") + paragraph("") + paragraph("[Hold]") + paragraph("Two [literal] words."))
        line, cue, comments = self.assert_spoken_words(["One.", "Two", "[literal]", "words."])
        self.assertEqual(cue["slides"], ["One.\n\nTwo [literal] words."])
        self.assertEqual([row["text"] for row in line["rows"]], ["One.", "Two [literal] words."])
        self.assertEqual([spoken_text(p) for p in spoken_paragraphs(document_xml(self.script), preserve_blanks=True)],
                         ["One.", "", "Two [literal] words."])
        self.assertEqual("".join(word["separator"] + word["text"] for word in line["words"]),
                         "One.\nTwo [literal] words.")
        self.assertEqual(comments, [])

    def test_bracketed_soft_breaks_do_not_hide_legacy_dialogue(self):
        dialogue = "<w:p>" + run("[Leading.]") + "<w:r><w:br/></w:r>" + run("This is spoken.") \
            + "<w:r><w:br/></w:r>" + run("[Trailing.]") + "</w:p>"
        self.write_docx(paragraph("Example title") + paragraph("Spoken script") + dialogue)
        self.assert_spoken_words(["This", "is", "spoken."])

    def test_inline_bracket_phrases_and_nonbreaking_hyphen_preserve_speech(self):
        dialogue = "<w:p>" + run("[First inline] Spoken [second inline] tax") \
            + "<w:commentRangeStart w:id=\"7\"/><w:r><w:noBreakHyphen/></w:r>" \
            + run("free.") + "<w:commentRangeEnd w:id=\"7\"/></w:p>"
        self.write_docx(paragraph("Example title") + paragraph("Spoken script")
                        + table([HEADERS, [paragraph("L01"), dialogue, paragraph("")]]),
                        [("7", "Asset: Keep the hyphen")])
        _, _, comments = self.assert_spoken_words(
            ["[First", "inline]", "Spoken", "[second", "inline]", "tax\u2011free."])
        self.assertEqual(comments[0]["highlighted_text"], "\u2011free.")

    def test_headingless_asset_import_retains_legacy_main_story_indexes(self):
        self.write_docx(paragraph("Legacy title") + paragraph("[Retain legacy direction]")
                        + table([["<w:p>" + marked("One two") + "</w:p>", paragraph("Three")]]),
                        [("7", "Asset: Legacy table")])
        words, comments = assets.read_docx(self.script)
        self.assertEqual(words, ["Legacy", "title", "[Retain", "legacy", "direction]", "One", "two", "Three"])
        self.assertEqual((comments[0]["start"], comments[0]["end"], comments[0]["passage"]), (5, 6, "One two"))
        with self.assertRaisesRegex(ValueError, "Spoken script.*heading"):
            sources.load_script(self.project, self.script)

    def test_text_and_json_sentence_identity_and_literal_spacing_are_unchanged(self):
        for name, content, title in (
            ("Script.txt", "Dr. Smith has 1.5 apples.  Next sentence!\nTrailing fragment", "Script"),
            ("Script.json", json.dumps({"title": "JSON title", "spoken_script":
             ["Dr. Smith has 1.5 apples.  Next sentence!", "Trailing fragment"]}), "JSON title"),
        ):
            with self.subTest(name=name):
                path = self.project / name
                path.write_text(content, encoding="utf-8")
                data = sources.load_script(self.project, path)
                self.assertEqual(data["title"], title)
                self.assertEqual([row["text"] for row in data["rows"]],
                                 ["Dr. Smith has 1.5 apples.", "Next sentence!", "Trailing fragment"])
                self.assertEqual([row["id"] for row in data["rows"]],
                                 [sources.row_id(data["source"], number) for number in range(1, 4)])
                self.assertEqual("".join(word["separator"] + word["text"] for word in data["words"]),
                                 "Dr. Smith has 1.5 apples.  Next sentence!\nTrailing fragment")

    def test_line_unreadable_docx_retains_filename_error(self):
        self.script.write_bytes(b"not a Word package")
        with self.assertRaisesRegex(ValueError, "Cannot read Word script: Example.docx"):
            sources.load_script(self.project, self.script)


class ReaderDependencyTests(unittest.TestCase):
    def test_downstream_workflow_dependencies_include_shared_reader(self):
        stages = ("1-Pre Production/2-Cue Cards", "1-Pre Production/3-Extracts",
                  "1-Pre Production/4-Asset Breakdown", "3-Post Production/3-Find Takes",
                  "3-Post Production/4-Take Selection", "3-Post Production/5-Face Tracking",
                  "3-Post Production/6-First Cut")
        for stage in stages:
            with self.subTest(stage=stage):
                definition = json.loads((SKILLS / stage / "dependencies.json").read_text())
                self.assertIn("studio/script_docx.py", definition["workflow_dependencies"])


if __name__ == "__main__":
    unittest.main()
