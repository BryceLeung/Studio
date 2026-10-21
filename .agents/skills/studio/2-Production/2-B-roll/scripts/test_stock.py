"""Offline tests: python -m unittest discover -s <scripts> -p test_stock.py."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import stock


class StockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_normalizes_both_providers(self):
        pexels = {"videos": [{"id": 1, "url": "https://pexels.com/video/1/",
                              "user": {"name": "Author"}, "duration": 4,
                              "video_files": [{"id": 2, "link": "https://videos.pexels.com/1.mp4"}]}]}
        pixabay = {"hits": [{"id": 3, "pageURL": "https://pixabay.com/videos/id-3/",
                            "user": "Filmmaker", "duration": 5,
                            "videos": {"large": {"url": "https://cdn.pixabay.com/3.mp4"}}}]}
        self.assertEqual(stock.normalize("pexels", pexels)[0]["provider_id"], "1")
        self.assertEqual(stock.normalize("pixabay", pixabay)[0]["variants"][0]["id"], "large")

    @patch.dict(os.environ, {"PIXABAY_API_KEY": "secret-pixabay", "PEXELS_API_KEY": "secret-pexels"})
    def test_pixabay_cache_24_hours_no_secrets(self):
        raw = {"hits": [{"id": 3, "pageURL": "https://pixabay.com/videos/3/",
                         "user": "secret-pixabay", "duration": 3, "videos": {}}]}
        with patch.object(stock, "open_url", return_value=io.BytesIO(json.dumps(raw).encode())) as network:
            first = stock.search("pixabay", "family", self.root)
            again = stock.search("pixabay", "family", self.root)
            self.assertEqual(first, again)
            self.assertEqual(network.call_count, 1)
        cache = next(self.root.glob("*.json"))
        self.assertNotIn("secret-pixabay", cache.read_text())
        data = json.loads(cache.read_text())
        data["cached_at"] -= stock.TTL + 1
        cache.write_text(json.dumps(data))
        with patch.object(stock, "open_url", return_value=io.BytesIO(b'{"hits":[]}')) as network:
            stock.search("pixabay", "family", self.root)
            network.assert_called_once()

    @patch.dict(os.environ, {"PEXELS_API_KEY": "test-key"})
    def test_pexels_endpoint_and_auth(self):
        with patch.object(stock, "open_url", return_value=io.BytesIO(b'{"videos":[]}')) as network:
            stock.search("pexels", "hands", self.root)
            args, kwargs = network.call_args
            self.assertTrue(args[0].startswith("https://api.pexels.com/v1/videos/search?"))
            self.assertNotIn("test-key", args[0])
            self.assertEqual(args[2], {"Authorization": "test-key"})
            self.assertTrue(kwargs["api"])

    def test_url_and_workspace_rejections(self):
        for url in ("https://pixabay.com.evil.test/a", "http://pixabay.com/a",
                    "https://user:pass@pixabay.com/a", "https://pixabay.com:8080/a",
                    "https://pixabay.com/a?key=secret", "https://pexels.com/a"):
            with self.subTest(url=url), self.assertRaises(stock.StockError):
                stock.allowed_url(url, "pixabay")
        with self.assertRaises(stock.StockError):
            stock.external(stock.WORKSPACE / "scratch")
        with self.assertRaises(stock.StockError):
            stock.external(self.root / "2-B-roll" / "ASSET-000001")

    def test_redirect_policies(self):
        request = urllib.request.Request("https://api.pexels.com/v1/videos/search",
                                         headers={"Authorization": "secret"})
        with self.assertRaises(stock.StockError):
            stock.SafeRedirect("pexels", True).redirect_request(
                request, None, 302, "redirect", {}, "https://api.pexels.com/new")
        with self.assertRaises(stock.StockError):
            stock.SafeRedirect("pexels").redirect_request(
                request, None, 302, "redirect", {}, "https://evil.test/a")
        redirect = stock.SafeRedirect("pexels").redirect_request(
            request, None, 302, "redirect", {}, "https://videos.pexels.com/a.mp4")
        self.assertIsNone(redirect.get_header("Authorization"))

    def test_http_errors_hide_body_url_and_key(self):
        error = urllib.error.HTTPError("https://pixabay.com/?key=SECRET", 429, "SECRET", {}, None)
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(stock.StockError) as raised:
                stock.open_url("https://pixabay.com/api/videos/?key=SECRET", "pixabay", api=True)
        self.assertNotIn("SECRET", str(raised.exception))
        self.assertIn("429", str(raised.exception))

    def test_api_requests_identify_application_and_request_json(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(b'{}')
            with stock.open_url('https://pixabay.com/api/videos/?key=local-test', 'pixabay', api=True):
                pass
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('User-agent'), 'Studio B-roll/1.0')
            self.assertEqual(request.get_header('Accept'), 'application/json')

    @patch.object(stock.shutil, "which", return_value="ffprobe")
    def test_download_verified_and_no_overwrite(self, _):
        output = self.root / "selected.mp4"
        with patch.object(stock, "open_url", return_value=io.BytesIO(b"video")), patch.object(
                stock, "probe", return_value={"duration_seconds": 4}):
            result = stock.download("pixabay", "https://cdn.pixabay.com/a.mp4", output)
        self.assertEqual(output.read_bytes(), b"video")
        self.assertFalse(result["candidate_registered"])
        self.assertFalse(result["content_reviewed"])
        with self.assertRaises(stock.StockError):
            stock.download("pixabay", "https://cdn.pixabay.com/a.mp4", output)
        self.assertEqual(list(self.root.glob("*.part")), [])

    @patch.object(stock.shutil, "which", return_value="ffprobe")
    def test_invalid_download_cleans_scratch(self, _):
        for content in (b"", b"html error"):
            with self.subTest(content=content):
                with patch.object(stock, "open_url", return_value=io.BytesIO(content)), patch.object(
                        stock, "probe", side_effect=stock.StockError("Invalid video")):
                    with self.assertRaises(stock.StockError):
                        stock.download("pexels", "https://videos.pexels.com/a.mp4", self.root / "bad.mp4")
                self.assertEqual(list(self.root.iterdir()), [])

    @patch.object(stock.shutil, "which", return_value="ffprobe")
    def test_probe_rejects_audio_and_accepts_video(self, _):
        from subprocess import CompletedProcess
        for streams, expected in (([{"codec_type": "audio"}], False),
                                  ([{"codec_type": "video", "width": 1920, "height": 1080}], True)):
            payload = json.dumps({"streams": streams, "format": {"duration": "4"}})
            with patch.object(stock.subprocess, "run", return_value=CompletedProcess([], 0, payload)):
                if expected:
                    self.assertEqual(stock.probe(self.root / "video")["duration_seconds"], 4)
                else:
                    with self.assertRaises(stock.StockError):
                        stock.probe(self.root / "video")

    def saved_hit(self, variants=None, thumbnails=None):
        hit = {"provider": "pixabay", "provider_id": "3", "page_url": "https://pixabay.com/videos/3/",
               "variants": variants or [], "thumbnails": thumbnails or []}
        path = self.root / "results.json"
        path.write_text(json.dumps({"provider": "pixabay", "results": [hit]}))
        return path, hit

    def variants(self):
        return [{"id": str(w), "width": w, "height": w // 2,
                 "url": f"https://cdn.pixabay.com/{w}.mp4"} for w in (3840, 480, 1920, 640, 960)]

    def test_preview_and_acquisition_select_by_real_dimensions(self):
        _, hit = self.saved_hit(self.variants())
        self.assertEqual(stock.select_variant(hit)["width"], 640)
        self.assertEqual(stock.select_variant(hit, "acquire")["width"], 3840)
        hit["variants"].reverse()
        self.assertEqual(stock.select_variant(hit)["width"], 640)
        hit["variants"] = [v for v in hit["variants"] if v["width"] < 640]
        self.assertEqual(stock.select_variant(hit)["width"], 480)
        with self.assertRaisesRegex(stock.StockError, "lower --min-width"):
            stock.select_variant(hit, "acquire")
        self.assertEqual(stock.select_variant(hit, "acquire", 400)["width"], 480)

    def test_variant_missing_invalid_dimensions_and_hls_rejected(self):
        _, hit = self.saved_hit([{"width": None}, {"width": True, "height": 40, "url": "https://cdn.pixabay.com/v"},
                                {"width": 640, "height": 360, "url": "https://cdn.pixabay.com/v.m3u8"}])
        with self.assertRaisesRegex(stock.StockError, "valid dimensions"):
            stock.select_variant(hit)
        hit["variants"] = [{"width": 640, "height": 360, "url": "https://evil.test/a.mp4"}]
        with self.assertRaises(stock.StockError):
            stock.select_variant(hit)

    @patch.object(stock.shutil, "which", return_value="ffprobe")
    def test_selected_preview_reports_selection_and_never_reviews(self, _):
        path, _ = self.saved_hit(self.variants())
        with patch.object(stock, "open_url", return_value=io.BytesIO(b"video")) as network, patch.object(
                stock, "probe", return_value={"duration_seconds": 3}):
            result = stock.selected_download(path, "3", self.root / "preview.mp4")
        self.assertEqual(network.call_args.args[0], "https://cdn.pixabay.com/640.mp4")
        self.assertEqual(result["purpose"], "preview")
        self.assertFalse(result["content_reviewed"])
        self.assertFalse(result["candidate_registered"])

    def test_normalized_provider_thumbnails(self):
        raw = {"hits": [{"id": 3, "pageURL": "https://pixabay.com/videos/3/", "videos": {
            "tiny": {"url": "https://cdn.pixabay.com/v.mp4", "width": 640, "height": 360,
                     "thumbnail": "https://cdn.pixabay.com/v.jpg"}}}]}
        hit = stock.normalize("pixabay", raw)[0]
        self.assertEqual(hit["thumbnails"][0]["width"], 640)
        self.assertEqual(hit["variants"][0]["thumbnail_url"], "https://cdn.pixabay.com/v.jpg")
        pexels = {"videos": [{"id": 4, "url": "https://pexels.com/video/4/",
                              "image": "https://images.pexels.com/poster.jpg",
                              "video_pictures": [{"nr": 0, "picture": "https://images.pexels.com/frame.jpg"}]}]}
        self.assertEqual(len(stock.normalize("pexels", pexels)[0]["thumbnails"]), 2)
        raw["hits"][0]["videos"]["tiny"]["thumbnail"] = "https://evil.test/image.jpg"
        with self.assertRaises(stock.StockError):
            stock.normalize("pixabay", raw)

    def test_thumbnail_signatures(self):
        samples = [(b"\xff\xd8\xffframe\xff\xd9", "jpeg"),
                   (b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + b"\0" * 17, "png"),
                   (b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8 " + b"\0" * 4, "webp")]
        for content, kind in samples:
            self.assertEqual(stock.image_format(content), kind)
        for content in (b"", b"<html>error</html>", b"\xff\xd8\xfftruncated", b"RIFF\0\0\0\0WEBPVP8 "):
            with self.assertRaises(stock.StockError):
                stock.image_format(content)

    def test_thumbnail_download_safe_and_no_overwrite(self):
        path, _ = self.saved_hit(thumbnails=[{"url": "https://cdn.pixabay.com/v.jpg"}])
        output = self.root / "thumbnail.jpg"
        with patch.object(stock, "open_url", return_value=io.BytesIO(b"\xff\xd8\xffframe\xff\xd9")) as network:
            result = stock.thumbnail(path, "3", output)
            with self.assertRaises(stock.StockError):
                stock.thumbnail(path, "3", output)
            self.assertEqual(network.call_count, 1)
        self.assertEqual(result["format"], "jpeg")
        self.assertFalse(result["content_reviewed"])
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_bad_thumbnail_never_published_and_size_limited(self):
        path, _ = self.saved_hit(thumbnails=[{"url": "https://cdn.pixabay.com/v.jpg"}])
        for content in (b"<html>", b"x" * 21):
            with patch.object(stock, "MAX_IMAGE_BYTES", 20), patch.object(
                    stock, "open_url", return_value=io.BytesIO(content)):
                with self.assertRaises(stock.StockError):
                    stock.thumbnail(path, "3", self.root / "bad.jpg")
            self.assertFalse((self.root / "bad.jpg").exists())

    def test_old_cache_still_honors_ttl_and_preview_fallback(self):
        import hashlib
        path, hit = self.saved_hit(self.variants())
        del hit["thumbnails"]
        old_result = {"provider": "pixabay", "results": [hit]}
        cache_id = hashlib.sha256(json.dumps(["pixabay", "family", 1]).encode()).hexdigest()
        envelope = {"cached_at": stock.time.time(), "result": old_result}
        (self.root / (cache_id + ".json")).write_text(json.dumps(envelope))
        with patch.object(stock, "open_url") as network:
            self.assertEqual(stock.search("pixabay", "family", self.root), old_result)
            network.assert_not_called()
        path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(stock.StockError, "video preview"):
            stock.thumbnail(path, "3", self.root / "old.jpg")
        self.assertEqual(stock.select_variant(stock.load_result(path, "3"))["width"], 640)

    def test_result_file_missing_duplicate_id_and_bad_provider(self):
        path, hit = self.saved_hit()
        with self.assertRaises(stock.StockError):
            stock.load_result(path, "missing")
        for data in ({"provider": "evil", "results": [hit]},
                     {"provider": "pixabay", "results": [hit, hit]}, {}):
            path.write_text(json.dumps(data))
            with self.assertRaises(stock.StockError):
                stock.load_result(path, "3")

    def test_search_saves_reusable_results_and_refuses_overwrite_before_request(self):
        result = {'provider': 'pixabay', 'results': [self.saved_hit()[1]]}
        output = self.root / 'saved-search.json'
        with patch.object(stock, 'search', return_value=result) as search:
            report = stock.search_to_file('pixabay', 'phone table', self.root / 'cache', output)
            self.assertEqual(json.loads(output.read_text()), result)
            self.assertEqual(report['count'], 1)
            self.assertEqual(stock.load_result(output, '3')['provider_id'], '3')
            with self.assertRaises(stock.StockError):
                stock.search_to_file('pixabay', 'phone table', self.root / 'cache', output)
            search.assert_called_once()
        self.assertFalse(report['content_reviewed'])

    def test_batch_saves_explicit_queries_and_partial_failure_without_retry(self):
        result = {'provider': 'pixabay', 'results': []}
        destination = self.root / 'batch'
        with patch.object(stock, 'search', side_effect=[result, stock.StockError('failure')]) as search:
            with self.assertRaisesRegex(stock.StockError, 'No automatic retry'):
                stock.search_batch('pixabay', ['phone table', 'phone down', 'after call'], self.root / 'cache', destination)
            self.assertEqual(search.call_count, 2)
        report = json.loads((destination / 'batch.json').read_text())
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['failed_query_index'], 2)
        self.assertTrue((destination / 'search-01.json').is_file())
        self.assertFalse((destination / 'search-02.json').exists())
        with patch.object(stock, 'search') as search:
            with self.assertRaises(stock.StockError):
                stock.search_batch('pixabay', ['phone table'], self.root / 'cache', destination)
            search.assert_not_called()

    def test_batch_success_and_invalid_queries(self):
        destination = self.root / 'complete'
        with patch.object(stock, 'search', return_value={'provider': 'pixabay', 'results': []}) as search:
            report = stock.search_batch('pixabay', ['phone table', 'phone down'], self.root / 'cache', destination)
            self.assertEqual(search.call_count, 2)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(len(report['completed']), 2)
            self.assertEqual(json.loads((destination / 'batch.json').read_text()), report)
        for queries in ([], ['x', 'x'], [' '], ['x' * 101], [str(i) for i in range(9)]):
            with self.subTest(queries=queries), self.assertRaises(stock.StockError):
                stock.search_batch('pixabay', queries, self.root / 'cache', self.root / 'invalid')
        self.assertFalse((self.root / 'invalid').exists())

    def test_smallest_sampling_rendition_is_available_without_raw_url_orchestration(self):
        path, hit = self.saved_hit(self.variants())
        chosen = stock.select_variant(hit, 'samples')
        self.assertEqual(chosen['width'], min(v['width'] for v in hit['variants']))
        with patch.object(stock, 'download', return_value={'content_reviewed': False}) as download:
            result = stock.selected_download(path, '3', self.root / 'samples.mp4', smallest=True)
        self.assertEqual(result['purpose'], 'samples')
        self.assertEqual(download.call_args.args[1], chosen['url'])


if __name__ == "__main__":
    unittest.main()
