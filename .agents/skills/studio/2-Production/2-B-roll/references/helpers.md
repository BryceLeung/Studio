# Python helper interfaces

All project commands use `python "<skill>/scripts/broll.py" <action> --project-root "<resolved-project>"`. Use the coordinator's known plan-specification path during initialization. After initialization, keep revised text and review JSON in the current numbered run, with search-related inputs and review outputs beneath their allocated attempt directory. Helpers never author editorial plans or judge visual suitability; the agent provides those after reading the current request and inspecting video.

## Local worker invocation

Stage 2.2 defaults to coordinator-launched local subagents; read the assignment and ownership rules in the skill. The coordinator initializes requests and assigns exactly one asset per worker. Workers call the documented CLI with the same resolved project root and their assigned `--asset`. Normal searches omit `--cache-dir` and `--output-dir`: the helper uses the asset's `cache/` and allocates the next attempt directory beneath the current `search.NN/`. Workers run the complete sourcing/review loop and return evidence to the coordinator. The CLI serializes shared project writes with the local mutation guard; provider calls and review may overlap across assets. Do not call internal mutation functions directly, run `complete` from workers, or treat OneDrive sync as a cross-machine lock. The coordinator joins workers before full-stage `check` and `complete`.

## Discovery and initial requests

`context` is read-only and lists eligible IDs and their current registry fields. Before any stage execution, follow the root skill's shared audit. All project mutations and `check` enforce prerequisites again.

`init --spec "<plans.json>" [--asset A10]` creates missing `search.01.md` worker input/output journals, containing the verbatim brief followed by asset identity, sourcing, shot coverage and scoring/stop settings. Use `--asset` to initialize only the selected eligible asset. The plans object maps each new eligible ASSET ID to an explicit sourcing mode and required shots:

```json
{
  "A10": {
    "sourcing": "manual",
    "shots": ["Family at Disney", "Preparing lunch"]
  }
}
```

Choose `manual` for actual-family requirements, `stock` for stock sourcing, or `either` where the brief explicitly permits both. `shots` contains genuinely required shots; use an empty list for a request without separately required shots. Do not turn optional shot examples or alternative editorial ideas into new requirements. For role-based alternatives, use `shots: []` unless the brief has genuinely mandatory coverage. Give the worker the script passage, editorial role and at least three visual directions; record the chosen direction in search reasons and each accepted candidate's idea label, visible action and script connection in assessment `notes`. Three clips must represent three different visual ideas; source uniqueness alone does not establish diversity. This is an editorial check, not an automatic Python validation. Plans need include only missing assets; an empty object is valid on an unchanged rerun. Missing plans fail before publication. Initial request bytes are UTF-8 encoding of the parsed Asset brief, without added headings or a trailing newline.

## Request changes

`revise --asset A10 --file "<full-current-request.txt>" --spec "<plan.json>" --reason "<user-requested-change>"`

The plan is one object, e.g. `{"sourcing":"manual","shots":["Preparing lunch"]}`. The text file contains the full replacement sourcing brief, including unchanged requirements. The helper creates the next numbered `search.NN.md` with this full brief and reason, assigns the next logical revision ID, and retains all earlier journals and run directories. It stores the content SHA-256 and journal offsets, alongside the current 1.4 row, so a changed upstream brief requires explicit reconciliation. Before calling revise, resolve any conflict with local refinements with the user. Simply requesting more candidates or changing search terms appends to the current journal without a brief revision or budget reset. The `search.NN.md` number identifies a logical brief revision, not each individual query. Do not create `Requirements.md` or `Requirements.vN.md` files.

## Linked asset searches and search notes

Use this command for Pexels/Pixabay asset searches, after initialization and prerequisite validation. Storyblocks uses the MCP bridge described below:

```text
python "<skill>/scripts/broll.py" search --project-root "<resolved-project>" --asset A02 --provider pixabay --query "putting phone down" --reason "Find the requested action instead of scrolling or typing"
```

