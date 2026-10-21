# Python helper interfaces

All project commands use `python "<skill>/scripts/broll.py" <action> --project-root "<resolved-project>"`. Inputs such as plans, revised text and review JSON should live in external scratch. Helpers never author editorial plans or judge visual suitability; the agent provides those after reading the current request and inspecting video.

## Discovery and initial requests

`context` is read-only and lists eligible IDs and their current registry fields. Before any stage execution, follow the root skill's shared audit. All project mutations and `check` enforce prerequisites again.

`init --spec "<plans.json>" [--asset ASSET-000010]` creates missing `request.md` journals. Use `--asset` to initialize only the selected eligible asset. The plans object maps each new eligible ASSET ID to an explicit sourcing mode and required shots:

```json
{
  "ASSET-000010": {
    "sourcing": "manual",
    "shots": ["Family at Disney", "Preparing lunch"]
  }
}
```

Choose `manual` for actual-family requirements, `stock` for stock sourcing, or `either` where the brief explicitly permits both. `shots` contains genuinely required shots; use an empty list for a request without separately required shots. Do not turn optional shot examples into new requirements. Plans need include only missing assets; an empty object is valid on an unchanged rerun. Missing plans fail before publication. Initial request bytes are UTF-8 encoding of the parsed Asset brief, without added headings or a trailing newline.

## Request changes

`revise --asset ASSET-000010 --file "<full-current-request.txt>" --spec "<plan.json>" --reason "<user-requested-change>"`

The plan is one object, e.g. `{"sourcing":"manual","shots":["Preparing lunch"]}`. The text file contains the full replacement sourcing brief, including unchanged requirements. The helper appends this full brief and reason to the existing `request.md`, assigns the next logical revision ID and retains earlier entries. It stores the content SHA-256 and journal offsets, alongside the current 1.4 row, so a changed upstream brief requires explicit reconciliation. Before calling revise, resolve any conflict with local refinements with the user. Simply requesting more candidates or changing search terms does not require a brief revision. There are no separate `request.vN.md` files.

## Linked asset searches and search notes

Use this command for every real asset search, after initialization and prerequisite validation:

```text
python "<skill>/scripts/broll.py" search --project-root "<resolved-project>" --asset ASSET-000002 --provider pixabay --query "putting phone down" --query "phone on table" --reason "Find the requested action instead of scrolling or typing" --cache-dir "<scratch>/stock-cache" --output-dir "<scratch>/asset-000002-search-02"
```

Supply explicit queries by repeating `--query`; `--reason`, `--cache-dir` and `--output-dir` are required. The helper allows the initial distinct term plus at most five refinements per logical brief revision, across invocations and providers, and preflights the whole requested batch against the remaining budget. Case/whitespace variants are the same term. Recorded failed attempts count as tried terms; an intentional same-term retry before exhaustion consumes no extra refinement. Review results before choosing the next refinement. Budget rejection makes no API calls, output folders or journal changes. The helper requires an initialized eligible asset and current prerequisites. It appends each term and its reason to that asset's `request.md` before calling the maintained stock wrapper, then appends the outcome, result count and saved-result location, or a sanitized failure. The returned search IDs identify attempts for follow-up notes; output also reports refinements used/remaining and whether the limit has been reached. Search terms and result locations never contain API keys.

After visual inspection, append the outcome and next-term rationale:

```text
python "<skill>/scripts/broll.py" search-note --project-root "<resolved-project>" --asset ASSET-000002 --search-id 1 --reason "No suitable action: results show scrolling. Next try placing the phone face down."
```

`search-note` appends only; it never rewrites earlier text or changes the creative brief. Search attempts, outcomes and review notes do not advance the brief revision or invalidate assessments against that brief. The ledger separately validates journal exact-byte SHA-256, byte size and event offsets. Creative revisions keep their own content fingerprints, so a growing search history is distinct from changing the request itself.

