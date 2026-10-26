"""Import Storyblocks MCP evidence and acquire explicitly selected licensed footage.

No MCP credentials, account identities or licensing calls are made by this CLI.
The agent calls MCP; this helper validates and consumes its captured responses.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit

import stock


def require(ok, message):
    if not ok:
        raise stock.StockError(message)


def payload(value):
    """Accept structured MCP output or its JSON text block, rejecting tool errors."""
    require(isinstance(value, dict) and not value.get('isError'), 'Invalid or failed Storyblocks MCP response.')
    if isinstance(value.get('structuredContent'), dict):
        return payload(value['structuredContent'])
    if 'content' in value:
        blocks = [block for block in value['content'] if block.get('type') == 'text']
        require(len(blocks) == 1, 'Expected one JSON text block in MCP response.')
        return payload(json.loads(blocks[0]['text']))
    return value


def read_response(path):
    path = Path(path)
    require(path.stat().st_size <= 8 * 1024 * 1024, 'MCP response exceeds the size limit.')
    return payload(json.loads(path.read_text(encoding='utf-8-sig')))


def item_id(value):
    require(type(value) in (str, int) and re.fullmatch(r'[1-9][0-9]*', str(value)), 'Invalid Storyblocks item ID.')
    return str(value)


def page_url(value):
    stock.allowed_url(value, 'storyblocks')
    require(urlsplit(value).hostname in ('storyblocks.com', 'www.storyblocks.com'), 'Expected a Storyblocks detail page URL.')
    return value


def normalize_search(raw, query):
    raw = payload(raw)
    require(isinstance(raw.get('results'), list) and len(raw['results']) <= 250, 'Invalid Storyblocks result list.')
    if 'keywords' in raw:
        require(raw['keywords'] == query, 'MCP search query differs from the recorded request.')
    results, seen = [], set()
    for item in raw['results']:
        sid = item_id(item['id'])
        require(sid not in seen and item.get('contentClass') in ('video', 'videos'), 'Duplicate ID or non-video Storyblocks result.')
        seen.add(sid)
        page = page_url(item['details_url'])
        thumbs = []
        if item.get('thumbnail_url'):
            stock.allowed_url(item['thumbnail_url'], 'storyblocks')
            thumbs.append({'url': item['thumbnail_url'], 'kind': 'poster'})
        hit = {'provider': 'storyblocks', 'provider_id': sid, 'page_url': page,
               'title': item.get('title'), 'author': None,
               'duration_seconds': item.get('duration'), 'variants': [], 'thumbnails': thumbs}
        # A search preview is known to be 480p. Width requires provider aspect metadata.
        ratio = item.get('aspectRatio')
        if item.get('preview_url') and type(ratio) in (int, float) and math.isfinite(ratio) and 0 < ratio < 10:
            stock.allowed_url(item['preview_url'], 'storyblocks')
            hit['variants'] = [{'id': '_480p', 'url': item['preview_url'], 'width': round(480 * ratio),
                                'height': 480, 'file_type': 'video/mp4', 'preview_only': True}]
        results.append(hit)
    return {'provider': 'storyblocks', 'provider_url': 'https://www.storyblocks.com', 'query': query,
            'page': raw.get('page', 1), 'results': results, 'stats': raw.get('stats'),
            'search_identifiers': raw.get('search_identifiers'), 'transport': 'mcp'}


def details_result(hit, raw):
    raw = payload(raw)
    require(hit['provider'] == 'storyblocks' and item_id(raw['id']) == hit['provider_id'], 'Storyblocks details ID mismatch.')
    require(raw.get('contentClass') in ('video', 'videos'), 'Expected video details.')
    formats = raw.get('download_formats', {})
    dimensions = {}
    for renditions in formats.values():
        for variant in renditions.values():
            if stock.valid_dimensions(variant):
                dimensions[variant['height']] = variant['width'] / variant['height']
    ratios = list(dimensions.values())
    require(bool(ratios), 'Details need download_formats with verified dimensions to size previews.')
    ratio = ratios[0]
    require(all(abs(ratio - other) < .02 for other in ratios), 'Inconsistent Storyblocks aspect ratios.')
    previews = []
    for name, url in raw.get('preview_urls', {}).items():
        match = re.fullmatch(r'_(\d+)p', name)
        require(match is not None, 'Unknown Storyblocks preview resolution.')
        height = int(match[1])
        stock.allowed_url(url, 'storyblocks')
        preview = {'id': name, 'url': url, 'height': height, 'width': round(height * ratio),
                   'file_type': 'video/mp4', 'preview_only': True}
        require(stock.valid_dimensions(preview), 'Invalid preview dimensions.')
        previews.append(preview)
    require(previews, 'No Storyblocks video previews available.')
    result = dict(hit, variants=previews, download_formats=formats,
                  author=raw.get('contributor', {}).get('username'), duration_seconds=raw.get('duration'))
    return result


def import_details(result_file, provider_id, response_file, output):
    hit = stock.load_result(result_file, provider_id)
    raw = read_response(response_file)
    if 'response' in raw:
        require(raw.get('tool') == 'mcp__storyblocks__get_stock_item_details'
                and raw.get('arguments', {}).get('stockItemId') == int(provider_id)
                and raw['arguments'].get('contentClass') == 'videos', 'Mismatched details MCP call.')
        raw = raw['response']
    updated = details_result(hit, raw)
    stock.save_json_new(output, {'provider': 'storyblocks', 'results': [updated], 'transport': 'mcp'})
    return {'result_file': str(output), 'provider_id': updated['provider_id'], 'licensed': False}


def capture(tool, arguments_file, output):
    """Persist exact tool arguments and returned JSON, supplied on process stdin."""
    require(tool in ('mcp__storyblocks__search_videos', 'mcp__storyblocks__get_stock_item_details',
                     'mcp__storyblocks__get_download_url'), 'Unsupported Storyblocks MCP tool.')
    arguments = json.loads(Path(arguments_file).read_text(encoding='utf-8-sig'))
    if isinstance(arguments, dict):
        arguments = arguments.get('arguments', arguments)
    require(isinstance(arguments, dict), 'MCP arguments must be an object.')
    data = sys.stdin.read(8 * 1024 * 1024 + 1)
    require(len(data) <= 8 * 1024 * 1024, 'MCP capture exceeds the size limit.')
    response = json.loads(data)
    payload(response)
    stock.save_json_new(output, {'tool': tool, 'arguments': arguments, 'response': response})
    return {'response_file': str(output), 'tool': tool, 'mcp_called': False}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def acquisition_plan(result_file, provider_id, authorization, min_width=1920):
    hit = stock.load_result(result_file, provider_id)
    require(hit['provider'] == 'storyblocks', 'Expected a Storyblocks result.')
    require(isinstance(authorization, str) and authorization.strip(), 'Record the explicit user download request.')
    require(type(min_width) is int and min_width > 0, 'Minimum width must be positive.')
    variants = [(name, v) for name, v in hit.get('download_formats', {}).get('MP4', {}).items()
                if stock.valid_dimensions(v) and v['width'] >= min_width]
    require(variants, 'No MP4 format meets the requested minimum width; import extended download_formats first.')
    name, variant = max(variants, key=lambda pair: pair[1]['width'] * pair[1]['height'])
    require(not isinstance(variant.get('file_size_bytes'), (int, float))
            or variant['file_size_bytes'] <= stock.MAX_BYTES,
            'Selected Storyblocks format exceeds the download limit; choose a smaller format before licensing.')
    return {'provider_id': hit['provider_id'], 'page_url': hit['page_url'], 'author': hit.get('author'),
            'authorization': authorization.strip(), 'format': name, 'variant': variant,
            'tool': 'mcp__storyblocks__get_download_url',
            'arguments': {'contentClass': 'videos', 'stockItemId': int(hit['provider_id'])}}


def prepare_acquisition(result_file, provider_id, authorization, output, min_width=1920):
    require(shutil.which('ffprobe'), 'ffprobe must be available before preparing a licensed download.')
    plan = acquisition_plan(result_file, provider_id, authorization, min_width)
    target = stock.external(output)
    require(not target.exists(), 'Acquisition plan already exists; do not repeat a licensing call.')
    stock.save_json_new(target, plan)
    return dict(plan, plan_file=str(target), warning='Calling this MCP tool records a licensed download. Call once; reuse its response on local retries.')


def acquire(plan_file, response_file, url, output, license_file):
    """Consume one already-issued download URL; never call licensing or retry it."""
    plan = json.loads(Path(plan_file).read_text(encoding='utf-8-sig'))
    sid = item_id(plan['provider_id'])
    require(plan.get('authorization') and plan.get('arguments') == {'contentClass': 'videos', 'stockItemId': int(sid)}
            and plan.get('tool') == 'mcp__storyblocks__get_download_url', 'Invalid acquisition plan.')
    source_page = page_url(plan['page_url'])
    require(stock.valid_dimensions(plan['variant']), 'Invalid acquisition format dimensions.')
    raw = read_response(response_file)
    require(raw.get('tool') == plan['tool'] and raw.get('arguments') == plan['arguments'],
            'Download response must identify the exact prepared MCP call and item.')
    response = payload(raw['response'])
    require(url in set(strings(response)), 'Selected URL must occur character-for-character in the captured MCP download response.')
    stock.allowed_url(url, 'storyblocks')
    require('/watermarks/' not in urlsplit(url).path, 'Watermarked previews cannot be licensed candidates.')
    target, receipt = stock.external(output), stock.external(license_file)
    require(target != receipt and not target.exists() and not receipt.exists(), 'Acquisition outputs already exist or collide; inspect before retrying.')
    result = stock.download('storyblocks', url, target, _licensed=True)
    # Probe again for dimensions: do not label a preview as the selected full-resolution file.
    import subprocess
    info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(target)],
                                    capture_output=True, text=True, check=True, timeout=60).stdout)
    require(any(s.get('codec_type') == 'video' and s.get('width') == plan['variant']['width']
                and s.get('height') == plan['variant']['height'] for s in info.get('streams', [])),
            'Acquired dimensions differ from the selected format; inspect the unregistered file.')
    timestamp = datetime.now(timezone.utc).isoformat()
    evidence = {'origin': 'Storyblocks', 'source_id': sid, 'source_url': source_page,
                'description': 'Acquired Storyblocks footage; editorial description must follow visual inspection.',
                'license': {'url': source_page, 'text': 'Storyblocks MCP get_download_url returned a licensed download for this selected item.',
                            'checked_at': timestamp, 'attribution': f"{plan.get('author') or 'Contributor not supplied'}, Storyblocks, {source_page}",
                            'acquisition': {'tool': plan['tool'], 'stock_item_id': sid, 'authorization': plan['authorization'],
                                            'response_sha256': hashlib.sha256(Path(response_file).read_bytes()).hexdigest(),
                                            'sha256': result['sha256'], 'format': plan['format']}}}
    stock.save_json_new(receipt, evidence)
    return dict(result, provenance_file=str(receipt), licensed_response_reused=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    record = sub.add_parser('capture')
    for flag in ('tool', 'arguments-file', 'output'):
        record.add_argument('--' + flag, required=True)
    details = sub.add_parser('details')
    for flag in ('result-file', 'provider-id', 'response-file', 'output'):
        details.add_argument('--' + flag, required=True)
    plan = sub.add_parser('prepare-acquisition')
    for flag in ('result-file', 'provider-id', 'authorization', 'output'):
        plan.add_argument('--' + flag, required=True)
    plan.add_argument('--min-width', type=int, default=1920)
    get = sub.add_parser('acquire')
    for flag in ('plan-file', 'response-file', 'url', 'output', 'license-file'):
        get.add_argument('--' + flag, required=True)
    args = vars(parser.parse_args())
    action = args.pop('action')
    try:
        result = {'capture': capture, 'details': import_details, 'prepare-acquisition': prepare_acquisition, 'acquire': acquire}[action](**args)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, TypeError):
        raise SystemExit('Storyblocks operation failed; inspect inputs and existing artifacts. No MCP call or automatic retry was made.') from None


if __name__ == '__main__':
    main()
