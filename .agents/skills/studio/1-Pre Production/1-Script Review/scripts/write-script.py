"""Create a new filming-script DOCX from JSON, without replacing existing work."""

import argparse
from io import BytesIO
import json
from pathlib import Path
import sys

from docx import Document
from docx.shared import Inches, Pt


REVIEW_FIELDS = {
    "objective": "Objective",
    "audience": "Intended audience",
    "ending_cta": "Ending / CTA",
    "factual_caveats": "Factual caveats",
}


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
    words = len(" ".join(paragraphs).split())
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
    with target.open("xb") as stream:
        stream.write(buffer.getvalue())
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