If a batch falls short, record why, choose new terms within the existing brief and try again within the five-refinement budget. Do not ask to rewrite the creative request merely because the first query failed. After the fifth refinement is reviewed without sufficient passing matches, stop and report terms tried, best supported scores and repeated mismatches. Ask the user whether the threshold is too high or a better search direction is needed. Do not lower thresholds, reset the counter, fabricate a brief revision or use the low-level wrappers to continue past the limit. A genuine user-authorized creative revision starts a new budget. Stop immediately when the target is met. The low-level `stock.py search` and `search-batch` commands below are standalone scratch utilities; they do not record asset history and must not replace this linked workflow.

## Register acquired footage

`register --asset ASSET-000010 --file "<local-video>" --spec "<provenance.json>"`

User footage:

```json
{
  "origin": "User",
  "source_id": "family-original-2024-07-15-clip-12",
  "description": "Family walking toward the park entrance"
}
```

The source ID identifies the original clip, including across copies or alternate encodes. Registration checks duplicate source IDs and byte hashes within the asset. Do not invent a fresh source ID to count a duplicate rendition. Videos already dropped directly into this asset folder are registered in place with their filename preserved. Files outside the asset folder are copied into C-numbered files, preserving the originals. Multiple unregistered handoff files can be registered one by one; completion remains blocked until all recognised files have records.

Stock provenance additionally requires:

```json
{
  "origin": "Pexels",
  "source_id": "provider-video-id",
  "source_url": "https://www.pexels.com/video/provider-video-id/",
  "description": "Actual description after inspection",
  "license": {
    "url": "https://www.pexels.com/license/",
    "text": "Retained applicable licence text or provider licence certificate",
    "checked_at": "2026-10-20T12:00:00Z",
    "attribution": "Creator name, provider credit and original page URL"
  }
}
```

Use actual provider IDs, URLs, dates and retained licence evidence. The example placeholders are not valid provenance. Pixabay uses origin `Pixabay` and a Pixabay page. Storyblocks is refused. A manual-only current request refuses stock registrations. Registration probes video metadata using ffprobe, stores SHA-256 and shared media identity, and creates no suitability assessment.

## Assess current applicability

Watch the footage, then call:

`assess --asset ASSET-000010 --candidate C001 --spec "<assessment.json>"`

```json
{
  "suitable": true,
  "reviewed_by": "Agent after video inspection",
  "notes": "Usable framing and action match the current request.",
  "ranges": [[1.2, 5.8]],
  "shots": ["Family at Disney"]
}
```

Ranges use source seconds, start-inclusive/end-exclusive, within verified duration. Shot labels match the current request's plan exactly. Reject unsuitable footage with `suitable:false`, notes explaining why and `ranges:[]`/`shots:[]` as appropriate. New assessments append to history. A request revision makes earlier assessments ineligible until reassessed against that revision; the helper never silently carries suitability forward.

## Check and complete

`check` is read-only, including prerequisite validation. It checks the complete current eligible set, required shots, current assessments, exact-byte journal integrity and logical request history, duplicate sources/bytes, candidate identities and video metadata. It stops on the first actionable problem. `Candidates.json` is schema `studio.broll.candidates`, version 2; no older-schema migration is performed during active development.

`complete` runs the same checks, builds portable input/output/workflow fingerprints and passes them to shared `record_done`. The nested output inventory watches `request.md`, `Candidates.json` and videos, including historical ASSET folders too. It never writes a custom DONE or approves anything. Common read-only `scripts/validation.py --stage 2.2` checks the resulting evidence. New media, journal entries or candidate changes make prior completion stale; search-only entries do not change candidate applicability against the brief. Failed prerequisite checks never invalidate ancestors.

## Stock discovery and scratch downloads

`python "<skill>/scripts/stock.py" search --provider pexels --query "parent preparing lunch" --cache-dir "<external-scratch>/stock-cache" --output "<external-scratch>/lunch-results.json"`

