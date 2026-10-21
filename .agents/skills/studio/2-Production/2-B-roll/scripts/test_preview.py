"""Offline preview tests, including local synthetic video when FFmpeg is installed."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import preview
import stock


class SamplingTests(unittest.TestCase):
    def test_midpoints_cover_window_without_sampling_eof(self):
        self.assertEqual(preview.sample_times(8, 4), [1, 3, 5, 7])
        self.assertEqual(preview.sample_times(10, 2, 2, 6), [3, 5])
        self.assertEqual(preview.sample_times(1, 1), [0.5])
        for duration in (0.001, 1, 10000):
            times = preview.sample_times(duration, 24)
            self.assertTrue(all(0 < t < duration for t in times))
            self.assertEqual(times, sorted(set(times)))

    def test_invalid_sample_counts_and_windows(self):
        for count in (0, 25, -1, 1.5, True, '8', None):
            with self.subTest(count=count), self.assertRaises(preview.PreviewError):
                preview.sample_times(10, count)
        for duration, start, end in ((0, 0, None), (-1, 0, None), (10, -1, 3),
                                     (10, 3, 3), (10, 4, 3), (10, 0, 11),
                                     (True, 0, None), (float('inf'), 0, None),
                                     (10, float('nan'), 3), (10, 0, float('inf')),
                                     (10, '0', 5), (10, False, 5)):
            with self.subTest(window=(duration, start, end)), self.assertRaises(preview.PreviewError):
                preview.sample_times(duration, start=start, end=end)


class PreviewValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='studio-preview-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'unchanged source fixture')
        self.info = {'duration_seconds': 10, 'size_bytes': self.source.stat().st_size,
                     'mtime_ns': self.source.stat().st_mtime_ns, 'path': str(self.source)}

    def test_invalid_sheet_dimensions(self):
        for width in (159, 961, 170, True, 480.0):
            with self.subTest(width=width), self.assertRaises(preview.PreviewError):
                preview.sheet(self.source, self.root / 'sheet', width=width)
        for columns in (0, 7, True, 2.0):
            with self.subTest(columns=columns), self.assertRaises(preview.PreviewError):
                preview.sheet(self.source, self.root / 'sheet', columns=columns)

    def test_segment_validation(self):
        for start, duration in ((-1, 1), (0, 0), (0, 31), (True, 1),
                                (0, float('nan')), (float('inf'), 1), ('0', 1)):
            with self.subTest(start=start, duration=duration), self.assertRaises(preview.PreviewError):
                preview.segment(self.source, self.root / 'out.mp4', start, duration)
        with patch.object(preview, 'source_info', return_value=(self.source, self.info)):
            with self.assertRaisesRegex(preview.PreviewError, 'exceeds'):
                preview.segment(self.source, self.root / 'out.mp4', 9, 2)
            with self.assertRaises(preview.PreviewError):
                preview.segment(self.source, self.root / 'out.jpg', 0, 1)
        for width in (159, 1922, 321, True, 960.0):
            with self.subTest(width=width), self.assertRaises(preview.PreviewError):
                preview.segment(self.source, self.root / 'out.mp4', 0, 1, width)

    def test_workspace_and_canonical_stage_outputs_refused(self):
        targets = (stock.WORKSPACE / 'preview-test-never-created',
                   self.root / '2-B-roll' / 'ASSET-000001')
        with patch.object(preview, 'source_info', return_value=(self.source, self.info)), \
                patch.object(preview, 'ffmpeg') as encode:
            for target in targets:
                with self.subTest(target=target), self.assertRaises(stock.StockError):
                    preview.sheet(self.source, target)
                with self.assertRaises(stock.StockError):
                    preview.segment(self.source, target / 'out.mp4', 0, 1)
                self.assertFalse(target.exists())
            encode.assert_not_called()

    def test_existing_outputs_refused_without_modification(self):
        folder = self.root / 'sheet'
        folder.mkdir()
        sentinel = folder / 'keep.txt'
        sentinel.write_bytes(b'preserve')
        clip = self.root / 'out.mp4'
        clip.write_bytes(b'preserve clip')
        with patch.object(preview, 'source_info', return_value=(self.source, self.info)), \
                patch.object(preview, 'ffmpeg') as encode:
            with self.assertRaises(preview.PreviewError):
                preview.sheet(self.source, folder)
            with self.assertRaises(preview.PreviewError):
                preview.segment(self.source, clip, 0, 1)
            encode.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b'preserve')
        self.assertEqual(clip.read_bytes(), b'preserve clip')

    def test_decode_failure_cleans_scratch_and_preserves_source(self):
        before = self.source.read_bytes()
        def fail(arguments, **kwargs):
            Path(arguments[-1]).write_bytes(b'partial')
            raise preview.PreviewError('synthetic decoder failure')
        with patch.object(preview, 'source_info', return_value=(self.source, self.info)), \
                patch.object(preview, 'ffmpeg', side_effect=fail):
            with self.assertRaises(preview.PreviewError):
                preview.sheet(self.source, self.root / 'sheet')
            with self.assertRaises(preview.PreviewError):
                preview.segment(self.source, self.root / 'out.mp4', 0, 1)
        self.assertEqual(list(self.root.iterdir()), [self.source])
        self.assertEqual(self.source.read_bytes(), before)

    def test_failed_publication_removes_only_new_directory(self):
        def fake_frame(arguments, **kwargs):
            Path(arguments[-1]).write_bytes(b'frame')
        with patch.object(preview, 'source_info', return_value=(self.source, self.info)), \
                patch.object(preview, 'ffmpeg', side_effect=fake_frame), \
                patch.object(preview.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                preview.sheet(self.source, self.root / 'sheet', count=1)
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_shortlist_downloads_selected_thumbnails_and_records_without_scoring(self):
        search = self.root / 'search.json'
        search.write_text(json.dumps({'provider': 'pixabay', 'results': [
            {'provider': 'pixabay', 'provider_id': str(i), 'page_url': f'https://pixabay.com/videos/id-{i}/', 'author': 'Creator'}
            for i in (1, 2, 3)]}))
        def thumbnail(result_file, provider_id, output):
            if provider_id == '2':
                raise stock.StockError('No thumbnail')
            Path(output).write_bytes(b'thumbnail')
        def encode(arguments, **kwargs):
            Path(arguments[-1]).write_bytes(b'review image')
        output = self.root / 'shortlist'
        with patch.object(stock, 'thumbnail', side_effect=thumbnail) as download, patch.object(preview, 'ffmpeg', side_effect=encode):
            report = preview.shortlist(search, output, limit=2)
        self.assertEqual(download.call_count, 2)
        self.assertEqual((report['ready'], report['unavailable']), (1, 1))
        saved = json.loads((output / 'shortlist.json').read_text())
        self.assertEqual([item['status'] for item in saved['items']], ['ready', 'unavailable'])
        self.assertFalse(saved['content_reviewed'])
        self.assertFalse(saved['candidate_registered'])
        self.assertNotIn('score', saved['items'][0])
        with patch.object(stock, 'thumbnail') as download, self.assertRaises(preview.PreviewError):
            preview.shortlist(search, output)
        download.assert_not_called()

    def test_shortlist_validates_inputs_before_network_and_preserves_failures(self):
        search = self.root / 'search.json'
        search.write_text(json.dumps({'provider': 'pixabay', 'results': [
            {'provider': 'pixabay', 'provider_id': '3', 'page_url': 'https://pixabay.com/videos/id-3/'}]}))
        with patch.object(stock, 'thumbnail', side_effect=stock.StockError('Unavailable')):
            report = preview.shortlist(search, self.root / 'unavailable')
        self.assertIsNone(report['contact_sheet'])
        self.assertEqual(report['unavailable'], 1)
        self.assertTrue(Path(report['manifest']).is_file())
        with patch.object(stock, 'thumbnail') as download:
            for limit in (0, 25, True):
                with self.assertRaises(preview.PreviewError):
                    preview.shortlist(search, self.root / 'invalid', limit=limit)
            search.write_text(json.dumps({'provider': 'pixabay', 'results': []}))
            with self.assertRaises(preview.PreviewError):
                preview.shortlist(search, self.root / 'empty')
            download.assert_not_called()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and ffprobe required')
class RealVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='studio-preview-video-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'synthetic.mp4'
        subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=12:duration=3',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                        '-shortest', str(self.source)], check=True, capture_output=True, timeout=60)
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.before_stat = self.source.stat()

    def assert_source_unchanged(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)
        self.assertEqual(self.source.stat().st_mtime_ns, self.before_stat.st_mtime_ns)

    def test_labelled_frames_sheet_and_provenance_manifest(self):
        target = self.root / 'sheet'
        result = preview.sheet(self.source, target, count=4, start=0, end=3, width=320, columns=2)
        manifest = json.loads((target / 'preview.json').read_text())
        self.assertEqual(manifest['source_sha256'], self.before)
        self.assertEqual(manifest['sample_window'], [0, 3])
        self.assertEqual([f['seek_seconds'] for f in manifest['frames']], [0.375, 1.125, 1.875, 2.625])
        self.assertEqual((manifest['rows'], manifest['columns']), (2, 2))
        self.assertEqual(len(list(target.glob('frame-*.jpg'))), 4)
        for frame in manifest['frames']:
            self.assertGreater((target / frame['file']).stat().st_size, 100)
        self.assertGreater((target / 'contact-sheet.jpg').stat().st_size, 100)
        # A successful drawtext execution exercises actual labelled-frame encoding.
        for data in (manifest, result):
            self.assertIs(data['content_reviewed'], False)
            self.assertIs(data['candidate_registered'], False)
        self.assertEqual(set(p.name for p in target.iterdir()),
                         {'preview.json', 'contact-sheet.jpg', *[f['file'] for f in manifest['frames']]})
        self.assert_source_unchanged()

    def test_segment_is_short_silent_and_keeps_source(self):
        output = self.root / 'segment.mp4'
        result = preview.segment(self.source, output, 0.5, 1, 320)
        data = json.loads(subprocess.run([shutil.which('ffprobe'), '-v', 'error', '-show_streams',
                                         '-show_format', '-of', 'json', str(output)],
                                        check=True, capture_output=True, text=True, timeout=60).stdout)
        self.assertEqual([s['codec_type'] for s in data['streams']], ['video'])
        self.assertEqual(data['streams'][0]['width'], 320)
        self.assertAlmostEqual(float(data['format']['duration']), 1, delta=0.15)
        self.assertEqual(result['source_range_seconds'], [0.5, 1.5])
        for key in ('audio', 'content_reviewed', 'candidate_registered'):
            self.assertIs(result[key], False)
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'synthetic.mp4', 'segment.mp4'})
        self.assert_source_unchanged()


if __name__ == '__main__':
    unittest.main()
