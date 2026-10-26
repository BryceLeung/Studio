"""Word comment import, reconciliation and protected Markdown/JSON registry checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
from scripts import stage_completion as completion
from scripts.validation import audit

STAGE = Path("1-Pre Production/4-Asset Breakdown")
SCRIPTS = Path("1-Pre Production/1-Script Review/Approved")
SHORTLIST = Path("1-Pre Production/3-Extracts/APPROVED/Short-Form Excerpt Shortlist.md")
TYPES = ("B-roll", "Graphic", "Still", "Animation", "Screen Recording", "Audio", "Other")
REVIEWS = ("Pending Review", "Keep", "Reject")
FIELDS = ("script", "start", "end", "passage", "extracts", "type", "brief", "notes")
HEADERS = ("ID", "Origin", "Source Key", "Script", "Start", "End", "Passage", "Extracts",
           "Type", "Brief", "Notes", "Review", "Active", "Change", "Provenance", "Proposal")
COLUMNS = dict(zip(FIELDS, ("Script", "Start", "End", "Passage", "Extracts", "Type", "Brief", "Notes")))
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def normalized(text):
    return " ".join(text.split())


def read_docx(path):
    """Read the visible main story, tracking overlapping Word comment ranges."""
    with ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        comments = (ET.fromstring(archive.read("word/comments.xml"))
                    if "word/comments.xml" in archive.namelist() else [])
    definitions = {}
    for comment in comments:
        key = comment.get(W + "id")
        if key in definitions:
            raise ValueError("Duplicate Word comment ID")
        comment_text = "\n".join("".join(t.text or "" for t in p.iter(W + "t"))
                                 for p in comment.findall(W + "p"))
        asset = re.match(r"^\s*Asset:\s*(.*)$", comment_text, re.I | re.S)
        definitions[key] = {"comment_id": key, "author": comment.get(W + "author", ""),
                            "date": comment.get(W + "date", ""),
                            "comment_text": comment_text,
                            "brief": asset.group(1).strip() if asset else None}
    parts, length, opened, ranges = [], 0, {}, {}

    def add(text):
        nonlocal length
        parts.append(text)
        length += len(text)

    def visit(node):
        # Deleted revisions and field instructions are not spoken script text.
        if node.tag in (W + "del", W + "moveFrom", W + "instrText"):
            return
        if node.tag == W + "commentRangeStart":
            key = node.get(W + "id")
            if key in opened or key in ranges:
                raise ValueError("Duplicate Word comment range")
            opened[key] = length
        elif node.tag == W + "commentRangeEnd":
            key = node.get(W + "id")
            if key not in opened:
                raise ValueError("Word comment ends without a start")
            ranges[key] = (opened.pop(key), length)
        elif node.tag == W + "t":
            add(node.text or "")
        elif node.tag in (W + "tab", W + "br", W + "cr"):
            add(" ")
        else:
            for child in node:
                visit(child)
            if node.tag == W + "p":
                add("\n")

    body = document.find(W + "body")
    if body is None:
        raise ValueError("Missing Word document body")
    visit(body)
    if opened:
        raise ValueError("Unclosed Word comment range")
    for key, definition in definitions.items():
        if definition["brief"] is not None and key not in ranges:
            raise ValueError(f"Asset: comment {key} in {path.name} has no anchored script range")
    text = "".join(parts)
    tokens = list(re.finditer(r"\S+", text))
    words = [match.group() for match in tokens]
    imported = []
    for key, (start, end) in ranges.items():
        if key not in definitions:
            raise ValueError("Word range references missing comment")
        if definitions[key]["brief"] is None:
            continue
        indices = [i for i, token in enumerate(tokens) if token.start() < end and token.end() > start]
        if not indices or not definitions[key]["brief"]:
            raise ValueError("Word comment needs a nonempty highlighted passage and brief")
        imported.append(dict(definitions[key], highlighted_text=text[start:end], start=indices[0], end=indices[-1],
                             passage=" ".join(words[indices[0]:indices[-1] + 1])))
    if not words:
        raise ValueError(f"Empty script: {path.name}")
    return words, imported


def extract_context(text, scripts):
    """Follow 1.3's existing numbered shortlist format without postproduction imports."""
    result = []
    for match in re.finditer(r"^## (\d+)\. ([^\n]+)\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        number, title, body = match.groups()
        status = re.search(r"\*\*Review status:\*\* (.+)", body)
        if not status or status.group(1).strip() != "Approved":
            continue
        source = re.search(r"\*\*Source:\*\* (.+)", body)
        if not source or "### Provisional spoken script" not in body:
            raise ValueError(f"Extract {number} lacks source/spoken script")
        paths = [path for path in scripts if Path(path).name == source.group(1).strip()]
        if len(paths) != 1:
            raise ValueError(f"Unknown/ambiguous extract source: {number}")
        path = paths[0]
        words = scripts[path]["words"]
        alternatives = []
        jump = False
        for paragraph in re.split(r"\n\s*\n", body.split("### Provisional spoken script", 1)[1].strip()):
            if paragraph.strip() == "> [PROPOSED JUMP]":
                jump = True
                continue
            if paragraph.startswith(">"):
                raise ValueError("Unknown extract directive")
            wanted = paragraph.split()
            if not wanted:
                continue
            alternatives.append([(i, i + len(wanted) - 1) for i in range(len(words) - len(wanted) + 1)
                                 if words[i:i + len(wanted)] == wanted])
            jump = False
        solutions = []

        def walk(index, spans):
            if len(solutions) > 1:
                return
            if index == len(alternatives):
                solutions.append(spans)
                return
            for start, end in alternatives[index]:
                if not spans or start > spans[-1][1]:
                    walk(index + 1, spans + [[start, end]])

        walk(0, [])
        if not alternatives or jump or len(solutions) != 1:
            raise ValueError(f"Extract {number} passages need unique ordered source anchors")
        identity = "EXTRACT-" + str(int(number))
        if any(item["id"] == identity for item in result):
            raise ValueError("Duplicate extract number")
        result.append({"id": identity, "title": title.strip(), "script": path, "spans": solutions[0]})
    # Do not mistake a differently formatted approved entry for a no-extract decision.
    if len(re.findall(r"\*\*Review status:\*\*\s*Approved\b", text)) != len(result):
        raise ValueError("Approved extract is outside the supported numbered shortlist format")
    return result


def context(project):
    project = Path(project).resolve(strict=True)
    directory = project / SCRIPTS
    paths = sorted((p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".docx"),
                   key=lambda p: p.name) if directory.is_dir() else []
    if not paths:
        raise ValueError("No approved scripts")
    paths.append(project / SHORTLIST)
    for path in paths:
        if not path.resolve(strict=True).is_relative_to(project):
            raise ValueError("Input escapes project")
    scripts = {}
    for path in paths[:-1]:
        words, comments = read_docx(path)
        scripts[path.relative_to(project).as_posix()] = {"words": words, "comments": comments}
    return {"inputs": [completion.fingerprint(p, project, "project") for p in paths],
            "scripts": scripts,
            "extracts": extract_context(paths[-1].read_text(encoding="utf-8-sig"), scripts)}


def validate_proposal(proposal, ctx):
    if not isinstance(proposal, dict) or any(field not in proposal for field in FIELDS):
        raise ValueError("Proposal lacks required fields")
    for field in ("script", "passage", "brief", "notes", "type"):
        if not isinstance(proposal[field], str) or not proposal[field].strip():
            raise ValueError(f"Empty/invalid {field}")
    if proposal["type"] not in TYPES or proposal["script"] not in ctx["scripts"]:
        raise ValueError("Invalid type or script reference")
    start, end = proposal["start"], proposal["end"]
    words = ctx["scripts"][proposal["script"]]["words"]
    if type(start) is not int or type(end) is not int or not 0 <= start <= end < len(words):
        raise ValueError("Invalid passage word range")
    if proposal["passage"] != " ".join(words[start:end + 1]):
        raise ValueError("Passage no longer matches script")
    refs = proposal["extracts"]
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs):
        raise ValueError("Invalid extract references")
    for ref in refs:
        extract = next((item for item in ctx["extracts"] if item["id"] == ref), None)
        if (extract is None or extract["script"] != proposal["script"] or
                not any(start <= right and end >= left for left, right in extract["spans"])):
            raise ValueError("Extract does not intersect asset passage")