Use `--provider pixabay` for Pixabay. Keys come from `PEXELS_API_KEY` / `PIXABAY_API_KEY` environment variables; never put keys into query arguments or tracked files. Search output includes provider credit, source-page links and download variants; keep creator credits when presenting candidates. `--page N` explicitly selects a page, with no automatic pagination. Queries are targeted, not bulk harvesting. Pixabay responses are cached for the required 24-hour window. Cache writes belong in external scratch, not the project or workflow repository.

`--output` saves the complete normalized search response for subsequent thumbnail/preview/acquisition commands and prints a compact result summary. It refuses an existing output before making a provider request. Omitting `--output` retains the original JSON-on-stdout behavior.

For several deliberately selected queries, use the maintained batch command:

```text
python "<skill>/scripts/stock.py" search-batch --provider pixabay --query "putting phone down" --query "phone table" --cache-dir "<scratch>/stock-cache" --output-dir "<scratch>/asset-000002-search-01"
```

Repeat `--query` for each intended query, up to eight distinct queries per invocation. No automatic query expansion, pagination, provider fallback or network retry occurs. A new output directory contains `search-01.json`, `search-02.json`, etc. plus `batch.json` summarizing IDs, authors, source pages and durations. On a failed query, completed files remain and `batch.json` records the incomplete run and failing query index; later queries are not attempted. Existing directories are refused. A later intentional retry can use a new output directory while reusing the same 24-hour cache.

All provider API calls and repeatable discovery orchestration belong in these maintained scripts. Do not substitute inline Python clients, shell API requests or one-off query/result-writing loops. Add missing operations to the helper with tests, then invoke its CLI.

`python "<skill>/scripts/stock.py" download --provider pexels --url "<selected-variant-url>" --output "<external-scratch>/chosen.mp4"`

The downloader accepts selected-provider HTTPS URLs, validates redirects, limits file size, streams to a temporary file, probes video and publishes without overwrite. It does not acquire licence evidence or assess suitability. Inspect downloaded footage, collect the applicable provider licence evidence, then register and assess separately. If external scratch or network requires tool approval, request it normally; do not change destinations to bypass permissions.

## Thumbnail, small-preview and full-resolution selection

Use a result file saved by `broll.py search` for asset work. Standalone scratch `stock.py search --output` and `search-batch` files, or an existing search cache JSON envelope, are also supported by the preview tools. Do not put API keys in that file. Each result includes provider thumbnail URLs where supplied; old caches remain valid for their full 24-hour window. If an old cached result has no thumbnail, use its video preview rather than bypassing the cache window.

```text
python "<skill>/scripts/stock.py" thumbnail --result-file "<results.json>" --provider-id 11613 --output "<scratch>/thumbnail.jpg"
python "<skill>/scripts/stock.py" preview --result-file "<results.json>" --provider-id 11613 --output "<scratch>/preview.mp4"
python "<skill>/scripts/stock.py" acquire --result-file "<results.json>" --provider-id 11613 --output "<scratch>/full.mp4" --min-width 1920
```

`thumbnail` retrieves one provider poster image, checks JPEG/PNG/WebP container signatures and a 20 MiB size limit, and refuses replacement. Use an image viewer to inspect it; a file signature alone is not proof of meaningful visual content. The returned format identifies the actual container.

To script a result page's thumbnail downloads and create a single review sheet:

```text
python "<skill>/scripts/preview.py" shortlist --result-file "<results.json>" --output-dir "<scratch>/thumbnail-review-01" --limit 20
```

This processes at most the explicitly selected limit (1-24 results), without extra searches. It saves provider thumbnails, a labelled `thumbnail-sheet.jpg` and `shortlist.json` with source-page credits and image availability. Missing/failed images are recorded as unavailable, not visually rejected. If none can be obtained, the report has no contact sheet and directs video-preview fallback. Existing output directories are refused. The agent inspects the sheet and supplies editorial decisions; the script never invents a score.

