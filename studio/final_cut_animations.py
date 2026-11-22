"""Import approved animation media and build disabled project-timeline cutaways."""
from collections import defaultdict
from fractions import Fraction
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET

from studio.fingerprints import matches_identity, raw_sha256


RESOLVER = Path(".agents/skills/studio/2-Production/3-Animations/scripts/animations.py")
EVENT = "Animations"
APPROVALS = Path("2-Production/3-Animations")
HASH_KEY = "studio.animation.sha256"


def load(project, workflow, *, ffprobe="ffprobe"):
    spec = importlib.util.spec_from_file_location("studio_animations_for_final_cut", workflow / RESOLVER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    project = Path(project).resolve(strict=True)
    entries, diagnostics = [], []
    errors = (ValueError, TypeError, KeyError, AttributeError, IndexError, OSError,
              RuntimeError, OverflowError, ZeroDivisionError, subprocess.SubprocessError)
    try:
        if not any((project / helper.REGISTRY / name).exists()
                   for name in ("Asset Registry.md", "Asset Registry.json")):
            return entries, diagnostics
        rows = helper.eligible(project)
    except errors as error:
        return entries, [f"Asset Registry: {error}"]
    for asset, row in rows.items():
        try:
            approved = helper.folder(project, asset) / "APPROVED"
            helper.confined(project, approved.relative_to(project).as_posix())
            if not approved.exists():
                continue
            files = sorted(approved.iterdir())
        except errors as error:
            diagnostics.append(f"{asset}: {error}")
            continue
        for path in files:
            relative = path.relative_to(project).as_posix()
            try:
                helper.require_approved_file(project, asset, path)
                if not path.stat().st_size:
                    raise ValueError("Empty approved video")
                video, _, _ = helper.probe_editorial(path, ffprobe)
                rate = video.get("avg_frame_rate")
                fps = (Fraction(rate) if rate and rate != "0/0" else 0) or Fraction(video["r_frame_rate"])
                if "duration_ts" in video:
                    duration = Fraction(video["duration_ts"]) * Fraction(video["time_base"])
                else:
                    duration = Fraction(video["duration"])
                if (fps <= 0 or duration <= 0 or int(video["width"]) <= 0 or int(video["height"]) <= 0
                        or video.get("disposition", {}).get("attached_pic")):
                    raise ValueError("Invalid video dimensions, frame rate, or duration")
                entries.append({"asset_id": asset, "path": relative, "sha256": raw_sha256(path),
                                "registry": row, "width": int(video["width"]), "height": int(video["height"]),
                                "frame": 1 / fps, "duration": duration})
            except errors as error:
                diagnostics.append(f"{relative}: {error}")
    return entries, diagnostics


def verify_sources(root, project, output):
    """An imported snapshot stays valid when versions are added, not when its media changes."""
    project = project.resolve()
    for asset in root.findall("resources/asset"):
        fingerprint = asset.find(f"metadata/md[@key='{HASH_KEY}']")
        if fingerprint is None:
            continue
        media = asset.find("media-rep[@kind='original-media']")
        if media is None or not media.get("src"):
            raise ValueError(f"Missing animation media reference in {output.name}")
        url = urlsplit(media.attrib["src"])
        if (url.scheme not in ("", "file") or url.query or url.fragment
                or url.netloc and (url.scheme != "file" or url.netloc != "localhost")):
            raise ValueError(f"Invalid animation media URL in {output.name}")
        # Resolve against the final XML location even if its bytes are still staged in memory.
        source = (output.parent / unquote(url.path)).resolve()
        if (not source.is_relative_to(project) or not source.is_relative_to(project / APPROVALS)
                or len(source.relative_to(project / APPROVALS).parts) != 3 or source.parent.name != "APPROVED"
                or not source.is_file() or not matches_identity(
                    source, {"method": "sha256", "sha256": fingerprint.attrib["value"]})):
            raise ValueError(f"Approved animation source changed or is missing: {source} ({output.name})")


def add(parent, tag, **attributes):
    return ET.SubElement(parent, tag, {key: str(value) for key, value in attributes.items()})


def label(row):
    return f"{row['ID']} {' '.join(row['Brief'].split()[:12])}".strip()


class AnimationIntake:
    def __init__(self, entries, project, output, resources, resource_id, frame, time):
        self.frame, self.time = frame, time
        self.assets = defaultdict(list)
        formats = {}
        for entry in entries:
            source_frame, duration = entry["frame"], entry["duration"]
            key = (entry["width"], entry["height"], source_frame)
            if key not in formats:
                formats[key] = resource_id()
                add(resources, "format", id=formats[key], width=key[0], height=key[1],
                    frameDuration=time(source_frame))
            identifier = resource_id()
            name = f"{label(entry['registry'])} [{Path(entry['path']).stem}]"
            node = add(resources, "asset", id=identifier, name=name, start="0s", duration=time(duration),
                       hasVideo="1", videoSources="1", format=formats[key])
            source = (project / entry["path"]).resolve(strict=True)
            url = quote(Path(os.path.relpath(source, output.parent.resolve())).as_posix(), safe="/.")
            add(node, "media-rep", kind="original-media", src=url)
            add(add(node, "metadata"), "md", key=HASH_KEY, value=entry["sha256"])
            self.assets[entry["asset_id"]].append((entry, identifier, name, duration, source_frame))

    def browser(self, library):
        if not self.assets:
            return
        event = add(library, "event", name=EVENT)
        for versions in self.assets.values():
            for _, ref, name, duration, _ in versions:
                add(event, "asset-clip", ref=ref, name=name, start="0s", duration=self.time(duration), srcEnable="video")

    def anchors(self, document, auditions, extract_number):
        """Resolve the registry's exact spoken range and stage at the containing group's first word."""
        result, skipped = defaultdict(list), []
        script = document["script"]
        script_path = script.get("source", {}).get("path")
        ranges = {a["sentence"]: a["candidates"][0]["script"] for a in auditions}
        for asset, versions in self.assets.items():
            row = versions[0][0]["registry"]
            if row["Script"] != script_path:
                continue
            if extract_number is not None:
                refs = json.loads(row["Extracts"])
                if refs and not any(int(ref.removeprefix("EXTRACT-")) == extract_number for ref in refs):
                    continue
            start, end = int(row["Start"]), int(row["End"])
            if (not 0 <= start <= end < len(script["words"]) or
                    " ".join(w["text"] for w in script["words"][start:end + 1]) != row["Passage"]):
                skipped.append({"asset_id": asset, "reason": "script_anchor_mismatch"})
                continue
            sentence = next((i for i, span in ranges.items() if span["word_start"] <= start <= span["word_end"]), None)
            if sentence is None:
                skipped.append({"asset_id": asset, "reason": "anchor_not_in_project"})
                continue
            result[sentence].append(asset)
            if end > ranges[sentence]["word_end"]:
                skipped.append({"asset_id": asset, "reason": "anchor_spans_groups_placed_at_first"})
        return result, skipped

    def clip(self, parent, version, duration, index, **attrs):
        _, ref, name, _, source_frame = version
        node = ET.Element("asset-clip", ref=ref, name=name, start="0s", duration=self.time(duration),
                          srcEnable="video", **attrs)
        if self.frame != source_frame:
            add(node, "conform-rate", scaleEnabled="0")
        parent.insert(index, node)

    def place(self, clip, take, assets, snap, cursor):
        """Stage cutaways inside a trimmed mc-clip; report their offsets on the timeline."""
        placed, skipped = [], []
        if not assets:
            return placed, skipped
        if clip.tag != "mc-clip":
            raise ValueError("Animation cutaways require a direct mc-clip")
        clip_start = Fraction(clip.get("start", "0s").removesuffix("s"))
        clip_duration = Fraction(clip.attrib["duration"].removesuffix("s"))
        offset = max(0, snap(take["word_in"], self.frame) - take["cut_in"])
        coverage = min(clip_duration, take["cut_out"] - take["cut_in"]) - offset
        versions_to_place = []
        for asset in assets:
            for version in self.assets[asset]:
                path = version[0]["path"]
                duration = math.floor(min(coverage, version[3]) / self.frame) * self.frame
                if duration <= 0:
                    skipped.append({"asset_id": asset, "path": path, "take_key": take["take_key"],
                                    "reason": "no_usable_frame"})
                    continue
                versions_to_place.append((asset, version, duration))
        if not versions_to_place:
            return placed, skipped
        # Keep existing connected video and titles above the new cutaways.
        for child in clip:
            lane = int(child.get("lane", "0"))
            if lane > 0:
                child.set("lane", str(lane + len(versions_to_place)))
        index = next((i for i, child in enumerate(clip)
                      if child.tag in {"marker", "chapter-marker", "rating", "keyword", "analysis-marker", "metadata"}
                      or isinstance(child.tag, str) and child.tag.startswith("filter-")), len(clip))
        for lane, (asset, version, duration) in enumerate(versions_to_place, 1):
            self.clip(clip, version, duration, index + lane - 1, lane=str(lane),
                      offset=self.time(clip_start + offset), enabled="0")
            placed.append({"asset_id": asset, "path": version[0]["path"], "sentence": take["sentence"],
                           "take_key": take["take_key"], "offset": self.time(cursor + offset),
                           "duration": self.time(duration)})
        return placed, skipped
