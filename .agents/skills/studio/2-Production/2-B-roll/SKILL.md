---
name: studio-b-roll
description: Run or resume Studio substage 2.2, sourcing B-roll candidates from manually supplied footage, Pexels and Pixabay with append-only per-asset request and search journals. Does not source stills, graphics, animation or generate video.
---

# 2.2 - B-roll

## Execution helpers

Use Python 3.10+ and the standard-library helpers under `scripts/`. Candidate inspection requires an existing FFmpeg installation with `ffprobe` on PATH; do not install tools during validation. Read [the helper interfaces and JSON examples](references/helpers.md) before execution. Test with isolated fixtures, never live project artifacts.

`broll.py context` discovers approved assets read-only. `init` creates missing `request.md` journals from explicit sourcing/shot plans; `--asset` scopes initialization to one asset. `revise` appends a creative request revision. `search` records each query before calling the provider, then appends its outcome; `search-note` appends the review result or next-query rationale. `register` records supplied/downloaded video, and `assess` appends an editorial assessment after actual video review. `check` is read-only. `complete` builds rich nested evidence and invokes the shared recorder; generic inventory-only recording is deliberately refused. Every `broll.py` invocation requires `--project-root`.

Use `broll.py search` for all actual asset searches so the asset's `request.md` records their progress. The underlying `stock.py` search commands are scratch utilities, not substitutes for the linked workflow. API keys are read from `PEXELS_API_KEY` or `PIXABAY_API_KEY`, never request files or chat. If no key is configured, report the access requirement. `stock.py download` acquires a selected video into external scratch, verifies its video stream and refuses overwrites. Register it into the project separately. Searches do not assess or approve footage, and creative revisions require fresh targeted searches where appropriate.

Keep all provider API requests and repeatable orchestration in maintained Python scripts. Use `broll.py search` with explicitly named queries, saved result files and journaled outcomes. Do not run provider requests, query loops, result-file writing or thumbnail/download orchestration through ad hoc `python -c`, shell request snippets or temporary one-off clients. Extend and test the maintained helper when a repeatable operation is missing. Shell commands should invoke its documented CLI; the agent's work is selecting queries, inspecting evidence and making editorial decisions.

Use `preview.py shortlist` to download a bounded result page's thumbnails and build an ID-labelled review sheet in external scratch. It records missing/failed images as unavailable, without assigning scores or rejecting the underlying footage. Inspect the generated sheet before selecting matches for video previews.

Use `stock.py thumbnail` to screen a selected result, then `stock.py preview` for a smaller video rendition. `preview.py sheet` creates labelled samples across the whole video or a chosen time window, with individual frames and a provenance/timing sidecar. Inspect those images using the available image-viewing tool. `preview.py segment` creates a short silent clip for closer action review; it does not claim that an agent has watched it. Use `stock.py acquire` to download a selected full-resolution variant only after the preview appears suitable. See the helper reference for exact commands and limits.

Read the [Studio root skill](../../SKILL.md) for project resolution, prerequisite checks, logs, invalidation and completion. `dependencies.json` declares current 1.4 as the prerequisite. This branch does not require 2.1 presenter Footage Intake and does not become a prerequisite of existing presenter postproduction stages merely by being added.

## Purpose and scope

Collect at least three distinct usable video candidates for each active, approved asset whose current registry Type is `B-roll`. Read both protected 1.4 registry files through their supported parser; the human Review and Type fields in Markdown determine inclusion. Never select solely from potentially outdated JSON human-field snapshots. Skip Reject, inactive and non-B-roll assets; Pending Review upstream blocks execution through the prerequisite gate.

Use existing footage supplied manually by the user, Pexels and Pixabay. Storyblocks is reserved for later: do not search, require a subscription, purchase or download from it unless the user enables it. Do not generate video, search the user's Google Photos account, acquire stills, or assemble final edited sequences in this stage.

## Project artifacts

Keep artifacts under the resolved OneDrive project:

```text
2-Production/2-B-roll/
  ASSET-000010/
    request.md
    Candidates.json
    C001.mp4
    C002.mov
    C003.mp4
  LOG.md
  DONE
```

