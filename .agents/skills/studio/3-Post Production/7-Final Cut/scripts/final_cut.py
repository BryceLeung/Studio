#!/usr/bin/env python3
"""Finalize takes, lift subtitles, and stage approved animation cutaways."""
import argparse
from copy import deepcopy
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "studio/fingerprints.py").is_file())
sys.path.insert(0, str(ROOT))

from scripts.stage_invalidation import _default_log, invalidate, prepare_regeneration
from scripts.validation import load_stages, require_prerequisites, StageValidation
from studio.first_cut_exports import export_stem, exports
from studio import final_cut_animations as animations
from studio.fingerprints import sha256
from studio import score_versions

STEP = "3.7"
FIRST_CUT = Path("3-Post Production/6-First Cut")
STAGE = Path("3-Post Production/7-Final Cut")
SCORES = Path(score_versions.STAGE)
ANCHORS = {"audio", "video", "clip", "title", "mc-clip", "ref-clip", "sync-clip", "asset-clip", "audition", "spine", "caption"}
MARKERS = {"marker", "chapter-marker", "rating", "keyword", "analysis-marker"}


def timestamp(value):
    return f"{value}s"


def timing(node, attr, default="0s"):
    value = node.get(attr, default)
    if value is None or not value.endswith("s"):
        raise ValueError(f"Missing or invalid {attr} on {node.tag}: {value}")
    return Fraction(value[:-1])


def insert_anchor(clip, node):
    index = next((i for i, child in enumerate(clip)
                  if child.tag in MARKERS or child.tag == "metadata"
                  or isinstance(child.tag, str) and child.tag.startswith("filter-")), len(clip))
    clip.insert(index, node)


def finalize_take(item, resources, resource_id):
    wrapper = item
    if item.tag == "clip":
        primary = [child for child in item if child.tag in ANCHORS and child.get("lane", "0") == "0"]
        if len(primary) != 1 or primary[0].tag not in {"ref-clip", "audition"}:
            return item, None, None, []
        wrapper = primary[0]
    pick = wrapper
    if wrapper.tag == "audition":
        if not len(wrapper):
            raise ValueError("Empty audition in edited First Cut")
        pick = wrapper[0]  # FCP exports the active item first.
        for key in ("offset", "lane"):
            if key in wrapper.attrib:
                pick.set(key, wrapper.attrib[key])
        if item is not wrapper:
            index = list(item).index(wrapper)
            item.remove(wrapper)
            item.insert(index, pick)
    if pick.tag != "ref-clip":
        if wrapper.tag == "audition":
            raise ValueError("Expected a Studio take compound as the active audition pick")
        return item, None, None, []

    media = resources.get(pick.get("ref"))
    spine = None if media is None else media.find("sequence/spine")
    if spine is None or len(spine) != 1 or spine[0].tag != "mc-clip":
        raise ValueError(f"Unsupported compound {pick.get('name', pick.get('ref'))}: expected one multicam clip")
    multicam = spine[0]
    clip = pick if item is wrapper else item
    titles = multicam.findall("title")
    if titles and any(node.find("timeMap") is not None or node.find("conform-rate") is not None
                      for node in (clip, pick, multicam)):
        raise ValueError("Cannot safely lift subtitles through retiming or rate conform")
    # A connected title's offset is expressed in its parent's source time.
    shift = timing(multicam, "offset") - timing(multicam, "start")
    low_bound = timing(pick, "start")
    high_bound = low_bound + timing(pick, "duration", None)
    if clip is not pick:
        shift += timing(pick, "offset") - timing(pick, "start")
        low_bound = max(timing(clip, "start"), timing(pick, "offset"))
        high_bound = min(timing(clip, "start") + timing(clip, "duration", None),
                         timing(pick, "offset") + timing(pick, "duration", None))
    top = max((0, *(int(node.get("lane", "0")) for node in clip)))
    for node in titles:
        if any(child.tag in ANCHORS for child in node):
            raise ValueError("Cannot safely lift a subtitle with connected clips of its own")
        offset = timing(node, "offset") + shift
        low = max(offset, low_bound)
        high = min(offset + timing(node, "duration", None), high_bound)
        if low >= high:
            continue
        lifted = deepcopy(node)
        lifted.set("offset", timestamp(low))
        lifted.set("start", timestamp(timing(node, "start") + low - offset))
        lifted.set("duration", timestamp(high - low))
        lifted.set("lane", str(top + max(1, int(node.get("lane", "1")))))
        if pick.get("enabled") == "0" or multicam.get("enabled") == "0":
            lifted.set("enabled", "0")
        styles = {}
        for definition in lifted.iter("text-style-def"):
            new_id = resource_id()
            styles[definition.attrib["id"]] = new_id
            definition.set("id", new_id)
        for style in lifted.iter("text-style"):
            if style.get("ref") in styles:
                style.set("ref", styles[style.get("ref")])
        insert_anchor(clip, lifted)
    return clip, media, multicam, titles


