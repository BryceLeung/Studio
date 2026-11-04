"""Create a new filming-script DOCX from JSON, without replacing existing work."""

import argparse
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import re
import sys

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
from studio.script_docx import (  # noqa: E402
    HEADERS, W, is_direction, paragraph_text, script_blocks, script_rows, spoken_text,
)


REVIEW_FIELDS = {
    "objective": "Objective",
    "audience": "Intended audience",
    "ending_cta": "Ending / CTA",
    "factual_caveats": "Factual caveats",
}


def publish_new(target: Path, content: bytes) -> None:
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing file: {target}")
    with target.open("xb") as stream:
        stream.write(content)


def split_paragraph(paragraph):
    """Split soft breaks without flattening runs, hyperlinks, or comment markers."""
    def split(node):
        if node.tag in (W + "br", W + "cr") and node.get(W + "type", "textWrapping") == "textWrapping":
            return [None, None]
        if node.tag == W + "t" and "\n" in (node.text or ""):
            parts = []
            for text in node.text.split("\n"):
                part = deepcopy(node)
                part.text = text
                part.set(qn("xml:space"), "preserve")
                parts.append(part)
            return parts
        if not len(node):
            return [deepcopy(node)]
        properties = [child for child in node if child.tag in (W + "pPr", W + "rPr")]

        def shell():
            result = deepcopy(node)
            for child in list(result):
                result.remove(child)
            for child in properties:
                result.append(deepcopy(child))
            return result

        parts = [shell()]
        for child in node:
            if child in properties:
                continue
            for index, piece in enumerate(split(child)):
                if index:
                    parts.append(shell())
                if piece is not None:
                    parts[-1].append(piece)
        return parts

    parts = split(paragraph)
    for part in parts[1:]:
        for name in ("paraId", "textId"):
            part.attrib.pop("{http://schemas.microsoft.com/office/word/2010/wordml}" + name, None)
    return parts


def conversion_rows(document):
    nontext_tags = {W + name for name in (
        "drawing", "pict", "object", "br", "pageBreakBefore", "bookmarkStart", "bookmarkEnd",
        "commentRangeStart", "commentRangeEnd", "commentReference", "footnoteReference", "endnoteReference",
    )}
    nontext_tags.update("{http://schemas.openxmlformats.org/officeDocument/2006/math}" + name
                        for name in ("oMath", "oMathPara"))

    def nontext_content(paragraph):
        return any(node.tag in nontext_tags for node in paragraph.iter())

    blocks = script_blocks(document.element, require_heading=True)
    if any(block.tag == W + "tbl" for block in blocks):
        source_rows = [(row.dialogue, row.directions) for row in script_rows(document.element)]
    else:
        if any(block.tag != W + "p" for block in blocks):
            raise ValueError("Unsupported content in script body; expected paragraphs or the script table")
        source_rows = [(blocks, [])]
    rows, leading, pending = [], [], []
    for dialogue, directions in source_rows:
        for source in dialogue:
            for paragraph in split_paragraph(source):
                text = paragraph_text(paragraph)
                if not text.strip():
                    if nontext_content(paragraph):
                        (rows[-1][0] if rows else pending).append(paragraph)
                    continue
                if is_direction(text):
                    (rows[-1][1] if rows else leading).append(paragraph)
                else:
                    rows.append(([*pending, paragraph], leading))
                    pending = []
                    leading = []
        for source in directions:
            for paragraph in split_paragraph(source):
                if paragraph_text(paragraph).strip() or nontext_content(paragraph):
                    (rows[-1][1] if rows else leading).append(paragraph)
    if leading or pending:
        rows.append((pending, leading))
    return rows


def strip_direction_brackets(paragraph):
    """Remove only outer direction delimiters, retaining their runs and anchors."""
    cuts, offset = set(), 0
    for line in paragraph_text(paragraph).splitlines(keepends=True):
        if is_direction(line):
            cuts.update((offset + len(line) - len(line.lstrip()), offset + len(line.rstrip()) - 1))
        offset += len(line)
    if not cuts:
        return
    offset = 0

    def visit(node):
        nonlocal offset
        if node.tag in (W + "del", W + "moveFrom", W + "instrText"):
            return
        if not len(node):
            text = paragraph_text(node)
            if node.tag == W + "t":
                node.text = "".join(character for index, character in enumerate(text) if offset + index not in cuts)
                node.set(qn("xml:space"), "preserve")
            offset += len(text)
        else:
            for child in node:
                visit(child)

    visit(paragraph)


