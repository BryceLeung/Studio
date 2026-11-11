"""Install or check stage 3.2's isolated WhisperX tools without loading a model."""

import argparse
from importlib import import_module
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


ENVIRONMENT = Path(__file__).resolve().parents[1] / '.venv-tools'
PACKAGES = {
    'whisperx': '3.8.6',
    'torch': '2.8.0',
    'torchaudio': '2.8.0',
    'torchvision': '0.23.0',
}
if sys.platform == 'win32':
    PACKAGES['torchcodec'] = '0.7.0'
REQUIREMENTS = tuple(f'{name}=={pin}' for name, pin in PACKAGES.items())
REPAIR_REQUIREMENTS = ('RapidFuzz>=3,<4', 'num2words>=0.5.14,<0.6', 'python-docx>=1.1,<2')


def environment_python():
    return ENVIRONMENT / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def dependency_info():
    installed = {name: version(name) for name in PACKAGES}
    for name, pin in PACKAGES.items():
        if installed[name].split('+')[0] != pin:
            raise RuntimeError(f'{name} must be {pin}; found {installed[name]}')
    for name in ('torch', 'torchaudio', 'torchvision', 'whisperx.asr', 'whisperx.alignment', 'whisperx.diarize'):
        import_module(name)
    # Local repair rebuilds Takes headlessly in this same isolated environment.
    for distribution, module in (('RapidFuzz', 'rapidfuzz'), ('num2words', 'num2words'), ('python-docx', 'docx')):
        installed[distribution] = version(distribution)
        import_module(module)
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('WhisperX requires FFmpeg on PATH; install it before continuing')
    result = subprocess.run([ffmpeg, '-version'], check=True, capture_output=True, text=True)
    subprocess.run([sys.executable, '-m', 'whisperx', '--help'],
                   check=True, capture_output=True, text=True)
    return dict(installed, python=sys.version.split()[0], ffmpeg=ffmpeg,
                ffmpeg_version=result.stdout.splitlines()[0])


def probe_environment():
    python = str(environment_python())
    subprocess.run([python, '-m', 'pip', 'check'], check=True, capture_output=True, text=True)
    result = subprocess.run([python, str(Path(__file__).resolve()), '--probe'],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check without installing anything')
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.probe:
            print(json.dumps(dependency_info()))
            return 0
        if environment_python().is_file():
            try:
                info = probe_environment()
            except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError):
                if args.check:
                    raise
                print('Dependency check failed; installing pinned requirements', flush=True)
            else:
                print('already done')
                print(json.dumps(info, indent=2))
                return 0
        elif args.check:
            raise RuntimeError(f'Transcription dependencies are not installed at {ENVIRONMENT}')
        if not environment_python().is_file():
            if not (3, 10) <= sys.version_info[:2] < (3, 14):
                raise RuntimeError('WhisperX 3.8.6 requires Python 3.10 through 3.13; use 3.13')
            if ENVIRONMENT.exists():
                raise RuntimeError(f'Incomplete environment at {ENVIRONMENT}; inspect it before retrying')
            print(f'Creating {ENVIRONMENT}', flush=True)
            venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
        subprocess.run([str(environment_python()), '-m', 'pip', 'install', *REQUIREMENTS, *REPAIR_REQUIREMENTS], check=True)
        print(json.dumps(probe_environment(), indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, ImportError, subprocess.CalledProcessError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            for output in (error.stdout, error.stderr):
                if output:
                    print(output.strip(), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
