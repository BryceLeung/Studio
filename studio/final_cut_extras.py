"""Link optional intake footage in a browser-only Final Cut EXTRAS event."""
from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


SOURCE = Path("2-Production/1-Footage Intake/EXTRAS")
EVENT = "EXTRAS"


def sources(project, definition):
    rule = next(rule for rule in definition["inputs"] if rule["folder"] == SOURCE.as_posix())
    folder = project / SOURCE
    if folder.resolve() != folder or (folder.exists() and not folder.is_dir()):
        raise ValueError(f"Invalid EXTRAS intake directory: {folder}")
    if not folder.exists():
        return []
    paths = []
    for path in sorted(folder.iterdir(), key=lambda p: (p.name.casefold(), p.name)):
        if not re.fullmatch(rule["filename_regex"], path.name, re.I):
            continue
        if path.is_symlink():
            raise ValueError(f"Redirected EXTRAS source: {path.name}")
        if path.is_file():
            paths.append(path)
    return paths


def probe(path, ffprobe):
    result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise ValueError(f"Cannot probe EXTRAS footage {path.name}: {result.stderr.strip()}")
    info = json.loads(result.stdout)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ValueError(f"No video stream in EXTRAS footage: {path.name}")
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    rate = next((video[key] for key in ("avg_frame_rate", "r_frame_rate")
                 if video.get(key) not in (None, "0/0", "0/1", "N/A")), "0")
    duration = info.get("format", {}).get("duration") or video.get("duration")
    if duration in (None, "N/A"):
        duration = video.get("duration")
    fps = Fraction(rate)
    if fps <= 0 or not duration or duration == "N/A" or Fraction(duration) <= 0:
        raise ValueError(f"Invalid video rate or duration in EXTRAS footage: {path.name}")
    frame = 1 / fps
    duration = round(Fraction(duration) / frame) * frame
    if duration <= 0 or int(video["width"]) <= 0 or int(video["height"]) <= 0:
        raise ValueError(f"Invalid video dimensions or usable duration in EXTRAS footage: {path.name}")
    return video, audio, frame, duration


def load(paths, ffprobe):
    entries = []
    for path in paths:
        try:
            video, audio, frame, duration = probe(path, ffprobe)
            audio_attrs = {} if not audio else dict(
                hasAudio="1", audioSources=str(len(audio)), audioRate=audio[0]["sample_rate"],
                audioChannels=str(sum(int(s["channels"]) for s in audio)))
            entries.append((path, str(video["width"]), str(video["height"]), frame, duration, audio_attrs))
        except (subprocess.SubprocessError, ArithmeticError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"EXTRAS footage {path.name}: {error}") from error
    return entries


def browser(library, resources, resource_id, entries):
    if not entries:
        return
    if library.find(f"event[@name='{EVENT}']") is not None:
        raise ValueError("Edited First Cut already contains an EXTRAS event")
    event = ET.SubElement(library, "event", name=EVENT)
    for path, width, height, frame, duration, audio_attrs in entries:
        format_id, asset_id = resource_id(), resource_id()
        ET.SubElement(resources, "format", id=format_id, width=width, height=height, frameDuration=f"{frame}s")
        asset = ET.SubElement(resources, "asset", id=asset_id, name=path.stem, start="0s",
                              duration=f"{duration}s", hasVideo="1", videoSources="1", format=format_id, **audio_attrs)
        ET.SubElement(asset, "media-rep", kind="original-media", src=path.as_uri())
        ET.SubElement(event, "asset-clip", ref=asset_id, name=path.stem, start="0s", duration=f"{duration}s")


def validate(root, paths):
    events = root.findall(f"library/event[@name='{EVENT}']")
    if not paths and not events:
        return
    if not paths or len(events) != 1 or any(clip.tag != "asset-clip" for clip in events[0]):
        raise ValueError("Final Cut XML must contain a single browser-only EXTRAS event when footage is available")
    refs = [clip.get("ref") for clip in events[0]]
    ids = set(refs)
    assets = [asset for asset in root.findall("resources/asset") if asset.get("id") in ids]
    urls = [node.get("src", "") for asset in assets for node in asset.findall("media-rep[@kind='original-media']")]
    if (len(refs) != len(paths) or len(assets) != len(paths) or len(ids) != len(paths)
            or sorted(urls) != sorted(path.as_uri() for path in paths)):
        raise ValueError("EXTRAS event does not match the current footage inventory")
