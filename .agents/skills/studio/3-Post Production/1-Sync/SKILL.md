---
name: studio-sync
description: Run or resume Studio substage 3.1, synchronizing each camA/camB/lav recording group and writing signed millisecond offsets to one JSON file per group.
---

# 3.1 — Sync

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project resolution, prerequisite validation, logs, completion manifests, and invalidation. Run after 2.1 Footage Intake. This stage estimates synchronization offsets; fragment boundaries, script matching, and editing timelines belong to later stages.

## Inputs and outputs

Read the project's media in `2-Production/1-Footage Intake/`. Each recording group (called a shot in this stage) contains exactly one non-empty file for each role:

```text
<Script>__T<nn>__camA.mov or .mp4
<Script>__T<nn>__camB.mov or .mp4
<Script>__T<nn>__lav.wav
```

Extensions are case-insensitive. Ignore unrelated files, including Audacity `.aup3` projects. A missing role, empty file, or multiple supported files for one role prevents generation for the requested set until corrected; report the filenames. Read the source media in place.

Write one `<Script>__T<nn>.json` per recording group directly in the project's OneDrive `3-Post Production/1-Sync/` folder. Paths inside JSON are relative to the project root and use forward slashes, so they can be used on Windows and macOS.

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

For a scoped trial or rerun, add `--shot "What Is Freedom For__T01"`. Repeat `--shot` for several exact group names. A scoped run does not complete the whole stage while other groups remain unprocessed.

The generator decodes the first audio stream from each file as mono 8 kHz PCM, then uses the BBC library's MFCC correlation with a 32-sample hop (4 ms search interval) and its default 2,000-frame correlation limit. It decodes each input once per group and keeps the decoded audio in memory. A missing audio stream, decoder error, insufficient audio, silence, or nonfinite or degenerate correlation fails that group. Other independent groups are attempted; any failure makes the command exit nonzero. A failed group does not receive a new JSON.

The offsets are estimates rounded to integer milliseconds. The search interval is not a guarantee of accuracy. This stage calculates a constant offset; it does not measure recorder clock drift or acoustical delay, and source audio timestamps and alternate audio streams are not used. Repeated speech can produce ambiguous matches. Preserve scores for later inspection and report questionable results rather than presenting them as verified synchronization. The initial T01 comparison agreed with FCP within one 30 fps frame; that observation does not establish accuracy on all footage.

## Logs, completion, and reruns

Create the step folder and an initial `LOG.md` when executing this stage. Append every attempt, dependency failure, result, and handoff using the root skill's `scripts/append-log.py`. Record the selected groups, settings, output filenames, reported offsets and scores, any failures, and next action. The generator writes JSON only; the agent manages Studio logs and completion markers.

Existing JSON with matching source fingerprints, settings, dependency versions, and script hashes is reused. Valid human edits to estimates are preserved. A stale or invalid existing file is reported and preserved. Inspect changes before choosing `--overwrite`, which explicitly recomputes and replaces the selected files. Preserve superseded human work when needed. Do not delete orphaned JSON automatically when groups disappear; flag the discrepancy during validation.

Success means upstream steps are valid and every supported recording group in the intake folder has a valid current JSON with three finite, machine-readable estimates, integer millisecond offsets, camA zero, and matching input fingerprints and generator metadata. This is computational completion, not a claim of listening-verified accuracy. Never mark the stage complete on a partial or failed run.

Write `DONE` only after checking that condition. Fingerprint this skill and both scripts with SHA-256, the full set of supported media paths using byte size and modification time in nanoseconds, and every JSON output with SHA-256. Record the dependency and algorithm metadata and the exact output set. Exclude `LOG.md`, `DONE`, and unrelated intake files. On rerun, validate upstream and this step's manifests in workflow order; invalidate changed completion markers under the root skill before regenerating affected groups. Keep unrelated and still-current results.
