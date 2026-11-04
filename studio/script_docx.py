"""Read Studio script bodies without mixing dialogue with production notes."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADERS = ("ID", "Spoken Dialog", "Stage Directions")


@dataclass
class ScriptRow:
    line_id: str
    dialogue: tuple
    directions: tuple


def document_xml(source):
    """Accept a DOCX path or bytes; retain original elements for comment anchors."""
    try:
        with ZipFile(BytesIO(source) if isinstance(source, bytes) else source) as archive:
            return ET.fromstring(archive.read("word/document.xml"))
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError(f"Cannot read script DOCX: {error}") from error


def paragraph_text(paragraph):
    def visible(node):
        if node.tag in (W + "del", W + "moveFrom", W + "instrText"):
            return ""
        if node.tag == W + "t":
            return node.text or ""
        if node.tag == W + "tab":
            return "\t"
        if node.tag == W + "noBreakHyphen":
            return "\u2011"
        if node.tag in (W + "br", W + "cr"):
            return "\n" if node.get(W + "type", "textWrapping") == "textWrapping" else " "
        return "".join(visible(child) for child in node)

    return visible(paragraph)


def is_direction(text):
    text = text.strip()
    if not text.startswith("["):
        return False
    depth = 0
    for index, character in enumerate(text):
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
    return False


def spoken_text(paragraph):
    return "\n".join(line for line in paragraph_text(paragraph).splitlines()
                     if not is_direction(line))


def script_title(root):
    body = root.find(W + "body")
    return next((paragraph_text(p).strip() for p in body.findall(W + "p")
                 if paragraph_text(p).strip()), "")


def script_blocks(root, require_heading=False):
    body = root.find(W + "body")
    if body is None:
        raise ValueError("DOCX has no document body")
    blocks = [node for node in body if node.tag != W + "sectPr"]
    headings = [i for i, node in enumerate(blocks)
                if node.tag == W + "p" and paragraph_text(node).strip().lower() == "spoken script"]
    if len(headings) > 1:
        raise ValueError("Script must have only one Spoken script heading")
    if not headings:
        if require_heading:
            raise ValueError("Script requires a Spoken script heading before its body")
        return blocks
    return blocks[headings[0] + 1:]


def script_rows(root, require_three_column=False):
    blocks = script_blocks(root, require_heading=require_three_column)
    tables = [node for node in blocks if node.tag == W + "tbl"]
    if not tables:
        if require_three_column:
            raise ValueError("Approved script requires an ID / Spoken Dialog / Stage Directions table")
        rows, leading = [], []
        for paragraph in blocks:
            if paragraph.tag != W + "p":
                raise ValueError("Unsupported content in script body")
            text = paragraph_text(paragraph)
            if text.strip() and not spoken_text(paragraph).strip():
                (rows[-1].directions if rows else leading).append(paragraph)
            elif text.strip():
                rows.append(ScriptRow("", [paragraph], leading))
                leading = []
        if leading:
            rows.append(ScriptRow("", [], leading))
        return [ScriptRow(row.line_id, tuple(row.dialogue), tuple(row.directions)) for row in rows]
    if len(tables) != 1 or any(node is not tables[0] and (
        node.tag != W + "p" or paragraph_text(node).strip()
    ) for node in blocks):
        raise ValueError("Script body must contain one three-column table, without extra text")
    table = tables[0]
    if any(child.tag not in (W + "tblPr", W + "tblGrid", W + "tr") for child in table):
        raise ValueError("Unsupported content in script table; expected plain Word rows")
    xml_rows = table.findall(W + "tr")
    if not xml_rows:
        raise ValueError("Script table is empty")
    rows = []
    for index, row in enumerate(xml_rows):
        if any(child.tag not in (W + "trPr", W + "tc") for child in row):
            raise ValueError("Unsupported content in script row; expected plain Word cells")
        cells = row.findall(W + "tc")
        if len(cells) != 3 or any(
            cell.find(".//" + W + "gridSpan") is not None
            or cell.find(".//" + W + "vMerge") is not None
            or cell.find(W + "tbl") is not None for cell in cells
        ):
            raise ValueError("Script rows require three unmerged cells without nested tables")
        if any(child.tag not in (W + "tcPr", W + "p") for cell in cells for child in cell):
            raise ValueError("Unsupported content in script cell; expected Word paragraphs")
        paragraphs = [tuple(cell.findall(W + "p")) for cell in cells]
        texts = ["\n".join(paragraph_text(p) for p in group).strip() for group in paragraphs]
        if index == 0:
            if tuple(texts) != HEADERS:
                raise ValueError("Script table headers must be ID / Spoken Dialog / Stage Directions")
            continue
        if require_three_column and texts[0] != f"L{index:02d}":
            raise ValueError(f"Script row {index} must have ID L{index:02d}; found {texts[0]!r}")
        if require_three_column and not any(texts[1:]):
            raise ValueError(f"Script row {index} is empty")
        if require_three_column and any(is_direction(line) for line in texts[1].splitlines()):
            raise ValueError(f"Script row {index} has a stage direction in Spoken Dialog")
        rows.append(ScriptRow(texts[0], paragraphs[1], paragraphs[2]))
    if require_three_column and not any(spoken_text(p).strip() for row in rows for p in row.dialogue):
        raise ValueError("Approved script must contain spoken dialogue")
    return rows


def spoken_paragraphs(root, preserve_blanks=False):
    rows = script_rows(root)
    blocks = script_blocks(root)
    if preserve_blanks and not any(block.tag == W + "tbl" for block in blocks):
        return [p for p in blocks if p.tag == W + "p"
                and (not paragraph_text(p).strip() or spoken_text(p).strip())]
    return [p for row in rows for p in row.dialogue if spoken_text(p).strip()]


def validate_three_column(path):
    try:
        script_rows(document_xml(Path(path)), require_three_column=True)
    except ValueError as error:
        raise ValueError(f"{Path(path).name}: {error}") from error


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Script DOCX to read without modifying it")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        root = document_xml(args.source)
        rows = script_rows(root)
        if args.format == "json":
            result = {"title": script_title(root), "rows": [
                {"id": row.line_id,
                 "dialogue": [spoken_text(p) for p in row.dialogue if spoken_text(p).strip()],
                 "directions": [paragraph_text(p) for p in row.directions if paragraph_text(p).strip()]}
                for row in rows]}
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("\n\n".join(spoken_text(p) for row in rows for p in row.dialogue if spoken_text(p).strip()))
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