def comment_coverage(root):
    """Compare original comment selections, not new table IDs or header text."""
    root = deepcopy(root)
    blocks = script_blocks(root, require_heading=True)
    if any(block.tag == W + "tbl" for block in blocks):
        selected = {p for row in script_rows(root) for p in (*row.dialogue, *row.directions)}
    else:
        selected = set(blocks)
    # Delimiters are layout syntax, not part of the anchored production request.
    for paragraph in selected:
        strip_direction_brackets(paragraph)
    active, content = set(), {}

    def visit(node, included=False):
        if node.tag in (W + "del", W + "moveFrom", W + "instrText"):
            return
        if node.tag == W + "p":
            included = node in selected
        identifier = node.get(W + "id")
        if node.tag == W + "commentRangeStart":
            if identifier in active:
                raise ValueError("Script contains a duplicate Word comment range")
            active.add(identifier)
            content.setdefault(identifier, [])
        elif node.tag == W + "commentRangeEnd":
            if identifier not in active:
                raise ValueError("Conversion would reorder a Word comment range; reanchor it to spoken dialogue")
            active.remove(identifier)
        elif included and not len(node):
            text = paragraph_text(node)
            for key in active:
                content[key].append(text)
        for child in node:
            visit(child, included)
        if node.tag == W + "p" and included:
            for key in active:
                content[key].append("\n")

    visit(root)
    if active:
        raise ValueError("Script contains an unclosed Word comment range")
    return {key: " ".join("".join(parts).split()) for key, parts in content.items()}


def materialize_table_text(document, table, rows):
    """Carry nonconditional table text defaults into a paragraph-only layout."""
    styles, style = [], table.style
    while style is not None:
        styles.insert(0, style)
        style = style.base_style
    defaults = {"pPr": {}, "rPr": {}}
    for style in styles:
        for conditional in style.element.findall(W + "tblStylePr"):
            if conditional.find(W + "rPr") is not None or conditional.find(W + "pPr") is not None:
                raise ValueError("Conditional table text formatting cannot be flattened safely; use paragraph/run styles")
        for kind in defaults:
            properties = style.element.find(W + kind)
            if properties is not None:
                defaults[kind].update({child.tag: child for child in properties})

    def style_keys(style, kind):
        keys = set()
        while style is not None:
            properties = style.element.find(W + kind)
            if properties is not None:
                keys.update(child.tag for child in properties)
            style = style.base_style
        return keys

    for dialogue, directions in rows:
        for element in (*dialogue, *directions):
            paragraph = Paragraph(element, document._body)
            properties = element.get_or_add_pPr()
            existing = {child.tag for child in properties} | style_keys(paragraph.style, "pPr")
            for key, value in defaults["pPr"].items():
                if key not in existing:
                    properties.append(deepcopy(value))
            for run in element.iter(W + "r"):
                properties = run.get_or_add_rPr()
                character_style = properties.find(W + "rStyle")
                styled = set()
                if character_style is not None:
                    style_id = character_style.get(W + "val")
                    style = next((item for item in document.styles if item.style_id == style_id), None)
                    styled = style_keys(style, "rPr")
                existing = {child.tag for child in properties} | style_keys(paragraph.style, "rPr") | styled
                for key, value in defaults["rPr"].items():
                    if key not in existing:
                        properties.append(deepcopy(value))


