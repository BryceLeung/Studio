"""Offline MCP bridge tests. Never call the licensing tool or use live projects."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import storyblocks as sb
import stock


class StoryblocksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.item = {'id': 347279143, 'contentClass': 'video', 'title': 'Older and younger hands',
                     'details_url': 'https://www.storyblocks.com/video/stock/hands-347279143',
                     'duration': 13, 'aspectRatio': 16 / 9,
                     'thumbnail_url': 'https://dm0qx8t0i9gc9.cloudfront.net/thumbnails/1.jpg',
                     'preview_url': 'https://dm0qx8t0i9gc9.cloudfront.net/watermarks/1_P480.mp4?a=1&b=2'}
        self.details = {'id': self.item['id'], 'contentClass': 'video', 'duration': 13,
                        'contributor': {'username': 'Example'},
                        'download_formats': {'MP4': {'_1080p': {'width': 1920, 'height': 1080},
                                                     '_2160p': {'width': 3840, 'height': 2160}}},
                        'preview_urls': {f'_{h}p': f'https://dm0qx8t0i9gc9.cloudfront.net/watermarks/1_P{h}.mp4'
                                         for h in (180, 360, 480, 720)}}

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def result(self):
        hit = sb.normalize_search({'results': [self.item]}, 'elderly hands')['results'][0]
        hit = sb.details_result(hit, self.details)
        return self.write('details.json', {'provider': 'storyblocks', 'results': [hit]})

    def test_mcp_envelopes_query_and_error_validation(self):
        raw = {'results': [self.item], 'keywords': 'elderly hands'}
        for wrapped in (raw, {'structuredContent': raw}, {'content': [{'type': 'text', 'text': json.dumps(raw)}]}):
            self.assertEqual(sb.normalize_search(wrapped, 'elderly hands')['results'][0]['provider_id'], '347279143')
        for bad in ({'isError': True}, dict(raw, keywords='other'), {'results': [self.item, self.item]},
                    {'results': [dict(self.item, contentClass='audio')]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sb.normalize_search(bad, 'elderly hands')

    def test_details_preview_selection_never_exposes_acquisition(self):
        hit = stock.load_result(self.result(), self.item['id'])
        self.assertEqual(stock.select_variant(hit, 'samples')['height'], 180)
        self.assertEqual(stock.select_variant(hit, 'preview')['height'], 360)
        with self.assertRaisesRegex(ValueError, 'acquisition requires'):
            stock.select_variant(hit, 'acquire')
        with self.assertRaisesRegex(ValueError, 'ID mismatch'):
            sb.details_result(hit, dict(self.details, id=99))

    def test_capture_and_details_import_preserve_mcp_envelope_and_credit(self):
        args = self.write('arguments.json', {'arguments': {'contentClass': 'videos', 'stockItemId': self.item['id'],
                                                          'extended': 'download_formats'}})
        captured = self.root / 'captured.json'
        with patch.object(sb.sys, 'stdin', io.StringIO(json.dumps({'structuredContent': self.details}))):
            sb.capture('mcp__storyblocks__get_stock_item_details', args, captured)
        result = sb.normalize_search({'results': [self.item]}, 'hands')
        result_file = self.write('search.json', result)
        output = self.root / 'enriched.json'
        sb.import_details(result_file, self.item['id'], captured, output)
        self.assertEqual(stock.load_result(output, self.item['id'])['author'], 'Example')
        original = captured.read_bytes()
        with patch.object(sb.sys, 'stdin', io.StringIO(json.dumps(self.details))), self.assertRaisesRegex(ValueError, 'already exists'):
            sb.capture('mcp__storyblocks__get_stock_item_details', args, captured)
        self.assertEqual(captured.read_bytes(), original)

    def test_untrusted_domains_and_direct_download_are_refused(self):
        for url in ('http://www.storyblocks.com/a', 'https://storyblocks.com.evil.example/a',
                    'https://unrelated.cloudfront.net/a', 'https://user@www.storyblocks.com/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                stock.allowed_url(url, 'storyblocks')
        with self.assertRaisesRegex(ValueError, 'licensed acquisition'):
            stock.download('storyblocks', 'https://www.storyblocks.com/full.mp4', self.root / 'clip.mp4')
        with self.assertRaisesRegex(ValueError, 'uses MCP'):
            stock.search('storyblocks', 'hands', self.root / 'cache')

    def plan(self):
        plan = self.root / 'plan.json'
        sb.prepare_acquisition(self.result(), str(self.item['id']), 'User: download this selected clip', plan)
        return plan

    def test_plan_selects_full_resolution_and_requires_user_request(self):
        with self.assertRaisesRegex(ValueError, 'explicit user'):
            sb.acquisition_plan(self.result(), str(self.item['id']), '')
        plan = sb.acquisition_plan(self.result(), str(self.item['id']), 'Download clip')
        self.assertEqual(plan['variant']['width'], 3840)
        self.assertEqual(plan['arguments'], {'contentClass': 'videos', 'stockItemId': self.item['id']})
        with self.assertRaisesRegex(ValueError, 'minimum width'):
            sb.acquisition_plan(self.result(), str(self.item['id']), 'Download clip', 8000)

    def response(self, plan, url):
        call = json.loads(plan.read_text())
        return self.write('download-response.json', {'tool': call['tool'], 'arguments': call['arguments'],
                                                    'response': {'urls': {'MP4': {'_2160p': url}}}})

    def test_acquire_reuses_signed_response_and_binds_receipt_to_bytes(self):
        plan = self.plan()
        url = 'https://dm0qx8t0i9gc9.cloudfront.net/downloads/full.mp4?sig=a%2Bb&expires=123'
        response = self.response(plan, url)
        output, receipt = self.root / 'full.mp4', self.root / 'provenance.json'
        content = b'fixture video bytes'
        def downloaded(provider, selected, target, **kwargs):
            self.assertEqual(selected, url)
            self.assertTrue(kwargs['_licensed'])
            target.write_bytes(content)
            return {'sha256': hashlib.sha256(content).hexdigest(), 'output': str(target)}
        dimensions = SimpleNamespace(stdout=json.dumps({'streams': [{'codec_type': 'video', 'width': 3840, 'height': 2160}]}))
        with patch.object(stock, 'download', side_effect=downloaded) as get, patch('subprocess.run', return_value=dimensions):
            sb.acquire(plan, response, url, output, receipt)
            evidence = json.loads(receipt.read_text())
            self.assertEqual(evidence['license']['acquisition']['sha256'], hashlib.sha256(content).hexdigest())
            self.assertEqual(evidence['source_id'], str(self.item['id']))
            with self.assertRaisesRegex(ValueError, 'already exist'):
                sb.acquire(plan, response, url, output, receipt)
            self.assertEqual(get.call_count, 1)

    def test_acquisition_refuses_mismatched_item_url_and_watermark(self):
        plan = self.plan()
        url = 'https://dm0qx8t0i9gc9.cloudfront.net/downloads/full.mp4?sig=x'
        response = self.response(plan, url)
        with patch.object(stock, 'download') as get:
            with self.assertRaisesRegex(ValueError, 'character-for-character'):
                sb.acquire(plan, response, url + 'changed', self.root / 'a.mp4', self.root / 'a.json')
            wrapped = json.loads(response.read_text())
            wrapped['arguments']['stockItemId'] = 99
            response.write_text(json.dumps(wrapped))
            with self.assertRaisesRegex(ValueError, 'exact prepared'):
                sb.acquire(plan, response, url, self.root / 'a.mp4', self.root / 'a.json')
            preview = self.item['preview_url']
            response = self.response(plan, preview)
            with self.assertRaisesRegex(ValueError, 'Watermarked'):
                sb.acquire(plan, response, preview, self.root / 'a.mp4', self.root / 'a.json')
            get.assert_not_called()

    def test_full_resolution_mismatch_never_publishes_receipt(self):
        plan = self.plan()
        url = 'https://dm0qx8t0i9gc9.cloudfront.net/downloads/full.mp4'
        response = self.response(plan, url)
        output, receipt = self.root / 'wrong.mp4', self.root / 'wrong.json'
        def downloaded(*args, **kwargs):
            output.write_bytes(b'wrong resolution')
            return {'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
        dimensions = SimpleNamespace(stdout=json.dumps({'streams': [{'codec_type': 'video', 'width': 640, 'height': 360}]}))
        with patch.object(stock, 'download', side_effect=downloaded), patch('subprocess.run', return_value=dimensions):
            with self.assertRaisesRegex(ValueError, 'dimensions differ'):
                sb.acquire(plan, response, url, output, receipt)
        self.assertFalse(receipt.exists())


if __name__ == '__main__':
    unittest.main()