Create one folder per included ASSET ID. Preserve registry IDs exactly. Create only the folders needed for the current run. `DONE` is written only by the shared completion recorder after validation passes. Manually supplied videos may keep their original filenames; candidate IDs remain stable separately. Registration from outside the asset folder copies the file to the next C-numbered filename and preserves its source.

Candidate IDs, logical request revision IDs and search IDs increase monotonically within each asset; never overwrite or recycle an earlier ID. Historical asset folders and footage remain when an asset is later rejected or retyped; they do not count toward the current required set.

## Append-only request and search journal

On first intake, create `request.md` containing the approved registry's Asset brief verbatim, including its existing spelling and paragraph breaks. Record the source registry fingerprint, ASSET ID, script/passage applicability and brief fingerprint in candidate evidence, separately from the verbatim brief. Do not expand the initial brief into a shot list or silently add creative instructions.

Keep one readable file per asset. When the user changes the creative request, `revise` appends the full new brief and reason to this same file and records a new logical revision ID and content SHA-256 in the ledger. Carry unchanged requirements forward; clarify consequential ambiguities rather than inventing requirements. An unchanged rerun creates no revision. Do not create `request.vN.md` files.

Every search term is appended before the provider call, followed by its outcome, result count and saved-result location or sanitized failure. Append the visual-review outcome and rationale for the next terms with `search-note`. Search entries never change the creative brief's revision or fingerprint, so they do not invalidate existing candidate suitability. A candidate assessment binds to the current logical brief revision, not to the hash of the growing journal.

The ledger records journal byte size, exact-byte SHA-256 and event offsets, plus historical brief content hashes and offsets. Never edit earlier journal text in place. The latest logical brief revision is the sourcing request. If the approved 1.4 brief changes, reconcile it against local refinements before appending a new revision; ask when their intent conflicts. Search-language changes and more alternatives require journal entries, not creative revisions. Framing or other changed creative requirements use `revise`; a changed purpose, source passage, media type or approval belongs in 1.4 first. Do not mutate the protected registry as an incidental sourcing action. This active-development schema has no automatic migration or compatibility path for earlier versioned files or ledgers.

## Run or resume

1. Resolve the project and validate prerequisites and existing stage evidence through the shared workflow. Reuse a current stage on an ordinary rerun. An explicit request for more footage or revised requirements starts a scoped refinement even when the last completion is current.
2. Inspect the current eligible asset set, request journals and candidate records before searching. Use the common invalidation transition when beginning incomplete/stale execution or changing completed work. Preserve journal history, evidence and footage in place on ordinary workflow reruns; these are not disposable generator outputs.
3. Reuse valid requests and suitable existing candidates. Identify each asset's shortfall against the latest request and the three-candidate minimum. Keep journal, log and evidence publication serialized; do not concurrently mutate the same asset's journal.
4. For personal/family footage, present a concrete manual handoff stating the ASSET ID, destination folder and needed footage. The user supplies files; inspect and register them after arrival. Never silently replace an actual-family requirement with stock people. An unresolved handoff remains incomplete while independent stock work can continue.
5. Search Pexels and Pixabay for requests that allow stock. Read current provider download and licence terms at execution time, then use the staged matching loop below. Compare the actual subject/action, context, framing and crop flexibility to the current request. A cooking shot does not establish lunch packing; stock people do not establish the user's family. Sparse samples cannot establish smooth motion, complete action or the absence of an intervening unwanted shot. Do not claim continuous playback when only sampled frames were inspected. Preview derivatives, result pages, thumbnails and unacquired links do not count as candidates. If access/download tools fail, report the limitation and preserve successful work.
6. Record each candidate and its current-request assessment. Complete checks and append a result or human handoff to the normal stage log. Record completion only after every included asset satisfies the success condition.

Never assume an unavailable family clip exists. Existing brief fallbacks may be followed where they are explicit; otherwise ask whether the user wants to supply footage, revise the request, or revise the asset in 1.4. Do not silently waive the minimum because fewer personal clips exist.