def proposals(ctx, envelope):
    if (not isinstance(envelope, dict) or envelope.get("inputs") != ctx["inputs"] or
            not isinstance(envelope.get("processed"), list) or
            any(not isinstance(path, str) for path in envelope["processed"]) or
            sorted(envelope.get("processed", [])) != sorted(item["path"] for item in ctx["inputs"])):
        raise ValueError("Agent must process exactly the current input snapshot")
    result = {}
    for path, script in ctx["scripts"].items():
        for comment in script["comments"]:
            key = "comment:" + path + "#" + comment["comment_id"]
            refs = [e["id"] for e in ctx["extracts"] if e["script"] == path and
                    any(comment["start"] <= end and comment["end"] >= start for start, end in e["spans"])]
            proposal = {field: comment[field] for field in ("start", "end", "passage", "brief")}
            proposal.update(script=path, extracts=refs, type="Other", notes="Imported Word comment; review asset type and rationale.")
            result[key] = {"origin": "User", "provenance": comment, "fields": proposal}
    if not isinstance(envelope.get("suggestions"), list):
        raise ValueError("Missing suggestions list (use [] for an analyzed empty slate)")
    for item in envelope["suggestions"]:
        validate_proposal(item, ctx)
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Agent suggestion requires a stable key")
        key = "agent:" + key
        if key in result:
            raise ValueError("Duplicate suggestion key")
        result[key] = {"origin": "AI", "provenance": {"key": item["key"]},
                       "fields": {field: item[field] for field in FIELDS}}
    return result


