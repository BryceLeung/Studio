from argparse import ArgumentParser
from docx import Document
from pathlib import Path
import hashlib
import json
import re


parser = ArgumentParser(description="Prepare editable cue-card text plans from approved Studio scripts.")
parser.add_argument("project", type=Path, help="OneDrive Studio project folder")
parser.add_argument("--max-chars", type=int, default=245, help="Approximate characters per initial speaking slide")
args = parser.parse_args()

project = args.project.resolve(strict=True)
approved = project / "1-Pre Production" / "1-Script Review" / "Approved"
working = project / "1-Pre Production" / "2-Cue Cards" / "working"
if not approved.is_dir():
    parser.error(f"Approved script folder does not exist: {approved}")
if args.max_chars < 100:
    parser.error("--max-chars must be at least 100")
working.mkdir(parents=True, exist_ok=True)
sources = sorted(approved.glob("*.docx"))
if not sources:
    parser.error(f"No approved Word scripts found in {approved}")

for source in sources:
    paragraphs = [paragraph.text for paragraph in Document(source).paragraphs]
    spoken = paragraphs[paragraphs.index("Spoken script") + 1 :]

    units = []
    break_before = 0
    for paragraph in spoken:
        if not paragraph.strip():
            break_before = max(break_before, 2)
            continue
        for line in paragraph.split("\n"):
            sentences = re.split(r"(?<=[.!?…])\s+(?=[A-Z“‘])", line)
            for sentence in sentences:
                if sentence:
                    units.append((sentence, break_before))
                    break_before = 0
            break_before = max(break_before, 1)

    slides = []
    current = ""
    for unit, newlines in units:
        separator = "\n" * newlines if newlines else " "
        candidate = current + (separator if current else "") + unit
        if current and len(candidate) > args.max_chars:
            slides.append(current)
            current = unit
        else:
            current = candidate
    if current:
        slides.append(current)

    source_words = re.findall(r"\S+", " ".join(spoken))
    slide_words = re.findall(r"\S+", "\n".join(slides))
    assert source_words == slide_words, f"Spoken coverage differs: {source.name}"

    plan = {
        "source_docx": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest().upper(),
        "title": paragraphs[0],
        "slides": slides,
        "spoken_word_count": len(source_words),
        "coverage": "exact token sequence",
    }
    target = working / f"{source.stem}.json"
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(source.name, len(slides), "speaking slides", max(map(len, slides)), "max characters")