Supply explicit queries with `--query`; `--reason` is required. Omit optional `--cache-dir` and `--output-dir` in the normal project flow. If supplied, they must equal the assigned asset cache and next canonical attempt directory; an explicit output directory is allowed only for a single-query invocation. The default cache is `A02/cache/`, reused across runs. Each provider attempt gets a new directory such as `A02/search.01/01/`; the next refinement or retry uses `02/`, then `03/`, and so on. Every attempt retains `attempt.json`; the provider outcome is saved as `outcome.json`, with `results.json` on success. Failed attempts also retain their directory and recorded outcome. Each item in the returned `searches` array contains `output_dir`, `result_file`, `search_id`, `attempt` and the asset-relative `directory`; the top-level `request_journal` identifies the current `search.NN.md`. Use the returned attempt `output_dir` to place its thumbnails, previews, sheets and review JSON; do not assume the global search ID equals its per-run directory number. The helper allows the initial distinct term plus at most five refinements per logical brief revision, across invocations and providers, and preflights the whole requested batch against the remaining budget. Case/whitespace variants are the same term. Recorded failed attempts count as tried terms; an intentional same-term retry before exhaustion consumes no extra refinement. Review results before choosing the next refinement. Budget rejection makes no API calls, output folders or journal changes. The helper requires an initialized eligible asset and current prerequisites. It appends each term and its reason to that asset's current `search.NN.md` before calling the maintained stock wrapper, then appends the outcome, result count and saved-result location, or a sanitized failure. The returned search IDs identify attempts for follow-up notes; output also reports refinements used/remaining and whether the limit has been reached. Search terms and result locations never contain API keys.

After visual inspection, append the outcome and next-term rationale:

```text
python "<skill>/scripts/broll.py" search-note --project-root "<resolved-project>" --asset A02 --search-id 1 --reason "No suitable action: results show scrolling. Next try placing the phone face down."
```

Pass `--spec "<attempt-dir>/review.json"` to record inspected clip scores in the worker's current `search.NN.md`:

```json
{
  "reviews": [
    {
      "file": "Result 1.mp4",
      "source_id": "actual-provider-video-id",
      "stage": "contact-sheet",
      "ratings": {
        "subject_action": 4,
        "context_tone": 3,
        "editing_usefulness": 4,
        "technical_quality": 5
      },
      "evidence": "Inspected contact-sheet samples at 0, 2, 4 seconds; continuity still uncertain.",
      "decision": "Closer review"
    }
  ]
}
```

The helper calculates the total; this example produces 77/100. Optional `weights` use the same four keys and must sum to 100; the saved review always retains the weights used. Use `null` for an unknown rating: the total displays as `Pending`, never zero. A supplied `score` must equal the calculated total. Stages are `thumbnail`, `contact-sheet`, `closer-review`, and `full-resolution`. The filename identifies the actual local preview/acquired video; preserve `source_id` across renamed renditions. Ratings require actual inspection and `evidence` describing what was inspected. Threshold and hard-match decisions remain the worker's responsibility; recording a score does not register or accept a candidate.

The human-readable output uses literal tabs inside a text block:

```text
family lunch
	Result 1.mp4: 77/100 (contact-sheet; Closer review)
	Result 2.mov: 54/100 (contact-sheet; Reject)
```

Append another review when closer inspection changes a score. Each review repeats its search term and identifies the stage; earlier scores remain history, not an average. The next search attempt appends its term below. Terms are recorded before provider calls, with review pending; provider failures and empty result sets never receive fabricated scores. Record every inspected result, including rejections and unknowns, before choosing another query. Notes without a spec remain available for failures, zero results or next-term rationale, and explicitly say no scored results were recorded. Search notes cannot attach new reviews to a superseded brief. Full-resolution reviews should also be recorded here before transferring the final decision to candidate assessment evidence.

