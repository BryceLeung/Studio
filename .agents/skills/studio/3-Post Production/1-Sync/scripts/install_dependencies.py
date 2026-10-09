"""Install the local Python environment used by Studio stage 3.1 Sync."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


WORKFLOW_ROOT = Path(__file__).resolve().parents[6]
ENVIRONMENT = WORKFLOW_ROOT / ".venv-sync"
BBC_REVISION = "6b8b12b04104e66d890f8feea080889aa775d9b7"
BBC_ARCHIVE = f"https://github.com/bbc/audio-offset-finder/archive/{BBC_REVISION}.zip"
BBC_SHA256 = "6f74eca0f7cfb7ffb2f6036792cc6531f9abb90aceaf83233a6517c82aab1403"
IMAGEIO_FFMPEG_VERSION = "0.6.0"
REQUIREMENTS = (
    f"audio-offset-finder @ {BBC_ARCHIVE}#sha256={BBC_SHA256}",
    f"imageio-ffmpeg=={IMAGEIO_FFMPEG_VERSION}",
)


def environment_python() -> Path:
    return ENVIRONMENT / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def dependency_info() -> dict:
    """Run inside the environment; validate the installed source and FFmpeg."""
    from importlib.metadata import distribution, version
    import imageio_ffmpeg

    distribution_info = distribution("audio-offset-finder")
    direct_url = json.loads(distribution_info.read_text("direct_url.json") or "{}")
    archive_hash = direct_url.get("archive_info", {}).get("hashes", {}).get("sha256")
    if direct_url.get("url") != BBC_ARCHIVE or archive_hash != BBC_SHA256:
        raise RuntimeError("audio-offset-finder is not the pinned BBC source revision")
    if version("imageio-ffmpeg") != IMAGEIO_FFMPEG_VERSION:
        raise RuntimeError("imageio-ffmpeg does not match the pinned version")
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve(strict=True)
    completed = subprocess.run(
        [str(ffmpeg), "-version"], check=True, capture_output=True, text=True,
    )
    return {
        "python_version": sys.version.split()[0],
        "audio_offset_finder_version": distribution_info.version,
        "audio_offset_finder_revision": BBC_REVISION,
        "imageio_ffmpeg_version": version("imageio-ffmpeg"),
        "numpy_version": version("numpy"),
        "scipy_version": version("scipy"),
        "librosa_version": version("librosa"),
        "ffmpeg_version": completed.stdout.splitlines()[0],
        "ffmpeg_executable": str(ffmpeg),
    }


def probe_environment() -> dict:
    """Check dependency consistency, pinned versions, and FFmpeg without installing."""
    subprocess.run(
        [str(environment_python()), "-m", "pip", "check"],
        check=True, capture_output=True, text=True,
    )
    code = (
        "import json, sys; "
        "sys.path.insert(0, sys.argv[1]); "
        "from install_dependencies import dependency_info; "
        "print(json.dumps(dependency_info()))"
    )
    completed = subprocess.run(
        [str(environment_python()), "-B", "-c", code, str(Path(__file__).resolve().parent)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Check the existing environment without installing packages",
    )
    args = parser.parse_args()

    try:
        if environment_python().is_file():
            try:
                probe_environment()
            except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError):
                if args.check:
                    raise
                print("Dependency check failed; installing required dependencies", flush=True)
            else:
                print("already done")
                return 0
        elif args.check:
            raise RuntimeError("Sync dependencies are not installed; run this script without --check")

        if not environment_python().is_file():
            if not (3, 10) <= sys.version_info[:2] <= (3, 14):
                raise RuntimeError("Use Python 3.10 through 3.14 (3.13 is recommended)")
            if ENVIRONMENT.exists():
                raise RuntimeError(
                    f"An incomplete environment exists at {ENVIRONMENT}; "
                    "repair or remove it explicitly before reinstalling"
                )
            print(f"Creating {ENVIRONMENT}", flush=True)
            venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
        python = str(environment_python())
        subprocess.run([python, "-m", "ensurepip", "--upgrade"], check=True)
        subprocess.run([python, "-m", "pip", "install", *REQUIREMENTS], check=True)
        info = probe_environment()
        print(f"Sync dependencies ready: {ENVIRONMENT}")
        print(json.dumps(info, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            for output in (error.stdout, error.stderr):
                if output:
                    print(output.strip(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