def convert(xml, project, source, output, document, extract_number, entries=()):
    # Preserve comment nodes (some FCP filters store settings in them) and title whitespace.
    root = ET.fromstring(xml, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    projects = list(root.iter("project"))
    resources = root.find("resources")
    if root.tag != "fcpxml" or len(projects) != 1 or resources is None:
        raise ValueError(f"Export exactly one project as FCPXML: {source.name}")
    edit = projects[0]
    sequence = edit.find("sequence")
    spine = edit.find("sequence/spine")
    if spine is None or not len(spine):
        raise ValueError(f"Missing primary storyline: {source.name}")
    by_id = {node.get("id"): node for node in resources if "id" in node.attrib}
    frame = timing(by_id[sequence.attrib["format"]], "frameDuration", None)
    if frame <= 0:
        raise ValueError("Project frame duration must be positive")
    ids = {node.get("id") for node in root.iter() if "id" in node.attrib}
    serial = 0

    def resource_id():
        nonlocal serial
        while True:
            serial += 1
            candidate = f"r{serial}"
            if candidate not in ids:
                ids.add(candidate)
                return candidate

    for media in [*resources.findall("asset"), *resources.findall("asset/media-rep"), *resources.findall("effect")]:
        src = media.get("src")
        if src and not urlsplit(src).scheme and not Path(unquote(src)).is_absolute():
            target = (source.parent / unquote(src)).resolve()
            media.set("src", quote(Path(os.path.relpath(target, output.parent)).as_posix(), safe="/."))

    if entries:
        options = root.find("import-options")
        if options is None:
            options = ET.Element("import-options")
            root.insert(0, options)
        copy = options.find("option[@key='copy assets']")
        if copy is None:
            copy = ET.SubElement(options, "option", {"key": "copy assets"})
        copy.set("value", "0")
    intake = animations.AnimationIntake(entries, project, output, resources, resource_id, frame, timestamp)
    selected, notes, placements = [], [], []
    identified = {}
    compounds = {}
    for index, item in enumerate(list(spine)):
        clip, media, multicam, titles = finalize_take(item, by_id, resource_id)
        spine[index] = clip
        if multicam is None and clip.tag != "mc-clip":
            continue
        if media is not None:
            compounds[media] = (multicam, titles)
        angle_clip = multicam if multicam is not None else clip
        name = (media.get("name") if media is not None else None) or angle_clip.get("name", "")
        labels = [name, angle_clip.get("name", ""), clip.get("name", ""),
                  *(node.get("value", "") for node in angle_clip.findall("marker"))]
        label = next((match for value in labels if (match := re.match(r"S(\d+)\b", value))), None)
        sentence = int(label[1]) if label else None
        if document is not None and sentence is not None and 1 <= sentence <= len(document["script_groupings"]):
            identified.setdefault(sentence, []).append(clip)
        elif document is not None:
            notes.append({"clip": name, "reason": "no_studio_sentence_label"})
        selected.append({"sentence": sentence, "clip": name, "offset": clip.get("offset", "0s"),
                         "source_in": clip.get("start", "0s"), "duration": clip.get("duration"),
                         "angles": [node.attrib for node in angle_clip.findall("mc-source")]})

    if any(node.tag == "audition" for node in spine.iter()):
        raise ValueError("Unsupported nested audition in edited timeline")
    for media, (multicam, titles) in compounds.items():
        if titles:
            # Do not let FCP reuse the original library compound with embedded titles.
            media.attrib.pop("uid", None)
            media.attrib.pop("modDate", None)
            for title in titles:
                multicam.remove(title)
    ranges = [{"sentence": n, "candidates": [{"script": document["script_groupings"][n - 1]}]} for n in identified]
    anchors, warnings = intake.anchors(document, ranges, extract_number) if entries else ({}, [])
    notes.extend(warnings)
    for sentence, asset_ids in anchors.items():
        if len(identified[sentence]) != 1:
            notes.append({"sentence": sentence, "asset_ids": asset_ids, "reason": "ambiguous_repeated_sentence"})
            continue
        clip = identified[sentence][0]
        start = timing(clip, "start")
        end = start + timing(clip, "duration", None)
        title_offsets = [timing(node, "offset") for node in clip.findall("title") if node.get("enabled") != "0"
                         and start <= timing(node, "offset") < end]
        if not title_offsets:
            notes.append({"sentence": sentence, "asset_ids": asset_ids, "reason": "no_subtitle_timing"})
            continue
        take = {"sentence": sentence, "take_key": clip.get("name", str(sentence)), "cut_in": start,
                 "word_in": min(title_offsets)}
        placed, warnings = intake.place(clip, take, asset_ids, lambda value, unit: round(value / unit) * unit,
                                          timing(clip, "offset"))
        placements.extend(placed)
        notes.extend(warnings)
    intake.attach_cutaways(sequence)

    # Follow resource references from the edit rather than counting references in discarded takes.
    used = set()
    pending = [node.get("ref") for section in root if section is not resources for node in section.iter()]
    while pending:
        ref = pending.pop()
        if ref in by_id and ref not in used:
            used.add(ref)
            pending.extend(node.get("ref") for node in by_id[ref].iter())
    for media in resources.findall("media"):
        if media.find("sequence") is not None and media.get("id") not in used:
            resources.remove(media)
    edit.set("name", f"{edit.get('name', export_stem(source))} - Final Cut")
    for key in ("uid", "modDate"):
        edit.attrib.pop(key, None)
    library = root.find("library")
    if library is None:
        library = ET.SubElement(root, "library")
        for node in list(root):
            if node.tag == "event":
                root.remove(node)
                library.append(node)
        if edit in root:
            root.remove(edit)
            ET.SubElement(library, "event", name="Final Cut").append(edit)
    library.attrib.pop("location", None)
    for event in library.findall("event"):
        if edit in event:
            event.attrib.pop("uid", None)
            event.set("name", f"{export_stem(source)} - Final Cut")
    intake.browser(library)
    animations.verify_sources(root, project, output)
    payload = b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="utf-8") + b"\n"
    return payload, {"selected": selected, "subtitle_titles": len(list(spine.iter("title"))),
                     "placements": placements, "notes": notes}


