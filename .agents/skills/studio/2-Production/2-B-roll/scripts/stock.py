"""Targeted stock-video API search and scratch downloads; no candidate approval.

Uses only the Python standard library. ffprobe must already be on PATH for
downloads. Credentials come only from PEXELS_API_KEY / PIXABAY_API_KEY.
Search metadata is discovery evidence, not a licence snapshot or content review.
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
import time
import urllib.error
import urllib.parse
import urllib.request


PROVIDERS = ("pexels", "pixabay")
WORKSPACE = Path(__file__).resolve().parents[6]
TTL = 24 * 60 * 60
MAX_BYTES = 1024 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class StockError(ValueError):
    pass


def external(path):
    """Allow external scratch or dedicated, non-candidate project search artifacts.

    A Connected Projects link may resolve the project prefix. From the stage
    directory down, every existing component must be a real path, not a link.
    """
    original = Path(path)
    if any(part == '..' for part in original.parts):
        raise StockError('Artifact paths must not contain parent traversal.')
    original = original.absolute()
    try:
        resolved = original.resolve()
    except (OSError, RuntimeError, ValueError):
        raise StockError('Artifact path could not be resolved safely.') from None
    if resolved == WORKSPACE or WORKSPACE in resolved.parents:
        raise StockError("Use an external scratch directory outside the workspace.")
    stages = {'1-pre production', '2-production', '3-post production', '4-publish', '5-analyze'}
    original_parts = original.parts
    stage_indexes = [index for index, part in enumerate(original_parts)
                     if part.casefold() == '2-b-roll']
    protected = any(part.casefold() in stages or part.casefold() == '2-b-roll'
                    for part in (*original_parts, *resolved.parts))
    if not protected:
        return resolved
    message = ('Project artifacts must use 2-Production/2-B-roll/ASSET-xxxxxx/'
               'search.NN/NN/... or ASSET-xxxxxx/cache/...; protected stage paths are refused.')
    if len(stage_indexes) != 1:
        raise StockError(message)
    index = stage_indexes[0]
    tail = original_parts[index + 1:]
    if (index < 1 or original_parts[index - 1:index + 1] != ('2-Production', '2-B-roll') or
            len(tail) < 2 or not re.fullmatch(r'ASSET-[0-9]{6}', tail[0]) or
            int(tail[0][6:]) == 0):
        raise StockError(message)
    number = r'(?:0[1-9]|[1-9][0-9]+)'
    is_cache = tail[1] == 'cache'
    is_attempt = (len(tail) >= 3 and re.fullmatch(r'search\.' + number, tail[1]) and
                  re.fullmatch(number, tail[2]))
    if not (is_cache or is_attempt):
        raise StockError(message)
    descendants = original_parts[index - 1:]
    if any(part.casefold() == 'stale' or any(ord(char) < 32 or char in '\\:' for char in part)
           for part in descendants):
        raise StockError('Archive and unsafe artifact paths are refused.')
    project = Path(*original_parts[:index - 1]).resolve()
    if any(part.casefold() in stages or part.casefold() == '2-b-roll' for part in project.parts):
        raise StockError(message)
    cursor = project
    for part in descendants:
        cursor = cursor / part
        if cursor.is_symlink():
            raise StockError('Symlink redirects inside project artifact directories are refused.')
    if cursor != resolved:
        raise StockError('Artifact path changed while resolving the project.')
    return resolved


def allowed_url(url, provider, api=False):
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        roots = ("pexels.com",) if provider == "pexels" else ("pixabay.com",)
        valid_host = any(host == root or host.endswith("." + root) for root in roots)
        if api:
            valid_host = host == ("api.pexels.com" if provider == "pexels" else "pixabay.com")
        if (parsed.scheme != "https" or not valid_host or parsed.username or
                parsed.password or parsed.port not in (None, 443)):
            raise ValueError()
        if not api and any(k.lower() in ("key", "api_key", "authorization")
                           for k, _ in urllib.parse.parse_qsl(parsed.query)):
            raise ValueError()
    except (ValueError, TypeError):
        raise StockError("Only HTTPS URLs on the selected provider's domains are allowed.") from None
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, provider, api=False):
        self.provider, self.api = provider, api

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API requests carry secrets; do not follow redirects at all.
        if self.api:
            raise StockError("API redirects are refused to protect credentials.")
        allowed_url(newurl, self.provider)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def open_url(url, provider, headers=None, api=False):
    allowed_url(url, provider, api)
    request_headers = {'User-Agent': 'Studio B-roll/1.0', 'Accept': 'application/json' if api else '*/*'}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    try:
        return urllib.request.build_opener(SafeRedirect(provider, api)).open(request, timeout=45)
    except urllib.error.HTTPError as exc:
        raise StockError(f"Provider returned HTTP {exc.code}; check access or retry later.") from None
    except (urllib.error.URLError, OSError):
        raise StockError("Provider connection failed.") from None


def normalize(provider, raw):
    results = []
    for item in raw.get("videos" if provider == "pexels" else "hits", []):
        if provider == "pexels":
            thumbnails = [{"url": item["image"], "kind": "poster"}] if item.get("image") else []
            thumbnails += [{"url": p["picture"], "kind": "preview", "number": p.get("nr")}
                           for p in item.get("video_pictures", []) if p.get("picture")]
            variants = [{"id": str(v.get("id", "")), "url": v.get("link"),
                         "width": v.get("width"), "height": v.get("height"),
                         "quality": v.get("quality"), "file_type": v.get("file_type")}
                        for v in item.get("video_files", [])]
            page, author = item.get("url"), item.get("user", {}).get("name")
        else:
            thumbnails = [{"url": v["thumbnail"], "kind": "poster", "variant_id": quality,
                           "width": v.get("width"), "height": v.get("height")}
                          for quality, v in item.get("videos", {}).items() if v.get("thumbnail")]
            variants = [{"id": quality, "url": v.get("url"), "width": v.get("width"),
                         "height": v.get("height"), "size": v.get("size"),
                         "thumbnail_url": v.get("thumbnail")}
                        for quality, v in item.get("videos", {}).items()]
            page, author = item.get("pageURL"), item.get("user")
        allowed_url(page, provider)
        variants = [v for v in variants if v.get("url")]
        for variant in variants:
            allowed_url(variant["url"], provider)
        for thumbnail in thumbnails:
            allowed_url(thumbnail["url"], provider)
        results.append({"provider": provider, "provider_id": str(item["id"]),
                        "page_url": page, "author": author,
                        "duration_seconds": item.get("duration"), "variants": variants,
                        "thumbnails": thumbnails})
    return results


def redact(value, secrets):
    # Treat every provider-returned string as untrusted, including echoed keys.
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in value.items()}
    return value


def search(provider, query, cache_dir, page=1):
    if provider not in PROVIDERS or not query.strip() or len(query) > 100 or page < 1:
        raise StockError("Choose a provider, a 1–100 character query, and a positive page.")
    cache_dir = external(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    secrets = [os.environ.get("PEXELS_API_KEY", ""), os.environ.get("PIXABAY_API_KEY", "")]
    cache_id = hashlib.sha256(json.dumps([provider, query, page]).encode()).hexdigest()
    cache_path = external(cache_dir / (cache_id + ".json"))
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if 0 <= time.time() - cached["cached_at"] < TTL:
                return redact(cached["result"], secrets)
        except (ValueError, KeyError, TypeError):
            raise StockError("Search cache is invalid; inspect it before retrying.") from None
    key = os.environ.get(provider.upper() + "_API_KEY")
    if not key:
        raise StockError(f"Set {provider.upper()}_API_KEY locally before searching.")
    if provider == "pexels":
        url = "https://api.pexels.com/v1/videos/search?" + urllib.parse.urlencode(
            {"query": query, "page": page, "per_page": 15})
        headers = {"Authorization": key}
    else:
        url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
            {"q": query, "key": key, "page": page, "per_page": 20})
        headers = {}
    try:
        with open_url(url, provider, headers, api=True) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise StockError("Provider search response exceeded the size limit.")
        raw = json.loads(data)
        result = redact({"provider": provider, "query": query, "page": page,
                         "provider_url": "https://www.pexels.com" if provider == "pexels"
                         else "https://pixabay.com", "results": normalize(provider, raw)}, secrets)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, StockError):
            raise
        raise StockError("Provider returned an invalid search response.") from None
    # Only normalized, credential-free data is cached. No request URL or headers.
    payload = json.dumps({"cached_at": time.time(), "result": result}, indent=2)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_dir,
                                     suffix=".tmp", delete=False) as temp:
        temp.write(payload)
        temp_path = Path(temp.name)
    try:
        temp_path.replace(cache_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return result


def save_json_new(path, data):
    """Publish credential-free discovery output without replacing an existing file."""
    path = external(path)
    if path.exists():
        raise StockError('Result output already exists; overwriting is forbidden.')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write('\n')
        os.link(temporary, path)
    except FileExistsError:
        raise StockError('Result output already exists; overwriting is forbidden.') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def search_to_file(provider, query, cache_dir, output, page=1):
    """Search and save reusable result JSON, returning a compact discovery summary."""
    output = external(output)
    if output.exists():
        raise StockError('Result output already exists; overwriting is forbidden.')
    result = search(provider, query, cache_dir, page)
    save_json_new(output, result)
    return {'provider': provider, 'query': query, 'page': page, 'result_file': str(output),
            'count': len(result['results']), 'hits': [
                {key: hit.get(key) for key in ('provider_id', 'page_url', 'author', 'duration_seconds')}
                for hit in result['results']], 'content_reviewed': False, 'candidate_registered': False}


def search_batch(provider, query, cache_dir, output_dir):
    """Run only the explicitly named queries, preserving each result and partial progress."""
    if (provider not in PROVIDERS or not isinstance(query, list) or not 1 <= len(query) <= 8 or
            any(not isinstance(q, str) or not q.strip() or len(q) > 100 for q in query) or
            len({q.strip() for q in query}) != len(query)):
        raise StockError('Supply 1 to 8 distinct targeted queries, each 1 to 100 characters.')
    output_dir, cache_dir = external(output_dir), external(cache_dir)
    if output_dir.exists():
        raise StockError('Batch output directory already exists; use a new scratch directory.')
    output_dir.mkdir(parents=True)
    summaries = []
    for index, value in enumerate(query, 1):
        try:
            summaries.append(search_to_file(provider, value, cache_dir,
                                             output_dir / f'search-{index:02d}.json'))
        except (StockError, OSError):
            save_json_new(output_dir / 'batch.json', {'provider': provider, 'queries': query,
                          'status': 'incomplete', 'completed': summaries, 'failed_query_index': index,
                          'content_reviewed': False, 'candidate_registered': False})
            raise StockError('Search batch stopped; inspect batch.json and saved results. No automatic retry was made.') from None
    report = {'provider': provider, 'queries': query, 'status': 'complete', 'completed': summaries,
              'content_reviewed': False, 'candidate_registered': False}
    save_json_new(output_dir / 'batch.json', report)
    return report


def probe(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise StockError("ffprobe must be available on PATH to verify video downloads.")
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                                 "-of", "json", str(path)], capture_output=True,
                                text=True, timeout=60, check=True)
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if (not math.isfinite(duration) or duration <= 0 or
                not any(s.get("codec_type") == "video" and s.get("width", 0) > 0
                        and s.get("height", 0) > 0 for s in data.get("streams", []))):
            raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        raise StockError("Downloaded file did not pass ffprobe video verification.") from None
    return {"duration_seconds": duration}


def download(provider, url, output):
    if provider not in PROVIDERS:
        raise StockError("Unsupported provider.")
    allowed_url(url, provider)
    output = external(output)
    if output.exists():
        raise StockError("Output already exists; overwriting is forbidden.")
    if not shutil.which("ffprobe"):
        raise StockError("ffprobe must be available on PATH to verify video downloads.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".part", delete=False) as temp:
            temp_path = Path(temp.name)
            size = 0
            digest = hashlib.sha256()
            with open_url(url, provider) as response:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise StockError("Selected video exceeds the 1 GiB download limit.")
                    temp.write(chunk)
                    digest.update(chunk)
        if not size:
            raise StockError("Provider returned an empty download.")
        metadata = probe(temp_path)
        # Hard-link is atomic and refuses an existing destination (including races).
        os.link(temp_path, output)
        return {"provider": provider, "output": str(output), "bytes": size,
                "sha256": digest.hexdigest(), "technical": metadata,
                "content_reviewed": False, "candidate_registered": False}
    except FileExistsError:
        raise StockError("Output already exists; overwriting is forbidden.") from None
    except OSError:
        raise StockError("Scratch download could not be written or published.") from None
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


def load_result(result_file, provider_id):
    """Load one normalized search hit; cached metadata never bypasses URL checks."""
    try:
        path = Path(result_file)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        # Accept search stdout or an existing search-cache envelope.
        data = data.get("result", data)
        provider = data["provider"]
        if provider not in PROVIDERS:
            raise ValueError()
        matches = [hit for hit in data["results"] if str(hit["provider_id"]) == str(provider_id)]
        if len(matches) != 1 or matches[0].get("provider") != provider:
            raise ValueError()
        hit = matches[0]
        allowed_url(hit["page_url"], provider)
        return hit
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise StockError("Result file must contain exactly one matching normalized provider ID.") from None


def valid_dimensions(variant):
    return all(type(variant.get(k)) is int and 0 < variant[k] <= 32768 for k in ("width", "height"))


def select_variant(hit, purpose="preview", min_width=1920):
    """Preview: smallest >=640 wide, else largest. Acquisition: largest >=minimum."""
    if purpose not in ("preview", "acquire", "samples") or type(min_width) is not int or min_width < 1:
        raise StockError("Choose preview or acquire and a positive minimum width.")
    variants = []
    for variant in hit.get("variants", []):
        if not isinstance(variant, dict) or not valid_dimensions(variant) or not variant.get("url"):
            continue
        allowed_url(variant["url"], hit["provider"])
        # HLS playlists are not downloadable standalone candidates.
        if (variant.get("quality") == "hls" or
                urllib.parse.urlsplit(variant["url"]).path.lower().endswith(".m3u8") or
                variant.get("file_type") not in (None, "video/mp4", "video/webm", "video/quicktime")):
            continue
        variants.append(variant)
    if not variants:
        raise StockError("No downloadable video variants with valid dimensions are available.")
    def rank(v):
        return (v["width"] * v["height"], v["width"], v["height"], str(v.get("id", "")), v["url"])
    if purpose == 'samples':
        return min(variants, key=rank)
    if purpose == "preview":
        adequate = [v for v in variants if v["width"] >= 640]
        return min(adequate, key=rank) if adequate else max(variants, key=rank)
    adequate = [v for v in variants if v["width"] >= min_width]
    if not adequate:
        raise StockError(f"No variant meets minimum width {min_width}; explicitly choose a lower --min-width.")
    return max(adequate, key=rank)


def selected_download(result_file, provider_id, output, purpose="preview", min_width=1920, smallest=False):
    hit = load_result(result_file, provider_id)
    if smallest:
        if purpose != 'preview':
            raise StockError('Smallest rendition selection is only supported for previews.')
        purpose = 'samples'
    variant = select_variant(hit, purpose, min_width)
    result = download(hit["provider"], variant["url"], output)
    result.update({"provider_id": hit["provider_id"], "page_url": hit["page_url"],
                   "purpose": purpose, "variant": variant,
                   "selection_fallback": purpose == "preview" and variant["width"] < 640})
    return result


def image_format(data):
    """Check provider image container signatures without an imaging dependency."""
    if len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "jpeg"
    if len(data) >= 33 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return "png"
    if (len(data) >= 20 and data[:4] == b"RIFF" and data[8:12] == b"WEBP" and
            data[12:16] in (b"VP8 ", b"VP8L", b"VP8X") and
            int.from_bytes(data[4:8], "little") + 8 == len(data)):
        return "webp"
    raise StockError("Thumbnail did not have a supported JPEG, PNG or WebP signature.")


def thumbnail(result_file, provider_id, output):
    hit = load_result(result_file, provider_id)
    thumbnails = hit.get("thumbnails") or []
    if not thumbnails:
        raise StockError("No provider thumbnail is recorded. Use a video preview; older search caches stay valid for 24 hours.")
    if not isinstance(thumbnails, list) or not isinstance(thumbnails[0], dict):
        raise StockError("Invalid thumbnail metadata.")
    # Prefer the smallest adequate rendition; Pexels posters retain supplied order.
    sized = [t for t in thumbnails if isinstance(t, dict) and valid_dimensions(t)]
    if sized:
        adequate = [t for t in sized if t["width"] >= 640]
        chosen = min(adequate or sized, key=lambda t: (t["width"] * t["height"], t.get("url", "")))
    else:
        chosen = thumbnails[0]
    url = chosen.get("url")
    allowed_url(url, hit["provider"])
    output = external(output)
    if output.exists():
        raise StockError("Output already exists; overwriting is forbidden.")
    with open_url(url, hit["provider"]) as response:
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise StockError("Thumbnail exceeds the 20 MiB download limit.")
    kind = image_format(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".part", delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(data)
        os.link(temp_path, output)
    except FileExistsError:
        raise StockError("Output already exists; overwriting is forbidden.") from None
    except OSError:
        raise StockError("Thumbnail could not be written or published.") from None
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
    return {"provider": hit["provider"], "provider_id": hit["provider_id"],
            "page_url": hit["page_url"], "output": str(output), "bytes": len(data),
            "format": kind, "sha256": hashlib.sha256(data).hexdigest(),
            "content_reviewed": False, "candidate_registered": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    find = commands.add_parser("search", help="One targeted query page; no automatic pagination")
    find.add_argument("--provider", choices=PROVIDERS, required=True)
    find.add_argument("--query", required=True)
    find.add_argument("--cache-dir", required=True)
    find.add_argument("--page", type=int, default=1)
    find.add_argument("--output", help="Save normalized results to a new external scratch JSON file")
    batch = commands.add_parser('search-batch', help='Run explicitly named targeted queries and save result JSON files')
    batch.add_argument('--provider', choices=PROVIDERS, required=True)
    batch.add_argument('--query', action='append', required=True, help='Repeat for each intended search, at most eight')
    batch.add_argument('--cache-dir', required=True)
    batch.add_argument('--output-dir', required=True)
    get = commands.add_parser("download", help="Download one selected video into external scratch")
    get.add_argument("--provider", choices=PROVIDERS, required=True)
    get.add_argument("--url", required=True)
    get.add_argument("--output", required=True)
    for name in ("thumbnail", "preview", "acquire"):
        command_parser = commands.add_parser(name, help="Download selected search-hit media into external scratch")
        command_parser.add_argument("--result-file", required=True)
        command_parser.add_argument("--provider-id", required=True)
        command_parser.add_argument("--output", required=True)
        if name == "acquire":
            command_parser.add_argument("--min-width", type=int, default=1920)
        if name == 'preview':
            command_parser.add_argument('--smallest', action='store_true', help='Use the absolute smallest standalone rendition for contact-sheet sampling')
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        if command in ("preview", "acquire"):
            result = selected_download(**args, purpose=command)
        elif command == 'search-batch':
            result = search_batch(**args)
        elif command == 'search':
            output = args.pop('output')
            result = search_to_file(**args, output=output) if output else search(**args)
        else:
            result = {"download": download, "thumbnail": thumbnail}[command](**args)
        print(json.dumps(result, indent=2))
        return 0
    except (StockError, OSError):
        # StockError messages are deliberately fixed, never provider bodies/URLs.
        exc = sys.exc_info()[1]
        print(str(exc) if isinstance(exc, StockError) else "Scratch operation failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