`search-note` appends only; it never rewrites earlier text or changes the creative brief. Search attempts, outcomes and review notes do not advance the brief revision or invalidate assessments against that brief. The ledger separately validates each numbered journal path, exact-byte SHA-256, byte size and event offsets. Creative revisions keep their own content fingerprints, so a growing search history is distinct from changing the request itself.

If a batch falls short, record why, choose new terms within the existing brief and try again within the five-refinement budget. Do not ask to rewrite the creative request merely because the first query failed. After the fifth refinement is reviewed without sufficient passing matches, stop and report terms tried, best supported scores and repeated mismatches. Ask the user whether the threshold is too high or a better search direction is needed. Do not lower thresholds, reset the counter, fabricate a brief revision or use the low-level wrappers to continue past the limit. A genuine user-authorized creative revision starts a new budget. Stop immediately when the target is met. The low-level `stock.py search` and `search-batch` commands below are standalone scratch utilities; they do not record asset history and must not replace this linked workflow.

## Storyblocks MCP search, preview and licensed acquisition

The Python helpers do not call the chat's MCP tools or obtain their credentials. The agent makes each MCP call between maintained CLI preparation/import commands. Keep all call captures, arguments, details, plans, previews, receipts and review files inside the allocated attempt directory. No shell HTTP clients, inline Python provider clients or automatic provider retries are needed.

1. Save the deliberately chosen MCP search parameters as JSON, e.g. `{"keywords":"elderly holding hands","required_keywords":"hand","content_type":"footage"}`. Run:

```text
python "<skill>/scripts/broll.py" storyblocks-begin --project-root "<project>" --asset A02 --query "elderly holding hands" --reason "Find the missing age-contrasting supportive clasp" --spec "<parameters.json>"
```

This validates prerequisites, the live brief, sourcing mode and remaining budget, then journals the request before returning `search_id`, `output_dir`, `tool` and exact `arguments`. Call that Storyblocks MCP tool with those arguments, without invented user or project IDs. One attempt may be pending per asset. If the budget is exhausted, stop unless the user explicitly requested this particular Storyblocks retry; only then add `--authorization "<actual user request>"`. That exception authorizes one search, preserves all prior history and does not create a new brief revision or budget.

2. Capture the returned JSON without reconstructing or editing signed URLs. `storyblocks.py capture` reads JSON from process stdin and saves a new file:

```text
python "<skill>/scripts/storyblocks.py" capture --tool mcp__storyblocks__search_videos --arguments-file "<parameters.json>" --output "<attempt>/mcp-search.json"
python "<skill>/scripts/broll.py" storyblocks-finish --project-root "<project>" --asset A02 --search-id <id> --file "<attempt>/mcp-search.json"
```

Feed the exact serialized MCP return value to the capture process on stdin through the host's process interface. Capture accepts an arguments object or a preparation report containing `arguments`. The saved envelope is `{"tool":"<exact tool name>","arguments":{...},"response":{...exact returned JSON...}}`. Finish checks the captured call against the journal, saves normalized `results.json` and appends its outcome under the project guard. It refuses duplicate imports, superseded briefs and interrupted publication artifacts. For an MCP failure, run `storyblocks-finish --search-id <id> --failed` instead, recording a sanitized failure with no fabricated result count. An invalid import can be corrected before finishing; never silently overwrite published results or truncate the journal.

3. Use the existing `preview.py shortlist` and `stock.py thumbnail` tools with `results.json`. Before closer review, call `mcp__storyblocks__get_stock_item_details` with `contentClass: "videos"`, the selected `stockItemId`, and `extended: "download_formats,description"`. Capture that call to a new file using the same `capture` CLI, then run:

```text
python "<skill>/scripts/storyblocks.py" details --result-file "<attempt>/results.json" --provider-id <id> --response-file "<attempt>/mcp-details.json" --output "<attempt>/details-<id>.json"
python "<skill>/scripts/stock.py" preview --result-file "<attempt>/details-<id>.json" --provider-id <id> --output "<attempt>/previews/<id>.mp4" --smallest
```

