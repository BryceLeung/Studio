from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from import_keyframes import ImportErrorDetail, alpha_check, find_tesseract, inspect_sheet, safe_name


def sheet_with_poses(columns=3, rows=2):
    sheet = Image.new('RGBA', (400 * columns, 350 * rows))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=21)
    for x, y, name in [(0, 0, 'girl_shock.png'), (400 * (columns - 1), 350, 'girl_yell_open.png')]:
        draw.ellipse((x + 80, y + 35, x + 320, y + 285), fill='#a24c66')
        draw.rectangle((x + 25, y + 305, x + 375, y + 339), fill='white')
        draw.text((x + 200, y + 321), name, fill='black', font=font, anchor='mm')
    return sheet


class ValidationTests(unittest.TestCase):
    def test_grid_order_without_ocr(self):
        for grid, columns, rows, last_slot in [('3x2', 3, 2, 6), ('2x3', 2, 3, 4)]:
            with self.subTest(grid=grid):
                labels = [(None, None, False)] * 6
                labels[0] = ('girl_shock.png', 303, False)
                labels[last_slot - 1] = ('girl_yell_open.png', 303, False)
                options = {} if grid == '3x2' else {'grid': grid}
                with patch('import_keyframes.read_label', side_effect=labels):
                    crops, skipped = inspect_sheet(sheet_with_poses(columns, rows), 'not used', **options)
                self.assertEqual([crop.slot for crop in crops], [1, last_slot])
                self.assertEqual(skipped, [slot for slot in range(2, 7) if slot != last_slot])

    def test_portable_names(self):
        self.assertTrue(safe_name('girl_yell_open.png'))
        for invalid in ('../girl.png', 'dir/girl.png', 'dir\\girl.png', 'LPT1.test.png', 'art .png', 'art.jpg'):
            self.assertFalse(safe_name(invalid), invalid)

    def test_filled_alpha_and_faint_art_are_not_valid_sprites(self):
        self.assertIn('filled margins', alpha_check(Image.new('RGBA', (100, 100), (82, 42, 27, 255))))
        self.assertIn('too faint', alpha_check(Image.new('RGBA', (100, 100), (82, 42, 27, 75))))

    @patch('import_keyframes.read_label', return_value=(None, None, False))
    def test_visible_unlabelled_art_is_not_skipped(self, _read_label):
        with self.assertRaisesRegex(ImportErrorDetail, 'Slot 1: no unambiguous'):
            inspect_sheet(sheet_with_poses(), 'not used')

    @patch('import_keyframes.read_label')
    def test_duplicate_names_are_rejected_case_insensitively(self, read_label):
        blank = (None, None, False)
        read_label.side_effect = [('test.png', 303, False)] + [blank] * 4 + [('TEST.PNG', 303, False)]
        with self.assertRaisesRegex(ImportErrorDetail, 'Slot 6 .*duplicate'):
            inspect_sheet(sheet_with_poses(), 'not used')

    @patch('import_keyframes.read_label')
    def test_label_without_art_is_not_skipped(self, read_label):
        read_label.side_effect = [('empty.png', 300, False)] + [(None, None, False)] * 5
        with self.assertRaisesRegex(ImportErrorDetail, 'artwork is missing'):
            inspect_sheet(Image.new('RGBA', (800, 1050)), 'not used')


@unittest.skipUnless(find_tesseract(), 'requires local Tesseract')
class CliTests(unittest.TestCase):
    def test_legacy_grid_is_opt_in(self):
        crops, skipped = inspect_sheet(sheet_with_poses(2, 3), find_tesseract(), grid='2x3')
        self.assertEqual([(crop.slot, crop.name) for crop in crops],
                         [(1, 'girl_shock.png'), (4, 'girl_yell_open.png')])
        self.assertEqual(skipped, [2, 3, 5, 6])

    def test_ocr_dry_run_publish_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            sheet = Path(temporary) / 'sheet.png'
            output = Path(temporary) / 'output'
            sheet_with_poses().save(sheet)
            command = [sys.executable, str(Path(__file__).with_name('import_keyframes.py')),
                       str(sheet), '--output-dir', str(output)]
            dry_run = subprocess.run(command + ['--dry-run'], capture_output=True, text=True)
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertIn('Slot 6: girl_yell_open.png', dry_run.stdout)
            self.assertIn('Empty slots skipped: 2, 3, 4, 5', dry_run.stdout)
            self.assertFalse(output.exists())
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual({path.name for path in output.iterdir()}, {'girl_shock.png', 'girl_yell_open.png'})
            pose = output / 'girl_shock.png'
            original = pose.read_bytes()
            with Image.open(pose) as crop:
                self.assertEqual(crop.mode, 'RGBA')
                self.assertEqual(crop.getchannel('A').getbbox(), (80, 35, 321, 286))
                self.assertGreaterEqual(crop.height, 286)
                self.assertLess(crop.height, 305)
            repeat = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(repeat.returncode, 0)
            self.assertIn('Refusing to overwrite', repeat.stderr)
            self.assertEqual(pose.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
