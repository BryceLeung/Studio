"""Append-only animation requests, reproducible render candidates and human approvals."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
from http.client import HTTPConnection, HTTPException
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import webbrowser
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
from scripts import stage_completion as completion
from scripts.stage_invalidation import invalidate
from scripts.validation import audit, require_prerequisites

STEP = '2.3'
STAGE = Path('2-Production/3-Animations')
LOCAL = Path('.agents/skills/studio') / STAGE
RENDERER = ROOT / LOCAL / 'renderer'
REGISTRY = Path('1-Pre Production/4-Asset Breakdown')
DEFAULT_SETTINGS = {'width': 1920, 'height': 1080, 'fps': 30, 'durationInFrames': 150}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'Duplicate JSON field: {key}')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs)


def confined(root, relative):
    root = Path(root).resolve(strict=True)
    path = root / completion.relative_path(str(relative))
    require(path.resolve().is_relative_to(root), f'Path escapes root: {relative}')
    current = path
    while current != root:
        redirected = current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction())
        require(not redirected, f'Redirected path: {current}')
        current = current.parent
    return path


def files(root, relative):
    directory = confined(root, str(relative))
    if not directory.exists():
        return []
    require(directory.is_dir(), f'Expected directory: {directory}')
    result = []
    for child in sorted(directory.iterdir()):
        confined(root, child.relative_to(root).as_posix())
        if child.is_dir():
            result.extend(files(root, child.relative_to(root).as_posix()))
        else:
            require(child.is_file(), f'Expected regular file: {child}')
            result.append(child)
    return result


def atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.animation-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def registry_paths(project):
    result = [confined(project, (REGISTRY / name).as_posix())
              for name in ('Asset Registry.md', 'Asset Registry.json')]
    require(all(path.is_file() for path in result), 'Both canonical registry files are required')
    return result


def registry_helper():
    return module(ROOT / '.agents/skills/studio/1-Pre Production/4-Asset Breakdown/scripts/assets.py',
                  'studio_animation_registry')


def eligible_rows(entries):
    return {row['ID']: row for _, row in entries if row['Active'] == 'Yes'
            and row['Review'] == 'Keep' and row['Type'] in ('Graphic', 'Animation')}


def registry_context(row):
    return {key: row[key] for key in
            ('ID', 'Script', 'Passage', 'Start', 'End', 'Extracts', 'Brief', 'Notes', 'Type')}


def eligible(project):
    registry_paths(project)
    helper = registry_helper()
    return {asset: registry_context(row) for asset, row in
            eligible_rows(helper.rows(helper.load(project))[1]).items()}


def folder(project, asset):
    require(isinstance(asset, str) and re.fullmatch(r'(?:A(?:0[1-9]|[1-9][0-9])|ASSET-\d{6,})', asset), 'Invalid ASSET ID')
    return confined(project, (STAGE / asset).as_posix())


def settings(value):
    require(isinstance(value, dict) and set(value) in
            ({'width', 'height', 'fps', 'durationInFrames'},
             {'width', 'height', 'fps', 'durationInFrames', 'props'}), 'Invalid render settings fields')
    for key in ('width', 'height'):
        require(type(value[key]) is int and 16 <= value[key] <= 7680 and value[key] % 2 == 0,
                f'Invalid {key}: expected an even integer from 16 to 7680')
    require(type(value['durationInFrames']) is int and 1 <= value['durationInFrames'] <= 108000,
            'Invalid durationInFrames')
    require(type(value['fps']) in (int, float) and math.isfinite(value['fps']) and 1 <= value['fps'] <= 120,
            'Invalid fps')
    require('props' not in value or isinstance(value['props'], dict), 'props must be an object')
    digest(value)
    return value


def renderer_identity():
    confined(ROOT, (LOCAL / 'renderer').as_posix())
    excluded = {'node_modules', '.git', 'dist', 'build', '.cache'}
    paths = []
    def walk(directory):
        for child in sorted(directory.iterdir()):
            if child.name in excluded:
                continue
            confined(RENDERER, child.relative_to(RENDERER).as_posix())
            if child.is_dir():
                walk(child)
            else:
                require(child.is_file(), 'Renderer contains a non-file')
                paths.append(child)
    walk(RENDERER)
    require(all((RENDERER / name) in paths for name in ('render.mjs', 'probe.mjs')),
            'Missing maintained renderer or probe')
    return {p.relative_to(RENDERER).as_posix(): sha(p) for p in paths}


def load(project, asset):
    directory = folder(project, asset)
    ledger = read_json(confined(project, (STAGE / asset / 'Animation.json').as_posix()))
    require(ledger.get('schema') == 'studio.animation' and ledger.get('schema_version') == 1
            and ledger.get('asset') == asset, 'Invalid animation ledger identity/schema')
    require(isinstance(ledger.get('requests'), list) and ledger['requests'], 'Missing request history')
    expected = set()
    for version, request in enumerate(ledger['requests'], 1):
        name = f'request.{version:02d}.md'
        path = confined(project, (STAGE / asset / name).as_posix())
        require(request['version'] == version and request['registry']['ID'] == asset
                and path.is_file() and sha(path) == request['sha256'], 'Request history changed or interrupted')
        expected.add(name)
    actual = {p.name for p in directory.iterdir() if p.name.startswith('request.')}
    require(actual == expected, 'Unregistered request files; inspect before retrying')
    require(isinstance(ledger.get('renders'), list), 'Invalid render history')
    for version, render in enumerate(ledger['renders'], 1):
        require(render['version'] == version and render['status'] in ('rendering', 'failed', 'ready'),
                'Invalid render sequence/status')
        require(isinstance(render.get('approvals', []), list), 'Invalid approval history')
        for approval in render.get('approvals', []):
            require(render['status'] == 'ready' and approval['sha256'] == render['sha256']
                    and approval['registry']['ID'] == asset
                    and isinstance(approval['feedback'], str) and approval['feedback'].strip()
                    and isinstance(approval['approved_at'], str) and approval['approved_at'],
                    'Invalid approval record')
    return ledger


def current_input(project, asset, ledger, row):
    directory = folder(project, asset)
    require(ledger['requests'][-1]['registry'] == row,
            f'{asset}: registry context changed; record a new chat revision')
    source = files(project, (STAGE / asset / 'src').as_posix())
    require(directory / 'src/Composition.tsx' in source, 'Missing editable src/Composition.tsx')
    resources = files(project, (STAGE / asset / 'references').as_posix())
    setting_path = confined(project, (STAGE / asset / 'settings.json').as_posix())
    settings(read_json(setting_path))
    studio_paths = [confined(project, (STAGE / asset / name).as_posix())
                    for name in ('src/Studio.tsx', 'package.json', 'tsconfig.json')]
    require(all(path.is_file() for path in studio_paths),
            f'{asset}: missing required Studio entry/configuration')
    config = [path for path in studio_paths[1:] if path.is_file()]
    return {'registry': row, 'requests': [r['sha256'] for r in ledger['requests']],
            'files': {p.relative_to(directory).as_posix(): sha(p)
                      for p in source + resources + [setting_path] + config}, 'renderer': renderer_identity()}


def log(project, title, message):
    helper = module(ROOT / 'scripts/append-log.py', 'studio_animation_log')
    path = confined(project, (STAGE / 'LOG.md').as_posix())
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open('x', encoding='utf-8') as stream:
            stream.write('# Animations Log\n')
    helper.append_entry(path, title, [message], date.today())


def transition(project, reason):
    invalidate(project, ROOT, STEP, reason)


def studio_files(asset, value):
    """Portable asset files, with no per-asset dependencies or machine paths."""
    template = confined(ROOT, (LOCAL / 'renderer/template/Studio.tsx').as_posix())
    entry = template.read_text(encoding='utf-8')
    for key in DEFAULT_SETTINGS:
        entry = entry.replace(f'{key}={{{DEFAULT_SETTINGS[key]}}}', f'{key}={{{value[key]}}}')
    configuration = confined(ROOT, (LOCAL / 'renderer/template/tsconfig.json').as_posix())
    return {'src/Studio.tsx': entry.encode('utf-8'),
            'tsconfig.json': configuration.read_bytes(),
            'package.json': (json.dumps({'name': f'studio-{asset.lower()}', 'private': True,
                                         'version': '0.0.0'}, indent=2) + '\n').encode('utf-8')}


def init(project, asset):
    require_prerequisites(project, ROOT, STEP)
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, STEP)
        rows = eligible(project)
        require(asset in rows, 'Select a current Graphic or Animation asset')
        directory = folder(project, asset)
        if directory.exists() and (directory / 'Animation.json').exists():
            ledger = load(project, asset)
            current_input(project, asset, ledger, rows[asset])
            sketches = confined(project, (STAGE / asset / 'sketches').as_posix())
            if not sketches.exists():
                transition(project, f'Create sketches directory for animation {asset}')
                sketches.mkdir()
                log(project, 'Sketches directory created', f'{asset}: missing sketches directory added; existing work preserved.')
            require(sketches.is_dir(), f'Expected sketches directory: {sketches}')
            return {'asset': asset, 'status': 'reused', 'stage': audit(project, ROOT, STEP)[STEP]}
        template = confined(ROOT, (LOCAL / 'renderer/template/Composition.tsx').as_posix())
        require(template.is_file(), 'Missing maintained editable composition template')
        expected = {'src/Composition.tsx': template.read_bytes(),
                    'settings.json': (json.dumps(DEFAULT_SETTINGS, indent=2) + '\n').encode('utf-8'),
                    **studio_files(asset, DEFAULT_SETTINGS)}
        recovering = directory.exists()
        if recovering:
            # Only finish an untouched scaffold interrupted before request/ledger publication.
            # Never infer provenance for authored source, media, or an existing request.
            actual = {p.relative_to(directory).as_posix()
                      for p in files(project, (STAGE / asset).as_posix())}
            require({'src/Composition.tsx', 'settings.json'} <= actual <= set(expected)
                    and all((directory / name).read_bytes() == expected[name] for name in actual)
                    and {p.name for p in directory.iterdir() if p.is_dir()}
                         in ({'src', 'references', 'renders', 'snapshots', 'APPROVED'},
                             {'src', 'references', 'renders', 'snapshots', 'APPROVED', 'sketches'}),
                    'Interrupted initialization contains edited or unexpected files; preserve and inspect before recovery')
        transition(project, f'Initialize animation {asset}')
        if not recovering:
            directory.mkdir(parents=True)
            for name in ('src', 'references', 'renders', 'snapshots', 'APPROVED'):
                (directory / name).mkdir()
        (directory / 'sketches').mkdir(exist_ok=True)
        for relative, contents in expected.items():
            path = directory / relative
            if not path.exists():
                with path.open('xb') as stream:
                    stream.write(contents)
        text = '# Initial Approved Registry Context\n\n' + json.dumps(rows[asset], indent=2) + '\n'
        (directory / 'request.01.md').write_text(text, encoding='utf-8', newline='\n')
        ledger = {'schema': 'studio.animation', 'schema_version': 1, 'asset': asset,
                  'studio_ready': True, 'requests': [{'version': 1, 'registry': rows[asset],
                                'sha256': sha(directory / 'request.01.md')}], 'renders': []}
        atomic_json(directory / 'Animation.json', ledger)
        log(project, 'Animation initialized', f'{asset}: approved brief/passage retained; Studio entry, named editable scaffold and portable project config created. Author the approved visual before rendering. Human approval remains pending.')
        return {'asset': asset, 'status': 'initialized'}


def studio_url(directory, port):
    """Check the live Remotion identity rather than trusting a cached port."""
    for host in ('127.0.0.1', '::1'):
        connection = HTTPConnection(host, port, timeout=0.25)
        try:
            connection.request('GET', '/__remotion_config')
            response = connection.getresponse()
            if response.status == 200:
                data = json.loads(response.read(8192))
                if (isinstance(data, dict) and data.get('isRemotion') is True
                        and isinstance(data.get('cwd'), str)
                        and Path(data['cwd']).resolve() == directory):
                    address = f'[{host}]' if ':' in host else host
                    return f'http://{address}:{port}'
        except (OSError, HTTPException, ValueError, RuntimeError):
            pass
        finally:
            connection.close()
    return None


def serve_studio(directory, port, no_open):
    directory = directory.resolve(strict=True)
    cache = Path.home() / '.cache/studio-remotion' / digest(os.path.normcase(str(directory)))
    cache.mkdir(parents=True, exist_ok=True)
    saved = cache / 'port'
    child = None
    try:
        # Lock only discovery and startup, never the life of the editable server.
        with completion.mutation_guard(cache):
            try:
                previous = int(saved.read_text(encoding='ascii'))
            except (FileNotFoundError, ValueError):
                previous = None
            # Include the pinned Remotion default range for existing manual launches.
            probes = dict.fromkeys((previous, port, *range(3000, 3101)))
            for candidate in probes:
                if candidate is not None and 1 <= candidate <= 65535:
                    url = studio_url(directory, candidate)
                    if url:
                        saved.write_text(str(candidate), encoding='ascii')
                        break
            else:
                if port is None:
                    with socket.socket() as probe:
                        probe.bind(('127.0.0.1', 0))
                        port = probe.getsockname()[1]
                command = ['node', str(RENDERER / 'studio.mjs'), '--source-dir', str(directory / 'src'),
                           '--public-dir', str(directory / 'references'), '--port', str(port), '--no-open']
                child = subprocess.Popen(command, cwd=RENDERER)
                deadline = time.monotonic() + 30
                while not (url := studio_url(directory, port)):
                    code = child.poll()
                    if code is not None:
                        raise ValueError(f'Remotion Studio exited ({code}) before serving {directory} on port {port}')
                    if time.monotonic() >= deadline:
                        raise ValueError(f'Remotion Studio did not serve {directory} on port {port} within 30 seconds')
                    time.sleep(0.1)
                saved.write_text(str(port), encoding='ascii')
        print(f'Studio {"ready" if child is not None else "reused"}: {url} ({directory})', flush=True)
        if not no_open:
            webbrowser.open(url)
        if child is not None:
            code = child.wait()
            if code:
                raise subprocess.CalledProcessError(code, command)
        return url, child is None
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait()


def studio(project, asset, port=None, no_open=False):
    require_prerequisites(project, ROOT, STEP)
    require(port is None or type(port) is int and 1 <= port <= 65535, 'Invalid Studio port')
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, STEP)
        rows = eligible(project)
        require(asset in rows, 'Select a current Graphic or Animation asset')
        ledger = load(project, asset)
        require(ledger['requests'][-1]['registry'] == rows[asset], 'Registry context changed; record a revision first')
        directory = folder(project, asset)
        current_input(project, asset, ledger, rows[asset])
    url, reused = serve_studio(directory, port, no_open)
    return {'asset': asset, 'status': 'studio reused' if reused else 'studio closed', 'url': url}


def revise(project, asset, text):
    require_prerequisites(project, ROOT, STEP)
    require(isinstance(text, str) and text.strip(), 'Revision requires verbatim nonempty chat text')
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, STEP)
        rows = eligible(project)
        require(asset in rows, 'Asset is no longer selected')
        ledger = load(project, asset)
        version = len(ledger['requests']) + 1
        path = confined(project, (STAGE / asset / f'request.{version:02d}.md').as_posix())
        require(not path.exists(), 'Revision path already exists')
        transition(project, f'Animation {asset} chat request revision {version}')
        with path.open('x', encoding='utf-8', newline='') as stream:
            stream.write(text)
        ledger['requests'].append({'version': version, 'registry': rows[asset], 'sha256': sha(path)})
        atomic_json(folder(project, asset) / 'Animation.json', ledger)
        log(project, 'Animation revision', f'{asset}: preserved verbatim request {version}; earlier candidates and approvals retained. Creative revisions do not revoke approvals; changed registry context requires review.')
        return {'asset': asset, 'request': version}


def probe(path):
    try:
        result = subprocess.run(['node', str(RENDERER / 'probe.mjs'), str(path)], cwd=RENDERER,
                                check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'Video probe failed: {error.stderr or error}') from error
    return json.loads(result.stdout)


def probe_editorial(path, ffprobe):
    try:
        result = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0',
                                 '-show_streams', '-show_format', '-of', 'json', str(path)],
                                check=True, capture_output=True, text=True, encoding='utf-8', timeout=120)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'Video probe failed: {(error.stderr or str(error)).strip()}') from error
    data = json.loads(result.stdout)
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    require(video is not None, 'File does not contain a usable video stream')
    if 'duration_ts' in video:
        duration = Fraction(video['duration_ts']) * Fraction(video['time_base'])
    else:
        if 'duration' not in video:
            video['duration'] = data['format']['duration']
        duration = Fraction(video['duration'])
    rate = video.get('avg_frame_rate')
    fps = (Fraction(rate) if rate and rate != '0/0' else 0) or Fraction(video['r_frame_rate'])
    return video, float(fps), float(duration)


def valid_video(path, value, *, ffprobe=None):
    require(path.is_file() and path.stat().st_size > 0, f'Missing/empty video: {path}')
    if ffprobe is None:
        metadata = probe(path)
        fps, duration = float(metadata['fps']), float(metadata['durationInSeconds'])
    else:
        metadata, fps, duration = probe_editorial(path, ffprobe)
    require(metadata['width'] == value['width'] and metadata['height'] == value['height'],
            'Video dimensions differ from render settings')
    require(abs(fps - value['fps']) < 0.01, 'Video fps differs from render settings')
    require(math.isfinite(duration) and duration > 0
            and abs(duration - value['durationInFrames'] / value['fps']) <= 1.1 / value['fps'],
            'Video duration differs from render settings')
    return metadata


def run_renderer(snapshot, output):
    try:
        result = subprocess.run(['node', str(RENDERER / 'render.mjs'), '--source-dir', str(snapshot / 'src'),
                                 '--public-dir', str(snapshot / 'references'), '--settings',
                                 str(snapshot / 'settings.json'), '--output', str(output)],
                                cwd=RENDERER, check=True, capture_output=True, text=True, timeout=3600)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'Animation renderer failed: {error.stderr or error}') from error
    summary = json.loads(result.stdout)
    require(isinstance(summary, dict), 'Invalid renderer JSON summary')
    return summary


def inspect_settings(directory):
    try:
        result = subprocess.run(['node', str(RENDERER / 'render.mjs'), '--inspect',
                                '--source-dir', str(directory / 'src'), '--public-dir',
                                str(directory / 'references'), '--settings', str(directory / 'settings.json')],
                                cwd=RENDERER, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'Studio metadata inspection failed: {error.stderr or error}') from error
    return settings(json.loads(result.stdout))


def approved_videos(project, asset):
    directory = confined(project, (STAGE / asset / 'APPROVED').as_posix())
    require(directory.is_dir(), f'{asset}: missing APPROVED folder')
    result = []
    for child in sorted(directory.iterdir()):
        require_approved_file(project, asset, child)
        result.append(child)
    return result


def require_approved_file(project, asset, path):
    confined(project, path.relative_to(project).as_posix())
    require(path.is_file() and path.suffix.lower() == '.mp4',
            f'{asset}: APPROVED permits only MP4 files directly inside the folder')


def matching_renders(ledger, identity, size):
    matches = [record for record in ledger['renders'] if record['status'] == 'ready'
               and record['sha256'] == identity and record['size'] == size]
    require(matches, f'{ledger["asset"]}: approved video is unregistered')
    return matches


def accepted_renders(matches):
    # Explicit approvals supersede manual moves; without one, only the earliest
    # render can establish the context of these exact bytes.
    return [record for record in matches if record.get('approvals')] or matches[:1]


def approval_context(asset, record):
    approvals = record.get('approvals', [])
    require(not approvals or approvals[-1].get('logged', False),
            f'{asset}: approval publication unfinished; retry approve for version {record["version"]}')
    return approvals[-1]['registry'] if approvals else record['input']['registry']


def snapshot_settings(directory, record):
    return settings(read_json(directory / 'snapshots' / f'v{record["version"]:03d}' / 'settings.json'))


def validate_render(project, asset, record):
    directory = folder(project, asset)
    name = f'v{record["version"]:03d}'
    snapshot = directory / 'snapshots' / name
    actual = {p.relative_to(snapshot).as_posix(): sha(p)
              for p in files(project, snapshot.relative_to(project).as_posix())}
    require(actual == record['snapshot'], f'{asset}: changed render snapshot {name}')
    require(digest(record['input']) == record['input_sha256'], 'Changed render provenance')
    expected = dict(record['input']['files'])
    expected.update({f'request.{n:02d}.md': value
                     for n, value in enumerate(record['input']['requests'], 1)})
    require(all(actual.get(key) == value for key, value in expected.items()),
            'Snapshot differs from registered render inputs')
    require(read_json(snapshot / 'context.json') == record['input']['registry']
            and read_json(snapshot / 'renderer.json') == record['input']['renderer'],
            'Snapshot context or renderer differs from provenance')
    candidate = confined(project, (STAGE / asset / 'renders' / f'{name}.mp4').as_posix())
    require(not candidate.exists() or (sha(candidate) == record['sha256']
            and candidate.stat().st_size == record['size']), 'Registered candidate changed')
    return candidate


def approve(project, asset, version, text):
    require_prerequisites(project, ROOT, STEP)
    require(type(version) is int and version > 0, 'Approval requires an explicit positive render version')
    require(isinstance(text, str) and text.strip(), 'Approval requires verbatim nonempty human feedback')
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, STEP)
        rows = eligible(project)
        require(asset in rows, 'Asset is no longer selected')
        ledger = load(project, asset)
        require(all(r['status'] != 'rendering' for r in ledger['renders']), 'Render active or interrupted')
        require(version <= len(ledger['renders']), 'Unregistered render version')
        record = ledger['renders'][version - 1]
        require(record['status'] == 'ready', 'Only a ready registered full-animation render can be approved')
        source = validate_render(project, asset, record)
        matches = [p for p in approved_videos(project, asset)
                   if sha(p) == record['sha256'] and p.stat().st_size == record['size']]
        require(len(matches) <= 1, 'Duplicate approved render identity')
        target = matches[0] if matches else confined(project, (STAGE / asset / 'APPROVED' / f'v{version:03d}.mp4').as_posix())
        require(matches or not target.exists(), 'Approval destination exists; refusing replacement')
        video = target if matches else source
        require(video.is_file() and sha(video) == record['sha256']
                and video.stat().st_size == record['size'], 'Missing or changed registered render')
        valid_video(video, snapshot_settings(folder(project, asset), record))
        approvals = record.setdefault('approvals', [])
        continuing = bool(matches and approvals and approvals[-1]['registry'] == rows[asset])
        if continuing and approvals[-1].get('logged', False):
            return {'asset': asset, 'status': 'already approved', 'approved': target.relative_to(project).as_posix()}
        transition(project, f'Approve animation {asset} v{version:03d}')
        if not matches:
            descriptor, temporary = tempfile.mkstemp(prefix='.approval-', dir=folder(project, asset))
            temporary = Path(temporary)
            try:
                with os.fdopen(descriptor, 'wb') as output, source.open('rb') as stream:
                    shutil.copyfileobj(stream, output)
                    output.flush()
                    os.fsync(output.fileno())
                require(sha(temporary) == record['sha256'] and temporary.stat().st_size == record['size'],
                        'Render changed while publishing approval; retry after editing stops')
                # Atomic no-clobber publication; removing the temporary name leaves an independent file.
                os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        if not continuing:
            approvals.append({'approved_at': datetime.now(timezone.utc).isoformat(), 'feedback': text,
                              'registry': rows[asset], 'sha256': record['sha256'],
                              'path': target.relative_to(project).as_posix(), 'logged': False})
            atomic_json(folder(project, asset) / 'Animation.json', ledger)
        if source.exists():
            source.unlink()
        log(project, 'Animation approved', f'{asset} v{version:03d}: {approvals[-1]["feedback"]}\nApproved file: {target.relative_to(project).as_posix()}. Other approved versions preserved.')
        approvals[-1]['logged'] = True
        atomic_json(folder(project, asset) / 'Animation.json', ledger)
        return {'asset': asset, 'status': 'approved', 'approved': target.relative_to(project).as_posix()}


def render(project, asset):
    require_prerequisites(project, ROOT, STEP)
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, STEP)
        rows = eligible(project)
        require(asset in rows, 'Asset is no longer selected')
        ledger = load(project, asset)
        require(all(r['status'] != 'rendering' for r in ledger['renders']),
                'Render active or interrupted; inspect Animation.json before recovery')
        provenance = current_input(project, asset, ledger, rows[asset])
        directory = folder(project, asset)
        for record in reversed(ledger['renders']):
            if record['status'] != 'ready' or record['input'] != provenance:
                continue
            candidate = validate_render(project, asset, record)
            candidates = [candidate] if candidate.is_file() else approved_videos(project, asset)
            for candidate in candidates:
                if sha(candidate) != record['sha256'] or candidate.stat().st_size != record['size']:
                    continue
                valid_video(candidate, settings(read_json(directory / 'settings.json')))
                return {'asset': asset, 'status': 'reused',
                        'candidate': candidate.relative_to(project).as_posix(), 'sha256': record['sha256'],
                        'stage': audit(project, ROOT, STEP)[STEP]}
        transition(project, f'Resolve Studio metadata for {asset}')
        try:
            resolved = inspect_settings(directory)
            require(current_input(project, asset, load(project, asset), eligible(project)[asset]) == provenance,
                    'Animation inputs changed during Studio metadata inspection; retry after editing stops')
            value = settings(read_json(directory / 'settings.json'))
            updated = dict(value, **{key: resolved[key] for key in DEFAULT_SETTINGS})
            if value != updated:
                atomic_json(directory / 'settings.json', updated)
                log(project, 'Studio metadata captured', f'{asset}: captured width, height, fps and duration from the same Studio composition used for candidate rendering.')
            provenance = current_input(project, asset, ledger, rows[asset])
        except Exception as error:
            log(project, 'Studio metadata failed', f'{asset}: {error}. Source, candidates and approvals preserved; no new candidate registered.')
            raise
        version = len(ledger['renders']) + 1
        name = f'v{version:03d}'
        snapshot = confined(project, (STAGE / asset / 'snapshots' / name).as_posix())
        output = confined(project, (STAGE / asset / 'renders' / f'{name}.mp4').as_posix())
        require(not snapshot.exists() and not output.exists(), 'Render version exists; refusing replacement')
        transition(project, f'Render animation {asset} {name}')
        record = {'version': version, 'status': 'rendering', 'input': provenance,
                  'input_sha256': digest(provenance), 'started_at': datetime.now(timezone.utc).isoformat()}
        ledger['renders'].append(record)
        atomic_json(directory / 'Animation.json', ledger)
        try:
            snapshot.mkdir()
            (snapshot / 'src').mkdir()
            (snapshot / 'references').mkdir()
            for relative in provenance['files']:
                source = confined(project, (STAGE / asset / relative).as_posix())
                target = snapshot / completion.relative_path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                require(sha(target) == provenance['files'][relative], 'Input changed while snapshotting')
            for request in range(1, len(ledger['requests']) + 1):
                shutil.copyfile(directory / f'request.{request:02d}.md', snapshot / f'request.{request:02d}.md')
            atomic_json(snapshot / 'context.json', provenance['registry'])
            atomic_json(snapshot / 'renderer.json', provenance['renderer'])
            record['snapshot'] = {p.relative_to(snapshot).as_posix(): sha(p)
                                  for p in files(project, snapshot.relative_to(project).as_posix())}
            atomic_json(directory / 'Animation.json', ledger)
            summary = run_renderer(snapshot, output)
            valid_video(output, settings(read_json(snapshot / 'settings.json')))
            require(current_input(project, asset, load(project, asset), eligible(project)[asset]) == provenance,
                    'Animation inputs changed during rendering; candidate cannot be current')
            record.update(status='ready', sha256=sha(output), size=output.stat().st_size,
                          summary={key: summary[key] for key in DEFAULT_SETTINGS if key in summary})
        except Exception as error:
            record.update(status='failed', error=str(error))
            atomic_json(directory / 'Animation.json', ledger)
            log(project, 'Animation render failed', f'{asset} {name}: {error}. Retained source snapshot and any partial output; no approval changed.')
            raise
        atomic_json(directory / 'Animation.json', ledger)
        log(project, 'Animation candidate rendered', f'{asset} {name}: candidate ready for human review; approve the selected full-animation version through chat feedback.')
        return {'asset': asset, 'status': 'rendered',
                'candidate': output.relative_to(project).as_posix(), 'sha256': record['sha256']}


def inventory(project):
    directory = confined(project, STAGE.as_posix())
    if not directory.exists():
        return []
    result = []
    for child in sorted(directory.iterdir()):
        confined(project, child.relative_to(project).as_posix())
        if child.name in ('STALE', 'DONE', 'LOG.md', '.DONE.lock'):
            continue
        require(child.is_dir() and re.fullmatch(r'(?:A(?:0[1-9]|[1-9][0-9])|ASSET-\d{6,})', child.name),
                f'Unexpected animation stage entry: {child.name}')
        result.extend(files(project, child.relative_to(project).as_posix()))
    return result


def check(project):
    """Optional diagnostic for registered render provenance, not completion."""
    rows = eligible(project)
    inventory(project)
    stage = confined(project, STAGE.as_posix())
    if stage.exists():
        for child in stage.iterdir():
            if re.fullmatch(r'(?:A(?:0[1-9]|[1-9][0-9])|ASSET-\d{6,})', child.name):
                ledger = load(project, child.name)
                require(all(r['status'] != 'rendering' for r in ledger['renders']),
                        f'{child.name}: render active or interrupted')
    result = {}
    for asset, row in rows.items():
        ledger = load(project, asset)
        require(ledger['requests'][-1]['registry'] == row,
                f'{asset}: registry context changed; record a new chat revision')
        directory = folder(project, asset)
        require(all(r['status'] != 'rendering' for r in ledger['renders']),
                f'{asset}: render active or interrupted')
        registered = set()
        snapshots = set()
        for record in ledger['renders']:
            name = f'v{record["version"]:03d}'
            registered.add(f'{name}.mp4')
            snapshots.add(name)
            if record['status'] != 'ready':
                continue
            validate_render(project, asset, record)
        require({p.name for p in files(project, (STAGE / asset / 'renders').as_posix())} <= registered,
                'Unregistered render output; inspect before completion')
        snapshot_root = confined(project, (STAGE / asset / 'snapshots').as_posix())
        require(snapshot_root.is_dir() and {p.name for p in snapshot_root.iterdir()} <= snapshots,
                'Unregistered render snapshot; inspect before completion')
        approved = approved_videos(project, asset)
        selected = []
        identities = set()
        for video in approved:
            identity = sha(video)
            require(identity not in identities, f'{asset}: duplicate approved render identity')
            identities.add(identity)
            matches = matching_renders(ledger, identity, video.stat().st_size)
            compatible = False
            accepted = accepted_renders(matches)
            for record in matches:
                valid_video(video, snapshot_settings(directory, record))
                context = approval_context(asset, record)
                compatible = compatible or (record in accepted and context == row)
            if compatible:
                selected.append(video.relative_to(project).as_posix())
        require(selected, f'{asset}: at least one approved MP4 matching current registry context is required; review existing approvals')
        result[asset] = selected
    return result


def approval_inventory(project):
    """Read the human-selected MP4s for the current registry selection only."""
    result = {}
    for asset in eligible(project):
        videos = approved_videos(project, asset)
        require(videos, f'{asset}: at least one MP4 in APPROVED is required')
        for video in videos:
            require(video.stat().st_size > 0, f'{asset}: empty approved MP4: {video.name}')
        result[asset] = videos
    return result


def check_approvals(project):
    return {asset: [p.relative_to(project).as_posix() for p in videos]
            for asset, videos in approval_inventory(project).items()}


def editorial_inventory(project, *, ffprobe='ffprobe'):
    """Resolve optional First Cut approvals without validating stage completion or writing files."""
    entries, diagnostics = [], []
    data_errors = (ValueError, TypeError, KeyError, AttributeError, IndexError, OSError, RuntimeError,
                   OverflowError, ZeroDivisionError, subprocess.SubprocessError, BadZipFile, ParseError)
    try:
        project = Path(project).resolve(strict=True)
        paths = [confined(project, (REGISTRY / name).as_posix())
                 for name in ('Asset Registry.md', 'Asset Registry.json')]
        if not any(path.exists() for path in paths):
            return entries, diagnostics
        require(all(path.is_file() for path in paths), 'Both canonical registry files are required')
        helper = registry_helper()
        issues = []
        registry = helper.load(project, errors=issues)
        control = helper.controls(registry)
        require(control.get('schema') == 'studio.asset-registry.v1' and 'identities' in control,
                'Invalid registry schema or missing registry identity ledger')
        rows = eligible_rows(enumerate(registry['assets']))
        invalid = {}
        for issue in issues:
            if issue['asset'] is None:
                diagnostics.append(f'Asset Registry: {issue["message"]}')
            else:
                invalid.setdefault(issue['asset'], []).append(issue['message'])
        if diagnostics or not rows:
            return entries, diagnostics
    except data_errors as error:
        return entries, [f'Asset Registry: {error}']

    context = None
    for asset, row in rows.items():
        try:
            approved = confined(project, (STAGE / asset / 'APPROVED').as_posix())
            if not approved.exists():
                continue
            require(approved.is_dir(), 'Expected an APPROVED directory')
            videos = sorted(approved.iterdir(), key=lambda path: path.name)
            if not videos:
                continue
            require(asset not in invalid, '; '.join(invalid.get(asset, [])))
            if context is None:
                try:
                    context = helper.context(project)
                    stale_comments = helper.input_status(registry, context)['comments']['stale_proposals']
                except data_errors as error:
                    return entries, diagnostics + [f'Asset Registry: {error}']
            helper.validate_current_row(row, context, stale_comments)
            row = registry_context(row)
            ledger = load(project, asset)
        except data_errors as error:
            diagnostics.append(f'{asset}: {error}')
            continue

        identities = set()
        for video in videos:
            relative = video.relative_to(project).as_posix()
            try:
                require_approved_file(project, asset, video)
                size = video.stat().st_size
                require(size > 0, 'Empty approved video')
                identity = sha(video)
                require(identity not in identities, 'Duplicate approved render identity')
                matches = matching_renders(ledger, identity, size)
                contexts = [approval_context(asset, record) for record in matches]
                accepted = accepted_renders(matches)
                compatible = [record for record, saved in zip(matches, contexts)
                              if record in accepted and saved == row]
                require(compatible, 'Approval does not match current registry context')
                for record in compatible:
                    version = record['version']
                    try:
                        require(type(version) is int, 'Invalid render version')
                        validate_render(project, asset, record)
                        value = snapshot_settings(approved.parent, record)
                        metadata = valid_video(video, value, ffprobe=ffprobe)
                    except data_errors as error:
                        diagnostics.append(f'{relative} (version {version}): {error}')
                        continue
                    entries.append({'asset_id': asset, 'version': version, 'path': relative,
                                    'sha256': identity, 'registry': row, 'settings': value, 'video': metadata})
                    identities.add(identity)
                    break
            except data_errors as error:
                diagnostics.append(f'{relative}: {error}')
    return entries, diagnostics


def evidence(project):
    approved = approval_inventory(project)
    definition = completion.load_definition(ROOT / LOCAL / 'dependencies.json')
    dependencies = {LOCAL / 'SKILL.md', LOCAL / 'dependencies.json', LOCAL / 'scripts/validation.py',
                    Path('scripts/validation.py'), Path('scripts/stage_completion.py'),
                    Path('scripts/stage_invalidation.py'), Path('studio/fingerprints.py')}
    dependencies.update(Path(p) for p in definition['workflow_dependencies'])
    return {'schema': 'studio.done', 'schema_version': 1, 'step': STEP,
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'inputs': [completion.fingerprint(p, project, 'project') for p in registry_paths(project)],
            'outputs': [{'scope': 'project', 'path': p.relative_to(project).as_posix(),
                         'method': 'sha256', 'sha256': sha(p)}
                        for videos in approved.values() for p in videos],
            'dependencies': [completion.fingerprint(ROOT / p, ROOT, 'workflow') for p in sorted(dependencies)]}


def complete(project):
    require_prerequisites(project, ROOT, STEP)
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, STEP)
        confined(project, STAGE.as_posix()).mkdir(parents=True, exist_ok=True)
        manifest = evidence(project)
        completion.record_done(project, ROOT, STEP, manifest)
        log(project, 'Animations completed', 'Each selected Graphic/Animation asset has a non-empty MP4 in its human-managed APPROVED folder. Registration was not required.')
        return {'status': 'complete', 'assets': check_approvals(project)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('context', 'init', 'revise', 'render', 'approve', 'studio',
                                          'check', 'check-provenance', 'complete'))
    parser.add_argument('--project-root', required=True, type=Path)
    parser.add_argument('--asset-id')
    parser.add_argument('--version', type=int, help='Exact registered render version to approve')
    parser.add_argument('--port', type=int, help='Port for a new Studio; an existing asset server takes precedence')
    parser.add_argument('--no-open', action='store_true', help='Print the Studio URL without opening a browser')
    request = parser.add_mutually_exclusive_group()
    request.add_argument('--request', help='Verbatim chat revision or approval text')
    request.add_argument('--request-file', type=Path, help='UTF-8 file containing verbatim chat revision')
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    require_prerequisites(project, ROOT, STEP)
    if args.action == 'context':
        result = {'stage': STAGE.as_posix(), 'assets': eligible(project)}
    elif args.action == 'check':
        result = check_approvals(project)
    elif args.action == 'check-provenance':
        result = check(project)
    elif args.action == 'complete':
        result = complete(project)
    else:
        require(args.asset_id, f'{args.action} requires --asset-id')
        if args.action == 'init':
            result = init(project, args.asset_id)
        elif args.action == 'render':
            result = render(project, args.asset_id)
        elif args.action == 'studio':
            result = studio(project, args.asset_id, args.port, args.no_open)
        else:
            text = args.request_file.read_bytes().decode('utf-8') if args.request_file else args.request
            result = (approve(project, args.asset_id, args.version, text) if args.action == 'approve'
                      else revise(project, args.asset_id, text))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'Animations: {error}', file=sys.stderr)
        sys.exit(1)