The normalized details retain the search's real detail-page URL and contributor credit, verify item identity and obtain preview dimensions from provider format metadata. Preview URLs are marked preview-only; `stock.py acquire` and direct full-resolution `stock.py download` refuse Storyblocks acquisition. Reuse existing contact-sheet/segment review and `broll.py search-note --spec` scoring. A preview never counts as an acquired candidate. Fresh details can refresh expired preview links using new filenames without repeating a search or licensing call.

4. Only after the user explicitly requests downloading this selected item, prepare acquisition:

```text
python "<skill>/scripts/storyblocks.py" prepare-acquisition --result-file "<attempt>/details-<id>.json" --provider-id <id> --authorization "<actual user download request>" --min-width 1920 --output "<attempt>/acquisition-plan-<id>.json"
```

The plan selects the highest-resolution MP4 meeting the minimum width, records the user request and returns exact arguments for `mcp__storyblocks__get_download_url`. **Calling that tool licenses the item and records a download each time.** Do not call it for discovery or format inspection. Call it once for the plan, leave account identities to the connection defaults, and capture the complete response with `capture --tool mcp__storyblocks__get_download_url --arguments-file "<attempt>/acquisition-plan-<id>.json" --output "<attempt>/mcp-download-<id>.json"`.

5. Select the URL for the plan's format from that captured response and run:

```text
python "<skill>/scripts/storyblocks.py" acquire --plan-file "<attempt>/acquisition-plan-<id>.json" --response-file "<attempt>/mcp-download-<id>.json" --url "<exact selected signed URL>" --output "<attempt>/acquired/<id>.mp4" --license-file "<attempt>/provenance-<id>.json"
```

The URL must occur character-for-character in the captured response. The helper verifies tool/item identity, rejects watermarked sources, enforces trusted provider/CDN domains and the existing 1 GiB limit, probes the acquired dimensions, and emits provenance with the MCP response hash, authorization and acquired-file hash. Current trusted hosts are Storyblocks and its observed `dm0qx8t0i9gc9.cloudfront.net` CDN; an unfamiliar download host fails closed and needs a verified allowlist update, never a disabled URL check. Do not print or rewrite signed URL query parameters.

The receipt records the provider's licensed-download response, not a legal interpretation of account terms or a fabricated licence certificate. Retain the captured response as evidence. Reuse it for local network retries while its URLs remain valid; never automatically call `get_download_url` again. Inspect partial publication (including an acquired file without its receipt) before recovery. A new licensing call after expiry needs an explicit download request covering that call.

6. Inspect the acquired full-resolution footage and chosen ranges, replace the provenance's placeholder description with the actual visible content, then use the existing `register --file ... --spec "<attempt>/provenance-<id>.json"` and `assess` commands. Storyblocks provenance requires the receipt and matching video hash; registration remains distinct from editorial acceptance. Full-stage checking, duplicate-source checks, completion and all manual-footage restrictions remain unchanged.

## Register acquired footage

`register --asset A10 --file "<local-video>" --spec "<provenance.json>"`

User footage:

```json
{
  "origin": "User",
  "source_id": "family-original-2024-07-15-clip-12",
  "description": "Family walking toward the park entrance"
}
```

The source ID identifies the original clip, including across copies or alternate encodes. Registration checks duplicate source IDs and byte hashes within the asset. Do not invent a fresh source ID to count a duplicate rendition. Videos already dropped directly into this asset folder are registered in place with their filename preserved. Files outside the asset root, including full-resolution acquisitions beneath a numbered attempt directory, are copied into C-numbered root files, preserving the originals. Nested preview or acquired files do not count as candidates until registered into the root. Multiple unregistered handoff files can be registered one by one; completion remains blocked until all recognised files have records.

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

