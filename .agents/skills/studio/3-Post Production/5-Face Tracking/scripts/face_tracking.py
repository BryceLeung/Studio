#!/usr/bin/env python3
"""Track faces in camera ranges belonging to approved extract takes."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from fractions import Fraction
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.request import urlopen

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "studio/tools/line/mapping.py").is_file())
FIND_SCRIPT = ROOT / ".agents/skills/studio/3-Post Production/3-Find Takes/scripts/find_takes.py"
spec = importlib.util.spec_from_file_location("studio_find_takes_for_face_tracking", FIND_SCRIPT)
find = importlib.util.module_from_spec(spec)
spec.loader.exec_module(find)
from scripts.stage_completion import record_done  # noqa: E402
from scripts.validation import require_prerequisites  # noqa: E402

STAGE = Path("3-Post Production/5-Face Tracking")
SHORTLIST = Path("1-Pre Production/3-Extracts/APPROVED/Short-Form Excerpt Shortlist.md")
SYNC = Path("3-Post Production/1-Sync")
INTAKE = Path("2-Production/1-Footage Intake")
MODEL_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/"
             "47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SAMPLE_FPS = 4
PADDING_MS = 50


def fingerprint(path, project):
    return {"scope": "project", "path": path.relative_to(project).as_posix(), "sha256": find.sha256(path)}


def media_fingerprint(path, project):
    stat = path.stat()
    return {"scope": "project", "path": path.relative_to(project).as_posix(),
            "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def validate_done(project, folder):
    marker = project / folder / "DONE"
    manifest = json.loads(marker.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("outputs"), (list, dict)):
        raise ValueError(f"Invalid upstream completion: {marker}")
    def entries(value):
        if isinstance(value, list):
            for item in value:
                yield from entries(item)
        elif isinstance(value, dict):
            if "path" in value:
                yield value
            else:
                for item in value.values():
                    yield from entries(item)
    for section in ("dependencies", "inputs", "outputs"):
        for entry in entries(manifest.get(section, [])):
            raw = entry["path"].replace("\\", "/")
            if re.match(r"^[A-Za-z]:/", raw):
                if "/.agents/skills/studio/" in raw:
                    raw = ".agents/skills/studio/" + raw.split("/.agents/skills/studio/", 1)[1]
                    base = ROOT
                elif f"/{project.name}/" in raw:
                    raw = raw.split(f"/{project.name}/", 1)[1]
                    base = project
                else:
                    raise ValueError(f"Unknown legacy manifest root: {entry['path']}")
            else:
                base = ROOT if entry.get("scope") == "workflow" else project
            path = (base / raw).resolve()
            if path == ROOT / ".agents/skills/studio/SKILL.md":
                continue
            if not path.is_relative_to(base):
                raise ValueError(f"Manifest path outside project/workflow: {path}")
            path = path.resolve(strict=True)
            stat = path.stat()
            if "sha256" in entry and not find.fingerprints.matches(path, entry["sha256"]):
                raise ValueError(f"Stale upstream fingerprint: {path}")
            if stat.st_size != entry.get("bytes", entry.get("size_bytes", stat.st_size)) or stat.st_mtime_ns != entry.get("mtime_ns", stat.st_mtime_ns):
                raise ValueError(f"Changed upstream input: {path}")
            if "modified_utc" in entry:
                expected = datetime.fromisoformat(entry["modified_utc"].replace("Z", "+00:00")).timestamp()
                if abs(stat.st_mtime - expected) > 0.000001:
                    raise ValueError(f"Changed upstream timestamp: {path}")


def approved_passages(text):
    result = []
    for match in re.finditer(r"^## (\d+)\. ([^\n]+)\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        number, _, body = match.groups()
        status = re.search(r"\*\*Review status:\*\* (.+)", body)
        if not status or status.group(1).strip() != "Approved":
            continue
        source = re.search(r"\*\*Source:\*\* (.+)", body)
        if not source or "### Provisional spoken script" not in body:
            raise ValueError(f"Extract {number} lacks source or spoken script.")
        passages = []
        for paragraph in re.split(r"\n\s*\n", body.split("### Provisional spoken script", 1)[1].strip()):
            if paragraph.strip() == "> [PROPOSED JUMP]":
                continue
            if paragraph.startswith(">"):
                raise ValueError(f"Unknown extract directive: {paragraph}")
            passages.append(paragraph.strip())
        if not passages:
            raise ValueError(f"Extract {number} has no spoken passages.")
        result.append((int(number), source.group(1).strip(), passages))
    return result


def passage_groups(script, groups, passages, number):
    tokens = [word["text"] for word in script["words"]]
    alternatives = []
    for text in passages:
        wanted = text.split()
        matches = [(i, i + len(wanted) - 1) for i in range(len(tokens) - len(wanted) + 1)
                   if tokens[i:i + len(wanted)] == wanted]
        if not matches:
            raise ValueError(f"Extract {number} passage does not match the approved script: {text}")
        alternatives.append(matches)
    solutions = []
    def walk(index, chosen):
        if len(solutions) > 1:
            return
        if index == len(alternatives):
            solutions.append(chosen)
            return
        for span in alternatives[index]:
            if not chosen or span[0] > chosen[-1][1]:
                walk(index + 1, chosen + [span])
    walk(0, [])
    if len(solutions) != 1:
        raise ValueError(f"Extract {number} has {len(solutions)} ordered passage matches; review needed.")
    included = {i for start, end in solutions[0] for i in range(start, end + 1)}
    selected = []
    for group in groups:
        words = set(range(group["word_start"], group["word_end"] + 1))
        if words & included:
            if not words <= included:
                raise ValueError(f"Extract {number} crops a reviewed script group: {group}")
            selected.append(group)
    if set().union(*(set(range(g["word_start"], g["word_end"] + 1)) for g in selected)) != included:
        raise ValueError(f"Extract {number} has incomplete script group coverage.")
    return selected


def load_mapping(project, path):
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if document.get("schema") == find.SCHEMA:
        if document.get("schema_version") != 1:
            raise ValueError(f"Unsupported Takes version: {path}")
        document = dict(document, schema=find.mapping.SCHEMA, schema_version=3,
                        mapping_name=path.stem, version=1)
    document = find.mapping.validate_document(project, document, verify_inputs=False)
    if document["schema_version"] == 2:
        document = dict(document, script=document["sources"]["script"],
                        script_groupings=document["groupings"]["script"],
                        recordings=[{"transcript": document["sources"]["transcript"], "links": document["links"]}])
    script = project / document["script"]["source"]["path"]
    transcripts = [project / r["transcript"]["source"]["path"] for r in document["recordings"]]
    check = find.check_mapping(project, path, find.sources.load_script(project, script),
                               [find.sources.load_transcript(project, p) for p in transcripts])
    if not check["coverage"]["complete"]:
        raise ValueError(f"Incomplete reviewed mapping: {path}")
    return document, [script, *transcripts]


def required_ranges(project, extracts):
    """Map all approved extract alternatives into each camera's source seconds."""
    ranges, inputs = defaultdict(list), []
    if not extracts:
        return {}, []
    contexts = {}
    completed = json.loads((project / find.OUTPUT / "DONE").read_text(encoding="utf-8-sig"))
    if completed.get("completion_mode") == "manual-override":
        approved_versions = {item["path"] for item in completed["outputs"] if item.get("scope") == "project"}
    else:
        approved_versions = {item["path"] for item in completed["conditions"]["latest_versions"]}
    for number, source, passages in extracts:
        if source not in contexts:
            versions = find.mapping.takes_versions(project, Path(source).stem + "__Takes")
            if not versions:
                raise ValueError(f"No reviewed Takes mapping for {source}")
            path = versions[-1][1]
            if path.relative_to(project).as_posix() not in approved_versions:
                raise ValueError(f"Latest Takes mapping is not recorded in 3.3 DONE: {path}")
            document, sources = load_mapping(project, path)
            inputs.extend(fingerprint(p, project) for p in (path, *sources))
            contexts[source] = document
        document = contexts[source]
        selected = passage_groups(document["script"], document["script_groupings"], passages, number)
        discarded = {find.mapping.range_key(g) for g in document.get("script_discarded", [])}
        selected = {find.mapping.range_key(g) for g in selected if find.mapping.range_key(g) not in discarded}
        covered = set()
        for recording in document["recordings"]:
            transcript = recording["transcript"]
            match = re.search(r"__C(\d+)__lav\.json$", transcript["source"]["path"], re.I)
            if not match:
                raise ValueError(f"Unknown recording number: {transcript['source']['path']}")
            sync_path = project / SYNC / f"{Path(source).stem}__C{int(match[1]):02d}.json"
            sync = json.loads(sync_path.read_text(encoding="utf-8-sig"))
            if (sync.get("reference_role") != "camA" or
                    sync.get("offset_convention") != "positive_means_file_starts_later_than_camA" or
                    set(sync["clips"]) != {"camA", "camB", "lav"} or sync["clips"]["camA"]["offset_ms"] != 0):
                raise ValueError(f"Unsupported sync offsets: {sync_path}")
            audio = transcript.get("audio")
            if audio is None or (project / audio).resolve() != (project / sync["clips"]["lav"]["path"]).resolve():
                raise ValueError(f"Transcript lav audio does not match sync source: {sync_path}")
            inputs.append(fingerprint(sync_path, project))
            links = [link for link in recording["links"] if find.mapping.range_key(link["script"]) in selected]
            covered.update(find.mapping.range_key(link["script"]) for link in links)
            for link in links:
                span = link["transcript"]
                words = transcript["words"]
                start, end = words[span["word_start"]]["start_ms"], words[span["word_end"]]["end_ms"]
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or end < start:
                    raise ValueError(f"Invalid transcript timing in {transcript['source']['path']}: {span}")
                for role in ("camA", "camB"):
                    clip = sync["clips"][role]
                    media = (project / clip["path"]).resolve(strict=True)
                    if not media.is_relative_to(project / INTAKE):
                        raise ValueError(f"Camera source outside intake: {media}")
                    stat = media.stat()
                    if stat.st_size != clip["size_bytes"] or stat.st_mtime_ns != clip["mtime_ns"]:
                        raise ValueError(f"Camera source changed since sync: {media}")
                    if type(clip["offset_ms"]) is not int or type(sync["clips"]["lav"]["offset_ms"]) is not int:
                        raise ValueError(f"Noninteger sync offset: {sync_path}")
                    shift = sync["clips"]["lav"]["offset_ms"] - clip["offset_ms"]
                    camera_start = Fraction(str(start - PADDING_MS + shift)) / 1000
                    camera_end = Fraction(str(end + PADDING_MS + shift)) / 1000
                    if camera_end <= 0 or camera_start >= camera_end:
                        raise ValueError(f"Take outside camera coverage: {media}")
                    key = (media, role)
                    ranges[key].append((max(Fraction(0), camera_start), camera_end))
        if selected - covered:
            raise ValueError(f"Extract {number} has script groups without mapped takes: {sorted(selected - covered)}")
    merged = {}
    for key, spans in ranges.items():
        spans.sort()
        result = []
        for start, end in spans:
            if result and start <= result[-1][1]:
                result[-1] = (result[-1][0], max(end, result[-1][1]))
            else:
                result.append((start, end))
        merged[key] = result
        inputs.append(media_fingerprint(key[0], project))
    return merged, sorted({json.dumps(item, sort_keys=True) for item in inputs})


