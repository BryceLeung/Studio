"""Consume stage 3.5 paths for editable, horizontal-only 9:16 reframing."""
from fractions import Fraction
from bisect import bisect_left, bisect_right
import math
from statistics import median
import xml.etree.ElementTree as ET

import subtitles


WIDTH, HEIGHT = 1080, 1920
# Decoder/sample rounding tolerance within declared tracked ranges, not a gap limit.
SAMPLE_EDGE_TOLERANCE_MS = 300


def load_paths(cut, project):
    manifest = cut.validate_done(project, cut.FACE)
    index_path = project / cut.FACE / "Face Paths.json"
    index_fingerprint = cut.scoring.fingerprint(index_path, project, "project")
    def recorded(path, reference):
        return any(entry.get("scope") == reference["scope"] and entry.get("path") == reference["path"]
                   and cut.find.fingerprints.matches_identity(path, entry)
                   for entry in manifest["outputs"])
    if not recorded(index_path, index_fingerprint):
        raise ValueError("Face-path index is not recorded in stage 3.5 DONE.")
    index = cut.read_json(index_path)
    if index.get("schema") != "studio.face-path-index" or index.get("schema_version") != 1:
        raise ValueError("Unsupported face-path index.")
    paths, inputs = {}, [index_fingerprint]
    for entry in index["paths"]:
        path = (project / entry["path"]).resolve(strict=True)
        if entry.get("scope") != "project" or not path.is_relative_to(project / cut.FACE):
            raise ValueError("Face path outside stage 3.5.")
        actual = cut.scoring.fingerprint(path, project, "project")
        if (entry["path"] != actual["path"]
                or not cut.find.fingerprints.matches_identity(path, entry)
                or not recorded(path, actual)):
            raise ValueError(f"Unrecorded or changed face path: {path}")
        data = cut.read_json(path)
        if data.get("schema") != "studio.face-path" or data.get("schema_version") != 1:
            raise ValueError(f"Unsupported face path: {path}")
        media = data["media"]
        source = (project / media["path"]).resolve(strict=True)
        if (media.get("scope") != "project" or not source.is_relative_to(project / cut.INTAKE)
                or not cut.matches_identity(source, media)):
            raise ValueError(f"Stale face-path media: {path}")
        key = (source, data["role"])
        if data["role"] not in ("camA", "camB") or key in paths:
            raise ValueError(f"Duplicate/invalid face-path role: {path}")
        validate_path(data)
        paths[key] = data
        inputs.append(actual)
    return paths, inputs


def validate_path(data):
    if any(type(data[k]) is not int or data[k] <= 0 for k in ("width", "height")):
        raise ValueError("Invalid face-path dimensions.")
    previous = -1
    for start, end in data["ranges_ms"]:
        if type(start) is not int or type(end) is not int or not 0 <= start < end or start <= previous:
            raise ValueError("Invalid face-path ranges.")
        previous = end
    if not data["ranges_ms"] or not data["frames"]:
        raise ValueError("Empty face path.")
    previous = -1
    for sample in data["frames"]:
        timestamp, box, status = sample["time_ms"], sample["face"], sample["status"]
        if (type(timestamp) is not int or timestamp <= previous
                or status not in ("tracked", "held", "centered")
                or not any(a - 250 <= timestamp <= b for a, b in data["ranges_ms"])):
            raise ValueError("Invalid face-path sample/time.")
        if box is None:
            if status != "centered":
                raise ValueError("Missing face box.")
        elif (not isinstance(box, list) or len(box) != 4
              or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in box)):
            raise ValueError("Invalid normalized face box.")
        previous = timestamp
    for start, end in data["ranges_ms"]:
        samples = [s["time_ms"] for s in data["frames"] if start - 250 <= s["time_ms"] <= end]
        if (not samples or samples[0] > start + SAMPLE_EDGE_TOLERANCE_MS
                or samples[-1] < end - SAMPLE_EDGE_TOLERANCE_MS
                or any(b - a > 550 for a, b in zip(samples, samples[1:]))):
            raise ValueError("Incomplete face-path sample coverage; rerun stage 3.5.")


def locked_center(data, takes, role):
    """Choose one robust center from used source time, counting overlaps once."""
    chosen = {}
    times = [sample["time_ms"] for sample in data["frames"]]
    for take in takes:
        start = (take["cut_in"] - take["origins"][role]) * 1000
        end = (take["cut_out"] - take["origins"][role]) * 1000
        if not 0 <= start < end:
            raise ValueError(f"Invalid portrait source trim: {take['take_key']}")
        samples = [s for s in data["frames"] if start <= s["time_ms"] < end]
        if not samples:
            # A word can be shorter than the detector sampling interval. Use
            # the last observation at its midpoint, or the first leading one.
            index = max(0, bisect_right(times, (start + end) / 2) - 1)
            samples = [data["frames"][index]]
        chosen.update((s["time_ms"], s) for s in samples)
    for status in ("tracked", "held"):
        centers = [s["face"][0] for s in chosen.values() if s["status"] == status and s["face"] is not None]
        if centers:
            return median(centers)
    return 0.5