Use actual provider IDs, URLs, dates and retained licence evidence. The example placeholders are not valid provenance. Pixabay uses origin `Pixabay` and a Pixabay page. Storyblocks uses the acquisition provenance JSON produced by `storyblocks.py acquire`; its receipt hash must match the video bytes. Replace its placeholder description with an accurate description after inspection, preserving the acquisition evidence. A manual-only current request refuses stock registrations. Registration probes video metadata using ffprobe, stores SHA-256 and shared media identity, and creates no suitability assessment.

## Correct a stock creator credit

`correct-credit --asset A10 --candidate C001 --spec "<credit.json>" --reason "Verified creator against saved provider result"` corrects only the licence attribution. The specification contains `origin`, `source_id`, `source_url` matching the registered source exactly, plus the corrected `attribution`. Verify that credit against the saved provider response before calling. The guarded helper preserves the old credit, new credit, timestamp and reason in `credit_corrections`, updates attribution atomically and uses normal invalidation/logging. It cannot replace footage, change source identity or bypass suitability review. Never remove/re-register a candidate or hand-edit its ledger to fix a credit.

## Assess current applicability

Watch the footage, then call:

`assess --asset A10 --candidate C001 --spec "<assessment.json>"`

```json
{
  "suitable": true,
  "reviewed_by": "Agent after video inspection",
  "notes": "Idea: family outing. Visible action: family walking at Disney. Script connection: shared time together. Framing and inspected source ranges support this idea and the current request.",
  "ranges": [[1.2, 5.8]],
  "shots": ["Family at Disney"]
}
```

Ranges use source seconds, start-inclusive/end-exclusive, within verified duration. Shot labels match the current request's plan exactly. Reject unsuitable footage with `suitable:false`, notes explaining why and `ranges:[]`/`shots:[]` as appropriate. New assessments append to history. A request revision makes earlier assessments ineligible until reassessed against that revision; the helper never silently carries suitability forward.

## Check and complete

`check` is read-only, including prerequisite validation. It checks the complete current eligible set, required shots, current assessments, exact-byte journal integrity and logical request history, duplicate sources/bytes, candidate identities and video metadata. It stops on the first actionable problem. `Candidates.json` is schema `studio.broll.candidates`, version 3; no older-schema migration is performed during active development.

`complete` runs the same checks, builds portable input/output/workflow fingerprints and passes them to shared `record_done`. The nested output inventory watches all `search.NN.md` files, `Candidates.json`, root candidate videos and all nested numbered-run artifacts, including historical ASSET folders too. It detects added and removed artifacts; the provider `cache/` is excluded. Nested preview videos never count as candidates. It never writes a custom DONE or approves anything. Common read-only `scripts/validation.py --stage 2.2` checks the resulting evidence. New media, journal entries or candidate changes make prior completion stale; search-only entries do not change candidate applicability against the brief. Failed prerequisite checks never invalidate ancestors.

## Stock discovery and downloads

`python "<skill>/scripts/stock.py" search --provider pexels --query "parent preparing lunch" --cache-dir "<external-scratch>/stock-cache" --output "<external-scratch>/lunch-results.json"`

Use `--provider pixabay` for Pixabay. Keys come from `PEXELS_API_KEY` / `PIXABAY_API_KEY` environment variables; never put keys into query arguments or tracked files. Search output includes provider credit, source-page links and download variants; keep creator credits when presenting candidates. `--page N` explicitly selects a page, with no automatic pagination. Queries are targeted, not bulk harvesting. Pixabay responses are cached for the required 24-hour window. For linked project work, cache writes belong in the asset's `cache/`; result and review artifacts belong beneath `search.NN/NN/`. Standalone low-level utility use may still use external scratch. Neither path allows writes into workflow source, unrelated project areas or candidate root filenames.

`--output` saves the complete normalized search response for subsequent thumbnail/preview/acquisition commands and prints a compact result summary. It refuses an existing output before making a provider request. Omitting `--output` retains the original JSON-on-stdout behavior.