## Suitability scoring

Score each prospective match against the latest request, not against a generic idea of attractive footage. These weights are initial editorial settings and may be changed as the user refines the workflow. Record the weights used with each assessment; preserve earlier scores so changes remain interpretable.

| Criterion | Initial weight | Evidence to assess |
|---|---:|---|
| Subject and action | 40% | The requested person, object and activity are actually visible. |
| Context and tone | 25% | The setting and emotional tone fit the story and its ordinary family-life context where requested. |
| Editing usefulness | 25% | Usable source ranges, framing, crop flexibility and room to cut support the intended passage. |
| Technical quality | 10% | Visible focus, exposure, resolution and artifacts support use at the intended size. |

Rate each criterion from 0 to 5. Compute the total out of 100 as `sum(weight_percent * criterion_rating / 5)`; weights must sum to 100. Use 0 for no match, 1 for poor, 2 for weak, 3 for adequate, 4 for strong and 5 for an excellent match. Do not manufacture an exact rating for something the inspected evidence cannot establish: mark it unknown and request closer inspection. Technical quality from a thumbnail is provisional and does not establish full-resolution or motion quality.

Apply hard suitability checks before interpreting a weighted total. Reject a clear required-subject/action mismatch, or stock people offered as actual family footage. Unresolved provenance/licensing or unverified video usability blocks counting even when the visible match scores highly. Overall polish cannot compensate for a fundamental mismatch. For example, folding pasta is not packing a child's lunch, even if a search for lunch returned it.

Maintain separate thumbnail, contact-sheet and closer-review assessments. Each records the current logical brief revision/fingerprint, criterion ratings, weighted total where sufficiently supported, weights, evidence inspected, relevant source times, shots covered, rationale, uncertainties and decision. Later evidence can raise or lower the score; do not average earlier impressions into the latest score. The latest sufficiently supported assessment determines suitability. A new creative brief revision requires reassessment; search-only entries do not. A changed scoring policy requires recalculation of totals and decisions; repeat visual review only where the available evidence no longer supports the revised criteria.

A thumbnail supports quick rejection or shortlisting, not final acceptance. A contact sheet supports a refined provisional score. If motion, action continuity or context remains uncertain, inspect a short video segment or denser frame samples and state what was actually inspected. Extraction alone does not constitute review. Never claim continuous motion was reviewed when only still samples were available.

For a request with multiple shots, evaluate a clip against the particular required shot(s) it supplies; it need not depict the entire sequence alone. Record those shot labels, and assess coverage across the candidate set. Scores do not waive required shot coverage or the distinct-source rule. Reuse adequate existing candidates before searching for more.

The current Python `assess` helper stores supplied editorial review data; it does not call a vision model or calculate the weighted score automatically. Include the scoring details with the assessment specification/notes. No new model/API connection or model usage cost is authorized merely by this scoring policy.

## Staged matching loop

Use these initial settings alongside the criterion weights above. They are editable workflow settings, not fixed quality standards; retain the settings used with each recorded score.

| Setting | Initial value |
|---|---:|
| Contact-sheet score needed for closer review | 65/100 |
| Closer-review score needed to count as a candidate | 80/100 |
| Target distinct candidates per asset | 3 |
| Maximum search-term refinements after the initial term | 5 |

Threshold comparisons are inclusive. A high score never bypasses the hard suitability checks, provenance/licensing, actual acquired-video checks, or required shot coverage. Do not round a failing total upward to meet a threshold. If evidence is insufficient to support a score or decision, record that uncertainty and inspect further rather than inventing a passing total.

