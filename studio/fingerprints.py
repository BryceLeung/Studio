"""Portable file fingerprints: CRLF -> LF for known text, exact bytes otherwise.

BOMs, lone CRs, whitespace, encodings, and trailing newlines are preserved.
Do not use text normalization for media, Office containers, or model checksums.
"""
import hashlib
from pathlib import Path


TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".txt", ".json", ".jsonl", ".xml", ".fcpxml",
    ".csv", ".tsv", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".svg",
    ".sh", ".ps1", ".bat", ".cmd", ".sql", ".rst", ".srt", ".vtt",
})


def is_text(path):
    path = Path(path)
    return path.suffix.lower() in TEXT_SUFFIXES or path.name == "DONE"


def digest_bytes(content, text=False):
    if text:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def _file_digest(path, text):
    digest = hashlib.sha256()
    pending = b""
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if text:
                block = pending + block
                pending = b"\r" if block.endswith(b"\r") else b""
                if pending:
                    block = block[:-1]
                block = block.replace(b"\r\n", b"\n")
            digest.update(block)
    digest.update(pending)
    return digest.hexdigest()


def sha256(path, *, content=None):
    """Hash a known text file with LF newlines, or any other file byte-for-byte."""
    return (_file_digest(path, is_text(path)) if content is None
            else digest_bytes(content, text=is_text(path)))


def raw_sha256(path):
    return _file_digest(path, False)


def matches(path, expected):
    """Validate canonical hashes or an existing exact-byte legacy fingerprint."""
    expected = expected.lower()
    return sha256(path) == expected or (is_text(path) and raw_sha256(path) == expected)
