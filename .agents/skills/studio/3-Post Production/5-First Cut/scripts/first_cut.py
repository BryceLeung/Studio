#!/usr/bin/env python3
"""Build sentence auditions around independently editable multicam take compounds."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
from urllib.parse import quote, unquote
import xml.etree.ElementTree as ET

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "studio/tools/line/mapping.py").is_file())
SCORER = ROOT / ".agents/skills/studio/3-Post Production/4-Take Selection/scripts/score_takes.py"
spec = importlib.util.spec_from_file_location("studio_scoring_for_first_cut", SCORER)
scoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoring)
find = scoring.find
STAGE = Path("3-Post Production/5-First Cut")
INTAKE = Path("2-Production/1-Footage Intake")
SYNC = Path("3-Post Production/1-Sync")
EXTRACTS = Path("1-Pre Production/3-Extracts")
ROLES = ("camA", "camB", "lav")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def seconds(milliseconds):
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)) or not math.isfinite(milliseconds):
        raise ValueError(f"Missing/nonfinite word timestamp: {milliseconds}")
    return Fraction(str(milliseconds)) / 1000


def time(value):
    value = Fraction(value)
    return f"{value.numerator}/{value.denominator}s" if value.denominator != 1 else f"{value.numerator}s"


def parse_time(value):
    return Fraction(value.removesuffix("s"))


def snap(value, frame):
    """Nearest frame, ties toward the later frame; compute shared cuts only once."""
    return math.floor(value / frame + Fraction(1, 2)) * frame


def media_fingerprint(path, root):
    stat = path.stat()
    return {"scope": "project", "path": path.relative_to(root).as_posix(),
            "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def validate_done(project, folder):
    """Accept existing Studio SHA-256 and large-media manifest conventions."""
    marker = project / folder / "DONE"
    manifest = read_json(marker)
    if not isinstance(manifest, dict) or not manifest.get("outputs"):
        raise ValueError(f"Invalid upstream manifest: {marker}")
    def entries(value):
        # Older stages used dictionaries grouping fingerprints by input kind.
        if isinstance(value, dict):
            if "path" in value and any(k in value for k in ("sha256", "bytes", "size_bytes", "mtime_ns")):
                yield value
            else:
                for child in value.values():
                    yield from entries(child)
        elif isinstance(value, list):
            for child in value:
                yield from entries(child)
    for section in ("dependencies", "inputs", "outputs"):
        for entry in entries(manifest.get(section, [])):
            raw = Path(entry["path"])
            root = ROOT if entry.get("scope") == "workflow" else project
            path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
            if path == ROOT / ".agents/skills/studio/SKILL.md":
                continue
            if not (path.is_relative_to(project) or path.is_relative_to(ROOT)):
                raise ValueError(f"Manifest path outside workflow/project: {path}")
            stat = path.stat()
            if "sha256" in entry and find.sha256(path).lower() != entry["sha256"].lower():
                raise ValueError(f"Stale upstream fingerprint: {path}")
            size = entry.get("bytes", entry.get("size_bytes"))
            if size is not None and stat.st_size != size:
                raise ValueError(f"Changed media size: {path}")
            if "mtime_ns" in entry and stat.st_mtime_ns != entry["mtime_ns"]:
                raise ValueError(f"Changed media timestamp: {path}")
            if "modified_utc" in entry:
                expected = datetime.fromisoformat(entry["modified_utc"].replace("Z", "+00:00")).timestamp()
                if abs(stat.st_mtime - expected) > 0.000001:
                    raise ValueError(f"Changed media timestamp: {path}")
    return manifest


def probe(path, executable="ffprobe"):
    result = subprocess.run([executable, "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
    stream = video or (audio[0] if audio else None)
    if not stream:
        raise ValueError(f"No usable media streams: {path}")
    if Fraction(stream.get("start_time", "0")) != 0:
        raise ValueError(f"Nonzero source stream start needs explicit handling: {path}")
    duration = (Fraction(stream["duration_ts"]) * Fraction(stream["time_base"])
                if "duration_ts" in stream else Fraction(stream.get("duration", data["format"]["duration"])))
    return {"duration": duration, "video": video, "audio": audio}


def adjacent(left, right, positions):
    return (left["transcript_source"]["path"] == right["transcript_source"]["path"]
            and positions[find.mapping.range_key(right["script"]) ] ==
                positions[find.mapping.range_key(left["script"]) ] + 1
            and right["transcript"]["word_start"] == left["transcript"]["word_end"] + 1
            and left["classification"] != "flub" and right["classification"] != "flub")


def plan_takes(document, scores, syncs, probes, frame, padding_ms):
    """Return all take trims and sentence auditions, without altering source ranges."""
    positions = {find.mapping.range_key(g): i for i, g in enumerate(document["script_groupings"])}
    words = {r["transcript"]["source"]["path"]: r["transcript"]["words"] for r in document["recordings"]}
    grouped, by_recording, plan = defaultdict(list), defaultdict(list), []
    padding = seconds(padding_ms)
    for take in scores["takes"]:
        item = dict(take)
        number = take["recording_number"]
        sync = syncs[number]
        shift = -min(Fraction(0), *(seconds(c["offset_ms"]) for c in sync["clips"].values()))
        # Shift all three sources together, then preserve lav alignment at sample precision.
        shift = -math.floor(-shift / frame) * frame
        lav_origin = shift + seconds(sync["clips"]["lav"]["offset_ms"])
        offsets = {role: (shift + seconds(sync["clips"][role]["offset_ms"])) for role in ROLES}
        offsets = {role: (origin if role == "lav" else snap(origin, frame)) for role, origin in offsets.items()}
        source_words = words[take["transcript_source"]["path"]]
        first = source_words[take["transcript"]["word_start"]]
        last = source_words[take["transcript"]["word_end"]]
        start, end = lav_origin + seconds(first["start_ms"]), lav_origin + seconds(last["end_ms"])
        if end < start:
            raise ValueError(f"Reversed take timestamps: {take['take_key']}")
        # All candidates must be switchable to either camera without entering missing media.
        low = max(offsets.values())
        high = min(offsets[r] + probes[number][r]["duration"] for r in ROLES)
        lower_frame, upper_frame = -math.floor(-low / frame) * frame, math.floor(high / frame) * frame
        if start < low or end > high:
            raise ValueError(f"Take speech outside shared camera/lav coverage: {take['take_key']}")
        item.update(word_in=start, word_out=end,
                    cut_in=max(lower_frame, math.floor((start - padding) / frame) * frame),
                    cut_out=min(upper_frame, -math.floor(-(end + padding) / frame) * frame),
                    origins=offsets, shared_in=False, shared_out=False)
        grouped[find.mapping.range_key(take["script"])].append(item)
        by_recording[number].append(item)
        plan.append(item)
    for recording in by_recording.values():
        recording.sort(key=lambda t: t["transcript"]["word_start"])
        for left, right in zip(recording, recording[1:]):
            if adjacent(left, right, positions):
                boundary = snap((left["word_out"] + right["word_in"]) / 2, frame)
                if not (left["cut_in"] < boundary < right["cut_out"]):
                    raise ValueError("Shared midpoint produces an empty take.")
                left.update(cut_out=boundary, shared_out=True)
                right.update(cut_in=boundary, shared_in=True)
    discarded = {find.mapping.range_key(g) for g in document.get("script_discarded", [])}
    auditions, previous, angle = [], None, "camA"
    for index, group in enumerate(document["script_groupings"]):
        key = find.mapping.range_key(group)
        if key in discarded:
            continue
        candidates = sorted(grouped[key], key=lambda t: t["score"], reverse=True)
        if not candidates:
            raise ValueError(f"Script group {index + 1} has no audition candidates.")
        winner = candidates[0]
        continuous = previous is not None and adjacent(previous, winner, positions)
        if previous is not None and not continuous:
            angle = "camB" if angle == "camA" else "camA"
        for candidate in candidates:
            candidate["angle"] = angle
            candidate["sentence"] = index + 1
        auditions.append({"sentence": index + 1, "candidates": candidates, "continuous": continuous,
                          "text": find.mapping.range_text(document["script"], group)})
        previous = winner
    for item in plan:
        item.setdefault("angle", "camA")
        item.setdefault("sentence", positions[find.mapping.range_key(item["script"])] + 1)
        if item["cut_out"] <= item["cut_in"]:
            raise ValueError("Take has nonpositive duration.")
    if not auditions:
        raise ValueError("Cannot generate an empty project.")
    return plan, auditions


def element(parent, tag, **attributes):
    return ET.SubElement(parent, tag, {key: str(value) for key, value in attributes.items()})


def build_xml(project, output, name, document, scores, syncs, probes, padding_ms):
    first_video = probes[min(probes)]["camA"]["video"]
    frame = 1 / Fraction(first_video["r_frame_rate"])
    if frame <= 0:
        raise ValueError("Invalid project frame rate.")
    takes, auditions = plan_takes(document, scores, syncs, probes, frame, padding_ms)
    root = ET.Element("fcpxml", version="1.10")
    resources = element(root, "resources")
    ids = iter(range(1, 100000))
    def resource_id():
        return f"r{next(ids)}"
    def format_for(video, duration):
        identifier = resource_id()
        element(resources, "format", id=identifier, frameDuration=time(duration),
                width=video["width"], height=video["height"], colorSpace="1-1-1 (Rec. 709)")
        return identifier
    if (first_video.get("color_primaries"), first_video.get("color_transfer")) != ("bt709", "bt709"):
        raise ValueError("Project color space needs explicit handling for non-Rec.709 footage.")
    project_format = format_for(first_video, frame)
    multicams = {}
    for number, sync in sorted(syncs.items()):
        assets = {}
        for role in ROLES:
            media = probes[number][role]
            video, audio = media["video"], media["audio"]
            if role != "lav" and not video or role == "lav" and not audio:
                raise ValueError(f"Missing required stream for {role}.")
            identifier = resource_id()
            attributes = {"id": identifier, "name": Path(sync["clips"][role]["path"]).stem,
                          "start": "0s", "duration": time(media["duration"])}
            if video:
                if (video.get("color_primaries"), video.get("color_transfer")) != ("bt709", "bt709"):
                    raise ValueError("Non-Rec.709 camera footage needs explicit handling.")
                video_frame = 1 / Fraction(video["r_frame_rate"])
                attributes.update(hasVideo="1", videoSources="1", format=format_for(video, video_frame))
            if audio:
                attributes.update(hasAudio="1", audioSources=str(len(audio)),
                                  audioChannels=str(sum(s["channels"] for s in audio)), audioRate=audio[0]["sample_rate"])
            asset = element(resources, "asset", **attributes)
            source = (project / sync["clips"][role]["path"]).resolve(strict=True)
            # Relative to the XML itself, not to a Windows or Mac home directory.
            url = quote(Path(os.path.relpath(source, output.parent)).as_posix(), safe="/.")
            element(asset, "media-rep", kind="original-media", src=url)
            assets[role] = identifier
        identifier = resource_id()
        container = element(resources, "media", id=identifier, name=f"{name} C{number:02d}")
        multicam = element(container, "multicam", format=project_format, tcStart="0s", tcFormat="NDF")
        origins = next(t["origins"] for t in takes if t["recording_number"] == number)
        angles = {}
        for role in ROLES:
            angle_id = f"C{number:02d}-{role}"
            angles[role] = angle_id
            angle = element(multicam, "mc-angle", name=role, angleID=angle_id)
            origin = origins[role]
            if origin:
                element(angle, "gap", name="Gap", offset="0s", start="0s", duration=time(origin))
            media = probes[number][role]
            duration = media["duration"] if role == "lav" else math.floor(media["duration"] / frame) * frame
            clip = element(angle, "asset-clip", ref=assets[role], offset=time(origin), start="0s",
                           name=Path(sync["clips"][role]["path"]).stem, duration=time(duration), audioRole="dialogue")
            if media["video"] and Fraction(media["video"]["r_frame_rate"]) != 1 / frame:
                element(clip, "conform-rate", scaleEnabled="0")
        multicams[number] = (identifier, angles)
    compounds = {}
    for index, take in enumerate(takes, 1):
        identifier = resource_id()
        compounds[take["take_key"]] = identifier
        label = f"S{take['sentence']:03d} C{take['recording_number']:02d} T{index:03d} {take['classification']} score={take['score']}"
        media = element(resources, "media", id=identifier, name=label)
        duration = take["cut_out"] - take["cut_in"]
        sequence = element(media, "sequence", format=project_format, duration=time(duration), tcStart="0s", tcFormat="NDF")
        spine = element(sequence, "spine")
        mc_id, angles = multicams[take["recording_number"]]
        clip = element(spine, "mc-clip", ref=mc_id, name=label, offset="0s",
                       start=time(take["cut_in"]), duration=time(duration))
        element(clip, "mc-source", angleID=angles["lav"], srcEnable="audio")
        element(clip, "mc-source", angleID=angles[take["angle"]], srcEnable="video")
        element(clip, "marker", start=time(take["cut_in"]), duration=time(frame), value=f"{label} IN")
        # OUT labels the final included frame, so the marker stays inside the trim.
        element(clip, "marker", start=time(take["cut_out"] - frame), duration=time(frame), value=f"{label} OUT")
    library = element(root, "library")
    event = element(library, "event", name=f"{name} — First Cut")
    project_node = element(event, "project", name=name)
    total = sum((a["candidates"][0]["cut_out"] - a["candidates"][0]["cut_in"] for a in auditions), Fraction(0))
    sequence = element(project_node, "sequence", format=project_format, duration=time(total), tcStart="0s",
                       tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = element(sequence, "spine")
    cursor = Fraction(0)
    for audition in auditions:
        node = element(spine, "audition", offset=time(cursor))
        for take in audition["candidates"]:
            element(node, "ref-clip", ref=compounds[take["take_key"]], name=f"S{audition['sentence']:03d} {audition['text']}",
                    start="0s", duration=time(take["cut_out"] - take["cut_in"]), useAudioSubroles="1")
        winner = audition["candidates"][0]
        cursor += winner["cut_out"] - winner["cut_in"]
    ET.indent(root, space="    ")
    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="utf-8") + b"\n"
    report = {"script": name, "takes": len(takes), "auditions": len(auditions), "multicams": len(syncs),
              "duration_seconds": float(total), "frame_duration": time(frame), "padding_ms": padding_ms,
              "seamless_boundaries": sum(a["continuous"] for a in auditions),
              "camera_flips": sum(not a["continuous"] for a in auditions[1:]),
              "selected": [{"sentence": a["sentence"], "take_key": a["candidates"][0]["take_key"],
                            "camera": a["candidates"][0]["angle"],
                            "source_in": time(a["candidates"][0]["cut_in"]),
                            "source_out": time(a["candidates"][0]["cut_out"])} for a in auditions]}
    validate_xml(xml, output, report)
    return xml, report


def validate_xml(xml, output, report):
    root = ET.fromstring(xml)
    resources = {r.attrib["id"]: r for r in root.find("resources")}
    if len(resources) != len(root.find("resources")):
        raise ValueError("Duplicate XML resource IDs.")
    for node in root.iter():
        for attr in ("ref", "format"):
            if attr in node.attrib and node.attrib[attr] not in resources:
                raise ValueError("Unresolved XML resource reference.")
    for media in root.findall("resources/asset/media-rep"):
        if not (output.parent / unquote(media.attrib["src"])).resolve().is_file():
            raise ValueError("Media URL does not resolve to an existing intake file.")
    auditions = root.findall("library/event/project/sequence/spine/audition")
    if len(auditions) != report["auditions"] or len(root.findall("resources/media/sequence")) != report["takes"]:
        raise ValueError("Incomplete audition/compound inventory.")
    cursor = Fraction(0)
    for audition in auditions:
        if parse_time(audition.attrib["offset"]) != cursor or not len(audition):
            raise ValueError("Invalid audition timeline offset.")
        for candidate in audition:
            clip = resources[candidate.attrib["ref"]].find("sequence/spine/mc-clip")
            duration = parse_time(candidate.attrib["duration"])
            if duration <= 0 or duration != parse_time(clip.attrib["duration"]):
                raise ValueError("Invalid compound duration.")
            angles = {a.attrib["angleID"] for a in resources[clip.attrib["ref"]].findall("multicam/mc-angle")}
            if any(s.attrib["angleID"] not in angles for s in clip.findall("mc-source")):
                raise ValueError("Unknown multicam angle.")
        cursor += parse_time(audition[0].attrib["duration"])
    if cursor != parse_time(root.find("library/event/project/sequence").attrib["duration"]):
        raise ValueError("Project duration differs from active audition choices.")


def run(project, score_paths, padding_ms=50, ffprobe="ffprobe"):
    project = project.resolve(strict=True)
    directory = project / STAGE
    find.append_log(directory, "First Cut started", [f"padding_ms={padding_ms}; start camA; flip at nonadjacent selections."])
    failed = (3, 5)
    try:
        for step, folder in (((1, 1), find.APPROVED.parent), ((1, 3), EXTRACTS), ((2, 1), INTAKE),
                             ((3, 1), SYNC), ((3, 3), find.OUTPUT), ((3, 4), scoring.STAGE)):
            failed = step
            validate_done(project, folder)
        find.validate_approved(project)
        failed = (3, 5)
        inventory = find.discover(project)
        expected = [(project / scoring.STAGE / (s.stem + "__Scores.v1.json")).resolve() for s, _ in inventory]
        supplied = [(project / p).resolve(strict=True) if not p.is_absolute() else p.resolve(strict=True) for p in score_paths]
        if len(supplied) != len(set(supplied)) or set(supplied) != set(expected):
            raise ValueError("Pass one stage 3.4 score file for every approved script.")
        planned, inputs = [], []
        for (script, transcript_paths), score_path in zip(inventory, expected):
            scores = read_json(score_path)
            source_path = project / scores["source_mapping"]["path"]
            versions = find.mapping.takes_versions(project, script.stem + "__Takes")
            if not versions or source_path.resolve() != versions[-1][1].resolve():
                raise ValueError("Scores must reference the latest mapping version.")
            document = scoring.load_mapping(project, source_path)
            calculated = scoring.score_document(document, scoring.fingerprint(source_path, project, "project"))
            if calculated != scores:
                raise ValueError(f"Stale or changed scoring output: {score_path}")
            check = find.check_mapping(project, source_path, find.sources.load_script(project, script),
                                       [find.sources.load_transcript(project, p) for p in transcript_paths])
            if not check["coverage"]["complete"]:
                raise ValueError(f"Incomplete mapping: {source_path}")
            syncs, probes = {}, {}
            inputs.extend(scoring.fingerprint(p, project, "project") for p in (script, score_path, source_path, *transcript_paths))
            for recording in document["recordings"]:
                number = int(Path(recording["transcript"]["source"]["path"]).stem.rsplit("__", 2)[1][1:])
                sync_path = project / SYNC / f"{script.stem}__C{number:02d}.json"
                sync = read_json(sync_path)
                if (sync.get("reference_role") != "camA" or
                        sync.get("offset_convention") != "positive_means_file_starts_later_than_camA" or
                        set(sync["clips"]) != set(ROLES) or sync["clips"]["camA"]["offset_ms"] != 0):
                    raise ValueError("Unsupported synchronization convention.")
                probes[number] = {}
                for role, clip in sync["clips"].items():
                    path = (project / clip["path"]).resolve(strict=True)
                    if not path.is_relative_to(project / INTAKE):
                        raise ValueError("Media must be inside stage 2.1.")
                    if type(clip["offset_ms"]) is not int:
                        raise ValueError("Sync offset must be integer milliseconds.")
                    actual = media_fingerprint(path, project)
                    if actual["bytes"] != clip["size_bytes"] or actual["mtime_ns"] != clip["mtime_ns"]:
                        raise ValueError(f"Sync source changed: {path}")
                    probes[number][role] = probe(path, ffprobe)
                    inputs.append(actual)
                syncs[number] = sync
                inputs.append(scoring.fingerprint(sync_path, project, "project"))
            output = directory / (script.stem + ".FCPXML")
            xml, report = build_xml(project, output, script.stem, document, scores, syncs, probes, padding_ms)
            planned.append((output, xml, report))
        for output, xml, _ in planned:
            if output.exists() and output.read_bytes() != xml:
                raise ValueError(f"Existing First Cut differs; inspect before replacing: {output}")
        for output, xml, _ in planned:
            if not output.exists():
                with output.open("xb") as stream:
                    stream.write(xml)
            validate_xml(output.read_bytes(), output, next(r for p, _, r in planned if p == output))
        dependencies = [Path(__file__), Path(__file__).parents[1] / "SKILL.md", SCORER, scoring.FIND_SCRIPT,
                        ROOT / "scripts/append-log.py"]
        dependencies.extend(ROOT / "studio/tools/line" / name for name in
                            ("mapping.py", "sources.py", "combined.py", "quality.py", "actions.py", "boundaries.py"))
        # Extracts remain a declared input; no extract projects are emitted yet.
        inputs.extend(scoring.fingerprint(p, project, "project") for p in (project / EXTRACTS).iterdir()
                      if p.is_file() and p.name not in ("LOG.md", "DONE"))
        report = {"schema": "studio.first-cut.review", "schema_version": 1,
                  "generated": True, "complete": False, "done_written": False,
                  "review_required": "Import both FCPXML files in Final Cut; verify sync, auditions, cameras, and boundaries.",
                  "inputs": inputs, "dependencies": [scoring.fingerprint(p, ROOT, "workflow") for p in dependencies],
                  "outputs": [scoring.fingerprint(p, project, "project") for p, _, _ in planned],
                  "projects": [r for _, _, r in planned]}
        review_path = directory / "REVIEW.json"
        if review_path.exists() and read_json(review_path) != report:
            raise ValueError("Existing REVIEW.json differs; inspect before replacing.")
        if not review_path.exists():
            find.write_json(review_path, report)
        if (directory / "DONE").exists():
            find.invalidate(project, (3, 5), "First Cut regenerated; Final Cut import review required.")
        find.append_log(directory, "First Cut ready for import review", [
            json.dumps({"projects": [{k: v for k, v in r.items() if k != "selected"} for _, _, r in planned]}),
            "No DONE: Final Cut import/playback review pending. See REVIEW.json for fingerprints and selected source ranges."])
        return report
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        # Implementation/shape errors must never invalidate otherwise valid upstream work.
        actual_failed = (3, 5) if isinstance(error, (TypeError, KeyError)) else failed
        find.invalidate(project, actual_failed, f"First Cut failed: {error}")
        find.append_log(directory, "First Cut failed", [str(error), "Preserved existing work files."])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--scores", required=True, nargs="+", type=Path)
    parser.add_argument("--padding-ms", type=int, default=50)
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    if args.padding_ms < 0:
        parser.error("Padding must be nonnegative.")
    try:
        report = run(args.project_root, args.scores, args.padding_ms, args.ffprobe)
        print(json.dumps({"generated": report["generated"], "complete": report["complete"],
                          "review_required": report["review_required"],
                          "projects": [{k: v for k, v in p.items() if k != "selected"} for p in report["projects"]]}, indent=2))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"First Cut failed: {error}\n")


if __name__ == "__main__":
    main()