def contexts(guard):
    sources = {key: (path, None) for key, (_, path) in
               score_versions.latest_scores(guard.confined(SCORES)).items()}
    for directory in (FIRST_CUT / "Extracts", FIRST_CUT / "Extracts/Portrait"):
        path = guard.confined(directory / "Extract Mappings.json")
        if path.exists():
            for row in json.loads(path.read_text(encoding="utf-8-sig"))["projects"]:
                key = re.sub(r'[<>:"/\\|?*]', "-", row["script"]).casefold()
                if key in sources:
                    raise ValueError(f"Ambiguous First Cut project stem: {row['script']}")
                source_key = Path(row["source_script"]).stem.casefold()
                score, source_extract = sources[source_key]
                if source_extract is not None:
                    raise ValueError(f"Extract must map to a full script: {row['source_script']}")
                sources[key] = score, row["extract_number"]
    return sources


def run(project, ffprobe="ffprobe", regenerate=False):
    project = project.resolve(strict=True)
    require_prerequisites(project, ROOT, STEP)
    guard = StageValidation(project, ROOT, {})
    directory = guard.confined(STAGE)
    approved = guard.confined(FIRST_CUT / "APPROVED")
    directory.mkdir(parents=True, exist_ok=True)
    _default_log(directory, "Final Cut started", [f"Edited project exports: {approved.relative_to(project)}"])
    try:
        entries, diagnostics = animations.load(project, ROOT, ffprobe=ffprobe)
        lookup = contexts(guard)
        planned, projects = {}, []
        rule = load_stages(ROOT)[STEP]["inputs"][0]
        for key, source in exports(project, rule).items():
            if key not in lookup:
                raise ValueError(f"No current script or extract mapping for {export_stem(source)}")
            score, extract_number = lookup[key]
            scores = json.loads(guard.confined(score).read_text(encoding="utf-8-sig"))
            mapping = guard.confined(scores["source_mapping"]["path"])
            document = json.loads(mapping.read_text(encoding="utf-8-sig"))
            if document.get("schema_version") == 2:
                document = {"script": document["sources"]["script"],
                            "script_groupings": document["groupings"]["script"]}
            output = directory / (source.name if source.parent == approved else export_stem(source) + ".FCPXML")
            source_xml = source.read_bytes()
            planned[output], report = convert(source_xml, project, source, output, document, extract_number, entries)
            projects.append(dict(report, input=source.relative_to(project).as_posix(), output=output.name,
                                 input_sha256=sha256(source, content=source_xml),
                                 output_sha256=sha256(output, content=planned[output])))
        if not projects:
            raise ValueError("No approved edited First Cut FCPXML projects")
        report = {"projects": projects, "diagnostics": diagnostics,
                  "browser": [{key: item[key] for key in ("asset_id", "path", "sha256")} for item in entries]}
        planned[directory / "REVIEW.json"] = (json.dumps(report, indent=2) + "\n").encode()
        if any(path.is_symlink() for path in planned):
            raise ValueError("Final Cut output path is a symlink")
        changed = {path: data for path, data in planned.items() if not path.exists() or path.read_bytes() != data}
        replacing = [path for path in changed if path.exists()]
        if replacing and not regenerate:
            raise ValueError(f"Existing Final Cut differs; inspect before replacing: {replacing[0].name}")
        if replacing:
            prepare_regeneration(project, ROOT, STEP, replacing, "Explicit Final Cut regeneration.")
        elif changed:
            invalidate(project, ROOT, STEP, "Final Cut projects generated; approved renders required.")
        for path, data in changed.items():
            with path.open("xb") as stream:
                stream.write(data)
        guard.confined(STAGE / "APPROVED").mkdir(exist_ok=True)
        _default_log(directory, "Final Cut ready for review", [
            f"Projects: {', '.join(item['output'] for item in projects)}; see REVIEW.json for placements and diagnostics.",
            "Review in Final Cut Pro and render each project into APPROVED with the same name stem."])
        return report
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError, ET.ParseError) as error:
        invalidate(project, ROOT, STEP, f"Final Cut failed: {error}")
        _default_log(directory, "Final Cut failed", [str(error)])
        raise