def model_path():
    cache = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    target = cache / "studio" / "face_detection_yunet_2023mar.onnx"
    if target.exists() and find.fingerprints.matches(target, MODEL_SHA256):
        return target
    if target.exists():
        raise ValueError(f"Cached face model has wrong SHA-256: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with urlopen(MODEL_URL, timeout=60) as response:
                while chunk := response.read(65536):
                    stream.write(chunk)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    if not find.fingerprints.matches(temporary, MODEL_SHA256):
        temporary.unlink()
        raise ValueError("Downloaded face model failed SHA-256 verification.")
    temporary.replace(target)
    return target


def follow(previous, detections):
    """Choose the same solo face when possible; emit review statuses otherwise."""
    if not detections:
        return previous, "held" if previous else "centered"
    if previous:
        detections.sort(key=lambda d: math.dist(d[:2], previous[:2]))
        chosen = detections[0]
        if math.dist(chosen[:2], previous[:2]) > 0.25:
            return previous, "held"
    else:
        detections.sort(key=lambda d: d[2] * d[3], reverse=True)
        chosen = detections[0]
    if len(detections) > 1 and abs(detections[0][2] * detections[0][3] - detections[1][2] * detections[1][3]) < 0.2 * detections[0][2] * detections[0][3] and not previous:
        return None, "centered"
    if previous:
        chosen = [0.35 * chosen[i] + 0.65 * previous[i] for i in range(4)] + [chosen[4]]
    return chosen, "tracked"


def validate_path(document, header):
    if any(document.get(key) != value for key, value in header.items()):
        raise ValueError("Face path inputs, source, or algorithm changed.")
    frames = document.get("frames")
    if not isinstance(frames, list) or not frames or not isinstance(document.get("warnings"), list):
        raise ValueError("Face path has no validated samples/warnings.")
    if type(document.get("width")) is not int or type(document.get("height")) is not int or min(document["width"], document["height"]) <= 0:
        raise ValueError("Face path dimensions are invalid.")
    previous_time = -1
    for frame in frames:
        timestamp = frame.get("time_ms")
        if type(timestamp) is not int or timestamp <= previous_time or not any(
                start - 1000 / SAMPLE_FPS <= timestamp <= end for start, end in header["ranges_ms"]):
            raise ValueError("Face sample lies outside its source range or is unordered.")
        previous_time = timestamp
        if frame.get("status") not in ("tracked", "held", "centered"):
            raise ValueError("Unknown face tracking status.")
        face = frame.get("face")
        if face is None:
            if frame["status"] != "centered":
                raise ValueError("Held/tracked face has no box.")
        elif (not isinstance(face, list) or len(face) != 4 or
              any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 or v > 1 for v in face)):
            raise ValueError("Invalid normalized face box.")
    for start, end in header["ranges_ms"]:
        times = [f["time_ms"] for f in frames if start - 1000 / SAMPLE_FPS <= f["time_ms"] <= end]
        if (not times or times[0] > start + 1000 / SAMPLE_FPS + 50
                or times[-1] < end - 1000 / SAMPLE_FPS - 50):
            raise ValueError("Face path does not cover a required camera interval.")


