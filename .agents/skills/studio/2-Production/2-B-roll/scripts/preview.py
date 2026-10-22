"""Create labelled frame sheets and short silent review clips from local video.

Artifacts use external scratch or dedicated project search work folders. They support visual review;
creating them never constitutes a suitability assessment or asset registration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import stock


class PreviewError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PreviewError(message)


def number(value):
    return type(value) in (float, int) and math.isfinite(value)


def source_info(file):
    file = Path(file).resolve(strict=True)
    require(file.is_file(), 'Supply a local video file.')
    metadata = stock.probe(file)
    stat = file.stat()
    return file, {'path': str(file), 'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                  'duration_seconds': metadata['duration_seconds']}


def unchanged(file, info):
    stat = file.stat()
    require((stat.st_size, stat.st_mtime_ns) == (info['size_bytes'], info['mtime_ns']),
            'Source video changed during preview creation; retry with a stable file.')


def ffmpeg(arguments, *, cwd=None):
    binary = shutil.which('ffmpeg')
    require(binary is not None, 'ffmpeg must be available on PATH for video previews.')
    try:
        subprocess.run([binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', *map(str, arguments)],
                       capture_output=True, check=True, timeout=60, cwd=cwd)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b'').decode('utf-8', 'replace')[-1200:]
        raise PreviewError('FFmpeg could not create the local preview: ' + detail.strip()) from None
    except (OSError, subprocess.SubprocessError):
        raise PreviewError('FFmpeg could not create the preview. Check video decoding and required filters/encoders.') from None


def sample_times(duration, count=8, start=0.0, end=None):
    end = duration if end is None else end
    require(type(count) is int and 1 <= count <= 24, 'Choose 1 to 24 sample frames.')
    require(all(number(v) for v in (duration, start, end)) and 0 <= start < end <= duration,
            'Choose a positive sampling window inside the video duration.')
    # Midpoints avoid seeking to EOF and cover the whole requested window.
    return [start + (end - start) * (i + 0.5) / count for i in range(count)]


def digest(file):
    value = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def label_font():
    """Use an actual font file; Windows FFmpeg may lack a fontconfig setup."""
    candidates = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/arial.ttf',
                  Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                  Path('/System/Library/Fonts/Supplemental/Arial.ttf')]
    return next((path for path in candidates if path.is_file()), None)


def shortlist(result_file, output_dir, limit=20, columns=4):
    """Download one bounded result page's thumbnails and make a labelled review gallery."""
    require(type(limit) is int and 1 <= limit <= 24, 'Choose a thumbnail limit from 1 to 24.')
    require(type(columns) is int and 1 <= columns <= 6, 'Choose 1 to 6 columns.')
    source = Path(result_file)
    require(source.stat().st_size <= 8 * 1024 * 1024, 'Search result file is too large.')
    data = json.loads(source.read_text(encoding='utf-8-sig'))
    data = data.get('result', data)
    ids = [str(hit['provider_id']) for hit in data['results'][:limit]]
    require(bool(ids) and len(ids) == len(set(ids)) and all(re.fullmatch(r'[0-9]{1,20}', key) for key in ids),
            'Search page has no results or ambiguous provider IDs.')
    # Validate all selected result identities before any network operation.
    hits = [stock.load_result(source, key) for key in ids]
    target = stock.external(output_dir)
    require(not target.exists(), 'Output directory already exists; preview overwrites are refused.')
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'studio.broll.thumbnail-shortlist', 'schema_version': 1,
              'result_file': str(source.resolve()), 'provider': data['provider'], 'items': [],
              'content_reviewed': False, 'candidate_registered': False}
    with tempfile.TemporaryDirectory(prefix='.broll-shortlist-', dir=target.parent) as temporary:
        temporary = Path(temporary)
        font = label_font()
        if font is not None:
            shutil.copyfile(font, temporary / 'label-font.ttf')
        font_option = 'fontfile=label-font.ttf:' if font is not None else ''
        ready = 0
        for hit in hits:
            key = hit['provider_id']
            item = {'provider_id': key, 'page_url': hit['page_url'], 'author': hit.get('author')}
            try:
                stock.thumbnail(source, key, temporary / f'thumbnail-{key}.jpg')
                filters = ('scale=320:180:force_original_aspect_ratio=decrease,'
                           'pad=320:180:(ow-iw)/2:(oh-ih)/2,setsar=1,'
                           f"drawtext={font_option}text='ID {key}':x=8:y=8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.7")
                ffmpeg(['-i', temporary / f'thumbnail-{key}.jpg', '-vf', filters, '-frames:v', '1',
                        temporary / f'gallery-{ready + 1:04d}.jpg'], cwd=temporary)
                ready += 1
                item.update(status='ready', file=f'thumbnail-{key}.jpg', cell=ready)
            except (stock.StockError, PreviewError):
                # Failures never receive a visual suitability decision or score.
                item.update(status='unavailable', next_action='Use a small video preview; no image reviewed.')
            report['items'].append(item)
        if ready:
            ffmpeg(['-framerate', '1', '-start_number', '1', '-i', temporary / 'gallery-%04d.jpg',
                    '-vf', f'tile={columns}x{math.ceil(ready / columns)}:padding=4:margin=4:color=black',
                    '-frames:v', '1', temporary / 'thumbnail-sheet.jpg'])
            report['contact_sheet'] = 'thumbnail-sheet.jpg'
        if font is not None:
            (temporary / 'label-font.ttf').unlink()
        (temporary / 'shortlist.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        target.mkdir()
        created = []
        try:
            for path in temporary.iterdir():
                with path.open('rb') as stream, (target / path.name).open('xb') as output:
                    created.append(target / path.name)
                    shutil.copyfileobj(stream, output)
        except BaseException:
            for path in created:
                path.unlink()
            if not any(target.iterdir()):
                target.rmdir()
            raise
    return {'output_dir': str(target), 'contact_sheet': str(target / 'thumbnail-sheet.jpg') if ready else None,
            'manifest': str(target / 'shortlist.json'), 'ready': ready, 'unavailable': len(hits) - ready,
            'content_reviewed': False, 'candidate_registered': False}


def sheet(file, output_dir, count=8, start=0.0, end=None, width=480, columns=4):
    require(type(width) is int and 160 <= width <= 960 and width % 16 == 0,
            'Sheet cell width must be a multiple of 16 between 160 and 960.')
    require(type(columns) is int and 1 <= columns <= 6, 'Choose 1 to 6 columns.')
    file, info = source_info(file)
    times = sample_times(info['duration_seconds'], count, start, end)
    target = stock.external(output_dir)
    require(not target.exists(), 'Output directory already exists; preview overwrites are refused.')
    target.parent.mkdir(parents=True, exist_ok=True)
    height = width * 9 // 16
    rows = math.ceil(count / columns)
    manifest = {'schema': 'studio.broll.preview', 'schema_version': 1, 'source': info,
                'source_sha256': digest(file), 'sample_window': [start, info['duration_seconds'] if end is None else end],
                'columns': columns, 'rows': rows, 'frames': [], 'contact_sheet': 'contact-sheet.jpg',
                'timing': 'Labels are requested source seek times; sampled frames are not a continuous motion review.',
                'content_reviewed': False, 'candidate_registered': False}
    # Build entirely in scratch, then exclusively publish a new directory.
    # os.rename is not used: POSIX could replace an existing empty directory.
    with tempfile.TemporaryDirectory(prefix='.broll-preview-', dir=target.parent) as temporary:
        temporary = Path(temporary)
        font = label_font()
        if font is not None:
            shutil.copyfile(font, temporary / 'label-font.ttf')
        font_option = 'fontfile=label-font.ttf:' if font is not None else ''
        for index, timestamp in enumerate(times, 1):
            name = f'frame-{index:04d}.jpg'
            caption = f'{index:02d} | t={timestamp:.3f}s'
            filters = (f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                       f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,'
                       f"drawtext={font_option}text='{caption}':x=10:y=10:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.7")
            ffmpeg(['-ss', f'{timestamp:.9f}', '-i', file, '-vf', filters, '-frames:v', '1', temporary / name], cwd=temporary)
            require((temporary / name).is_file() and (temporary / name).stat().st_size > 0, 'Video sample frame was not produced.')
            manifest['frames'].append({'index': index, 'seek_seconds': timestamp, 'file': name})
        ffmpeg(['-framerate', '1', '-start_number', '1', '-i', temporary / 'frame-%04d.jpg',
                '-vf', f'tile={columns}x{rows}:padding=4:margin=4:color=black', '-frames:v', '1', temporary / 'contact-sheet.jpg'])
        require((temporary / 'contact-sheet.jpg').is_file(), 'Contact sheet was not produced.')
        if font is not None:
            (temporary / 'label-font.ttf').unlink()
        unchanged(file, info)
        (temporary / 'preview.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        target.mkdir()  # Atomic exclusivity check; existing outputs are never replaced.
        created = []
        try:
            for path in temporary.iterdir():
                with path.open('rb') as source, (target / path.name).open('xb') as output:
                    created.append(target / path.name)
                    shutil.copyfileobj(source, output)
        except BaseException:
            # Only remove files from the directory this invocation just created.
            for path in created:
                path.unlink()
            if not any(target.iterdir()):
                target.rmdir()
            raise
    return {'output_dir': str(target), 'contact_sheet': str(target / 'contact-sheet.jpg'),
            'manifest': str(target / 'preview.json'), 'frames': manifest['frames'],
            'content_reviewed': False, 'candidate_registered': False}


def segment(file, output, start, duration=5.0, width=960):
    require(number(start) and number(duration) and start >= 0 and 0 < duration <= 30,
            'Choose a nonnegative start and a segment duration up to 30 seconds.')
    require(type(width) is int and 160 <= width <= 1920 and width % 2 == 0, 'Choose an even width from 160 to 1920.')
    file, info = source_info(file)
    require(start + duration <= info['duration_seconds'], 'Review segment exceeds source duration.')
    target = stock.external(output)
    require(target.suffix.lower() == '.mp4' and not target.exists(), 'Use a new scratch .mp4 output; overwriting is refused.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.broll-segment-', dir=target.parent) as temporary:
        temporary = Path(temporary) / 'segment.mp4'
        ffmpeg(['-ss', f'{start:.9f}', '-i', file, '-t', f'{duration:.9f}', '-vf', f'scale={width}:-2',
                '-r', '12', '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '28', temporary])
        metadata = stock.probe(temporary)
        unchanged(file, info)
        os.link(temporary, target)  # Publish without overwrite, including concurrent races.
    return {'output': str(target), 'source': info, 'source_range_seconds': [start, start + duration],
            'technical': metadata, 'audio': False, 'content_reviewed': False, 'candidate_registered': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    gallery = sub.add_parser('shortlist', help='Download a bounded set of provider thumbnails and build an ID-labelled review sheet')
    gallery.add_argument('--result-file', required=True)
    gallery.add_argument('--output-dir', required=True)
    gallery.add_argument('--limit', type=int, default=20)
    gallery.add_argument('--columns', type=int, default=4)
    frames = sub.add_parser('sheet', help='Labelled contact sheet, individual frames and timing/provenance JSON')
    frames.add_argument('--file', required=True)
    frames.add_argument('--output-dir', required=True)
    frames.add_argument('--count', type=int, default=8)
    frames.add_argument('--start', type=float, default=0.0)
    frames.add_argument('--end', type=float)
    frames.add_argument('--width', type=int, default=480)
    frames.add_argument('--columns', type=int, default=4)
    clip = sub.add_parser('segment', help='Short, silent, low-bitrate review video')
    clip.add_argument('--file', required=True)
    clip.add_argument('--output', required=True)
    clip.add_argument('--start', type=float, required=True)
    clip.add_argument('--duration', type=float, default=5.0)
    clip.add_argument('--width', type=int, default=960)
    args = vars(parser.parse_args())
    action = args.pop('action')
    try:
        result = {'sheet': sheet, 'segment': segment, 'shortlist': shortlist}[action](**args)
        print(json.dumps(result, indent=2))
    except (PreviewError, stock.StockError, OSError):
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, (PreviewError, stock.StockError)) else 'Local preview operation failed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