def convert_script(source: Path, layout: str, output: Path = None) -> Path:
    """Convert a review draft in place structurally, but publish only a new file."""
    source = Path(source).expanduser().resolve(strict=True)
    if source.suffix.lower() != ".docx" or layout not in ("one-column", "three-column"):
        raise ValueError("Expected a .docx source and one-column or three-column layout")
    if output is None:
        match = re.fullmatch(r"(.+)\.v(\d+)", source.stem, re.I)
        stem = match[1] if match else source.stem
        version = int(match[2]) if match else 0
        pattern = re.compile(re.escape(stem) + r"\.v(\d+)\.docx", re.I)
        for sibling in source.parent.iterdir():
            found = pattern.fullmatch(sibling.name)
            if found:
                version = max(version, int(found[1]))
        target = source.with_name(f"{stem}.v{version + 1}.docx")
    else:
        target = Path(output).expanduser()
        if target.parent == Path("."):
            target = source.parent / target
        target = target.absolute()
    if target.suffix.lower() != ".docx":
        raise ValueError("Output must have a .docx extension")
    if target.parent.resolve().name.lower() == "approved":
        raise ValueError("Write converted drafts outside Approved/; only the human approves a revision")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing file: {target}")
    document = Document(source)
    original_comments = comment_coverage(document.element)
    source_table = next((table for table in document.tables
                         if table._tbl in script_blocks(document.element, require_heading=True)), None)
    if any(block.find(".//" + W + "sectPr") is not None
           for block in script_blocks(document.element, require_heading=True)):
        raise ValueError("Section breaks inside the script body are unsupported; convert a draft without them")
    rows = conversion_rows(document)
    if not rows:
        raise ValueError("Script body is empty")
    expected = [paragraph_text(p) for dialogue, _ in rows for p in dialogue if paragraph_text(p).strip()]
    if layout == "one-column" and source_table is not None:
        materialize_table_text(document, source_table, rows)
    body = document.element.body
    for block in script_blocks(document.element, require_heading=True):
        body.remove(block)
    if layout == "three-column":
        table = document.add_table(rows=1, cols=3)
        table.style = source_table.style if source_table is not None else "Table Grid"
        table.autofit = False
        section = document.sections[-1]
        width = section.page_width - section.left_margin - section.right_margin
        for column, proportion in zip(table.columns, (0.07, 0.65, 0.28)):
            column.width = int(width * proportion)
        for cell, label in zip(table.rows[0].cells, HEADERS):
            cell.text = label
            cell.paragraphs[0].runs[0].bold = True
        repeat = OxmlElement("w:tblHeader")
        table.rows[0]._tr.get_or_add_trPr().append(repeat)
        for number, (dialogue, directions) in enumerate(rows, 1):
            cells = table.add_row().cells
            cells[0].text = f"L{number:02d}"
            for cell, paragraphs, direction in zip(cells[1:], (dialogue, directions), (False, True)):
                if paragraphs:
                    cell._tc.remove(cell.paragraphs[0]._p)
                    for paragraph in paragraphs:
                        paragraph = deepcopy(paragraph)
                        if direction:
                            strip_direction_brackets(paragraph)
                        cell._tc.append(paragraph)
        for row in table.rows:
            for cell, proportion in zip(row.cells, (0.07, 0.65, 0.28)):
                cell.width = int(width * proportion)
    else:
        for dialogue, directions in rows:
            for paragraph in dialogue:
                body.insert_element_before(deepcopy(paragraph), "w:sectPr")
            for paragraph in directions:
                paragraph = deepcopy(paragraph)
                if paragraph_text(paragraph).strip() and not is_direction(paragraph_text(paragraph)):
                    first, last = OxmlElement("w:r"), OxmlElement("w:r")
                    first_text, last_text = OxmlElement("w:t"), OxmlElement("w:t")
                    first_text.text, last_text.text = "[", "]"
                    first.append(first_text)
                    last.append(last_text)
                    paragraph.insert(1 if paragraph.find(W + "pPr") is not None else 0, first)
                    paragraph.append(last)
                body.insert_element_before(paragraph, "w:sectPr")
    if comment_coverage(document.element) != original_comments:
        raise ValueError("Conversion would change a Word comment range; anchor it to spoken dialogue first")
    buffer = BytesIO()
    document.save(buffer)
    reopened = Document(BytesIO(buffer.getvalue()))
    actual = [spoken_text(p) for row in script_rows(reopened.element) for p in row.dialogue
              if paragraph_text(p).strip()]
    if actual != expected:
        raise ValueError("DOCX verification failed: spoken content changed")
    publish_new(target, buffer.getvalue())
    return target


