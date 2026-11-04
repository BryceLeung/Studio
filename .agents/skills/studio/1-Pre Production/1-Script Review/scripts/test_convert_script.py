import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
from docx.enum.text import WD_BREAK
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Pt


HELPER = Path(__file__).with_name("write-script.py")
spec = importlib.util.spec_from_file_location("convert_writer", HELPER)
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)
from studio.script_docx import (  # noqa: E402
    HEADERS, W, document_xml, paragraph_text, script_rows, spoken_paragraphs,
    spoken_text, validate_three_column,
)


class ConvertScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "My Script.docx"
        self.document = Document()
        self.document.add_heading("My Script", 0)
        self.document.add_paragraph("Review notes: [Not a direction]")
        table = self.document.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "Front matter table remains unchanged"
        self.document.add_heading("Spoken script", 1)

    def save(self, lines):
        for line in lines:
            self.document.add_paragraph(line)
        self.document.save(self.source)

    def convert(self, layout="three-column", output=None):
        return writer.convert_script(self.source, layout, output)

    def texts(self, path):
        return [(row.line_id, [paragraph_text(p) for p in row.dialogue],
                 [paragraph_text(p) for p in row.directions])
                for row in script_rows(document_xml(path))]

    def test_heading_matching_is_case_insensitive_in_both_layouts(self):
        for index, heading in enumerate(("Spoken Script", "spoken script", "SPOKEN SCRIPT", "  sPoKeN sCrIpT  ")):
            with self.subTest(heading=heading):
                self.document.paragraphs[-1].text = heading
                self.document.save(self.source)
                document = Document(self.source)
                document.add_paragraph("Speech.")
                document.add_paragraph("[Animation.]")
                document.save(self.source)
                three = self.convert(output=self.root / f"Case-{index}.docx")
                validate_three_column(three)
                self.assertEqual(Document(three).paragraphs[-1].text, heading)
                self.assertEqual(self.texts(three), [("L01", ["Speech."], ["Animation."])])
                one = writer.convert_script(three, "one-column")
                self.assertEqual([p.text for p in Document(one).paragraphs][-3:],
                                 [heading, "Speech.", "[Animation.]"])

    def test_directions_breaks_inline_brackets_and_front_matter(self):
        self.save(["[Open on presenter.]", "First spoken line.\n[Animation.]\n[Balance.]\nSecond [probably] line.",
                   "", "[End card.]", "Third line."])
        before = self.source.read_bytes()
        output = self.convert()
        self.assertEqual(output.name, "My Script.v1.docx")
        self.assertEqual(self.source.read_bytes(), before)
        validate_three_column(output)
        self.assertEqual(self.texts(output), [
            ("L01", ["First spoken line."], ["Open on presenter.", "Animation.", "Balance."]),
            ("L02", ["Second [probably] line."], ["End card."]),
            ("L03", ["Third line."], [""]),
        ])
        reopened = Document(output)
        self.assertEqual([p.text for p in reopened.paragraphs],
                         ["My Script", "Review notes: [Not a direction]", "Spoken script"])
        self.assertEqual(reopened.tables[0].cell(0, 0).text, "Front matter table remains unchanged")
        script = reopened.tables[1]
        self.assertEqual(tuple(cell.text for cell in script.rows[0].cells), HEADERS)
        widths = [column.width for column in script.columns]
        self.assertAlmostEqual(widths[1] / sum(widths), 0.65, places=3)
        self.assertFalse(script.autofit)
        self.assertIsNotNone(script.rows[0]._tr.find(".//" + W + "tblHeader"))
        self.assertFalse((self.root / "DONE").exists())

    def test_round_trip_normalizes_leading_directions_and_omits_ids(self):
        self.save(["[Leading.]", "First.", "[After.]", "Second."])
        table = self.convert()
        one = writer.convert_script(table, "one-column")
        self.assertEqual(one.name, "My Script.v2.docx")
        self.assertEqual([p.text for p in Document(one).paragraphs][3:],
                         ["First.", "[Leading.]", "[After.]", "Second."])
        three = writer.convert_script(one, "three-column")
        validate_three_column(three)
        self.assertEqual(self.texts(three), self.texts(table))

    def test_preserves_runs_styles_hyperlinks_and_comment_markers(self):
        paragraph = self.document.add_paragraph(style="Quote")
        paragraph.paragraph_format.space_after = Pt(17)
        run = paragraph.add_run("First bold.")
        run.bold, run.font.size = True, Pt(19)
        start = OxmlElement("w:commentRangeStart")
        start.set(qn("w:id"), "4")
        paragraph._p.insert(1, start)
        run.add_break()
        paragraph.add_run("[Italic direction.]").italic = True
        paragraph.add_run().add_break()
        link = OxmlElement("w:hyperlink")
        relationship = paragraph.part.relate_to("https://example.com/", RT.HYPERLINK, is_external=True)
        link.set(qn("r:id"), relationship)
        link_run, text = OxmlElement("w:r"), OxmlElement("w:t")
        text.text = "Linked speech."
        link_run.append(text)
        link.append(link_run)
        paragraph._p.append(link)
        end = OxmlElement("w:commentRangeEnd")
        end.set(qn("w:id"), "4")
        paragraph._p.append(end)
        self.document.save(self.source)
        three = self.convert()
        converted = Document(three)
        table = converted.tables[-1]
        speech = table.cell(1, 1).paragraphs[0]
        self.assertEqual(speech.style.name, "Quote")
        self.assertTrue(speech.runs[0].bold)
        self.assertEqual(speech.runs[0].font.size.pt, 19)
        self.assertEqual(speech.paragraph_format.space_after.pt, 17)
        self.assertTrue(any(run.italic for run in table.cell(1, 2).paragraphs[0].runs))
        hyperlink = table.cell(2, 1)._tc.find(".//" + W + "hyperlink")
        self.assertEqual(converted.part.rels[hyperlink.get(qn("r:id"))].target_ref, "https://example.com/")
        self.assertEqual(len(converted.element.findall(".//" + W + "commentRangeStart")), 1)
        self.assertEqual(len(converted.element.findall(".//" + W + "commentRangeEnd")), 1)
        one = writer.convert_script(three, "one-column")
        self.assertEqual([spoken_text(p) for p in spoken_paragraphs(document_xml(one))],
                         ["First bold.", "Linked speech."])
        self.assertIsNotNone(Document(one).element.find(".//" + W + "hyperlink"))

    def test_version_selection_and_explicit_output_never_overwrite(self):
        self.save(["Speech."])
        (self.root / "My Script.v7.docx").write_bytes(b"historical revision")
        output = self.convert()
        self.assertEqual(output.name, "My Script.v8.docx")
        explicit = self.convert(output=Path("Review Copy.docx"))
        self.assertEqual(explicit.parent, self.root)
        before = explicit.read_bytes()
        for target in (explicit, self.source):
            with self.assertRaises(FileExistsError):
                self.convert(output=target)
        self.assertEqual(explicit.read_bytes(), before)
        link = self.root / "Broken.docx"
        link.symlink_to(self.root / "missing")
        with self.assertRaises(FileExistsError):
            self.convert(output=link)
        other = self.root / "Other"
        other.mkdir()
        self.assertEqual(self.convert(output=other / "Custom.docx").parent, other)
        approved = self.root / "Approved"
        approved.mkdir()
        with self.assertRaisesRegex(ValueError, "human approves"):
            self.convert(output=approved / "Do not approve.docx")

    def test_regenerates_ids_and_splits_edited_dialogue_cells(self):
        self.save(["First.", "Second."])
        table_path = self.convert()
        document = Document(table_path)
        table = document.tables[-1]
        table.cell(1, 0).text = "L99"
        table.cell(2, 0).text = "L42"
        table.cell(1, 1).add_paragraph("Inserted.")
        table.cell(1, 1).add_paragraph("[New direction in dialogue.]")
        table.cell(1, 2).text = "Show balance"
        document.save(table_path)
        output = writer.convert_script(table_path, "three-column")
        validate_three_column(output)
        self.assertEqual([row[0] for row in self.texts(output)], ["L01", "L02", "L03"])
        self.assertEqual(self.texts(output)[1][2], ["New direction in dialogue.", "Show balance"])
        one = writer.convert_script(output, "one-column")
        self.assertIn("[Show balance]", [p.text for p in Document(one).paragraphs])

    def test_direction_only_content_is_preserved_but_not_approvable(self):
        self.save(["[Only direction.]", "[Another.]"])
        output = self.convert()
        self.assertEqual(self.texts(output), [("L01", [""], ["Only direction.", "Another."])])
        with self.assertRaisesRegex(ValueError, "spoken dialogue"):
            validate_three_column(output)
        one = writer.convert_script(output, "one-column")
        self.assertEqual([p.text for p in Document(one).paragraphs][3:], ["[Only direction.]", "[Another.]"])

    def test_page_break_is_not_a_logical_line_boundary(self):
        paragraph = self.document.add_paragraph("First")
        paragraph.add_run().add_break(WD_BREAK.PAGE)
        paragraph.add_run("continued.")
        self.document.save(self.source)
        output = self.convert()
        self.assertEqual(len(self.texts(output)), 1)
        self.assertEqual(self.texts(output)[0][1], ["First continued."])
        self.assertIsNotNone(Document(output).tables[-1].cell(1, 1)._tc.find(".//" + W + "br"))

    def test_standalone_pagebreaks_are_preserved_without_extra_ids(self):
        self.document.add_paragraph("First.")
        self.document.add_page_break()
        self.document.add_paragraph("Second.")
        self.document.save(self.source)
        output = self.convert()
        self.assertEqual([row[0] for row in self.texts(output)], ["L01", "L02"])
        self.assertEqual(len(Document(output).tables[-1]._tbl.findall(".//" + W + "br")), 1)
        one = writer.convert_script(output, "one-column")
        self.assertEqual(len(Document(one).element.findall(".//" + W + "br")), 1)

    def test_real_comments_and_relationships_are_retained(self):
        paragraph = self.document.add_paragraph("Commented speech.")
        self.document.add_comment(paragraph.runs, text="Asset: Show an animation", author="Reviewer")
        self.document.save(self.source)
        with ZipFile(self.source) as package:
            comments = package.read("word/comments.xml")
        output = self.convert()
        with ZipFile(output) as package:
            self.assertEqual(package.read("word/comments.xml"), comments)
        self.assertEqual(len(Document(output).comments), 1)

    def test_reordered_leading_comment_range_fails_safely(self):
        paragraph = self.document.add_paragraph("[Leading.]\nSpoken line.")
        self.document.add_comment(paragraph.runs, text="Asset: Ambiguous range", author="Reviewer")
        self.document.save(self.source)
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "comment range"):
            self.convert()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.root / "My Script.v1.docx").exists())

    def test_split_paragraph_ids_remain_unique(self):
        paragraph = self.document.add_paragraph("First.\nSecond.")
        attribute = "{http://schemas.microsoft.com/office/word/2010/wordml}paraId"
        paragraph._p.set(attribute, "A0000001")
        self.document.save(self.source)
        output = self.convert()
        ids = [p.get(attribute) for p in Document(output).element.iter(W + "p") if p.get(attribute)]
        self.assertEqual(ids, ["A0000001"])

    def test_inline_bracket_phrases_are_not_directions(self):
        self.save(["[First inline] This is spoken [second inline]", "[Show [nested] animation.]"])
        output = self.convert()
        self.assertEqual(self.texts(output), [(
            "L01", ["[First inline] This is spoken [second inline]"], ["Show [nested] animation."]
        )])
        validate_three_column(output)

    def test_comment_membership_change_is_rejected_even_with_valid_endpoint_order(self):
        leading = self.document.add_paragraph("[Leading.]")
        self.document.add_paragraph("First spoken.")
        last = self.document.add_paragraph("Second spoken.")
        self.document.add_comment([leading.runs[0], last.runs[0]], text="Asset: Original passage", author="Human")
        self.document.save(self.source)
        with self.assertRaisesRegex(ValueError, "change a Word comment range"):
            self.convert()
        self.assertFalse((self.root / "My Script.v1.docx").exists())

    def test_equation_only_paragraph_is_not_dropped(self):
        self.document.add_paragraph("Speech.")
        paragraph = self.document.add_paragraph()
        math = OxmlElement("m:oMath")
        run, text = OxmlElement("m:r"), OxmlElement("m:t")
        text.text = "x = 2"
        run.append(text)
        math.append(run)
        paragraph._p.append(math)
        self.document.save(self.source)
        three = self.convert()
        one = writer.convert_script(three, "one-column")
        for path in (three, one):
            self.assertEqual(len(Document(path).element.findall(".//" + qn("m:oMath"))), 1)

    def test_content_control_wrapped_table_row_fails_without_losing_speech(self):
        self.save(["First.", "Second."])
        table_path = self.convert()
        document = Document(table_path)
        table = document.tables[-1]._tbl
        wrapper, content = OxmlElement("w:sdt"), OxmlElement("w:sdtContent")
        row = table.findall(W + "tr")[-1]
        table.remove(row)
        content.append(row)
        wrapper.append(content)
        table.append(wrapper)
        document.save(table_path)
        with self.assertRaisesRegex(ValueError, "Unsupported content in script table"):
            writer.convert_script(table_path, "one-column")
        self.assertFalse((self.root / "My Script.v2.docx").exists())

    def test_inherited_table_text_formatting_survives_both_layouts(self):
        self.save(["Speech."])
        table_path = self.convert()
        document = Document(table_path)
        style = document.styles.add_style("Bold Script Table", WD_STYLE_TYPE.TABLE)
        style.font.bold = True
        style.font.size = Pt(18)
        style.paragraph_format.space_after = Pt(14)
        document.tables[-1].style = style
        document.save(table_path)
        three = writer.convert_script(table_path, "three-column")
        self.assertEqual(Document(three).tables[-1].style.name, "Bold Script Table")
        one = writer.convert_script(table_path, "one-column")
        speech = Document(one).paragraphs[-1]
        self.assertTrue(speech.runs[0].bold)
        self.assertEqual(speech.runs[0].font.size.pt, 18)
        self.assertEqual(speech.paragraph_format.space_after.pt, 14)

    def test_direct_run_formatting_overrides_table_defaults(self):
        self.save(["Speech."])
        table_path = self.convert()
        document = Document(table_path)
        style = document.styles.add_style("Bold Table Default", WD_STYLE_TYPE.TABLE)
        style.font.bold = True
        document.tables[-1].style = style
        document.tables[-1].cell(1, 1).paragraphs[0].runs[0].bold = False
        document.save(table_path)
        one = writer.convert_script(table_path, "one-column")
        self.assertFalse(Document(one).paragraphs[-1].runs[0].bold)

    def test_conditional_table_text_formatting_fails_instead_of_changing_font(self):
        self.save(["Speech."])
        table_path = self.convert()
        document = Document(table_path)
        style = document.styles.add_style("Conditional Font", WD_STYLE_TYPE.TABLE)
        condition = OxmlElement("w:tblStylePr")
        condition.set(qn("w:type"), "band1Horz")
        properties = OxmlElement("w:rPr")
        properties.append(OxmlElement("w:b"))
        condition.append(properties)
        style.element.append(condition)
        document.tables[-1].style = style
        document.save(table_path)
        with self.assertRaisesRegex(ValueError, "Conditional table text formatting"):
            writer.convert_script(table_path, "one-column")
        self.assertFalse((self.root / "My Script.v2.docx").exists())

    def test_unsupported_input_fails_without_publishing(self):
        self.document.add_table(rows=1, cols=2).cell(0, 0).text = "Unknown table"
        self.document.save(self.source)
        with self.assertRaisesRegex(ValueError, "three"):
            self.convert()
        self.assertFalse((self.root / "My Script.v1.docx").exists())
        self.assertEqual(list(self.root.glob("*.docx")), [self.source])

    def test_cli_uses_existing_writer(self):
        self.save(["Speech.", "[Animation.]"])
        command = [sys.executable, str(HELPER.with_name("convert-script.py")), str(self.source),
                   "--to", "three-column", "--output", "CLI.docx"]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        validate_three_column(self.root / "CLI.docx")
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stderr)

    def test_read_only_reader_cli_separates_dialogue_and_directions(self):
        self.save(["Speech.", "[Animation.]"])
        output = self.convert()
        before = output.read_bytes()
        command = [sys.executable, str(writer.ROOT / "studio/script_docx.py"), str(output)]
        text = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertEqual(text.stdout.strip(), "Speech.")
        result = subprocess.run([*command, "--format", "json"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"title": "My Script", "rows": [
            {"id": "L01", "dialogue": ["Speech."], "directions": ["Animation."]},
        ]})
        self.assertEqual(output.read_bytes(), before)

    def test_brackets_split_across_runs_preserve_direction_formatting_and_comments(self):
        self.document.add_paragraph("Speech with [inline] brackets.")
        paragraph = self.document.add_paragraph(style="Quote")
        paragraph.add_run("[").bold = True
        paragraph.add_run("Make an animation.").italic = True
        paragraph.add_run("]").bold = True
        self.document.add_comment(paragraph.runs, text="Production note", author="Human")
        self.document.save(self.source)
        three = self.convert()
        self.assertEqual(self.texts(three), [
            ("L01", ["Speech with [inline] brackets."], ["Make an animation."]),
        ])
        direction = Document(three).tables[-1].cell(1, 2).paragraphs[0]
        self.assertEqual(direction.style.name, "Quote")
        self.assertTrue(next(run for run in direction.runs if run.text).italic)
        self.assertEqual(len(Document(three).comments), 1)
        one = writer.convert_script(three, "one-column")
        self.assertEqual(Document(one).paragraphs[-1].text, "[Make an animation.]")
        self.assertTrue(next(run for run in Document(one).paragraphs[-1].runs
                             if run.text == "Make an animation.").italic)
        self.assertEqual(len(Document(one).comments), 1)

    def test_normalizes_legacy_bracketed_table_directions_without_double_wrapping(self):
        self.save(["Speech.", "[Original direction.]"])
        table_path = self.convert()
        document = Document(table_path)
        document.tables[-1].cell(1, 2).text = "[Legacy direction.]"
        document.save(table_path)
        three = writer.convert_script(table_path, "three-column")
        self.assertEqual(self.texts(three), [("L01", ["Speech."], ["Legacy direction."])])
        repeated = writer.convert_script(three, "three-column")
        self.assertEqual(self.texts(repeated), self.texts(three))
        for source in (table_path, three, repeated):
            one = writer.convert_script(source, "one-column")
            self.assertEqual(Document(one).paragraphs[-1].text, "[Legacy direction.]")


if __name__ == "__main__":
    unittest.main()