def analyze_video(media, spans, model):
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("Install opencv-python-headless>=4.8,<5 to run 3.5 Face Tracking.") from error
    capture = cv2.VideoCapture(str(media))
    if not capture.isOpened():
        raise ValueError(f"Cannot decode camera file: {media}")
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = capture.get(cv2.CAP_PROP_FPS)
    if width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0:
        capture.release()
        raise ValueError(f"Invalid camera frame dimensions/rate: {media}")
    resized_width = min(width, 640)
    resized_height = max(1, round(height * resized_width / width))
    detector = cv2.FaceDetectorYN.create(str(model), "", (resized_width, resized_height), 0.75, 0.3, 5000)
    frames, warnings = [], []
    try:
        for start, end in spans:
            if not capture.set(cv2.CAP_PROP_POS_MSEC, float(start * 1000)):
                raise ValueError(f"Cannot seek camera file: {media}")
            next_sample, previous, observed = float(start), None, False
            while True:
                ok, image = capture.read()
                if not ok:
                    break
                timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if timestamp <= 0:
                    timestamp = (capture.get(cv2.CAP_PROP_POS_FRAMES) - 1) / fps
                if timestamp + 1 / fps < next_sample:
                    continue
                if timestamp >= float(end):
                    observed = True
                    break
                observed = True
                if timestamp < next_sample - 1 / fps:
                    continue
                scaled = cv2.resize(image, (resized_width, resized_height))
                _, faces = detector.detect(scaled)
                detections = [] if faces is None else [
                    [float((face[0] + face[2] / 2) / resized_width),
                     float((face[1] + face[3] / 2) / resized_height),
                     float(face[2] / resized_width), float(face[3] / resized_height), float(face[-1])]
                    for face in faces]
                previous, status = follow(previous, detections)
                frame = {"time_ms": round(timestamp * 1000), "face": previous[:4] if previous else None,
                         "confidence": previous[4] if status == "tracked" else None, "status": status}
                frames.append(frame)
                if status != "tracked":
                    warnings.append({"time_ms": frame["time_ms"], "status": status})
                next_sample = timestamp + 1 / SAMPLE_FPS
            if (not observed or not frames or frames[-1]["time_ms"] < round(float(start) * 1000)
                    or frames[-1]["time_ms"] + 1000 / SAMPLE_FPS + 1000 / fps < float(end * 1000)):
                raise ValueError(f"No readable frames in required range {start}-{end}: {media}")
    finally:
        capture.release()
    return {"width": width, "height": height, "frames": frames, "warnings": warnings}