def new_registry():
    return {"assets": [], "inputs": [], "history": [], "control": {"asset_id_format": "A01-A99"}}


def allocation(registry, incoming):
    """Preflight stable IDs before either a dry run or publication."""
    compact = registry["control"].get("asset_id_format") == "A01-A99"
    existing = registry["assets"]
    known = {row["Source Key"] for row in existing}
    additions = sorted(incoming.keys() - known)
    maximum = max((int(row["ID"][1:] if compact else row["ID"].split("-")[1])
                   for row in existing), default=0)
    if compact and maximum + len(additions) > 99:
        raise ValueError("Asset limit reached: new projects allow at most 99 stable IDs (A01-A99); "
                         "rejected and retired IDs are not reused. Reduce the new proposals.")
    return [(key, f"A{maximum + offset:02d}" if compact else f"ASSET-{maximum + offset:06d}")
            for offset, key in enumerate(additions, 1)]


def rows(registry, *, errors=None):
    result = list(enumerate(registry["assets"]))
    compact = registry["control"].get("asset_id_format") == "A01-A99"
    if compact and (len(result) > 99 or any(not re.fullmatch(r"A(?:0[1-9]|[1-9][0-9])", str(row.get("ID")))
                                          for _, row in result)):
        message = "New registries require at most 99 stable asset IDs in A01-A99 format"
        if errors is None:
            raise ValueError(message)
        errors.append({"asset": None, "message": message})
    ids, keys = set(), set()
    for _, row in result:
        try:
            validate_registry_row(row, ids, keys)
        except (ValueError, TypeError, KeyError) as exc:
            if errors is None:
                raise
            errors.append({"asset": row.get("ID"), "message": str(exc)})
    for key, expected, message in (
            ("identities", {row["Source Key"]: row["ID"] for _, row in result},
             "Registry identities changed or rows were deleted; restore them before reconciliation"),
            ("proposal_hashes", proposal_hashes(registry),
             "Internal proposal metadata changed; restore it before reconciliation")):
        try:
            value = controls(registry).get(key)
            if value is not None and json.loads(value) != expected:
                raise ValueError(message)
        except (ValueError, TypeError) as exc:
            if errors is None:
                raise
            errors.append({"asset": None, "message": str(exc)})
    return {name: name for name in HEADERS}, result


def validate_registry_row(row, ids, keys):
    if set(row) != set(HEADERS):
        raise ValueError("Missing or unknown registry fields")
    if not isinstance(row["ID"], str) or not re.fullmatch(r"(?:A(?:0[1-9]|[1-9][0-9])|ASSET-\d{6,})", row["ID"]):
        raise ValueError("Invalid asset ID")
    key = row["Source Key"]
    if (row["ID"] in ids or not isinstance(key, str) or key in keys or
            not key.startswith(("comment:", "agent:"))):
        raise ValueError("Duplicate ID or invalid/duplicate source key")
    ids.add(row["ID"])
    keys.add(key)
    if row["Review"] not in REVIEWS or row["Active"] not in ("Yes", "No"):
        raise ValueError("Invalid review/activity")
    if row["Origin"] != ("User" if key.startswith("comment:") else "AI"):
        raise ValueError("Invalid asset origin")
    if row["Type"] not in TYPES or not isinstance(row["Brief"], str) or not row["Brief"].strip():
        raise ValueError("Invalid type/brief")
    proposal = json.loads(row["Proposal"])
    provenance = json.loads(row["Provenance"])
    if (not isinstance(proposal, dict) or set(proposal) != {"origin", "provenance", "fields"} or
            proposal["origin"] != row["Origin"] or proposal["provenance"] != provenance or
            not isinstance(proposal["fields"], dict) or set(proposal["fields"]) != set(FIELDS)):
        raise ValueError("Invalid internal proposal/provenance")


def proposal_hashes(registry):
    return {row["ID"]: digest([row["Proposal"], row["Provenance"]]) for row in registry["assets"]}


def field_value(field, value):
    return encoded(value) if field == "extracts" else value


def row_proposal(row):
    value = {field: row[column] for field, column in COLUMNS.items()}
    value["extracts"] = json.loads(value["extracts"])
    return value


def controls(registry):
    return registry["control"]


def set_control(registry, key, value):
    registry["control"][key] = value


