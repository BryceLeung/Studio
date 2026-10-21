"""Append-only B-roll request/search journals, candidates and completion evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
from scripts import stage_completion as completion
from scripts.validation import require_prerequisites

STAGE = Path('2-Production/2-B-roll')
REGISTRY = Path('1-Pre Production/4-Asset Breakdown')
EXTENSIONS = {'.mp4', '.mov', '.m4v', '.mkv', '.avi', '.webm', '.mxf'}
ID = re.compile(r'ASSET-\d{6}')
MAX_SEARCH_REFINEMENTS = 5


def now():
    return datetime.now(timezone.utc).isoformat()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'Duplicate JSON field: {key}')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def confined(project, relative):
    project = Path(project).resolve(strict=True)
    path = project / completion.relative_path(str(relative))
    require(path.resolve().is_relative_to(project), f'Path escapes project: {relative}')
    current = path
    while current != project:
        require(not current.is_symlink(), f'Redirected project path: {current}')
        current = current.parent
    return path


def atomic_json(path, data):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.broll-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def registry_paths(project):
    paths = [confined(project, (REGISTRY / name).as_posix()) for name in ('Asset Registry.md', 'Asset Registry.json')]
    require(all(path.is_file() for path in paths), 'Both 1.4 registry files are required')
    return paths


def eligible(project):
    registry_paths(project)
    helper = module(ROOT / '.agents/skills/studio/1-Pre Production/4-Asset Breakdown/scripts/assets.py', 'studio_broll_registry')
    rows = helper.rows(helper.load(project))[1]
    return {row['ID']: {key: row[key] for key in ('ID', 'Script', 'Passage', 'Start', 'End', 'Extracts', 'Brief', 'Type')}
            for _, row in rows if row['Active'] == 'Yes' and row['Review'] == 'Keep' and row['Type'] == 'B-roll'}


def folder(project, asset):
    require(isinstance(asset, str) and ID.fullmatch(asset), 'Invalid ASSET ID')
    return confined(project, (STAGE / asset).as_posix())


def valid_plan(sourcing, shots):
    require(sourcing in ('manual', 'stock', 'either'), 'Sourcing must be manual, stock or either')
    require(isinstance(shots, list) and all(isinstance(s, str) and s.strip() for s in shots)
            and len(shots) == len(set(shots)), 'Required shots must be unique nonempty strings')


def load(project, asset, *, allow_unregistered=False):
    directory = folder(project, asset)
    ledger = read_json(directory / 'Candidates.json')
    require(ledger.get('schema') == 'studio.broll.candidates' and ledger.get('schema_version') == 2
            and ledger.get('asset') == asset, 'Invalid candidate ledger identity/schema')
    requests = ledger['requests']
    require(isinstance(requests, list) and requests, 'Missing requests')
    path = confined(project, (STAGE / asset / 'request.md').as_posix())
    require(path.is_file(), 'Missing request.md journal')
    content = path.read_bytes()
    require(ledger.get('journal') == {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()},
            'Changed or interrupted append-only request journal; inspect request.md and Candidates.json')
    events = ledger.get('events')
    require(isinstance(events, list) and bool(events), 'Missing journal event evidence')
    cursor = 0
    for event in events:
        require(type(event['offset']) is int and type(event['length']) is int and event['offset'] == cursor
                and event['length'] > 0, 'Invalid journal event boundaries')
        cursor += event['length']
        require(hashlib.sha256(content[event['offset']:cursor]).hexdigest() == event['sha256'], 'Journal history changed')
    require(cursor == len(content), 'Untracked appended journal content')
    searches, finished = {}, set()
    for event in events:
        require(event['kind'] in ('request', 'search', 'search-result', 'search-note')
                and type(event['request_version']) is int and 1 <= event['request_version'] <= len(requests),
                'Invalid journal event kind or brief revision')
        if event['kind'] == 'search':
            key = event['search_id']
            require(type(key) is int and key == len(searches) + 1 and event['provider'] in ('pixabay', 'pexels')
                    and isinstance(event['query'], str) and event['query'].strip(), 'Invalid search attempt history')
            searches[key] = event['request_version']
        elif event['kind'] in ('search-result', 'search-note'):
            key = event['search_id']
            require(key in searches and searches[key] == event['request_version'], 'Search event has no matching attempt')
            if event['kind'] == 'search-result':
                require(key not in finished and event['status'] in ('succeeded', 'failed'), 'Duplicate or invalid search outcome')
                finished.add(key)
    for version, request in enumerate(requests, 1):
        require(request['version'] == version and request['file'] == 'request.md', 'Nonsequential request revision history')
        start, length = request['text_start'], request['text_length']
        require(type(start) is int and type(length) is int and start >= 0 and length > 0
                and start + length <= len(content), 'Invalid request text boundaries')
        require(hashlib.sha256(content[start:start + length]).hexdigest() == request['sha256'], 'Request revision text changed')
        matching = [event for event in events if event['kind'] == 'request' and event['request_version'] == version]
        require(len(matching) == 1 and matching[0]['offset'] <= start
                and start + length <= matching[0]['offset'] + matching[0]['length'], 'Missing request revision event')
        valid_plan(request['sourcing'], request['shots'])
        require(request['registry']['ID'] == asset, 'Request registry identity mismatch')
    actual = {p.name for p in directory.iterdir() if p.name.lower().startswith('request.')}
    require(actual == {'request.md'}, 'Unexpected request files; this development format uses only request.md')
    candidates = ledger['candidates']
    require(isinstance(candidates, list), 'Invalid candidates list')
    seen = set()
    for candidate in candidates:
        cid = candidate['id']
        require(isinstance(cid, str) and re.fullmatch(r'C\d{3,}', cid) and cid not in seen, 'Duplicate/invalid candidate ID')
        seen.add(cid)
        name = candidate['file']
        require(isinstance(name, str) and Path(name).name == name and '/' not in name and '\\' not in name
                and Path(name).suffix.lower() in EXTENSIONS, 'Candidate must have a local video filename')
        path = confined(project, (STAGE / asset / name).as_posix())
        require(path.is_file() and completion.matches_entry(candidate['identity'], project, ROOT), f'Changed/missing candidate: {name}')
        require(type(candidate['request_version']) is int and 1 <= candidate['request_version'] <= len(requests), 'Unknown originating request')
        require(candidate['origin'] in ('User', 'Pexels', 'Pixabay') and candidate['source_id'].strip(), 'Invalid candidate origin/source')
        require(isinstance(candidate['assessments'], list), 'Invalid assessment history')
    media = {p.name for p in directory.iterdir() if p.suffix.lower() in EXTENSIONS}
    require(len({c['file'] for c in candidates}) == len(candidates), 'Duplicate candidate file records')
    require(allow_unregistered or media == {c['file'] for c in candidates}, 'Unregistered video files; register supplied footage before completion')
    return ledger


def request_entry(project, asset, row, version, sourcing, shots, reason, text):
    valid_plan(sourcing, shots)
    require(isinstance(text, str) and text.strip(), 'Request text must be nonempty')
    return {'version': version, 'file': 'request.md', 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'registry': row, 'registry_inputs': [completion.fingerprint(p, project, 'project') for p in registry_paths(project)],
            'sourcing': sourcing, 'shots': shots, 'reason': reason, 'created_at': now()}


def append_journal(project, asset, ledger, text, kind, request_version, **metadata):
    """Append bytes first, then atomically publish their evidence. Interrupted pairs fail closed."""
    directory = folder(project, asset)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'request.md'
    content = path.read_bytes() if path.exists() else b''
    if path.exists():
        require(ledger.get('journal') == {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()},
                'Changed or interrupted append-only request journal')
    else:
        require(not ledger.get('journal') and not ledger.get('events'), 'Missing existing request journal')
    body = text.encode('utf-8')
    require(bool(body), 'Empty journal event')
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, 'O_BINARY', 0)
    if not path.exists():
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        require(os.write(descriptor, body) == len(body), 'Partial journal append; inspect before retrying')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    ledger.setdefault('events', []).append({'kind': kind, 'request_version': request_version,
        'created_at': now(), 'offset': len(content), 'length': len(body),
        'sha256': hashlib.sha256(body).hexdigest(), **metadata})
    ledger['journal'] = {'size': len(content) + len(body), 'sha256': hashlib.sha256(content + body).hexdigest()}
    atomic_json(directory / 'Candidates.json', ledger)


def write_request(project, asset, ledger, entry, text):
    require(entry['version'] == len(ledger['requests']) + 1, 'Nonsequential revision')
    prefix = '' if not ledger['requests'] else f'\n\n## Request revision {entry["version"]} | {now()}\n\nReason: {entry["reason"]}\n\n'
    entry['text_start'] = ledger.get('journal', {}).get('size', 0) + len(prefix.encode('utf-8'))
    entry['text_length'] = len(text.encode('utf-8'))
    ledger['requests'].append(entry)
    append_journal(project, asset, ledger, prefix + text, 'request', entry['version'])


def initialize(project, plans, rows):
    require(isinstance(plans, dict) and not (set(plans) - set(rows)), 'Plans reference ineligible assets')
    work = []
    for asset, row in rows.items():
        directory = folder(project, asset)
        if (directory / 'Candidates.json').exists():
            ledger = load(project, asset)
            require(ledger['requests'][-1]['registry'] == row, f'{asset}: registry changed; revise explicitly')
            continue
        require(not directory.exists() or not any(directory.iterdir()), f'{asset}: incomplete initialization; inspect files')
        require(asset in plans, f'{asset}: explicit sourcing/required-shot plan needed')
        plan = plans[asset]
        entry = request_entry(project, asset, row, 1, plan['sourcing'], plan['shots'], 'Approved 1.4 brief', row['Brief'])
        work.append((asset, row, entry))
    return work


def refinement_budget(ledger, queries):
    """Initial distinct term plus five refinements, shared across providers and invocations."""
    revision = ledger['requests'][-1]['version']
    normalize = lambda text: ' '.join(text.split()).casefold()
    terms = {normalize(event['query']) for event in ledger['events']
             if event['kind'] == 'search' and event['request_version'] == revision}
    planned = {normalize(query) for query in queries}
    maximum = 1 + MAX_SEARCH_REFINEMENTS
    message = ('Search refinement limit reached: initial term plus at most 5 refinements per brief revision. '
               'Stop and tell the user the threshold may be too high or the search direction needs changing.')
    require(len(terms) < maximum and len(terms | planned) <= maximum, message)
    total = len(terms | planned)
    return {'distinct_terms': total, 'refinements': max(0, total - 1),
            'refinements_remaining': maximum - total}


def record_search(project, asset, ledger, provider, query, reason):
    require(provider in ('pixabay', 'pexels'), 'Unsupported stock provider')
    require(isinstance(query, str) and query.strip() and len(query) <= 100 and '\n' not in query and '\r' not in query,
            'Search terms must be a single nonempty line of at most 100 characters')
    require(isinstance(reason, str) and reason.strip(), 'Record why this search term is being tried')
    budget = refinement_budget(ledger, [query])
    search_id = max([event['search_id'] for event in ledger['events'] if event['kind'] == 'search'] + [0]) + 1
    revision = ledger['requests'][-1]['version']
    text = (f'\n\n## Search {search_id} | {now()}\n\n'
            f'- Provider: {provider}\n- Search term: {query}\n- Request revision: {revision}\n'
            f'- Why this term: {reason.strip()}\n- Refinements used: {budget["refinements"]}/{MAX_SEARCH_REFINEMENTS}\n- Status: started\n')
    append_journal(project, asset, ledger, text, 'search', revision,
                   search_id=search_id, provider=provider, query=query, reason=reason.strip(), budget=budget)
    return search_id


def search_outcome(project, asset, ledger, search_id, status, *, count=None, result_file=None):
    attempts = [event for event in ledger['events'] if event['kind'] == 'search' and event['search_id'] == search_id]
    require(len(attempts) == 1, 'Unknown or ambiguous search ID')
    require(status in ('succeeded', 'failed'), 'Invalid search outcome')
    require(not any(event['kind'] == 'search-result' and event['search_id'] == search_id for event in ledger['events']),
            'Search outcome already recorded; append a search-note for later review')
    require(status != 'succeeded' or (type(count) is int and count >= 0 and isinstance(result_file, str)),
            'Successful search requires result count and saved result location')
    details = f'- Results returned: {count}\n- Saved results: {result_file}\n' if status == 'succeeded' else '- No result set acquired; inspect access or refine search before retrying.\n'
    text = f'\n\n### Search {search_id} outcome | {now()}\n\n- Status: {status}\n{details}'
    append_journal(project, asset, ledger, text, 'search-result', attempts[0]['request_version'],
                   search_id=search_id, status=status, count=count, result_file=result_file)


def search_note(project, asset, ledger, search_id, reason):
    attempts = [event for event in ledger['events'] if event['kind'] == 'search' and event['search_id'] == search_id]
    require(len(attempts) == 1 and isinstance(reason, str) and reason.strip(), 'Supply an existing search ID and nonempty review note')
    text = f'\n\n### Search {search_id} review | {now()}\n\n{reason.strip()}\n'
    append_journal(project, asset, ledger, text, 'search-note', attempts[0]['request_version'],
                   search_id=search_id, note=reason.strip())


def run_search(project, asset, provider, queries, reason, cache_dir, output_dir):
    """Journal every term before its API call, preserving results and failure outcomes."""
    stock = module(Path(__file__).with_name('stock.py'), 'studio_broll_stock')
    require(isinstance(queries, list) and 1 <= len(queries) <= 1 + MAX_SEARCH_REFINEMENTS
            and all(isinstance(q, str) for q in queries) and len(set(queries)) == len(queries),
            'Supply at most 6 explicit search terms, within the remaining five-refinement budget')
    require(provider in stock.PROVIDERS and all(isinstance(q, str) and q.strip() and len(q) <= 100
            and '\n' not in q and '\r' not in q for q in queries), 'Invalid search provider or term')
    require(isinstance(reason, str) and reason.strip(), 'Explain why these search terms are being tried')
    require_prerequisites(project, ROOT, '2.2')
    output_dir, cache_dir = stock.external(output_dir), stock.external(cache_dir)
    require(not output_dir.exists(), 'Search output directory exists; choose a new scratch directory')
    rows = eligible(project)
    require(asset in rows, 'Select a current eligible asset')
    ledger = load(project, asset)
    require(ledger['requests'][-1]['registry'] == rows[asset], 'Registry changed; revise explicitly first')
    require(ledger['requests'][-1]['sourcing'] != 'manual', 'Manual-only family footage request cannot search stock')
    refinement_budget(ledger, queries)  # Fail before creating output folders, invalidating or calling the API.
    output_dir.mkdir(parents=True)
    results = []
    from scripts.stage_invalidation import invalidate
    for query in queries:
        with completion.mutation_guard(project):
            require_prerequisites(project, ROOT, '2.2')
            ledger = load(project, asset)
            require(ledger['requests'][-1]['registry'] == eligible(project).get(asset), 'Asset changed during search batch')
            require(ledger['requests'][-1]['sourcing'] != 'manual', 'Request now requires manual footage')
            refinement_budget(ledger, [query])
            invalidate(project, ROOT, '2.2', f'Search term recorded for {asset}')
            search_id = record_search(project, asset, ledger, provider, query, reason)
            log(project, 'B-roll search started', f'{asset}: search {search_id}, provider {provider}, term {query}')
        try:
            result = stock.search_to_file(provider, query, cache_dir, output_dir / f'search-{search_id:04d}.json')
        except (stock.StockError, OSError):
            with completion.mutation_guard(project):
                ledger = load(project, asset)
                search_outcome(project, asset, ledger, search_id, 'failed')
                log(project, 'B-roll search failed', f'{asset}: search {search_id} failed; term and attempt preserved in request.md; no automatic retry.')
            raise ValueError(f'Search {search_id} failed; recorded in request.md. No further queries attempted.') from None
        with completion.mutation_guard(project):
            ledger = load(project, asset)
            search_outcome(project, asset, ledger, search_id, 'succeeded', count=result['count'], result_file=result['result_file'])
            log(project, 'B-roll search returned', f'{asset}: search {search_id} returned {result["count"]} results; review and refine the terms as needed.')
        results.append(dict(result, search_id=search_id))
    latest = load(project, asset)
    used = { ' '.join(event['query'].split()).casefold() for event in latest['events']
            if event['kind'] == 'search' and event['request_version'] == latest['requests'][-1]['version'] }
    return {'asset': asset, 'searches': results, 'request_journal': str(folder(project, asset) / 'request.md'),
            'refinements_used': max(0, len(used) - 1), 'refinements_remaining': max(0, 1 + MAX_SEARCH_REFINEMENTS - len(used)),
            'search_limit_reached': len(used) >= 1 + MAX_SEARCH_REFINEMENTS}


def probe(path):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                                capture_output=True, text=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('ffprobe could not verify video; install/configure FFmpeg or inspect the file') from exc
    data = json.loads(result.stdout)
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video'
              and s.get('width', 0) > 0 and s.get('height', 0) > 0 and not s.get('disposition', {}).get('attached_pic')]
    require(videos, 'No playable video stream found')
    duration = float(data.get('format', {}).get('duration') or videos[0].get('duration') or 0)
    require(math.isfinite(duration) and duration > 0, 'Video has no verifiable positive duration')
    return {'duration': duration, 'width': videos[0]['width'], 'height': videos[0]['height'], 'codec': videos[0].get('codec_name')}


def provenance(spec):
    require(spec['origin'] in ('User', 'Pexels', 'Pixabay'), 'Unsupported origin; Storyblocks is not enabled')
    for key in ('source_id', 'description'):
        require(isinstance(spec[key], str) and spec[key].strip(), f'{key} is required')
    if spec['origin'] != 'User':
        host = urlparse(spec['source_url'])
        domain = {'Pexels': 'pexels.com', 'Pixabay': 'pixabay.com'}[spec['origin']]
        require(host.scheme == 'https' and (host.hostname == domain or (host.hostname or '').endswith('.' + domain)), 'Provider page URL mismatch')
        licence = spec['license']
        require(all(isinstance(licence.get(k), str) and licence[k].strip() for k in ('url', 'text', 'checked_at')),
                'Stock licence URL, retained text and checked_at are required')


def register(project, asset, source, spec, ledger):
    provenance(spec)
    require(source.is_file() and source.suffix.lower() in EXTENSIONS, 'Supply a local video file')
    technical = probe(source)
    content = sha(source)
    require(not any(c['sha256'] == content for c in ledger['candidates']), 'Duplicate candidate bytes')
    require(not any(c['origin'] == spec['origin'] and c['source_id'] == spec['source_id'] for c in ledger['candidates']), 'Source already registered for this asset')
    if ledger['requests'][-1]['sourcing'] == 'manual':
        require(spec['origin'] == 'User', 'Manual family-footage request cannot accept stock')
    number = max([int(c['id'][1:]) for c in ledger['candidates']] + [0]) + 1
    cid = f'C{number:03d}'
    destination = (source if source.resolve().parent == folder(project, asset).resolve()
                   else folder(project, asset) / (cid + source.suffix.lower()))
    require(not any(c['file'].casefold() == destination.name.casefold() for c in ledger['candidates']), 'Candidate file already registered')
    require(destination == source or not destination.exists(), 'Candidate destination already exists')
    return destination, dict(spec, id=cid, file=destination.name, sha256=content, technical=technical,
                             original_filename=source.name, acquired_at=now(),
                             request_version=len(ledger['requests']), assessments=[])


def assess(ledger, candidate, spec):
    request = ledger['requests'][-1]
    require(type(spec.get('suitable')) is bool, 'suitable must be a boolean')
    require(isinstance(spec.get('reviewed_by'), str) and spec['reviewed_by'].strip(), 'reviewed_by is required')
    require(isinstance(spec.get('notes'), str), 'Review notes are required')
    shots = spec.get('shots')
    require(isinstance(shots, list) and all(s in request['shots'] for s in shots), 'Unknown shot coverage')
    ranges = spec.get('ranges')
    require(isinstance(ranges, list), 'Usable ranges must be a list')
    for span in ranges:
        require(isinstance(span, list) and len(span) == 2 and all(type(t) in (int, float) and math.isfinite(t) for t in span)
                and 0 <= span[0] < span[1] <= candidate['technical']['duration'], 'Invalid usable time range')
    require(not spec['suitable'] or bool(ranges), 'Suitable footage requires usable ranges')
    return dict(spec, request_version=request['version'], request_sha256=request['sha256'], assessed_at=now())


def inventory(project):
    directory = confined(project, STAGE.as_posix())
    result = []
    if directory.exists():
        for child in directory.iterdir():
            if ID.fullmatch(child.name):
                require(child.is_dir(), 'Asset path is not a directory')
                load(project, child.name)
                for path in child.iterdir():
                    if path.name == 'Candidates.json' or path.name.startswith('request.') or path.suffix.lower() in EXTENSIONS:
                        result.append(confined(project, path.relative_to(project).as_posix()))
    return sorted(result)


def check(project, rows=None):
    rows = eligible(project) if rows is None else rows
    require(bool(rows), 'No eligible B-roll assets; ask the user how to proceed')
    counts = {}
    for asset, row in rows.items():
        ledger = load(project, asset)
        request = ledger['requests'][-1]
        require(request['registry'] == row, f'{asset}: registry changed; reconcile the request')
        accepted, covered = [], set()
        for candidate in ledger['candidates']:
            provenance(candidate)
            reviews = [a for a in candidate['assessments'] if a['request_version'] == request['version']]
            if not reviews:
                continue
            review = reviews[-1]
            require(review['request_sha256'] == request['sha256'], 'Assessment request fingerprint mismatch')
            assess(ledger, candidate, review)
            if review['suitable']:
                require(request['sourcing'] != 'manual' or candidate['origin'] == 'User',
                        f'{asset}: current manual request cannot be satisfied by stock')
                require(sha(folder(project, asset) / candidate['file']) == candidate['sha256'], 'Candidate content changed')
                require(probe(folder(project, asset) / candidate['file']) == candidate['technical'], 'Video metadata differs from registration')
                accepted.append(candidate)
                covered.update(review['shots'])
        require(len(accepted) >= 3, f'{asset}: {len(accepted)}/3 usable candidates for current request')
        require(len({(c['origin'], c['source_id']) for c in accepted}) == len(accepted)
                and len({c['sha256'] for c in accepted}) == len(accepted), 'Duplicate source alternatives')
        require(set(request['shots']) <= covered, f'{asset}: required shots not covered: {sorted(set(request["shots"]) - covered)}')
        counts[asset] = len(accepted)
    inventory(project)
    return counts


def evidence(project):
    check(project)
    definition = completion.load_definition(Path(__file__).parents[1] / 'dependencies.json')
    local = Path('.agents/skills/studio') / STAGE
    dependencies = {local / 'SKILL.md', local / 'dependencies.json', local / 'scripts/validation.py',
                    Path('scripts/validation.py'), Path('scripts/stage_completion.py'), Path('scripts/stage_invalidation.py'), Path('studio/fingerprints.py')}
    dependencies.update(Path(p) for p in definition['workflow_dependencies'])
    return {'schema': 'studio.done', 'schema_version': 1, 'step': '2.2', 'completed_at': now(),
            'inputs': [completion.fingerprint(p, project, 'project') for p in registry_paths(project)],
            'outputs': [completion.fingerprint(p, project, 'project') for p in inventory(project)],
            'dependencies': [completion.fingerprint(ROOT / p, ROOT, 'workflow') for p in sorted(dependencies)]}


def log(project, title, message):
    helper = module(ROOT / 'scripts/append-log.py', 'studio_broll_log')
    from datetime import date
    helper.append_entry(confined(project, (STAGE / 'LOG.md').as_posix()), title, [message], date.fromisoformat(datetime.now().date().isoformat()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('context', 'init', 'revise', 'search', 'search-note', 'register', 'assess', 'check', 'complete'))
    parser.add_argument('--project-root', required=True, type=Path)
    parser.add_argument('--asset')
    parser.add_argument('--spec', type=Path, help='Plans, provenance or assessment JSON; see skill reference')
    parser.add_argument('--file', type=Path, help='Full revised request text or local source video')
    parser.add_argument('--reason')
    parser.add_argument('--candidate')
    parser.add_argument('--provider', choices=('pixabay', 'pexels'), default='pixabay')
    parser.add_argument('--query', action='append')
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--search-id', type=int)
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    if args.action == 'context':
        print(json.dumps({'assets': eligible(project), 'stage': STAGE.as_posix()}, indent=2))
        return
    if args.action == 'check':
        require_prerequisites(project, ROOT, '2.2')
        print(json.dumps(check(project), indent=2))
        return
    if args.action == 'search':
        require(args.asset and args.cache_dir and args.output_dir, 'Search requires --asset, --cache-dir and --output-dir')
        print(json.dumps(run_search(project, args.asset, args.provider, args.query, args.reason, args.cache_dir, args.output_dir), indent=2))
        return
    require_prerequisites(project, ROOT, '2.2')
    with completion.mutation_guard(project):
        require_prerequisites(project, ROOT, '2.2')
        if args.action == 'complete':
            manifest = evidence(project)
            completion.record_done(project, ROOT, '2.2', manifest)
            log(project, 'B-roll completed', 'All current B-roll assets have at least three usable candidates and required shot coverage; shared completion checks passed.')
            print(json.dumps({'status': 'complete', 'assets': check(project)}, indent=2))
            return
        rows = eligible(project)
        require(bool(rows), 'No eligible B-roll assets; ask the user how to proceed')
        spec = read_json(args.spec) if args.spec else None
        if args.action == 'init':
            if args.asset:
                require(args.asset in rows, 'Select a current eligible asset')
                rows = {args.asset: rows[args.asset]}
            work = initialize(project, spec, rows)
            if not work:
                print(json.dumps({'changed': False}))
                return
        else:
            require(args.asset in rows, 'Supply a current eligible --asset')
            ledger = load(project, args.asset, allow_unregistered=args.action == 'register')
            if args.action not in ('revise', 'search-note'):
                require(ledger['requests'][-1]['registry'] == rows[args.asset], 'Registry changed; revise explicitly first')
            if args.action == 'revise':
                require(args.file and args.reason and args.reason.strip(), 'Revision requires --file and --reason reflecting the user decision')
                require(isinstance(spec, dict), 'Revision requires sourcing/shot plan --spec')
                text = args.file.read_text(encoding='utf-8')
                require(text.strip(), 'Empty request')
                entry = request_entry(project, args.asset, rows[args.asset], len(ledger['requests']) + 1,
                                      spec['sourcing'], spec['shots'], args.reason, text)
            elif args.action == 'search-note':
                require(args.search_id is not None and args.reason and args.reason.strip(), 'Search note requires --search-id and --reason')
                require(any(event['kind'] == 'search' and event['search_id'] == args.search_id for event in ledger['events']), 'Unknown search ID')
            elif args.action == 'register':
                require(args.file and isinstance(spec, dict), 'Registration requires --file and provenance --spec')
                destination, candidate = register(project, args.asset, args.file, spec, ledger)
            elif args.action == 'assess':
                candidate = next((c for c in ledger['candidates'] if c['id'] == args.candidate), None)
                require(candidate is not None and isinstance(spec, dict), 'Supply registered --candidate and assessment --spec')
                review = assess(ledger, candidate, spec)
        from scripts.stage_invalidation import invalidate
        invalidate(project, ROOT, '2.2', f'B-roll {args.action}: {args.asset or "new requests"}')
        directory = confined(project, STAGE.as_posix())
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / 'LOG.md'
        if not log_path.exists():
            log_path.write_text('# B-roll log\n', encoding='utf-8')
        if args.action == 'init':
            for asset, row, entry in work:
                write_request(project, asset, {'schema': 'studio.broll.candidates', 'schema_version': 2,
                              'asset': asset, 'requests': [], 'candidates': []}, entry, row['Brief'])
        elif args.action == 'revise':
            write_request(project, args.asset, ledger, entry, text)
        elif args.action == 'search-note':
            search_note(project, args.asset, ledger, args.search_id, args.reason)
        elif args.action == 'register':
            # Exclusive creation preserves originals and refuses accidental replacements.
            if destination != args.file:
                with args.file.open('rb') as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output)
            require(sha(destination) == candidate['sha256'], 'Source changed while copying; inspect unregistered destination')
            candidate['identity'] = completion.fingerprint(destination, project, 'project')
            ledger['candidates'].append(candidate)
            atomic_json(folder(project, args.asset) / 'Candidates.json', ledger)
        elif args.action == 'assess':
            candidate['assessments'].append(review)
            atomic_json(folder(project, args.asset) / 'Candidates.json', ledger)
        log(project, f'B-roll {args.action}', f'Asset: {args.asset or "new eligible assets"}; {args.reason or "request/candidate evidence updated"}. Existing requests and footage preserved.')
        print(json.dumps({'status': 'saved', 'action': args.action, 'asset': args.asset}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit(f'B-roll: {error}')