def run(project, *, analyzer=analyze_video, get_model=model_path, regenerate=False):
    project = project.resolve(strict=True)
    require_prerequisites(project, ROOT, "3.5")
    directory = project / STAGE
    find.append_log(directory, "Face Tracking started", ["Analyze only approved extract take ranges from 3.3."])
    try:
        shortlist = project / SHORTLIST
        if not shortlist.is_file():
            raise ValueError(f"Approved shortlist is missing: {SHORTLIST}")
        extracts = approved_passages(shortlist.read_text(encoding="utf-8-sig")) if shortlist.is_file() else []
        ranges, references = required_ranges(project, extracts)
        inputs = [fingerprint(shortlist, project)] if shortlist.is_file() else []
        inputs.extend(json.loads(item) for item in references)
        dependencies = [Path(__file__), Path(__file__).parents[1] / "SKILL.md", FIND_SCRIPT,
                         ROOT / "studio/tools/line/mapping.py", ROOT / "scripts/append-log.py",
                         ROOT / "studio/fingerprints.py"]
        identity = {"schema": "studio.face-path", "schema_version": 1, "sample_fps": SAMPLE_FPS,
                    "padding_ms": PADDING_MS, "model_sha256": MODEL_SHA256,
                    "inputs": inputs, "dependencies": [
                        {"scope": "workflow", "path": p.relative_to(ROOT).as_posix(), "sha256": find.sha256(p)}
                        for p in dependencies]}
        planned = []
        model = None
        for (media, role), spans in sorted(ranges.items(), key=lambda item: (str(item[0][0]), item[0][1])):
            output = directory / (media.stem + "__FacePath.v1.json")
            header = dict(identity, media=media_fingerprint(media, project), role=role,
                          ranges_ms=[[round(float(a * 1000)), round(float(b * 1000))] for a, b in spans])
            if output.exists() and not regenerate:
                try:
                    current = json.loads(output.read_text(encoding="utf-8-sig"))
                    validate_path(current, header)
                except (ValueError, TypeError, KeyError) as error:
                    raise ValueError(f"Existing face path differs; inspect before replacing: {output}: {error}") from error
                planned.append((output, None))
                continue
            if model is None:
                model = get_model()
            result = dict(header, **analyzer(media, spans, model))
            validate_path(result, header)
            content = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
            if output.exists() and output.read_bytes() == content:
                content = None
            planned.append((output, content))
        path_outputs = [fingerprint(path, project) if content is None else
                        {"scope": "project", "path": path.relative_to(project).as_posix(),
                          "sha256": find.fingerprints.digest_bytes(content, text=True)}
                        for path, content in planned]
        index = directory / "Face Paths.json"
        inventory = {"schema": "studio.face-path-index", "schema_version": 1, "inputs": inputs,
                     "dependencies": identity["dependencies"], "paths": path_outputs,
                     "no_extracts": not extracts}
        index_bytes = (json.dumps(inventory, indent=2) + "\n").encode()
        if not regenerate and index.exists() and find.sha256(index) != find.fingerprints.digest_bytes(index_bytes, text=True):
            raise ValueError(f"Existing face path index differs; inspect before replacing: {index}")
        if regenerate:
            from scripts.stage_invalidation import prepare_regeneration
            selected = [path for path, content in planned if content is not None and path.exists()]
            if index.exists() and find.sha256(index) != find.fingerprints.digest_bytes(index_bytes, text=True):
                selected.append(index)
            if selected:
                prepare_regeneration(project, ROOT, "3.5", selected,
                                     "Explicit Face Tracking regeneration.", append_log=find.append_log)
        for path, content in planned:
            if content is not None:
                directory.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(content)
        if not index.exists():
            directory.mkdir(parents=True, exist_ok=True)
            with index.open("xb") as stream:
                stream.write(index_bytes)
        outputs = path_outputs + [fingerprint(index, project)]
        completion_dependencies = [Path(__file__).with_name("validation.py"),
                                   Path(__file__).parents[1] / "dependencies.json",
                                   ROOT / "scripts/validation.py", ROOT / "scripts/stage_completion.py",
                                   ROOT / "scripts/stage_invalidation.py"]
        completion_dependencies.extend(ROOT / "studio/tools/line" / name for name in
                                       ("sources.py", "actions.py", "quality.py", "combined.py", "boundaries.py"))
        manifest = {"step": "3.5", "schema": "studio.face-tracking.done", "inputs": inputs,
                    "dependencies": identity["dependencies"] + [
                        {"scope": "workflow", "path": p.relative_to(ROOT).as_posix(), "sha256": find.sha256(p)}
                        for p in completion_dependencies], "outputs": outputs,
                    "conditions": {"no_extracts": not extracts, "face_paths": [p["path"] for p in path_outputs]}}
        marker = directory / "DONE"
        if marker.exists() and json.loads(marker.read_text(encoding="utf-8-sig")) != manifest:
            find.invalidate(project, (3, 5), "Face Tracking completion changed; dependent stages need review.")
        manifest = record_done(project, ROOT, "3.5", manifest)
        find.append_log(directory, "Face Tracking complete", [f"Paths: {len(path_outputs)}; approved extracts: {len(extracts)}.",
                            "Low-confidence, held, and centered frames require editorial review in First Cut."])
        return manifest
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        find.invalidate(project, (3, 5), f"Face Tracking failed: {error}")
        find.append_log(directory, "Face Tracking failed", [str(error), "Existing path files preserved."])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--regenerate", action="store_true", help="Archive stale generated face paths before replacing")
    args = parser.parse_args()
    result = run(args.project_root, regenerate=args.regenerate)
    print(json.dumps({"step": result["step"], "paths": len(result["conditions"]["face_paths"]),
                      "no_extracts": result["conditions"]["no_extracts"]}, indent=2))


if __name__ == "__main__":
    main()
