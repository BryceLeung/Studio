"""Exercise Studio's shared newline and binary fingerprint convention."""
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from studio import fingerprints as fp


class FingerprintTests(unittest.TestCase):
    def test_captured_content_is_hashed_without_rereading_source(self):
        path = Path("not-on-disk.json")
        self.assertEqual(fp.sha256(path, content=b"{}\r\n"), fp.digest_bytes(b"{}\n"))
        self.assertNotEqual(fp.sha256(Path("model.onnx"), content=b"\r\n"), fp.digest_bytes(b"\n"))

    def test_text_normalizes_only_crlf(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "script.PY"
            for content in (b"\xef\xbb\xbfhello\nworld\r!\n", b"\xef\xbb\xbfhello\r\nworld\r!\r\n"):
                path.write_bytes(content)
                self.assertEqual(fp.sha256(path), hashlib.sha256(b"\xef\xbb\xbfhello\nworld\r!\n").hexdigest())
            path.write_bytes(b"\xef\xbb\xbfhello\nworld\r!")
            self.assertNotEqual(fp.sha256(path), fp.digest_bytes(b"\xef\xbb\xbfhello\nworld\r!\n"))

    def test_streaming_crlf_across_block_boundary_and_lone_final_cr(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            content = b"x" * (1024 * 1024 - 1) + b"\r\nend\r"
            path.write_bytes(content)
            self.assertEqual(fp.sha256(path), fp.digest_bytes(content, text=True))

    def test_binary_and_unknown_extensions_remain_exact(self):
        with TemporaryDirectory() as directory:
            for suffix in (".docx", ".pptx", ".onnx", ".zip", ".wav", ".mov", ".dat", ""):
                path = Path(directory) / ("binary" + suffix)
                path.write_bytes(b"\x00\r\n\xff")
                self.assertEqual(fp.sha256(path), fp.raw_sha256(path))
                self.assertNotEqual(fp.sha256(path), fp.digest_bytes(b"\x00\n\xff"))

    def test_legacy_raw_hash_and_real_change(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "notes.md"
            path.write_bytes(b"first\r\nsecond\r\n")
            legacy = fp.raw_sha256(path)
            canonical = fp.sha256(path)
            self.assertTrue(fp.matches(path, legacy.upper()))
            self.assertTrue(fp.matches(path, canonical))
            path.write_bytes(b"first\nsecond\n")
            self.assertTrue(fp.matches(path, canonical))
            path.write_bytes(b"first\nchanged\n")
            self.assertFalse(fp.matches(path, canonical))
            self.assertFalse(fp.matches(path, legacy))

    def test_empty_files_and_done_are_supported(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "DONE"
            path.write_bytes(b"")
            self.assertEqual(fp.sha256(path), fp.digest_bytes(b""))
            path.write_bytes(b"{}\r\n")
            self.assertEqual(fp.sha256(path), fp.digest_bytes(b"{}\n"))

    def test_media_uses_integer_seconds_and_other_files_use_content(self):
        with TemporaryDirectory() as directory:
            wav = Path(directory) / "recording.WAV"
            wav.write_bytes(b"first")
            os.utime(wav, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
            saved = fp.identity(wav)
            self.assertEqual(saved, {"method": "media-stat", "size_bytes": 5, "mtime_s": 1_700_000_000})
            self.assertTrue(fp.matches_identity(wav, saved))
            os.utime(wav, ns=(1_700_000_001_123_456_789, 1_700_000_001_123_456_789))
            self.assertFalse(fp.matches_identity(wav, saved))
            transcript = Path(directory) / "transcript.json"
            transcript.write_bytes(b"{}")
            self.assertEqual(fp.identity(transcript), {"method": "sha256", "sha256": fp.sha256(transcript)})
            self.assertTrue(fp.matches_identity(transcript, {"sha256": fp.sha256(transcript)}))

    def test_rendered_movie_extensions_use_media_stat(self):
        with TemporaryDirectory() as directory:
            for suffix in ("MOV", "mp4", "m4v", "mkv", "avi", "webm", "mxf"):
                path = Path(directory) / ("rendered." + suffix)
                path.write_bytes(b"rendered movie")
                saved = fp.identity(path)
                self.assertEqual(saved["method"], "media-stat")
                self.assertTrue(fp.matches_identity(path, saved))

    def test_media_precision_changes_match_but_seconds_and_size_changes_do_not(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.wav"
            path.write_bytes(b"audio")
            stamp = 1_700_000_000_123_456_700
            os.utime(path, ns=(stamp, stamp))
            canonical = fp.identity(path)
            for size_key in ("bytes", "size_bytes"):
                legacy = {size_key: 5, "mtime_ns": stamp}
                os.utime(path, ns=(stamp, 1_700_000_000_987_654_300))
                self.assertTrue(fp.matches_identity(path, legacy))
                self.assertTrue(fp.matches_identity(path, canonical))
                os.utime(path, ns=(stamp, stamp + 1_000_000_000))
                self.assertFalse(fp.matches_identity(path, legacy))
                self.assertFalse(fp.matches_identity(path, canonical))
            os.utime(path, ns=(stamp, stamp))
            self.assertFalse(fp.matches_identity(path, dict(canonical, size_bytes=6)))
            for invalid in ({"bytes": 5}, {"bytes": 5, "mtime_s": True},
                            {"bytes": 5, "mtime_ns": "1700000000000000000"}):
                with self.assertRaises(ValueError):
                    fp.matches_identity(path, invalid)


if __name__ == "__main__":
    unittest.main()
