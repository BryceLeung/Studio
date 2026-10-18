---
name: studio-sync
description: Run or resume Studio substage 3.1, synchronizing each camA/camB/lav recording group and writing signed millisecond offsets to one JSON file per group.
---

# 3.1 — Sync

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project resolution, prerequisite validation, logs, completion manifests, and invalidation. Run after 2.1 Footage Intake. This stage estimates synchronization offsets; fragment boundaries, script matching, and editing timelines belong to later stages.

## Inputs and outputs

Read the project's media in `2-Production/1-Footage Intake/`. Each recording group (called a shot in this stage) uses one clip number (`C01`, `C02`, and so on) shared by its three source clips, and contains exactly one non-empty file for each role:

```text
<Script>__C<nn>__camA.mov or .mp4
<Script>__C<nn>__camB.mov or .mp4
<Script>__C<nn>__lav.wav
```

`C<nn>` identifies the source clip set, not an individual performance; a take is an individual performance of a script passage within the synchronized source clips. Extensions are case-insensitive. Ignore unrelated files, including Audacity `.aup3` projects. A missing role, empty file, or multiple supported files for one role prevents generation for the requested set until corrected; report the filenames. Read the source media in place.

Write one `<Script>__C<nn>.json` per recording group directly in the project's OneDrive `3-Post Production/1-Sync/` folder. Paths inside JSON are relative to the project root and use forward slashes, so they can be used on Windows and macOS.

`clips.<role>.offset_ms` is an integer relative to camA's first decoded audio sample, with camA fixed at zero. A positive offset means the recording starts later than camA; a negative offset means it starts earlier. To align a target sample at source time `t_ms`, its time on the camA reference is `t_ms + offset_ms`. Do not clamp negative offsets; a later editing stage can shift all sources together if it needs a nonnegative timeline.

Each clip also records its path, byte size, modification time in nanoseconds, and `standard_score` (null for the reference). The score measures correlation peak strength; it is not a probability. The JSON records the schema version, group name, reference role, offset convention, UTC generation time, algorithm settings, dependency versions, BBC revision, and script hashes.

## Dependencies

Run the checked-in installer with Python 3.10–3.14; Python 3.13 is the recommended starting point:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/1-Sync/scripts/install_dependencies.py"
```

It first checks the existing `<workflow-root>/.venv-sync/` environment for dependency consistency, the pinned BBC source revision, the pinned `imageio-ffmpeg` version, and a working FFmpeg executable. If all checks pass, it prints `already done` and exits successfully without running installation commands or downloading packages. Otherwise it creates or reuses the environment, installs the required dependencies, and checks them again. The BBC revision is pinned because the published release's NumPy constraint does not support our Python 3.13 installation. Package downloads require network access. Keep this environment and the FFmpeg binary out of Git; the installer is the reproducible setup entrypoint. Transitive Python packages are resolved by pip for the platform and interpreter; their actual versions are recorded in each result.

Use `--check` to check the existing installation without installing packages. The generator selects this environment automatically and uses the bundled FFmpeg by its explicit path; global FFmpeg and manual environment activation are unnecessary.

## Generate offsets

After validating upstream completion markers and resolving the actual OneDrive project path, run:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/1-Sync/scripts/generate_offsets.py" "<OneDrive project folder>"
```

For a scoped trial or rerun, add `--shot "What Is Freedom For__C01"`. Repeat `--shot` for several exact group names. A scoped run does not complete the whole stage while other groups remain unprocessed.

## Parallel clip groups

Each complete `<Script>__C<nn>` clip group is an independent synchronization work unit. Its camA, camB, and lav files must be processed together, but offset computation for different clip groups can run concurrently, including different clip numbers with the same `<Script>` prefix. Do not group all clips for one script into a single mandatory task or split the three roles among workers. Use the actual double-underscore filename convention and the helper's discovered exact group names.

Follow the root skill's delegation policy, preferring visible Agent Manager local sessions when session creation is authorized. Assign each worker one exact clip group, its three input files, and exclusive responsibility for its `<Script>__C<nn>.json` result. Reuse current results rather than recomputing them to create parallel work, and bound concurrency by memory and CPU capacity because each group decodes audio into memory.