For several deliberately selected queries, use the maintained batch command:

```text
python "<skill>/scripts/stock.py" search-batch --provider pixabay --query "putting phone down" --query "phone table" --cache-dir "<scratch>/stock-cache" --output-dir "<scratch>/asset-000002-search-01"
```

Repeat `--query` for each intended query, up to eight distinct queries per invocation. No automatic query expansion, pagination, provider fallback or network retry occurs. A new output directory contains `search-01.json`, `search-02.json`, etc. plus `batch.json` summarizing IDs, authors, source pages and durations. On a failed query, completed files remain and `batch.json` records the incomplete run and failing query index; later queries are not attempted. Existing directories are refused. A later intentional retry can use a new output directory while reusing the same 24-hour cache.

All provider API calls and repeatable discovery orchestration belong in these maintained scripts. Do not substitute inline Python clients, shell API requests or one-off query/result-writing loops. Add missing operations to the helper with tests, then invoke its CLI.

`python "<skill>/scripts/stock.py" download --provider pexels --url "<selected-variant-url>" --output "<external-scratch>/chosen.mp4"`

The downloader accepts selected-provider HTTPS URLs, validates redirects, limits file size, streams to a temporary file, probes video and publishes without overwrite. It does not acquire licence evidence or assess suitability. Inspect downloaded footage, collect the applicable provider licence evidence, then register and assess separately. For linked project work, set the destination beneath the allocated attempt directory, then register the inspected full-resolution source into the asset root. Standalone utility use may download into external scratch. If the project destination or network requires tool approval, request it normally.

## Thumbnail, small-preview and full-resolution selection

Use a result file saved by `broll.py search` for asset work. Standalone scratch `stock.py search --output` and `search-batch` files, or an existing search cache JSON envelope, are also supported by the preview tools. Do not put API keys in that file. Each result includes provider thumbnail URLs where supplied; old caches remain valid for their full 24-hour window. If an old cached result has no thumbnail, use its video preview rather than bypassing the cache window.

```text
python "<skill>/scripts/stock.py" thumbnail --result-file "<attempt-dir>/results.json" --provider-id 11613 --output "<attempt-dir>/thumbnails/11613.jpg"
python "<skill>/scripts/stock.py" preview --result-file "<attempt-dir>/results.json" --provider-id 11613 --output "<attempt-dir>/previews/11613.mp4"
python "<skill>/scripts/stock.py" acquire --result-file "<attempt-dir>/results.json" --provider-id 11613 --output "<attempt-dir>/acquired/11613.mp4" --min-width 1920
```

`thumbnail` retrieves one provider poster image, checks JPEG/PNG/WebP container signatures and a 20 MiB size limit, and refuses replacement. Use an image viewer to inspect it; a file signature alone is not proof of meaningful visual content. The returned format identifies the actual container.

To script a result page's thumbnail downloads and create a single review sheet:

```text
python "<skill>/scripts/preview.py" shortlist --result-file "<attempt-dir>/results.json" --output-dir "<attempt-dir>/thumbnails" --limit 20
```

This processes at most the explicitly selected limit (1-24 results), without extra searches. It saves provider thumbnails, a labelled `thumbnail-sheet.jpg` and `shortlist.json` with source-page credits and image availability. Missing/failed images are recorded as unavailable, not visually rejected. If none can be obtained, the report has no contact sheet and directs video-preview fallback. Existing output directories are refused. The agent inspects the sheet and supplies editorial decisions; the script never invents a score.

`preview` chooses the smallest downloadable rendition at least 640 pixels wide. If all are smaller, it returns the largest available and flags the fallback. `acquire` chooses the highest-pixel-count rendition meeting the explicit minimum width; it fails if none qualifies rather than silently lowering the threshold. Width is literal: use an appropriate explicit minimum for portrait footage. Both report the selected variant, provider ID and source page and leave content unreviewed/unregistered. HLS playlists and variants without reliable dimensions are excluded.

