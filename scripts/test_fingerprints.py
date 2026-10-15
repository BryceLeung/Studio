"""Exercise Studio's shared newline and binary fingerprint convention."""
import hashlib
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


if __name__ == "__main__":
    unittest.main()
