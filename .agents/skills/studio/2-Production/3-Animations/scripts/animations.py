"""Append-only animation requests, reproducible render candidates and human approvals."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

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


def eligible(project):
    registry_paths(project)
    helper = module(ROOT / '.agents/skills/studio/1-Pre Production/4-Asset Breakdown/scripts/assets.py',
                    'studio_animation_registry')
    return {row['ID']: {key: row[key] for key in
                       ('ID', 'Script', 'Passage', 'Start', 'End', 'Extracts', 'Brief', 'Notes', 'Type')}
            for _, row in helper.rows(helper.load(project))[1]
            if row['Active'] == 'Yes' and row['Review'] == 'Keep'
            and row['Type'] in ('Graphic', 'Animation')}


def folder(project, asset):
    require(isinstance(asset, str) and re.fullmatch(r'ASSET-\d{6,}', asset), 'Invalid ASSET ID')
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
    return {'registry': row, 'requests': [r['sha256'] for r in ledger['requests']],
            'files': {p.relative_to(directory).as_posix(): sha(p)
                      for p in source + resources + [setting_path]}, 'renderer': renderer_identity()}


def log(project, title, message):
    helper = module(ROOT / 'scripts/append-log.py', 'studio_animation_log')
    helper.append_entry(confined(project, (STAGE / 'LOG.md').as_posix()), title, [message], date.today())


def transition(project, reason):
    invalidate(project, ROOT, STEP, reason)


def init(project, asset):
    require_prerequisites(project, ROOT, STEP)
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, STEP)
        rows = eligible(project)
        require(asset in rows, 'Select a current Graphic or Animation asset')
        directory = folder(project, asset)
        if directory.exists():
            ledger = load(project, asset)
            current_input(project, asset, ledger, rows[asset])
            return {'asset': asset, 'status': 'reused', 'stage': audit(project, ROOT, STEP)[STEP]}
        template = confined(ROOT, (LOCAL / 'renderer/template/Composition.tsx').as_posix())
        require(template.is_file(), 'Missing maintained editable composition template')
        transition(project, f'Initialize animation {asset}')
        directory.mkdir(parents=True)
        for name in ('src', 'references', 'renders', 'snapshots', 'APPROVED'):
            (directory / name).mkdir()
        shutil.copyfile(template, directory / 'src/Composition.tsx')
        atomic_json(directory / 'settings.json', DEFAULT_SETTINGS)
        text = '# Initial Approved Registry Context\n\n' + json.dumps(rows[asset], indent=2) + '\n'
        (directory / 'request.01.md').write_text(text, encoding='utf-8', newline='\n')
        ledger = {'schema': 'studio.animation', 'schema_version': 1, 'asset': asset,
                  'requests': [{'version': 1, 'registry': rows[asset],
                                'sha256': sha(directory / 'request.01.md')}], 'renders': []}
        atomic_json(directory / 'Animation.json', ledger)
        log(project, 'Animation initialized', f'{asset}: approved brief/passage retained; edit the demo composition before rendering. Human approval remains pending.')
        return {'asset': asset, 'status': 'initialized'}


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
        log(project, 'Animation revision', f'{asset}: preserved verbatim request {version}; earlier candidates and human approvals remain in place but are not current.')
        return {'asset': asset, 'request': version}


def probe(path):
    try:
        result = subprocess.run(['node', str(RENDERER / 'probe.mjs'), str(path)], cwd=RENDERER,
                                check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'Video probe failed: {error.stderr or error}') from error
    return json.loads(result.stdout)


def valid_video(path, value):
    require(path.is_file() and path.stat().st_size > 0, f'Missing/empty video: {path}')
    metadata = probe(path)
    require(metadata['width'] == value['width'] and metadata['height'] == value['height'],
            'Video dimensions differ from render settings')
    require(abs(float(metadata['fps']) - value['fps']) < 0.01, 'Video fps differs from render settings')
    duration = float(metadata['durationInSeconds'])
    require(math.isfinite(duration) and duration > 0
            and abs(duration - value['durationInFrames'] / value['fps']) <= 1.1 / value['fps'],
            'Video duration differs from render settings')


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


def approved_videos(project, asset):
    directory = confined(project, (STAGE / asset / 'APPROVED').as_posix())
    require(directory.is_dir(), f'{asset}: missing human-only APPROVED folder')
    result = []
    for child in sorted(directory.iterdir()):
        confined(project, child.relative_to(project).as_posix())
        require(child.is_file() and child.suffix.lower() == '.mp4',
                f'{asset}: APPROVED permits only MP4 files directly inside the folder')
        result.append(child)
    return result


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
        log(project, 'Animation candidate rendered', f'{asset} {name}: candidate ready for human review; move the chosen MP4 into this asset\'s APPROVED folder manually.')
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
        require(child.is_dir() and re.fullmatch(r'ASSET-\d{6,}', child.name),
                f'Unexpected animation stage entry: {child.name}')
        result.extend(files(project, child.relative_to(project).as_posix()))
    return result


def check(project):
    rows = eligible(project)
    inventory(project)
    stage = confined(project, STAGE.as_posix())
    if stage.exists():
        for child in stage.iterdir():
            if re.fullmatch(r'ASSET-\d{6,}', child.name):
                ledger = load(project, child.name)
                require(all(r['status'] != 'rendering' for r in ledger['renders']),
                        f'{child.name}: render active or interrupted')
    result = {}
    for asset, row in rows.items():
        ledger = load(project, asset)
        provenance = current_input(project, asset, ledger, row)
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
        require(len(approved) == 1, f'{asset}: exactly one human-approved MP4 is required')
        video = approved[0]
        matches = [r for r in ledger['renders'] if r['status'] == 'ready'
                   and r['input'] == provenance and r['sha256'] == sha(video)
                   and r['size'] == video.stat().st_size]
        require(matches, f'{asset}: approved video is unregistered or stale for current inputs')
        valid_video(video, settings(read_json(directory / 'settings.json')))
        result[asset] = video.relative_to(project).as_posix()
    return result


def evidence(project):
    check(project)
    definition = completion.load_definition(ROOT / LOCAL / 'dependencies.json')
    dependencies = {LOCAL / 'SKILL.md', LOCAL / 'dependencies.json', LOCAL / 'scripts/validation.py',
                    Path('scripts/validation.py'), Path('scripts/stage_completion.py'),
                    Path('scripts/stage_invalidation.py'), Path('studio/fingerprints.py')}
    dependencies.update(Path(p) for p in definition['workflow_dependencies'])
    dependencies.update(LOCAL / 'renderer' / p for p in renderer_identity())
    return {'schema': 'studio.done', 'schema_version': 1, 'step': STEP,
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'inputs': [completion.fingerprint(p, project, 'project') for p in registry_paths(project)],
            'outputs': [completion.fingerprint(p, project, 'project') for p in inventory(project)],
            'dependencies': [completion.fingerprint(ROOT / p, ROOT, 'workflow') for p in sorted(dependencies)]}


def complete(project):
    require_prerequisites(project, ROOT, STEP)
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, STEP)
        confined(project, STAGE.as_posix()).mkdir(parents=True, exist_ok=True)
        manifest = evidence(project)
        completion.record_done(project, ROOT, STEP, manifest)
        log(project, 'Animations completed', 'All selected Graphic/Animation assets have one current registered human-approved video; empty selections are valid.')
        return {'status': 'complete', 'assets': check(project)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('context', 'init', 'revise', 'render', 'check', 'complete'))
    parser.add_argument('--project-root', required=True, type=Path)
    parser.add_argument('--asset-id')
    request = parser.add_mutually_exclusive_group()
    request.add_argument('--request', help='Verbatim chat revision text')
    request.add_argument('--request-file', type=Path, help='UTF-8 file containing verbatim chat revision')
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    require_prerequisites(project, ROOT, STEP)
    if args.action == 'context':
        result = {'stage': STAGE.as_posix(), 'assets': eligible(project)}
    elif args.action == 'check':
        result = check(project)
    elif args.action == 'complete':
        result = complete(project)
    else:
        require(args.asset_id, f'{args.action} requires --asset-id')
        if args.action == 'init':
            result = init(project, args.asset_id)
        elif args.action == 'render':
            result = render(project, args.asset_id)
        else:
            text = args.request_file.read_bytes().decode('utf-8') if args.request_file else args.request
            result = revise(project, args.asset_id, text)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'Animations: {error}', file=sys.stderr)
        sys.exit(1)
