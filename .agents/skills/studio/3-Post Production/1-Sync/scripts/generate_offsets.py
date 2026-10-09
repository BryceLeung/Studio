"""Generate one sync-offset JSON per Studio recording group, relative to camA."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from install_dependencies import ENVIRONMENT, dependency_info, environment_python


FOOTAGE_PARTS = ("2-Production", "1-Footage Intake")
OUTPUT_PARTS = ("3-Post Production", "1-Sync")
ROLES = ("camA", "camB", "lav")
EXTENSIONS = {"camA": {"mov", "mp4"}, "camB": {"mov", "mp4"}, "lav": {"wav"}}
FILENAME = re.compile(
    r"^(?P<shot>(?P<script>.+)__C(?P<clip>\d{2}))__"
    r"(?P<role>camA|camB|lav)\.(?P<ext>[^.]+)$"
)
SAMPLE_RATE = 8000
HOP_LENGTH = 32
MAX_FRAMES = 2000
OFFSET_CONVENTION = "positive_means_file_starts_later_than_camA"


def discover_shots(footage: Path, selected: list[str] | None) -> dict:
    """Require exactly one non-empty file for each role in each selected group."""
    groups: dict[str, dict[str, Path]] = {}
    problems = []
    for path in sorted(footage.iterdir()):
        if not path.is_file():
            continue
        match = FILENAME.fullmatch(path.name)
        if not match or match["ext"].lower() not in EXTENSIONS[match["role"]]:
            continue
        shot, role = match["shot"], match["role"]
        if selected and shot not in selected:
            continue
        group = groups.setdefault(shot, {})
        if role in group:
            problems.append(f"{shot}: duplicate {role} files: {group[role].name}, {path.name}")
        group[role] = path
        if path.stat().st_size == 0:
            problems.append(f"{path.name}: file is empty")
    for shot, group in groups.items():
        missing = [role for role in ROLES if role not in group]
        if missing:
            problems.append(f"{shot}: missing {', '.join(missing)}")
    for shot in selected or []:
        if shot not in groups:
            problems.append(f"No footage found for {shot}")
    if not groups and not problems:
        problems.append(f"No camA/camB/lav recording groups found in {footage}")
    if problems:
        raise ValueError("\n".join(problems))
    return groups


def file_identity(path: Path, project: Path) -> dict:
    stat = path.stat()
    return {
        "path": path.relative_to(project).as_posix(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def generator_identity(dependencies: dict) -> dict:
    """Record numerical settings and versions so stale results are detectable."""
    return {
        "algorithm": "bbc/audio-offset-finder",
        "sample_rate_hz": SAMPLE_RATE,
        "hop_length_samples": HOP_LENGTH,
        "search_interval_ms": HOP_LENGTH * 1000 // SAMPLE_RATE,
        "max_correlation_frames": MAX_FRAMES,
        "dependencies": {k: v for k, v in dependencies.items() if k != "ffmpeg_executable"},
        "scripts_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("install_dependencies.py", "generate_offsets.py")
        },
    }


def reusable(output: Path, expected: dict) -> bool:
    """Preserve existing estimates only when inputs, settings, and schema match."""
    try:
        result = json.loads(output.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            return False
        for key, value in expected.items():
            if key != "clips" and result.get(key) != value:
                return False
        clips = result.get("clips", {})
        if not isinstance(clips, dict) or set(clips) != set(ROLES):
            return False
        for role, identity in expected["clips"].items():
            clip = clips[role]
            if not isinstance(clip, dict) or any(clip.get(k) != v for k, v in identity.items()):
                return False
            if type(clip.get("offset_ms")) is not int:
                return False
            score = clip.get("standard_score")
            if role == "camA":
                if clip["offset_ms"] != 0 or score is not None:
                    return False
            elif type(score) not in (int, float) or not math.isfinite(score) or score <= 0:
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def decode_audio(path: Path, ffmpeg: str):
    """Read the first audio stream once, without writing decoded media to disk."""
    import numpy as np

    command = [
        ffmpeg, "-nostdin", "-loglevel", "error", "-i", str(path),
        "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-acodec", "pcm_s16le", "-f", "s16le", "pipe:1",
    ]
    completed = subprocess.run(command, capture_output=True)
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg could not read audio from {path.name}: {message}")
    audio = np.frombuffer(completed.stdout, dtype="<i2").astype(float)
    if audio.size == 0 or np.std(audio) < 1e-10:
        raise ValueError(f"{path.name}: audio is empty or has no variation")
    return audio


def measure_shot(files: dict, ffmpeg: str) -> dict:
    import numpy as np
    from audio_offset_finder.audio_offset_finder import find_offset_between_buffers

    reference = decode_audio(files["camA"], ffmpeg)
    estimates = {"camA": {"offset_ms": 0, "standard_score": None}}
    for role in ("camB", "lav"):
        target = decode_audio(files[role], ffmpeg)
        result = find_offset_between_buffers(
            reference, target, SAMPLE_RATE, hop_length=HOP_LENGTH, max_frames=MAX_FRAMES,
        )
        offset = float(result["time_offset"])
        score = float(result["standard_score"])
        correlation = result["correlation"]
        if (
            not math.isfinite(offset) or not math.isfinite(score) or score <= 0
            or not np.all(np.isfinite(correlation)) or np.std(correlation) < 1e-10
        ):
            raise ValueError(f"{files[role].name}: no usable correlation peak")
        estimates[role] = {"offset_ms": round(offset * 1000), "standard_score": score}
        print(f"  {role}: {estimates[role]['offset_ms']:+d} ms; score {score:.2f}", flush=True)
    return estimates


def write_json(output: Path, result: dict, overwrite: bool) -> None:
    """Refuse existing files unless explicitly allowed to replace them."""
    content = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if not overwrite:
        created = False
        try:
            with output.open("x", encoding="utf-8", newline="\n") as handle:
                created = True
                handle.write(content)
        except OSError:
            if created:
                output.unlink(missing_ok=True)
            raise
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output.parent,
            prefix=f".{output.stem}-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Studio project folder")
    parser.add_argument(
        "--shot", action="append", help="Only process this exact group name; repeat for several groups",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Explicitly replace existing JSON files, including matching or edited results",
    )
    args = parser.parse_args()

    # Bootstrap from the checked-in command without requiring manual activation.
    if Path(sys.prefix).resolve() != ENVIRONMENT.resolve():
        if not environment_python().is_file():
            print("FAIL: run scripts/install_dependencies.py for stage 3.1 first", file=sys.stderr)
            return 1
        return subprocess.run(
            [str(environment_python()), str(Path(__file__).resolve()), *sys.argv[1:]],
        ).returncode

    try:
        project = args.project.resolve(strict=True)
        footage = project.joinpath(*FOOTAGE_PARTS)
        if not footage.is_dir():
            raise ValueError(f"Footage folder does not exist: {footage}")
        groups = discover_shots(footage, args.shot)
        dependencies = dependency_info()
        generator = generator_identity(dependencies)
        destination = project.joinpath(*OUTPUT_PARTS)
        destination.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError, ValueError, ImportError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    failures = 0
    for shot, files in groups.items():
        output = destination / f"{shot}.json"
        try:
            expected = {
                "schema_version": 1,
                "shot": shot,
                "reference_role": "camA",
                "offset_convention": OFFSET_CONVENTION,
                "generator": generator,
                "clips": {role: file_identity(files[role], project) for role in ROLES},
            }
            if output.exists() and not args.overwrite:
                if reusable(output, expected):
                    print(f"KEEP {output.name} (matching inputs and settings)", flush=True)
                    continue
                raise ValueError(f"{output.name} exists but is stale or invalid; inspect it before using --overwrite")
            print(f"Syncing {shot}", flush=True)
            estimates = measure_shot(files, dependencies["ffmpeg_executable"])
            # Do not publish results if the media changed while it was being read.
            if expected["clips"] != {role: file_identity(files[role], project) for role in ROLES}:
                raise ValueError("Source media changed during synchronization; rerun this group")
            result = {**expected, "generated_at_utc": datetime.now(timezone.utc).isoformat()}
            result["clips"] = {
                role: {**expected["clips"][role], **estimates[role]} for role in ROLES
            }
            write_json(output, result, args.overwrite)
            print(f"WROTE {output}", flush=True)
        except Exception as error:
            # A failed group must not prevent independent groups from being attempted.
            failures += 1
            print(f"FAIL {shot}: {error}", file=sys.stderr, flush=True)
    print(f"Sync: {len(groups) - failures}/{len(groups)} recording groups ready")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
