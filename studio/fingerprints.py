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

MEDIA_SUFFIXES = frozenset({".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".mxf", ".wav"})


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


def identity(path):
    """Fingerprint ingested media cheaply; hash all other file types."""
    path = Path(path)
    if path.suffix.lower() in MEDIA_SUFFIXES:
        stat = path.stat()
        return {"method": "media-stat", "size_bytes": stat.st_size,
                "mtime_s": stat.st_mtime_ns // 1_000_000_000}
    return {"method": "sha256", "sha256": sha256(path)}


def mtime_seconds(saved):
    """Read whole-second media timestamps, including persisted nanosecond records."""
    key = "mtime_s" if "mtime_s" in saved else "mtime_ns"
    value = saved.get(key)
    if type(value) is not int:
        raise ValueError("Media fingerprint requires an integer modification timestamp")
    return value if key == "mtime_s" else value // 1_000_000_000


def matches_identity(path, saved):
    """Honor a saved method, including legacy entries with only sha256."""
    method = saved.get("method", "sha256" if "sha256" in saved else "legacy-media-stat")
    if method in ("media-stat", "legacy-media-stat") and Path(path).suffix.lower() not in MEDIA_SUFFIXES:
        raise ValueError(f"Stat-only fingerprint is not allowed for non-media: {path}")
    if method == "sha256":
        return matches(path, saved["sha256"])
    if method in ("media-stat", "legacy-media-stat"):
        stat = Path(path).stat()
        size = saved.get("size_bytes", saved.get("bytes"))
        if type(size) is not int:
            raise ValueError("Media fingerprint requires an integer file size")
        return stat.st_size == size and stat.st_mtime_ns // 1_000_000_000 == mtime_seconds(saved)
    raise ValueError(f"Unknown fingerprint method: {method}")