def write_script(project_root: Path, filename: str, draft: dict) -> tuple[Path, int]:
    if (
        not filename
        or any(character in filename for character in '<>:"/\\|?*')
        or any(ord(character) < 32 for character in filename)
        or filename != filename.strip()
        or Path(filename).suffix.lower() != ".docx"
        or not Path(filename).stem.strip(". ")
    ):
        raise ValueError("filename must be a plain .docx filename, not a path")
    if not isinstance(draft, dict) or set(draft) - {"title", "review", "sources", "notes", "paragraphs"}:
        raise ValueError("draft must have title, paragraphs, and optional review, sources, and notes")
    title = draft.get("title")
    if not isinstance(title, str) or not title.strip() or "\n" in title or "\r" in title:
        raise ValueError("title must be a nonempty single-line string")
    paragraphs = draft.get("paragraphs")
    notes = draft.get("notes", [])
    sources = draft.get("sources", [])
    review = draft.get("review", {})
    if not isinstance(review, dict) or set(review) - REVIEW_FIELDS.keys():
        raise ValueError("review must be an object with objective, audience, ending_cta, or factual_caveats")
    if any(not isinstance(value, str) for value in review.values()):
        raise ValueError("review field values must be strings")
    if review.get("objective", "").strip() not in ("", "Reach", "Trust", "Traffic"):
        raise ValueError("review objective must be Reach, Trust, or Traffic")
    for label, values in (("paragraphs", paragraphs), ("notes", notes), ("sources", sources)):
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"{label} must be an array of nonempty strings")
    if not paragraphs:
        raise ValueError("paragraphs must contain spoken text")
    if any(value.strip() == "Spoken script" for value in [title, *notes, *sources, *review.values(), *paragraphs]):
        raise ValueError("Spoken script is a reserved heading; do not include it in the draft")
    words = len(" ".join(line for paragraph in paragraphs for line in paragraph.splitlines()
                         if not is_direction(line)).split())
    review_lines = [
        f"{label}: {review.get(key, '').strip() or 'Not specified'}"
        for key, label in REVIEW_FIELDS.items()
    ]
    review_lines.insert(2, f"Estimated runtime: {words / 160:.1f}-{words / 140:.1f} minutes ({words} spoken words at 140-160 wpm)")

    project = project_root.resolve(strict=True)
    step = project / "1-Pre Production" / "1-Script Review"
    if not step.is_dir():
        raise ValueError(f"Script Review folder does not exist: {step}")
    target = step / filename
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing candidate: {target}")

    document = Document()
    document.core_properties.title = title
    document.styles["Normal"].font.name = "Calibri"
    document.styles["Normal"].font.size = Pt(12)
    document.styles["Normal"].paragraph_format.space_after = Pt(8)
    for section in document.sections:
        section.top_margin = section.bottom_margin = Inches(0.75)
    document.add_heading(title, 0)
    document.add_heading("Review notes (not spoken)", 1)
    for line in review_lines:
        document.add_paragraph(line)
    document.add_heading("Sources (not spoken)", 1)
    for source in sources or ["Not supplied"]:
        document.add_paragraph(source)
    if notes:
        document.add_heading("Additional notes (not spoken)", 1)
        for note in notes:
            document.add_paragraph(note)
    document.add_page_break()
    document.add_heading("Spoken script", 1)
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)

    # Check the exact paragraph sequence before publishing to the project.
    buffer = BytesIO()
    document.save(buffer)
    buffer.seek(0)
    saved = [paragraph.text for paragraph in Document(buffer).paragraphs]
    expected_notes = ["Additional notes (not spoken)", *notes] if notes else []
    if saved != [
        title, "Review notes (not spoken)", *review_lines,
        "Sources (not spoken)", *(sources or ["Not supplied"]),
        *expected_notes, "", "Spoken script", *paragraphs,
    ]:
        raise ValueError("DOCX verification failed: paragraph content changed")
    publish_new(target, buffer.getvalue())
    return target, words


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path, help="Real OneDrive project directory")
    parser.add_argument("--filename", required=True, help="New candidate filename, including .docx")
    parser.add_argument("--input", type=Path, help="UTF-8 JSON draft file; omit to read JSON from stdin")
    args = parser.parse_args()
    try:
        if args.input:
            draft = json.loads(args.input.read_text(encoding="utf-8-sig"))
        else:
            draft = json.loads(sys.stdin.read().lstrip("\ufeff"))
        target, words = write_script(args.project_root, args.filename, draft)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Created candidate: {target}")
    print(f"Spoken words: {words}; estimated runtime at 140-160 wpm: {words / 160:.1f}-{words / 140:.1f} minutes")
    print("Awaiting human review; no approval, log entry, or completion marker written.")


if __name__ == "__main__":
    main()