INTRO = ("# Asset Registry\n\n"
         "Set Review to Pending Review, Keep, or Reject. Edit Type, Extracts, Passage, Asset, and Why in place.\n"
         "Extracts: use explicit IDs separated by commas, or Full film only. Keep rejected cards and stable IDs.\n"
         "Titles, Source, and Origin are read-only. Save edits before running helpers; machine evidence lives in Asset Registry.json (do not edit).\n\n")
HUMAN_FIELDS = ("Review", "Type", "Extracts", "Passage", "Brief", "Notes")


def prose(value):
    # Escape structural Markdown, so literal labels/headings cannot become delimiters.
    return re.sub(r"([\\`*<>#])", r"\\\1", value)


def unprose(value):
    decoded = re.sub(r"\\([\\`*<>#])", r"\1", value)
    if prose(decoded) != value:
        raise ValueError("Ambiguous prose syntax; escape literal \\, `, *, <, >, or # with a backslash")
    return decoded


def title(row):
    brief = normalized(json.loads(row["Proposal"])["fields"]["brief"])
    first = re.split(r"(?<=[.!?])\s", brief, maxsplit=1)[0]
    first = re.sub(r"^(?:At [^,]+, show |If [^,]+, )", "", first)
    first = re.split(r": |, (?:with |subject to |preferably |held |including )", first, maxsplit=1)[0]
    first = first[:1].upper() + first[1:]
    words = first.split()
    return " ".join(words[:12]) + ("..." if len(words) > 12 else "")


def render(registry):
    text = INTRO
    for row in registry["assets"]:
        refs = json.loads(row["Extracts"])
        text += (f"## {row['ID']} | {prose(title(row))}\n"
                 f"**Review:** {row['Review']}\n"
                 f"**Type:** {row['Type']} | **Origin:** {row['Origin']}\n"
                 f"**Source:** {prose(Path(row['Script']).stem)} | **Extracts:** "
                 + (", ".join(refs) if refs else "Full film only") + "\n"
                 "**Passage**\n"
                 + "\n".join("> " + prose(line) for line in row["Passage"].split("\n")) + "\n\n"
                  "**Asset:** " + prose(row["Brief"]) + "\n"
                  "**Why:** " + prose(row["Notes"]) + "\n\n")
    return text


def render_evidence(registry):
    metadata = {row["ID"]: {key: value for key, value in row.items()
                            if (key not in HUMAN_FIELDS and key != "ID") or key == "Extracts"}
                for row in registry["assets"]}
    evidence = {key: value for key, value in registry.items() if key != "assets"}
    evidence["asset_metadata"] = metadata
    evidence["evidence_sha256"] = digest(evidence)
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate metadata key: {key}")
        result[key] = value
    return result


def parse(text, evidence_text, *, errors=None):
    if not text.startswith(INTRO):
        raise ValueError("Registry header changed; only current compact Markdown/JSON registries are supported")
    body = text[len(INTRO):]
    final_paragraph = body.rsplit("\n\n", 1)[-1]
    if (body.endswith("\n") and not body.endswith("\n\n") and
            (final_paragraph.startswith("**Why:** ") or "\n**Why:** " in final_paragraph)):
        body += "\n"
    evidence = json.loads(evidence_text, object_pairs_hook=unique_object)
    if (not isinstance(evidence, dict) or
            not {"inputs", "history", "control", "asset_metadata", "evidence_sha256"} <= evidence.keys() or
            evidence.keys() - {"inputs", "history", "control", "migration", "asset_metadata", "evidence_sha256"} or
            not isinstance(evidence["inputs"], list) or not isinstance(evidence["history"], list) or
            not isinstance(evidence["control"], dict) or not isinstance(evidence["asset_metadata"], dict)):
        raise ValueError("Invalid machine evidence")
    checksum = evidence.pop("evidence_sha256")
    if checksum != digest(evidence):
        raise ValueError("Internal machine evidence changed; restore it before reconciliation")
    metadata = evidence.pop("asset_metadata")
    # Optional migration evidence is opaque history, never interpreted or applied.
    registry = dict(evidence, assets=[])
    pattern = (r"## (A(?:0[1-9]|[1-9][0-9])|ASSET-\d{6,}) \| ([^\n]+)\n"
               r"\*\*Review:\*\* ([^\n]+)\n"
               r"\*\*Type:\*\* ([^\n]+?) \| \*\*Origin:\*\* ([^\n]+)\n"
               r"\*\*Source:\*\* ([^\n]+?) \| \*\*Extracts:\*\* ([^\n]+)\n"
               r"\*\*Passage\*\*\n((?:> [^\n]*\n)+)\n"
               r"\*\*Asset:\*\* (.*?)\n\*\*Why:\*\* (.*?)\n\n(?=## (?:A[0-9]{2} \||ASSET-)|\Z)")
    while body:
        card = re.match(pattern, body, re.S)
        if not card:
            raise ValueError("Malformed/duplicate/out-of-order card fields or unknown text")
        identity, heading, review, kind, origin, source, refs, passage, brief, notes = card.groups()
        if identity not in metadata or any(row["ID"] == identity for row in registry["assets"]):
            raise ValueError("Registry identities changed or duplicated")
        internal = metadata[identity]
        if not isinstance(internal, dict) or set(internal) != (set(HEADERS) - set(HUMAN_FIELDS) - {"ID"}) | {"Extracts"}:
            raise ValueError("Invalid internal asset metadata")
        references = [] if refs == "Full film only" else refs.split(", ")
        row = dict(internal, ID=identity, Review=review, Type=kind,
                   Extracts=internal["Extracts"] if json.loads(internal["Extracts"]) == references else encoded(references),
                   Passage="\n".join(unprose(line[2:]) for line in passage[:-1].split("\n")),
                   Brief=unprose(brief), Notes=unprose(notes))
        if (heading != prose(title(row)) or origin != row["Origin"] or
                source != prose(Path(row["Script"]).stem)):
            raise ValueError("Read-only title, Source, or Origin changed")
        if refs != "Full film only" and not re.fullmatch(r"EXTRACT-\d+(?:, EXTRACT-\d+)*", refs):
            raise ValueError("Extracts must be explicit EXTRACT IDs or Full film only")
        registry["assets"].append(row)
        body = body[card.end():]
    if set(metadata) != {row["ID"] for row in registry["assets"]}:
        raise ValueError("Registry identities changed or rows were deleted")
    rows(registry, errors=errors)
    return registry


