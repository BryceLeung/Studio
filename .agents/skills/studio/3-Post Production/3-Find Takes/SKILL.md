---
name: studio-find-takes
description: Run or resume Studio substage 3.3, mapping approved scripts to word ranges in all matching transcript JSON files and generating one Takes JSON per script.
---

# 3.3 — Find Takes

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project
resolution, logging, and completion manifests. This stage finds performances of
approved script passages. It preserves the scripts and transcripts and generates
estimated mappings for review. Take selection and editing timelines are later
work.

## Inputs and outputs

Read the approved scripts in `1-Pre Production/1-Script Review/Approved/` and
native word-timestamped JSON files in `3-Post Production/2-Transcribe/`.
The source adapters support Word, plain-text, and script JSON; normal 1.1 outputs
are Word documents. Only text after a Word document's `Spoken script` heading is
included. Pair transcripts by filename:

```text
<Script>__C<nn>__lav.json
```

Include every matching recording, such as C01 and C02, in the script's output.
Missing transcripts, conflicting script stems, or a supported transcript with no
approved script fail validation. Audio, SRT, and sync offsets are not inputs.
Retain unknown word times as null; timestamps are relative to each original
audio file.

Write one file per approved script in the OneDrive project:

```text
3-Post Production/3-Find Takes/<Script>__Takes.json
```

For example, `Love Compounds Too__Takes.json`. The per-script file contains one
recording block per transcript, with source fingerprints, editable word
groupings, links, and discarded word ranges. Each link uses the same inclusive,
zero-based script/transcript word ranges as Line schema v2. See
[the format and initial algorithm](references/format.md) when reading or changing
the data contract.

## Run

Validate the 1.1 approved outputs against its manifest. An explicit request for
3.3 may use available 3.2 JSON even while 3.2's WER completion condition remains
undefined. Validate the actual JSON inputs; do not invent or write a 3.2 DONE
marker, and do not treat 3.3 completion as completion of 3.2.

Resolve the real OneDrive project root, then run with the dashboard Python or
another Python 3.9+ interpreter:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/3-Find Takes/scripts/find_takes.py" "<project-root>"
```

The script uses RapidFuzz (`RapidFuzz>=3,<4`), num2words (`num2words>=0.5.14,<0.6`),
and the repository's shared Line
source adapters and quality classifier. It does not require PySide6 or audio dependencies. `--output-dir`
supports an isolated trial; such a run does not complete the OneDrive stage.
The generator never reads benchmark mappings.

The initial algorithm advances through transcript words, comparing variable
spans to script groupings near a script cursor. It can revisit recent script
groups, search for a strong distant restart, and emit spans across or inside
initial sentence boundaries. Unmatched passages are discarded by this first
algorithm. These are estimates; benchmark mismatches identify needed tuning.
Quality is classified separately after normalizing case, punctuation, and
spacing. Exact requires identical normalized wording. Other readings use
full-sentence `rapidfuzz.fuzz.ratio`: Partial ≥75, Flub <75.
Normalization also accepts English digit/written-number forms and hyphen/space
variants, preserving audible number signs, decimal digits, and symbols.
These rules do not change
the span matcher's token-distance scores or candidate thresholds.

## Benchmark

The `benchmarks/` directory has one flat subdirectory per script. Each case
contains `<Script>.docx`, its `<Script>__C<nn>__lav.json` transcript files, and
human-reviewed `<Script>__Takes.vNNN.json` files. Takes filenames follow Line's versioned naming
convention, `<Script>__Takes.<version>.json`. Source references inside a case's
Takes JSON can retain their original project-relative paths; the runner resolves
adjacent filenames in memory and verifies source bytes and word snapshots.
It never rewrites the human review. A case contains no audio.

Only human-reviewed mappings and their corresponding script/transcript inputs
belong in `benchmarks/`. The current case is What Is Freedom For, using the v002
Line review of C01 and C02. The previous single-recording v001 review remains
preserved. Both Line schema v2 and multi-recording schema v3 are supported.
Love Compounds Too has no human-reviewed reference and is excluded. The manifest records fingerprints and the reviewed transcript
inventory; the runner rejects unreviewed cases or extra unreviewed transcripts.
Review JSON fingerprints normalize checkout line endings to LF; script and
transcript source fingerprints continue to verify exact bytes.
These project copies are explicitly requested fixtures; general project data
belongs in OneDrive.

Run `scripts/benchmark.py --output-dir <new-temporary-directory> --report
<new-report.json>`. Add `--case "<Script>"` to run one case. Predictions use the
same flat case directories and versioned filenames under the output directory.
It generates predictions first, then reads the references.
It writes JSON and Markdown results with exact take-range precision/recall,
word-assignment agreement, classification changes and scores, and individual
mismatches for each recording. Reference labels predate fuzzy scoring, so label
agreement is not an independent measurement of quality accuracy.
Machine predictions and comparison reports stay outside `benchmarks/`. The
first comparison report is preserved at `reports/initial/What Is Freedom
For.json` and `What Is Freedom For.md`. Preserve those measurements when tuning;
use fresh external destinations for new predictions and reports. Moving the
fixtures changes paths and fingerprints, not the initial accuracy measurements.

## Reruns, logs, and completion

Existing take files are reused only if their sources, algorithm settings/code,
recording inventory, and range structure are current. Valid human mapping edits
are preserved on reuse. Stale or malformed files are preserved and reported;
inspect them before explicitly choosing `--overwrite`. Do not remove orphaned
outputs automatically.

The agent creates the stage folder and initial `LOG.md` and appends every run,
failure, result, and handoff with the root skill's `scripts/append-log.py` helper.
Record input inventories/fingerprints, algorithm settings, output paths, take
counts, reuse/replacement decisions, benchmark results when requested, and the
next action. The generator writes take files; the agent manages logs and DONE.

**Success means every approved script has a valid current take file that includes
all its matching transcript files.** Missing script coverage or imperfect
matching accuracy does not prevent computational completion; report it for
review. Do not require a benchmark accuracy threshold or human approval to
declare generation complete. An empty approved set or any failed script is not
success.

After verifying that condition, write a JSON `DONE` containing the exact
approved-script, matching-transcript, and required-output inventories; SHA-256
fingerprints of those inputs/outputs; this skill, the generator, and the shared
Line adapters/actions/mapping implementation; algorithm metadata; and the UTC
completion time. Distinguish workflow-relative dependencies from project-relative
ones. Exclude the Studio root skill, benchmark fixtures/reports, logs, audio,
SRT, and DONE itself. Validate these inventories and fingerprints on rerun;
invalidate changed completion markers according to the root skill before
regeneration.
