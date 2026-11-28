"""Approved shortlist parsing and the canonical First Cut version inventory."""
from pathlib import Path
import re


SHORTLIST = Path("1-Pre Production/3-Extracts/APPROVED/Short-Form Excerpt Shortlist.md")
STAGE = Path("3-Post Production/6-First Cut")


def parse_shortlist(text):
    extracts = []
    headers = list(re.finditer(r"^## (\d+)\. ([^\n]+)\n(.*?)(?=^## |\Z)", text, re.M | re.S))
    # Existing approved shortlists may declare a format for a numbered range in
    # the introduction (e.g. “Extracts 1–4 retain their vertical presentation”).
    intro = text[:headers[0].start()] if headers else ""
    defaults = {}
    for start, end, label in re.findall(
        r"\bExtracts (\d+)[–-](\d+) (?:retain their|use) (vertical|portrait|horizontal|landscape)\b",
        intro, re.I,
    ):
        for number in (int(h[1]) for h in headers if int(start) <= int(h[1]) <= int(end)):
            defaults.setdefault(number, set()).add(orientation(label))
    for match in headers:
        number, title, body = match.groups()
        number = int(number)
        status = re.search(r"\*\*Review status:\*\* (.+)", body)
        if not status or not re.match(r"Approved\b", status[1].strip()):
            continue
        source = re.search(r"\*\*Source(?:/version)?:\*\* (.+)", body)
        if not source or "### Provisional spoken script" not in body:
            raise ValueError(f"Extract {number} lacks source or spoken script.")
        quoted = re.search(r"`([^`]+)`", source[1])
        metadata, spoken = body.split("### Provisional spoken script", 1)
        fields = re.findall(r"^\s*-\s+\*\*(?:Formats?|Versions?):\*\*\s*([^\n]+)", metadata, re.M | re.I)
        if len(fields) > 1:
            raise ValueError(f"Extract {number} has multiple format declarations.")
        formats = (set(map(orientation, re.findall(r"\b(?:portrait|vertical|landscape|horizontal|9:16|16:9)\b",
                                                   fields[0].split('. ')[0], re.I)))
                   if fields else defaults.get(number, set()))
        if fields and fields[0].strip().rstrip(".").casefold() == "both":
            formats = {"portrait", "landscape"}
        if not fields and len(formats) > 1:
            raise ValueError(f"Extract {number} has conflicting format declarations.")
        if fields and not formats:
            raise ValueError(f"Extract {number} format must specify portrait, landscape, or both.")
        passages, jump = [], False
        for paragraph in re.split(r"\n\s*\n", spoken.strip()):
            if re.fullmatch(r"\*\*\[.*\]\*\*", paragraph.strip()) or paragraph.strip() == "> [PROPOSED JUMP]":
                jump = True
                continue
            current = []

            def flush():
                nonlocal jump
                if current:
                    passages.append({"text": " ".join(current), "jump_before": jump})
                    current.clear()
                    jump = False

            for line in paragraph.splitlines():
                line = line.strip()
                if not line or line == ">":
                    flush()
                elif line.startswith(">"):
                    current.append(line[1:].strip())
                elif line.startswith(("- ", "* ")):
                    flush()
                else:
                    current.append(line)
            flush()
        if not passages or jump:
            raise ValueError(f"Empty extract or trailing jump: {number}")
        if any(item["number"] == number for item in extracts):
            raise ValueError(f"Duplicate approved extract number: {number}")
        extracts.append({"number": number, "title": title.strip(), "formats": sorted(formats),
                         "source": Path((quoted[1] if quoted else source[1]).strip()).name,
                         "passages": passages})
    return extracts


def orientation(label):
    return "portrait" if label.casefold() in ("portrait", "vertical", "9:16") else "landscape"


def variants(extracts):
    seen = set()
    for extract in extracts:
        if not extract["formats"]:
            raise ValueError(f"Extract {extract['number']} has no requested format. Add a Formats field to the approved shortlist.")
        for format_name in extract["formats"]:
            portrait = format_name == "portrait"
            name = f"{extract['number']:02d}-{extract['title']}" + (" Portrait" if portrait else "")
            folder = STAGE / ("Extracts/Portrait" if portrait else "Extracts")
            path = folder / (re.sub(r'[<>:"/\\|?*]', '-', name) + ".FCPXML")
            if path.stem.casefold() in seen:
                raise ValueError(f"Duplicate First Cut project stem: {path.stem}")
            seen.add(path.stem.casefold())
            yield path, name, portrait, extract


def selection(extract):
    """Editorial inputs for one extract, excluding other formats and proposals."""
    return {"number": extract["number"], "title": extract["title"], "source": extract["source"],
            "passages": [{k: p[k] for k in ("text", "jump_before")} for p in extract["passages"]]}
