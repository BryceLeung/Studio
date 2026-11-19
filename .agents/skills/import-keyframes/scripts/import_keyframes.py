"""Split a labelled contact sheet into checked PNG crops (default: 3 columns, 2 rows)."""

import argparse
import csv
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageOps


NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,119}\.png", re.IGNORECASE | re.ASCII)
RESERVED = re.compile(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", re.IGNORECASE)


class ImportErrorDetail(ValueError):
    pass


@dataclass
class Crop:
    slot: int
    name: str
    image: Image.Image
    warnings: list[str]


def safe_name(text):
    return bool(NAME.fullmatch(text) and not RESERVED.fullmatch(text.split('.')[0].rstrip())
                and not text[:-4].endswith(('.', ' ')))


def label_box(cell, start):
    """Locate an inset white label strip when the sheet uses boxed labels."""
    w, h = cell.size
    opaque = Image.alpha_composite(Image.new('RGBA', cell.size, 'black'), cell).convert('L')
    center = opaque.crop((int(w * .24), start, int(w * .76), h))
    sample = center.load()
    rows = []
    for y in range(center.height):
        if sum(sample[x, y] >= 242 for x in range(0, center.width, 3)) < center.width * .29:
            continue
        if min(opaque.getpixel((int(w * .035), start + y)),
               opaque.getpixel((int(w * .965), start + y))) >= 235:
            continue
        rows.append(start + y)
    groups = []
    for row in rows:
        if not groups or row - groups[-1][-1] > max(3, h // 20):
            groups.append([])
        groups[-1].append(row)
    boxes = [group for group in groups if len(group) >= max(4, h // 100)]
    return (boxes[-1][0], boxes[-1][-1] + 1) if boxes else None


def read_lines(image, executable, psm):
    flat = Image.alpha_composite(Image.new('RGBA', image.size, 'white'), image).convert('L')
    flat = ImageOps.expand(flat.resize((flat.width * 2, flat.height * 2), Image.Resampling.LANCZOS),
                           border=12, fill='white')
    data = BytesIO()
    flat.save(data, 'PNG')
    try:
        result = subprocess.run([executable, 'stdin', 'stdout', '-l', 'eng', '--psm', str(psm), 'tsv'],
                                input=data.getvalue(), capture_output=True, timeout=20, check=True)
    except subprocess.CalledProcessError as exc:
        raise ImportErrorDetail(f"Tesseract failed: {exc.stderr.decode(errors='replace').strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ImportErrorDetail('Tesseract timed out') from exc
    groups = {}
    words = csv.DictReader(StringIO(result.stdout.decode('utf-8-sig').lstrip('\r\n')), delimiter='\t')
    if not {'level', 'text', 'block_num', 'par_num', 'line_num', 'top'}.issubset(words.fieldnames or ()):
        raise ImportErrorDetail('Tesseract returned an invalid TSV header')
    for word in words:
        if word['level'] != '5' or not word['text'].strip():
            continue
        key = tuple(word[field] for field in ('block_num', 'par_num', 'line_num'))
        groups.setdefault(key, []).append(word)
    return [(' '.join(word['text'].strip() for word in words),
             (min(int(word['top']) for word in words) - 12) // 2)
            for words in groups.values()]


def read_label(cell, executable):
    w, h = cell.size
    start = h * 3 // 4
    box = label_box(cell, start)
    if box:
        top, bottom = box
        # OCR only the inset strip, not the graphic or its outer border.
        mono = cell.convert('L')
        edges = [mono.crop((0, y, w, y + 1)).point(lambda p: 255 if p >= 242 else 0).getbbox()
                 for y in (top, bottom - 1)]
        left = max(edge[0] for edge in edges if edge)
        right = min(edge[2] for edge in edges if edge)
        if right > left:
            lines = read_lines(cell.crop((left, top, right, bottom)), executable, 7)
            if len(lines) == 1 and safe_name(lines[0][0]):
                return lines[0][0], max(1, top - 2), False
    lines = read_lines(cell.crop((0, start, w, h)), executable, 11)
    matches = [(text, y) for text, y in lines if safe_name(text)]
    if len(matches) == 1:
        text, y = matches[0]
        return text, max(1, box[0] - 2 if box else start + y - max(2, h // 100)), False
    return None, max(1, box[0] - 2) if box else None, bool(box or lines)


def visible(image):
    alpha = image.getchannel('A')
    hist = alpha.histogram()
    if sum(hist[32:]) < max(12, image.width * image.height // 10000):
        return False
    if hist[0] >= max(1, image.width * image.height // 100):
        return True
    # A flat, opaque blank panel also counts as empty.
    flat = Image.alpha_composite(Image.new('RGBA', image.size, 'white'), image)
    return any(hi - lo > 12 for lo, hi in flat.convert('RGB').resize((32, 32)).getextrema())


def alpha_check(image):
    alpha = image.getchannel('A')
    total = image.width * image.height
    hist = alpha.histogram()
    corner_size = max(2, min(image.size) // 25)
    if sum(hist[128:]) < max(12, total // 10000):
        return 'artwork alpha is too faint'
    upper_corners = [alpha.crop((x, 0, x + corner_size, corner_size)).histogram()[0]
                     for x in (0, image.width - corner_size)]
    if hist[0] < max(1, total // 100) or min(upper_corners) < corner_size ** 2 * .75:
        return 'no usable transparent background (opaque or filled margins)'
    return None


def inspect_sheet(image, executable, allow_opaque=False, *, grid='3x2'):
    w, h = image.size
    columns, rows = {'3x2': (3, 2), '2x3': (2, 3)}[grid]
    if w < 80 * columns or h < 80 * rows:
        raise ImportErrorDetail(f'Sheet is too small for a {columns}-column, {rows}-row grid')
    crops, skipped, errors, names = [], [], [], set()
    for slot in range(1, columns * rows + 1):
        row, col = divmod(slot - 1, columns)
        cell = image.crop((col * w // columns, row * h // rows,
                           (col + 1) * w // columns, (row + 1) * h // rows))
        name, cutoff, suspicious = read_label(cell, executable)
        crop = cell.crop((0, 0, cell.width, cutoff)) if cutoff is not None else cell
        if not visible(crop) and not name and not suspicious:
            skipped.append(slot)
            continue
        if not name:
            errors.append(f'Slot {slot}: no unambiguous, safe .png filename recognized')
            continue
        if not visible(crop):
            errors.append(f'Slot {slot} ({name}): label is present but artwork is missing or not visible')
            continue
        key = name.casefold()
        if key in names:
            errors.append(f'Slot {slot} ({name}): duplicate filename')
            continue
        names.add(key)
        failure = alpha_check(crop)
        warnings = []
        if failure:
            if not allow_opaque or failure == 'artwork alpha is too faint':
                errors.append(f'Slot {slot} ({name}): {failure}')
                continue
            warnings.append(f'{failure}; reference crop only')
        bounds = crop.getchannel('A').point(lambda value: 255 if value >= 128 else 0).getbbox()
        if not failure and bounds and (bounds[0] == 0 or bounds[1] == 0 or bounds[2] == crop.width):
            warnings.append('artwork touches a top or side edge; check for clipping')
        crops.append(Crop(slot, name, crop, warnings))
    if errors:
        raise ImportErrorDetail('\n'.join(errors))
    return crops, skipped


def publish(crops, directory):
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for crop in crops:
            path = directory / crop.name
            with path.open('xb') as stream:
                created.append(path)
                crop.image.save(stream, 'PNG')
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def find_tesseract():
    if sys.platform == 'win32':
        local = Path(__file__).resolve().parents[1] / '.venv-tools' / 'Tesseract-OCR' / 'tesseract.exe'
        if local.is_file():
            return str(local)
    return shutil.which('tesseract')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sheet', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--grid', choices=('3x2', '2x3'), default='3x2', help='columns x rows (default: 3x2)')
    parser.add_argument('--dry-run', action='store_true', help='validate and report without writing')
    parser.add_argument('--allow-opaque', action='store_true', help='keep the original background for reference only')
    args = parser.parse_args()
    executable = find_tesseract()
    if not executable:
        parser.exit(1, 'Import failed: Tesseract with English OCR data is required in the skill environment or on PATH.\n')
    try:
        with Image.open(args.sheet) as source:
            if getattr(source, 'n_frames', 1) != 1:
                raise ImportErrorDetail('Provide a single-frame contact sheet image')
            image = ImageOps.exif_transpose(source).convert('RGBA')
        crops, skipped = inspect_sheet(image, executable, args.allow_opaque, grid=args.grid)
        existing = {path.name.casefold() for path in args.output_dir.iterdir()} if args.output_dir.exists() else set()
        collisions = [crop.name for crop in crops if crop.name.casefold() in existing]
        if collisions:
            raise ImportErrorDetail(f"Refusing to overwrite: {', '.join(collisions)}")
        if crops and not args.dry_run:
            publish(crops, args.output_dir)
    except (OSError, ImportErrorDetail, Image.DecompressionBombError) as exc:
        parser.exit(1, f'Import failed:\n{exc}\n')
    for crop in crops:
        print(f'Slot {crop.slot}: {crop.name} ({crop.image.width}x{crop.image.height})')
        for warning in crop.warnings:
            print(f'  Warning: {warning}')
    if skipped:
        print('Empty slots skipped: ' + ', '.join(map(str, skipped)))
    print(f'Dry run: {len(crops)} file(s) ready; nothing written.' if args.dry_run
          else f'Wrote {len(crops)} PNG file(s) to {args.output_dir}')


if __name__ == '__main__':
    main()