Use `preview --smallest` for the contact-sheet sampling pass: it selects the absolute smallest valid standalone rendition and reports purpose `samples`. This keeps variant selection inside the maintained Python helper rather than requiring custom result parsing or raw download-URL orchestration.

## Sampled frames, contact sheets and closer review

```text
python "<skill>/scripts/preview.py" sheet --file "<attempt-dir>/previews/11613.mp4" --output-dir "<attempt-dir>/sheets/11613" --count 8
python "<skill>/scripts/preview.py" sheet --file "<attempt-dir>/previews/11613.mp4" --output-dir "<attempt-dir>/sheets/11613-detail" --start 10 --end 15 --count 12
python "<skill>/scripts/preview.py" segment --file "<attempt-dir>/previews/11613.mp4" --start 10 --duration 5 --output "<attempt-dir>/previews/11613-detail.mp4"
```

These commands work on any supplied local video, including manual family footage. For linked B-roll work, output artifacts beneath an allocated `ASSET-ID/search.NN/NN/` attempt directory; provider caches belong only in the asset cache. Standalone preview utilities may still use external scratch outside the repository and canonical workflow outputs. The numbered attempt area is the supported project-preview exception; do not write previews into an asset root, approval folder, archive or another stage. Existing outputs are never overwritten. Use unique review directory/file names on reruns.

`sheet` takes evenly spaced samples at bin midpoints, avoiding end-of-file seeks. Default: 8 labelled 480x270 cells, 4 columns, covering the whole duration. `--start`/`--end` select a source-time window. `--count` supports 1-24 frames, `--columns` supports 1-6, and `--width` supports multiples of 16 from 160-960; aspect ratio is preserved within a padded 16:9 cell. Source-time labels are requested seek times, not frame-accurate edit boundaries.

The output directory contains `contact-sheet.jpg`, `frame-0001.jpg` etc., and `preview.json` with source SHA-256, file identity, sample window, timestamps and filenames. Inspect the sheet, then individual frames if needed. The helper explicitly supplies a system font file on Windows, macOS or Linux where a known font is available, avoiding a missing Windows fontconfig setup. FFmpeg must provide scaling, tiling and drawtext filters.

`segment` creates a silent MP4 at 12 fps, default width 960, with a requested duration up to 30 seconds. It uses FFmpeg/libx264 and verifies output metadata with ffprobe. It returns the original source-time range, allowing denser frame sampling within that interval. It is a review derivative, not a candidate or final edit. Segment timestamps are approximate seek boundaries, and a 12-fps derivative cannot prove the original's full-frame-rate motion quality.

Review means actually inspecting the produced artifacts through available tools. Extraction alone is not review. Compare subject, activity, context, framing, crop flexibility and usable duration against the current request. Record the review method and limitations in assessment notes. A thumbnail may justify a rejection or shortlist, but suitability requires more evidence. Sparse frames do not prove smooth motion or a complete action; inspect denser windows or continuous playback where needed. Download and check the full-resolution source before registering it, and confirm that selected ranges still apply. None of these commands automatically approves, registers or counts preview derivatives toward the three-candidate minimum.

Official provider API references: [Pexels](https://www.pexels.com/api/documentation/) and [Pixabay](https://pixabay.com/api/docs/). Review their current terms during sourcing. Automated API searches need a configured key; browser sourcing remains an alternative when supported.

## Publication recovery

The project mutation guard serializes helper writes. Ledger JSON is atomically replaced; journal append/video creation and ledger publication are separate operations. A failure after a journal append can leave untracked bytes and blocks retry. Inspect the journal and prior ledger, then reconcile them using the original input; never silently truncate or rewrite journal history merely to make the helper pass. A completed but unregistered video may be registered in place after inspection. Partial copied videos must be identified and resolved before registration; ffprobe metadata alone does not prove an uninterrupted recording or complete decode. This active-development format intentionally has no compatibility or migration machinery for older project schemas.