1. **Thumbnail screening.** Download and inspect the provider thumbnail. Reject obvious subject/action/context mismatches immediately, recording a concise reason. Shortlist plausible or ambiguous matches. The thumbnail score is provisional and cannot mark a candidate. If the provider or an existing valid cache supplies no thumbnail, record that limitation and proceed to the smallest video preview rather than bypassing the provider's cache window.
2. **Contact-sheet scoring.** For shortlisted results, download the smallest usable standalone video rendition to external scratch, create a contact sheet from samples across its duration, and inspect it. Record or refine the four criterion ratings, total, rationale, shot coverage and uncertainties. Reject supported scores below 65; advance supported scores of 65 or higher to closer review. If sparse sampling cannot resolve a potentially good match, obtain the missing visual evidence instead of treating an unknown criterion as zero or silently passing it. The small download here exists to generate the sheet; this is not yet the full-resolution candidate acquisition.
3. **Closer low-resolution review.** For matches passing the contact-sheet threshold, inspect a larger low-resolution rendition where useful, a short source-time segment, or denser samples of promising windows. Reuse the small download where sufficient. Refine the ratings and score again based on the evidence actually inspected; scores can decrease. Resolve material action, framing, crop and continuity uncertainty before acceptance. Sampled stills alone do not justify a claim of having reviewed continuous motion.
4. **Mark passing candidates.** When the latest closer-review score is at least 80, acquire the full-resolution rendition, check that its content and proposed usable source ranges agree with the preview, retain provenance/licence evidence, and register it. Append the final assessment including the score and settings. Mark it suitable only after those checks pass; a passing preview score alone does not count as an acquired candidate. Poor full-resolution evidence can lower the score or reject the clip. Preserve rejected match records and existing candidate history.
5. **Stop at the target or refine the search.** Count current, distinct, suitable acquired candidates against the asset's latest request, including qualifying existing footage. Stop searching and downloading for that asset as soon as the target is met; do not complete an extra batch or keep collecting alternatives automatically. Plan complementary shot coverage as candidates accumulate. If the target is met but required shot coverage is missing, report the gap and ask whether to replace a choice, refine the request or collect more; do not declare completion or silently exceed the target. When a batch yields too few suitable matches, append the review outcome and next-query rationale, adjust the search terms within the existing brief and search again. Try alternate action words, objects or context, subject to the five-refinement cap below. Never lower thresholds or pad the set with weak matches.

Allow the initial distinct search term plus **at most five term refinements per logical brief revision**, across every invocation and provider. Case and whitespace changes do not create new terms. Review results between refinements; do not spend the entire budget on an unreviewed batch. The Python helper counts recorded attempts, preflights the remaining budget, and refuses further searches once it is exhausted. A failed API request still records its term; retrying that same term before exhaustion does not consume another refinement. Do not repeat exhausted terms, switch providers, or append artificial brief revisions to evade the stop rule.

After the fifth refinement's results are reviewed, if matches still fall below the applicable threshold or the target remains unmet, stop and tell the user. Report the terms tried, candidates accepted, best supported scores and recurring mismatch. Explain that the threshold may be too high or the search direction may need changing; ask which to adjust. Do not silently lower thresholds, broaden the creative brief, reset the budget or continue searching. Only a real, explicitly authorized creative revision starts a new per-revision budget.

The default target and current Python completion minimum are both three. An explicit request for more footage may raise the collection target. A request to reduce the completion minimum below three needs a deliberate helper/validator policy change as well as an editorial setting change. The score thresholds and weighted-score computation above are currently enforced by the agent workflow, not automatically by `broll.py check`.

For step 2, use `stock.py preview --smallest` to select the provider's absolute smallest standalone rendition without hand-written URL selection. Step 3 may use `stock.py preview` without that flag for a rendition at least 640 pixels wide, or `preview.py sheet`/`segment` for closer inspection. Keep all screening and preview artifacts in external scratch. Record screening outcomes in a scratch review JSON tied to provider ID, logical brief revision/fingerprint and the scoring settings; append its outcome and next-query rationale with `search-note`, then transfer the final assessment to `Candidates.json` after registration. Do not store discovery failures or preview derivatives as counted candidates.

## Requests containing multiple shots

Track the shots or actions described by the request and which candidates cover them. For example, Disney footage and lunch preparation can remain separate clips under the same ASSET ID. Keep any ordering instructions with the request; do not require a preassembled montage or perform final editing here.

The minimum is **three candidates per asset**, not three per shot. Cover the request's required shots across the candidate set; distinguish explicit requirements from optional examples such as “two or three may be enough.” Flag missing coverage instead of counting unrelated clips to satisfy the number. If the distinction is unclear, ask the user.

