from argparse import ArgumentParser
from docx import Document
from pathlib import Path
import json
import re
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "studio/fingerprints.py").is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from studio.fingerprints import sha256
from scripts.validation import require_prerequisites
from scripts.stage_invalidation import invalidate, prepare_regeneration


parser = ArgumentParser(description="Prepare editable cue-card text plans from approved Studio scripts.")
parser.add_argument("project", type=Path, help="OneDrive Studio project folder")
parser.add_argument("--max-chars", type=int, default=245, help="Approximate characters per initial speaking slide")
parser.add_argument("--overwrite", action="store_true", help="Archive and replace changed existing plans after reviewing human edits")
args = parser.parse_args()

project = args.project.resolve(strict=True)
require_prerequisites(project, ROOT, "1.2")
approved = project / "1-Pre Production" / "1-Script Review" / "Approved"
working = project / "1-Pre Production" / "2-Cue Cards" / "working"
if not approved.is_dir():
    parser.error(f"Approved script folder does not exist: {approved}")
if args.max_chars < 100:
    parser.error("--max-chars must be at least 100")
sources = sorted(approved.glob("*.docx"))
if not sources:
    parser.error(f"No approved Word scripts found in {approved}")

def make_plan(source):
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
    if not slides:
        raise ValueError(f"No spoken script found: {source.name}")
    assert source_words == slide_words, f"Spoken coverage differs: {source.name}"

    plan = {
        "source_docx": str(source),
        "source_sha256": sha256(source).upper(),
        "title": paragraphs[0],
        "slides": slides,
        "spoken_word_count": len(source_words),
        "coverage": "exact token sequence",
    }
    return plan


replacement_refused = False
try:
    jobs = []
    for source in sources:
        plan = make_plan(source)
        target = working / f"{source.stem}.json"
        if target.exists():
            try:
                unchanged = json.loads(target.read_text(encoding="utf-8")) == plan
            except (ValueError, OSError):
                unchanged = False
            if unchanged:
                print(f"KEEP {target.name} (unchanged plan)")
                continue
            if not args.overwrite:
                replacement_refused = True
                raise FileExistsError(f"Review the existing plan before passing --overwrite: {target}")
        jobs.append((target, json.dumps(plan, ensure_ascii=False, indent=2)))
    if jobs:
        working.mkdir(parents=True, exist_ok=True)
        prepare_regeneration(project, ROOT, "1.2", [target for target, _ in jobs if target.exists()],
                             "Publishing changed cue-card plans")
        for target, content in jobs:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(content)
            print(f"WROTE {target}")
except Exception as error:
    if not replacement_refused:
        invalidate(project, ROOT, "1.2", f"Cue-card planning failed: {error}")
    raise