def add_framing(clip, data, video, takes, role, frame, *, motion="locked", reference_takes=None):
    validate_path(data)
    if (data["width"], data["height"]) != (video["width"], video["height"]):
        raise ValueError("Face-path dimensions differ from camera media.")
    rotation = video.get("tags", {}).get("rotate", "0")
    if (float(rotation) % 360 or any(float(s.get("rotation", 0)) % 360 for s in video.get("side_data_list", []))
            or video.get("sample_aspect_ratio", "1:1") not in ("1:1", "N/A")):
        raise ValueError("Portrait framing requires unrotated square-pixel camera media.")
    aspect = data["width"] / data["height"]
    if aspect < WIDTH / HEIGHT:
        raise ValueError("Camera is narrower than the portrait canvas.")
    if not takes:
        raise ValueError("Portrait framing requires at least one used take.")
    if motion not in ("locked", "tracked"):
        raise ValueError("Unknown portrait motion mode.")
    # Fill preserves the full camera height. FCP position units are percent of
    # sequence height; clamp the crop center so neither side exposes black.
    half_crop = (WIDTH / HEIGHT) / aspect / 2
    def position(sample):
        center = sample["face"][0] if sample["face"] is not None else 0.5
        center = min(1 - half_crop, max(half_crop, center))
        return f"{(0.5 - center) * aspect * 100:.8f} 0"
    points = {}
    holds = []
    range_ends = [end for _, end in data["ranges_ms"]]
    previous, previous_range, previous_time = None, None, None
    for sample in data["frames"]:
        timestamp = subtitles.frame_at(Fraction(sample["time_ms"], 1000), frame)
        current_range = bisect_left(range_ends, sample["time_ms"])
        if previous is not None and current_range != previous_range:
            # Linear FCP keyframes need an explicit flat endpoint; restrict the
            # change to the single frame where tracking resumes, not the gap.
            if timestamp - frame > previous_time:
                points[timestamp - frame] = position(previous)
            holds.append((previous["time_ms"], sample["time_ms"], "gap_hold", previous["status"]))
        # If two observations snap to one frame, the later observation wins.
        points[timestamp] = position(sample)
        previous, previous_range, previous_time = sample, current_range, timestamp

    first, last = data["frames"][0], data["frames"][-1]
    holds = [(0, first["time_ms"], "leading_hold", first["status"]), *holds]
    warnings = []
    for take in takes:
        start = (take["cut_in"] - take["origins"][role]) * 1000
        end = (take["cut_out"] - take["origins"][role]) * 1000
        if not 0 <= start < end:
            raise ValueError(f"Invalid portrait source trim: {take['take_key']}")
        statuses = {s["status"] for s in data["frames"] if start <= s["time_ms"] < end
                    and s["status"] != "tracked"}
        intervals = []
        for low, high, kind, status in [*holds, (last["time_ms"], end, "trailing_hold", last["status"])]:
            low, high = max(start, low), min(end, high)
            if low < high:
                intervals.append({"start_ms": round(float(low), 3), "end_ms": round(float(high), 3), "kind": kind})
                statuses.add(kind)
                if status != "tracked":
                    statuses.add(status)
        if statuses:
            warnings.append({"take_key": take["take_key"], "camera": role, "statuses": sorted(statuses),
                             "held_intervals_ms": intervals})
        # Explicit leading/trailing anchors also make holds editable in Final Cut.
        low, high = (subtitles.frame_at(value / 1000, frame) for value in (start, end))
        if low < min(points):
            points[low] = position(first)
        if high > max(points):
            points[high] = position(last)
    # FCP import trials verified this Fit + relative enlargement combination.
    # Native-pixel sizing with None and an implicit Fill did not fill the frame.
    scale = aspect / (WIDTH / HEIGHT)
    ET.SubElement(clip, "adjust-conform", {"type": "fit"})
    if motion == "locked":
        center = locked_center(data, reference_takes or takes, role)
        value = position({"face": [center]})
    else:
        value = points[min(points)]
    transform = ET.SubElement(clip, "adjust-transform", {"position": value,
                                                       "scale": f"{scale:.10f} {scale:.10f}"})
    if motion == "locked":
        # This lives once in the source angle, shared by every sentence and
        # audition. No per-word/per-take reset or pan keyframes are emitted.
        center = min(1 - half_crop, max(half_crop, center))
        for take in takes:
            start = (take["cut_in"] - take["origins"][role]) * 1000
            end = (take["cut_out"] - take["origins"][role]) * 1000
            if any(start <= s["time_ms"] < end and s["face"] is not None
                   and (s["face"][0] - s["face"][2] / 2 < center - half_crop
                        or s["face"][0] + s["face"][2] / 2 > center + half_crop)
                   for s in data["frames"]):
                warning = next((w for w in warnings if w["take_key"] == take["take_key"]), None)
                if warning is None:
                    warning = {"take_key": take["take_key"], "camera": role, "statuses": [],
                               "held_intervals_ms": []}
                    warnings.append(warning)
                warning["statuses"] = sorted(set(warning["statuses"]) | {"face_outside_locked_crop"})
        return warnings
    param = ET.SubElement(transform, "param", {"name": "position"})
    animation = ET.SubElement(param, "keyframeAnimation")
    for timestamp, value in sorted(points.items()):
        # FCP rejects interpolation attributes for compound position values,
        # even though the generic FCPXML keyframe DTD permits them. Use the
        # time/value-only representation in Apple's position animation example.
        ET.SubElement(animation, "keyframe", {"time": subtitles.time(timestamp), "value": value})
    return warnings