`preview` chooses the smallest downloadable rendition at least 640 pixels wide. If all are smaller, it returns the largest available and flags the fallback. `acquire` chooses the highest-pixel-count rendition meeting the explicit minimum width; it fails if none qualifies rather than silently lowering the threshold. Width is literal: use an appropriate explicit minimum for portrait footage. Both report the selected variant, provider ID and source page and leave content unreviewed/unregistered. HLS playlists and variants without reliable dimensions are excluded.

Use `preview --smallest` for the contact-sheet sampling pass: it selects the absolute smallest valid standalone rendition and reports purpose `samples`. This keeps variant selection inside the maintained Python helper rather than requiring custom result parsing or raw download-URL orchestration.

## Sampled frames, contact sheets and closer review

```text
python "<skill>/scripts/preview.py" sheet --file "<scratch>/preview.mp4" --output-dir "<scratch>/review-01" --count 8
python "<skill>/scripts/preview.py" sheet --file "<scratch>/preview.mp4" --output-dir "<scratch>/detail-01" --start 10 --end 15 --count 12
python "<skill>/scripts/preview.py" segment --file "<scratch>/preview.mp4" --start 10 --duration 5 --output "<scratch>/detail.mp4"
```

These commands work on any supplied local video, including manual family footage. Outputs must be external scratch, outside the repository and canonical B-roll folders. Existing outputs are never overwritten. Use unique review directory/file names on reruns.

`sheet` takes evenly spaced samples at bin midpoints, avoiding end-of-file seeks. Default: 8 labelled 480x270 cells, 4 columns, covering the whole duration. `--start`/`--end` select a source-time window. `--count` supports 1-24 frames, `--columns` supports 1-6, and `--width` supports multiples of 16 from 160-960; aspect ratio is preserved within a padded 16:9 cell. Source-time labels are requested seek times, not frame-accurate edit boundaries.

The output directory contains `contact-sheet.jpg`, `frame-0001.jpg` etc., and `preview.json` with source SHA-256, file identity, sample window, timestamps and filenames. Inspect the sheet, then individual frames if needed. The helper explicitly supplies a system font file on Windows, macOS or Linux where a known font is available, avoiding a missing Windows fontconfig setup. FFmpeg must provide scaling, tiling and drawtext filters.

`segment` creates a silent MP4 at 12 fps, default width 960, with a requested duration up to 30 seconds. It uses FFmpeg/libx264 and verifies output metadata with ffprobe. It returns the original source-time range, allowing denser frame sampling within that interval. It is a review derivative, not a candidate or final edit. Segment timestamps are approximate seek boundaries, and a 12-fps derivative cannot prove the original's full-frame-rate motion quality.

Review means actually inspecting the produced artifacts through available tools. Extraction alone is not review. Compare subject, activity, context, framing, crop flexibility and usable duration against the current request. Record the review method and limitations in assessment notes. A thumbnail may justify a rejection or shortlist, but suitability requires more evidence. Sparse frames do not prove smooth motion or a complete action; inspect denser windows or continuous playback where needed. Download and check the full-resolution source before registering it, and confirm that selected ranges still apply. None of these commands automatically approves, registers or counts preview derivatives toward the three-candidate minimum.

Official provider API references: [Pexels](https://www.pexels.com/api/documentation/) and [Pixabay](https://pixabay.com/api/docs/). Review their current terms during sourcing. Automated API searches need a configured key; browser sourcing remains an alternative when supported.

## Publication recovery

The project mutation guard serializes helper writes. Ledger JSON is atomically replaced; journal append/video creation and ledger publication are separate operations. A failure after a journal append can leave untracked bytes and blocks retry. Inspect the journal and prior ledger, then reconcile them using the original input; never silently truncate or rewrite journal history merely to make the helper pass. A completed but unregistered video may be registered in place after inspection. Partial copied videos must be identified and resolved before registration; ffprobe metadata alone does not prove an uninterrupted recording or complete decode. This active-development format intentionally has no compatibility or migration machinery for older project schemas.