def preview(project, stem):
    """Preview the presenter edit only without invoking the stage transition."""
    project = project.resolve(strict=True)
    rule = load_stages(ROOT)[STEP]["inputs"][0]
    source = exports(project, rule).get(stem.casefold())
    if source is None:
        raise ValueError(f"No approved First Cut export with stem: {stem}")
    scratch = project / "3-Post Production/0-Tmp"
    if scratch.is_symlink() or scratch.parent.is_symlink():
        raise ValueError(f"Preview directory must not be a symlink: {scratch}")
    output = scratch / f"{export_stem(source)} - Final Cut Preview.fcpxml"
    payload, report = convert(source.read_bytes(), project, source, output, None, None)
    scratch.mkdir(exist_ok=True)
    if output.is_symlink():
        raise ValueError(f"Preview output must not be a symlink: {output}")
    if not output.exists() or output.read_bytes() != payload:
        with output.open("xb") as stream:
            stream.write(payload)
    return {"output": str(output), "selected_takes": len(report["selected"]),
            "subtitle_titles": report["subtitle_titles"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--regenerate", action="store_true")
    parser.add_argument("--preview", metavar="APPROVED_STEM", help="write a presenter-only preview into 0-Tmp")
    args = parser.parse_args()
    try:
        if args.preview and args.regenerate:
            parser.error("--preview cannot be combined with --regenerate")
        result = preview(args.project_root, args.preview) if args.preview else run(
            args.project_root, args.ffprobe, args.regenerate)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError, ET.ParseError) as error:
        parser.exit(1, f"Final Cut failed: {error}\n")


if __name__ == "__main__":
    main()