Run one supported `generate_offsets.py "<project>" --shot "<Script>__C<nn>"` invocation per assigned clip group. Different groups may run concurrently on the same machine. A single invocation with several selected groups still processes those groups serially. Check/install dependencies once through the coordinator before launching workers; do not run installers concurrently.

Each worker registers its clip group in `.SYNC-JOBS/<job-key>.json` while computing. The generator rejects duplicate ownership and preserves interrupted job records for inspection rather than automatically removing them. A process-shared project mutation lock serializes source/output rechecks, archive preparation, final JSON publication, and failure invalidation, without holding that lock during audio decoding or offset measurement. Do not delete or replace `.studio-mutations.lock`: its presence is normal, and the operating system releases its lock when the owning process exits. These locks coordinate one host only; do not run workers on different machines against the same OneDrive project.

Each clip group has its own `STALE/PENDING/<job-key>.json`, keyed by SHA-256 of the exact group name and recording that name as `job_id`. The stage-wide `STALE/PENDING.json` remains the explicit completion baseline; numeric `STALE/N/` archives remain unchanged. Shared graph invalidation still applies to the whole stage and its dependents when any group fails. The coordinator collects every worker's result or failure, serializes its own log writes, then validates the full clip-group inventory before recording 3.1 complete. Completion refuses active/interrupted jobs and validates and clears all of this stage's pending pointers only after success, preserving historical archives. Do not bypass normal publication helpers or let workers record partial stage completion.

The generator decodes the first audio stream from each file as mono 8 kHz PCM, then uses the BBC library's MFCC correlation with a 32-sample hop (4 ms search interval) and its default 2,000-frame correlation limit. It decodes each input once per group and keeps the decoded audio in memory. A missing audio stream, decoder error, insufficient audio, silence, or nonfinite or degenerate correlation fails that group. Other independent groups are attempted; any failure makes the command exit nonzero. A failed group does not receive a new JSON.

The offsets are estimates rounded to integer milliseconds. The search interval is not a guarantee of accuracy. This stage calculates a constant offset; it does not measure recorder clock drift or acoustical delay, and source audio timestamps and alternate audio streams are not used. Repeated speech can produce ambiguous matches. Preserve scores for later inspection and report questionable results rather than presenting them as verified synchronization. The initial T01 comparison agreed with FCP within one 30 fps frame; that observation does not establish accuracy on all footage.

## Logs, completion, and reruns

Use the root skill's shared prerequisite check before normal generation. After validating offsets for every recording group, invoke the shared completion recorder with `--stage 3.1`; do not write `DONE` by hand. A successful `--shot` run alone is not evidence that all groups are complete; the recorder validates the full inventory.

Create the step folder and an initial `LOG.md` when executing this stage. Append every attempt, dependency failure, result, and handoff using the root skill's `scripts/append-log.py`. Record the selected groups, settings, output filenames, reported offsets and scores, any failures, and next action. The generator publishes offset JSON and manages job records and shared invalidation/archive bookkeeping; the coordinator manages run-summary logs and final completion recording.

Existing JSON with matching source fingerprints, settings, dependency versions, and script hashes is reused. Valid human edits to estimates are preserved. A stale or invalid existing file is reported and preserved. Inspect changes before choosing `--overwrite`, which explicitly recomputes the selected files. After measurement and replacement verification, the common regeneration helper archives selected old JSON into the pending `STALE/N/` before publication. Execution failures archive affected `DONE` markers through the same dependency graph but do not move JSON or media. A matching default rerun leaves completion untouched. Do not delete orphaned JSON automatically when groups disappear; flag the discrepancy during validation.

Success means upstream steps are valid and every supported recording group in the intake folder has a valid current JSON with three finite, machine-readable estimates, integer millisecond offsets, camA zero, and matching input fingerprints and generator metadata. This is computational completion, not a claim of listening-verified accuracy. Never mark the stage complete on a partial or failed run.

Use the shared recorder only after checking that condition. It fingerprints the stage implementation with SHA-256, the full supported media inventory using the common size/integer-second method, and every JSON output with SHA-256. The generated offset JSON retains its own dependency/algorithm provenance and legacy source identity representation. Exclude `LOG.md`, `DONE`, and unrelated intake files. On rerun, use shared validation for upstream evidence and this step's saved results; do not mutate markers during preflight or status. Keep unrelated and still-current results.