The same source can support several assets where it fits. Record shared source identity so reuse is visible. Within one asset, duplicate downloads, alternate encodes and crops of the same clip do not count as distinct alternatives. A single file may cover several shots but counts only once.

## Candidate evidence

`Candidates.json` uses schema `studio.broll.candidates`, version 2; the helper validates the exact-byte append-only journal, logical brief history and file identities before mutation. It retains:

- Asset identity, initial registry provenance, current logical request revision/fingerprint, and earlier request identities with content hashes and journal offsets.
- Journal byte size and SHA-256, ordered event offsets, and stable search IDs with terms, outcomes and follow-up notes.
- Stable candidate ID, relative local video path, file identity and source identity.
- Origin (`User`, `Pexels`, or `Pixabay`), original filename or provider ID, and provider page URL when applicable.
- Acquisition date and stock licence/attribution evidence applicable when acquired; retain this alongside the footage rather than relying solely on a changing URL.
- Content description, usable time ranges, shots covered and the assessment against the current request.
- The logical brief revision that originally produced the candidate and subsequent reassessments, preserving previous outcomes.

Do not treat the asset's 1.4 approval as approval of a specific downloaded clip. Candidate collection has no separate human clip-selection gate in this draft; final footage choice belongs to editing. Do not fabricate provenance, licence evidence, technical checks or a user review.

Include the visual review method in assessment notes: which thumbnail/samples/segment were actually inspected, relevant source times, what matches, and any unresolved motion or context concerns. Reject false keyword matches with a specific reason. Recheck proposed ranges against the acquired full-resolution source; low-resolution previews may differ slightly in duration. Keep preview artifacts in external scratch, not in ASSET folders or completion inventories. Creating a preview never marks footage suitable automatically.

## Completion and later refinement

Completion requires a current 1.4 prerequisite, all current eligible assets processed, valid request/candidate records, and at least three distinct usable local video candidates per included ASSET ID assessed against its latest logical request revision. Required shot coverage must be resolved. Missing family footage or unanswered requirements prevent completion for that asset. Superseded/unsuitable clips and historical brief entries remain preserved but do not satisfy the current minimum.

If no eligible B-roll assets remain, report that condition and ask how the user wants to proceed; do not invent assets or infer an empty-stage completion policy.

Later requests reopen only affected work editorially: append a logical request revision when requirements change, or retain the current brief revision when simply collecting more footage or changing search terms. Reassess old candidates after creative changes and acquire additional candidates as needed. Search-only journal entries do not require reassessment. Valid candidates may count toward the revised request; do not regenerate three new files unnecessarily. Continue beyond three when explicitly asked for more.

The validator checks the live eligible asset set, append-only journal integrity, latest logical request identity, candidate files, distinct sources and current assessments. Completion evidence watches `request.md`, `Candidates.json` and recognised candidate video files in the asset folders, including preserved retired assets, with inventories that detect additions, removals and superseded applicability. Appending a search makes the prior completion snapshot stale but leaves current-brief candidate suitability intact. A per-asset folder alone or three arbitrary nonempty files is insufficient. Helpers do not infer visual suitability: that is the recorded agent review, while ffprobe verifies stream metadata and SHA-256 detects duplicate bytes.

Interrupted journal publication can leave untracked appended bytes; interrupted import can leave an unregistered video. Stop and inspect rather than silently truncate or overwrite history. Recover an unregistered complete video using `register`; reconcile journal/ledger mismatches against the last valid ledger and original input before retrying. An invalid ledger is never silently rebuilt. No legacy-data migration is required while this stage is in active development; do not add preservation or migration machinery for earlier schemas.

Use the shared invalidator and recorder for completion changes; never write DONE directly. Re-record only after full-stage validation passes. Common dependency rules determine affected downstream markers; preserve unrelated presenter branches. Log the request, affected assets, reused/acquired footage, result and next action. No archive cleanup, deletion of rejected candidates, or replacement of valid footage is implied by a rerun.