def load(project, *, errors=None):
    path = Path(project) / STAGE / "Asset Registry.md"
    sidecar = path.with_suffix(".json")
    if (path.parent / ".assets-publication.json").exists():
        raise ValueError("Interrupted registry pair publication; inspect .assets-publication.json and retained recovery files before retrying")
    for target in (path, sidecar):
        if target.is_symlink() or not target.resolve().is_relative_to(Path(project).resolve()):
            raise ValueError("Registry escapes project or is symlinked")
    if not path.exists():
        if sidecar.exists():
            raise ValueError("Missing Asset Registry.md for existing Asset Registry.json")
        return new_registry()
    original = snapshot(project)
    text = path.read_text(encoding="utf-8")
    if not sidecar.is_file():
        raise ValueError("Missing Asset Registry.json")
    result = parse(text, sidecar.read_text(encoding="utf-8"), errors=errors)
    if (path.parent / ".assets-publication.json").exists() or snapshot(project) != original:
        raise ValueError("Registry pair changed while loading; retry after publication finishes")
    return result


def snapshot(project):
    path = Path(project) / STAGE / "Asset Registry.md"
    return [p.read_bytes() if p.exists() else None for p in (path, path.with_suffix(".json"))]


def save(registry, project, *, expected_snapshot=None):
    path = Path(project) / STAGE / "Asset Registry.md"
    targets = (path, path.with_suffix(".json"))
    guard = path.parent / ".assets-publication.json"
    for target in (*targets, guard):
        if target.is_symlink() or not target.resolve().is_relative_to(Path(project).resolve()):
            raise ValueError("Stage escapes project")
    path.parent.mkdir(parents=True, exist_ok=True)
    if guard.exists():
        raise ValueError("Interrupted registry pair publication; inspect .assets-publication.json")
    originals = [p.read_bytes() if p.exists() else None for p in targets]
    if expected_snapshot is not None and originals != expected_snapshot:
        raise ValueError("Registry changed before publication; refusing to overwrite edits")
    content = [render(registry).encode("utf-8"), render_evidence(registry).encode("utf-8")]
    if originals == content:
        return
    temporary, backups, published = [], [], []
    guarded = False
    try:
        for data, old in zip(content, originals):
            descriptor, name = tempfile.mkstemp(prefix=".assets-", dir=path.parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            temporary.append(Path(name))
            if old is None:
                backups.append(None)
            else:
                descriptor, name = tempfile.mkstemp(prefix=".assets-recovery-", dir=path.parent)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(old)
                backups.append(Path(name))
        if [p.read_bytes() if p.exists() else None for p in targets] != originals:
            raise ValueError("Registry changed before publication; refusing to overwrite edits")
        # Readers refuse this guard, including after a crash between the two replacements.
        with guard.open("x", encoding="utf-8") as stream:
            guarded = True
            json.dump({"targets": [p.name for p in targets],
                       "previous": [p.name if p else None for p in backups]}, stream)
        try:
            for i, (temp, target) in enumerate(zip(temporary, targets)):
                if originals[i] != content[i]:
                    os.replace(temp, target)
                    published.append(i)
        except BaseException:
            for i in reversed(published):
                if backups[i] is None:
                    targets[i].unlink()
                else:
                    os.replace(backups[i], targets[i])
            guard.unlink()
            guarded = False
            raise
        guard.unlink()
        guarded = False
    finally:
        for temp in temporary + ([] if guarded else backups):
            if temp is not None and temp.exists():
                temp.unlink()


def reconcile(project, envelope):
    """Caller serializes mutations and performs shared invalidation before publication."""
    ctx = context(project)
    incoming = proposals(ctx, envelope)
    original = snapshot(project)
    registry = load(project)
    _, existing = rows(registry)
    before = encoded(registry)
    old_inputs = registry["inputs"]
    inputs_changed = old_inputs != ctx["inputs"]
    for key, identity in allocation(registry, incoming):
        item = incoming[key]
        values = {"ID": identity, "Origin": item["origin"], "Source Key": key,
                  "Review": "Pending Review", "Active": "Yes", "Change": "New proposal",
                  "Provenance": encoded(item["provenance"]), "Proposal": encoded(item)}
        values.update({COLUMNS[field]: field_value(field, value) for field, value in item["fields"].items()})
        registry["assets"].append(values)
    for number, row in existing:
        item = incoming.get(row["Source Key"])
        previous = json.loads(row["Proposal"])
        active = "Yes" if item else "No"
        changed = item is not None and item != previous
        if inputs_changed or changed or active != row["Active"]:
            registry["history"].append([datetime.now(timezone.utc).isoformat(), row["ID"],
                                  "Inputs/proposal/applicability changed", encoded(row)])
            for name, value in (("Review", "Pending Review"), ("Active", active),
                                ("Change", "Review changed inputs/proposal" if item else "Removed from current slate; reject")):
                row[name] = value
        if changed:
            # Three-way merge: never overwrite a human modification.
            for field, value in item["fields"].items():
                column = COLUMNS[field]
                if row[column] == field_value(field, previous["fields"][field]):
                    row[column] = field_value(field, value)
            row["Proposal"] = encoded(item)
            row["Provenance"] = encoded(item["provenance"])
    registry["inputs"] = ctx["inputs"]
    set_control(registry, "schema", "studio.asset-registry.v1")
    set_control(registry, "processed", encoded(envelope["processed"]))
    set_control(registry, "identities", encoded({
        row["Source Key"]: row["ID"] for row in registry["assets"]}))
    set_control(registry, "proposal_hashes", encoded(proposal_hashes(registry)))
    if before != encoded(registry):
        save(registry, project, expected_snapshot=original)
    return coverage(registry, ctx)


def slate_digest(registry, *, entries=None):
    if entries is None:
        _, entries = rows(registry)
    return digest({"assets": [row for _, row in entries],
                   "inputs": registry["inputs"], "processed": controls(registry).get("processed")})


def coverage(registry, ctx, *, reviews=("Keep",), entries=None):
    if entries is None:
        _, entries = rows(registry)
    covered = {path: set() for path in ctx["scripts"]}
    extract_covered = {item["id"]: set() for item in ctx["extracts"]}
    for _, row in entries:
        if row["Review"] not in reviews or row["Active"] != "Yes":
            continue
        proposal = row_proposal(row)
        validate_proposal(proposal, ctx)
        span = set(range(proposal["start"], proposal["end"] + 1))
        covered[proposal["script"]].update(span)
        for ref in proposal["extracts"]:
            extract_covered[ref].update(span)
    return {"advisory_only": True, "full_script_target": 0.25, "extract_target": 1.0,
            "scripts": {path: len(covered[path]) / len(script["words"]) for path, script in ctx["scripts"].items()},
            "extracts": {item["id"]: len(extract_covered[item["id"]] &
                {i for start, end in item["spans"] for i in range(start, end + 1)}) /
                sum(end - start + 1 for start, end in item["spans"]) for item in ctx["extracts"]}}


def input_status(registry, ctx):
    control = controls(registry)
    try:
        processed = json.loads(control.get("processed", "[]"))
        processed_valid = isinstance(processed, list) and all(isinstance(path, str) for path in processed)
    except (ValueError, TypeError):
        processed, processed_valid = [], False
    expected = {"comment:" + path + "#" + item["comment_id"]
                for path, script in ctx["scripts"].items() for item in script["comments"]}
    active = [row["Source Key"] for row in registry["assets"]
              if row["Origin"] == "User" and row["Active"] == "Yes"]
    imported = proposals(ctx, {"inputs": ctx["inputs"],
                             "processed": [e["path"] for e in ctx["inputs"]], "suggestions": []})
    stale = [row["ID"] for row in registry["assets"] if row["Origin"] == "User" and
             row["Active"] == "Yes" and json.loads(row["Proposal"]) != imported.get(row["Source Key"])]
    return {"inputs_current": registry["inputs"] == ctx["inputs"],
            "processed_valid": processed_valid,
            "all_inputs_processed": processed_valid and sorted(processed) == sorted(e["path"] for e in ctx["inputs"]),
            "comments": {"expected": len(expected), "active_imported": len(active),
                         "missing": sorted(expected - set(active)), "unexpected": sorted(set(active) - expected),
                         "stale_proposals": stale,
                         "complete": expected == set(active) and len(active) == len(expected) and not stale}}


def registry_issues(registry, ctx, *, require_confirmation=True, entries=None):
    """Shared completion checks; inspection consumes every issue, check only the first."""
    control = controls(registry)
    if control.get("schema") != "studio.asset-registry.v1":
        yield {"kind": "error", "asset": None, "message": "Invalid registry schema"}
    if "identities" not in control:
        yield {"kind": "error", "asset": None, "message": "Missing registry identity ledger"}
    status = input_status(registry, ctx)
    if not status["inputs_current"] or not status["all_inputs_processed"]:
        yield {"kind": "error", "asset": None, "message": "Not all current inputs have been processed; reconcile first"}
    if entries is None:
        _, entries = rows(registry)
    comments = status["comments"]
    if comments["missing"] or comments["unexpected"] or comments["active_imported"] != comments["expected"]:
        yield {"kind": "error", "asset": None, "message": "Current Word comments have not all been imported exactly once"}
    for _, row in entries:
        if row["Review"] == "Pending Review":
            yield {"kind": "pending", "asset": row["ID"], "message": f"Pending Review: {row['ID']}"}
        try:
            validate_current_row(row, ctx, comments["stale_proposals"])
        except (ValueError, TypeError, KeyError) as exc:
            yield {"kind": "error", "asset": row["ID"], "message": str(exc)}
    if not any(row["Review"] == "Keep" for _, row in entries) and require_confirmation:
        try:
            confirmation = json.loads(control.get("empty_confirmation", "{}"))
            if not isinstance(confirmation, dict):
                raise ValueError("Invalid empty-slate confirmation")
        except (ValueError, TypeError) as exc:
            yield {"kind": "error", "asset": None, "message": str(exc)}
            return
        current_slate = slate_digest(registry, entries=entries)
        if (confirmation.get("slate") != current_slate or not confirmation.get("by") or
                not confirmation.get("reason") or not confirmation.get("utc")):
            yield {"kind": "confirmation", "asset": None,
                   "message": "Empty/all-rejected slate requires explicit recorded human confirmation"}


def validate_current_row(row, ctx, stale_comments):
    if row["Active"] == "No":
        if row["Review"] != "Reject":
            raise ValueError("Inactive assets must be rejected")
    else:
        validate_proposal(row_proposal(row), ctx)
        if row["ID"] in stale_comments:
            raise ValueError("Word comment proposal is stale; reconcile first")


def check(project, *, require_confirmation=True):
    ctx = context(project)
    if not (Path(project) / STAGE / "Asset Registry.md").is_file():
        raise ValueError("Missing Asset Registry.md")
    registry = load(project)
    for issue in registry_issues(registry, ctx, require_confirmation=require_confirmation):
        raise ValueError(issue["message"])
    return coverage(registry, ctx)


def inspect(project):
    """Read-only diagnostics, including pending rows that are not yet approved."""
    report = {"completion_ready": False, "errors": [], "issues": [], "counts": None,
              "input_status": None, "coverage": None}
    try:
        ctx = context(project)
        report["inputs"] = ctx["inputs"]
        registry = load(project, errors=report["errors"])
        entries = list(enumerate(registry["assets"]))
        report["counts"] = {"total": len(entries), **{
            label: {value: sum(row[field] == value for _, row in entries) for value in values}
            for label, field, values in (("review", "Review", REVIEWS), ("origin", "Origin", ("User", "AI")),
                                          ("activity", "Active", ("Yes", "No")))}}
        if not (Path(project) / STAGE / "Asset Registry.md").is_file():
            report["errors"].append({"asset": None, "message": "Missing Asset Registry.md"})
        report["input_status"] = input_status(registry, ctx)
        for issue in registry_issues(registry, ctx, entries=entries):
            report["errors" if issue["kind"] == "error" else "issues"].append(issue)
        valid = []
        for entry in entries:
            try:
                validate_proposal(row_proposal(entry[1]), ctx)
                valid.append(entry)
            except (ValueError, TypeError, KeyError):
                pass
        report["coverage"] = {
            "proposed_is_approved": False, "excludes_invalid_rows": True,
            "proposed": coverage(registry, ctx, reviews=("Pending Review", "Keep"), entries=valid),
            "kept": coverage(registry, ctx, entries=valid)}
    except (ValueError, TypeError, KeyError, AttributeError, OSError, BadZipFile, ET.ParseError) as exc:
        report["errors"].append({"asset": None, "message": str(exc)})
    report["completion_ready"] = not report["errors"] and not report["issues"]
    return report


def indexed_context(ctx):
    lines = ["Word indices are zero-based and inclusive."]
    for path, script in ctx["scripts"].items():
        lines.extend(("", f"Script: {path}", " ".join(f"[{i}] {word}" for i, word in enumerate(script["words"]))))
        for comment in script["comments"]:
            lines.append(f"Comment {comment['comment_id']} [{comment['start']}-{comment['end']}]: {comment['brief']}")
    for extract in ctx["extracts"]:
        lines.append(f"\n{extract['id']}: {extract['title']} ({extract['script']})")
        words = ctx["scripts"][extract["script"]]["words"]
        for start, end in extract["spans"]:
            lines.append(f"[{start}-{end}] " + " ".join(words[start:end + 1]))
    return "\n".join(lines)


def dry_run(project, envelope):
    ctx = context(project)
    incoming = proposals(ctx, envelope)
    registry = load(project)  # Same pre-publication evidence validation, without a mutation guard.
    allocation(registry, incoming)
    return {"valid_snapshot": True, "inputs": ctx["inputs"], "processed": envelope["processed"],
            "counts": {"total": len(incoming), **{origin: sum(item["origin"] == origin for item in incoming.values())
                                                  for origin in ("User", "AI")}}, "published": False}


def confirm_empty(project, by, reason):
    if not by.strip() or not reason.strip():
        raise ValueError("Human identity and confirmed reason required")
    original = snapshot(project)
    check(project, require_confirmation=False)
    registry = load(project)
    if any(row["Review"] == "Keep" for _, row in rows(registry)[1]):
        raise ValueError("Slate is not empty/all rejected")
    set_control(registry, "empty_confirmation", encoded({"by": by, "reason": reason,
                "utc": datetime.now(timezone.utc).isoformat(), "slate": slate_digest(registry)}))
    save(registry, project, expected_snapshot=original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("context", "reconcile", "inspect", "check", "confirm-empty"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--suggestions", type=Path)
    parser.add_argument("--by")
    parser.add_argument("--reason")
    parser.add_argument("--indexed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for supplied, allowed, flag in ((args.indexed, "context", "--indexed"),
                                    (args.dry_run, "reconcile", "--dry-run"),
                                    (args.suggestions is not None, "reconcile", "--suggestions"),
                                    (args.by is not None, "confirm-empty", "--by"),
                                    (args.reason is not None, "confirm-empty", "--reason")):
        if supplied and args.action != allowed:
            parser.error(f"{flag} is only supported for {allowed}")
    if args.action == "reconcile" and not args.suggestions:
        parser.error("reconcile requires --suggestions")
    project = args.project_root.resolve(strict=True)
    if args.action == "context":
        ctx = context(project)
        if (project / STAGE / "Asset Registry.md").exists():
            ctx["registry"] = [row for _, row in rows(load(project))[1]]
        if args.indexed:
            sys.stdout.reconfigure(encoding="utf-8")
            print(indexed_context(ctx))
        else:
            print(json.dumps(ctx, ensure_ascii=False, indent=2))
    elif args.action == "inspect":
        report = inspect(project)
        print(json.dumps(report, indent=2))
        if report["errors"]:
            raise SystemExit(1)
    elif args.action == "reconcile" and args.dry_run:
        envelope = json.loads(args.suggestions.read_text(encoding="utf-8-sig"))
        print(json.dumps(dry_run(project, envelope), indent=2))
    elif args.action == "check":
        print(json.dumps(check(project), indent=2))
    else:
        with completion.mutation_guard(project):
            outcomes = audit(project, ROOT, "1.4", prerequisites_only=True)
            if any(item["status"] != "current" for item in outcomes.values()):
                raise ValueError("Prerequisites are not current; use shared validation")
            from scripts.stage_invalidation import invalidate
            if args.action == "reconcile":
                envelope = json.loads(args.suggestions.read_text(encoding="utf-8-sig"))
                incoming = proposals(context(project), envelope)  # Reject bad/stale proposals before invalidating.
                registry = load(project)  # Reject missing/tampered evidence before invalidating.
                allocation(registry, incoming)  # Reject exhausted IDs before invalidating completed work.
                invalidate(project, ROOT, "1.4", "Asset registry reconciliation")
                print(json.dumps(reconcile(project, envelope), indent=2))
            else:
                if not args.by or not args.reason:
                    parser.error("confirm-empty requires --by and --reason after human confirmation")
                check(project, require_confirmation=False)
                invalidate(project, ROOT, "1.4", "Explicit human empty-slate confirmation")
                confirm_empty(project, args.by, args.reason)


if __name__ == "__main__":
    main()
