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

If no numbered Takes version exists for an approved script, generate its initial
mapping in the OneDrive project:

```text
3-Post Production/3-Find Takes/<Script>__Takes.v1.json
```

For example, `Love Compounds Too__Takes.v1.json`. Line saves subsequent reviews
as `<Script>__Takes.v2.json`, `.v3.json`, and so on in the same stage folder.
The per-script file contains one
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
another Python 3.9+ interpreter. This is the complete stage command; do not
create a separate runner or wrapper script:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/3-Find Takes/scripts/find_takes.py" "<project-root>"
```

The command initializes `LOG.md` when absent, validates the 1.1 approved-output
inventory, fingerprints, and empty-candidate condition against its DONE,
validates actual transcript inputs, generates only missing v1 files, checks
latest versions, logs the result, and manages completion markers. Missing or
changed approved outputs fail the prerequisite check before generation and
invalidate 1.1 and dependent completion. This stage checks the declared approved
outputs; it does not rerun the interactive Script Review workflow.

The script uses RapidFuzz (`RapidFuzz>=3,<4`), num2words (`num2words>=0.5.14,<0.6`),
and the repository's shared Line
source adapters and quality classifier. It does not require PySide6 or audio dependencies. `--output-dir`
supports an isolated trial with its own log; such a run does not write DONE or
invalidate completion markers in the OneDrive project. `--check-coverage` is a
read-only diagnostic and does not manage logs or completion markers.
The generator never reads benchmark mappings.

The base algorithm applies the scored transcript regroupings described below
after the initial alignment, keeps script groups fixed, and records the boundary
settings, code fingerprint, and per-recording diagnostics in the take file.
There is no boundary-refinement switch. Generation provenance is retained for
diagnostics; existing review versions are checked against current sources and
the success condition rather than rejected for older generator metadata.

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

The `benchmarks/manifest.json` lists each script case and its fixture-relative
`directory`. Keep the current nesting: `benchmarks/What Is Freedom For/` contains
separate `What Is Freedom For/` and `Love Compounds Too/` case folders. Each case
contains `<Script>.docx`, its `<Script>__C<nn>__lav.json` transcript files, and
human-reviewed `<Script>__Takes.json` or `<Script>__Takes.vNNN.json` files.
Source references inside a case's
Takes JSON can retain their original project-relative paths; the runner resolves
adjacent filenames in memory and verifies source bytes and word snapshots.
It never rewrites the human review. A case contains no audio.

Only human-reviewed mappings and their corresponding script/transcript inputs
belong in `benchmarks/`. What Is Freedom For uses the user-approved mapping of
C01 and C02, including the local rewind correction after "having...". Love
Compounds Too uses the human-edited v001 review of C01, copied as
`Love Compounds Too__Takes.json`. Find Takes schema v1 and Line review
schemas v2/v3 are supported. A generated mapping becomes a reference only after
the user explicitly approves it; generation alone does not establish approval.
The manifest records fingerprints and the reviewed transcript
inventory; the runner rejects unreviewed cases or extra unreviewed transcripts.
The optional `reviews` inventory lists preserved versions and the transcripts
reviewed in each version. `--all-versions` evaluates these separately rather
than treating an archived single-recording review as covering later recordings.
Review JSON fingerprints normalize checkout line endings to LF; script and
transcript source fingerprints continue to verify exact bytes.
These project copies are explicitly requested fixtures; general project data
belongs in OneDrive.

See [the benchmark guide](benchmarks/README.md) for the folder tree, reference
inventory, and known imperfect Partial match in Love Compounds Too. The two
shortened "then another" readings require a human interpretation of repeated
script lines. Retain the edited mapping as the reference and count the current
algorithm's disagreement in ordinary metrics. It remains a case for manual
review; do not tune the algorithm solely to force agreement with this example.

Run `scripts/benchmark.py --output-dir <new-temporary-directory> --report
<new-report.json>`. Add `--case "<Script>"` to run one case. Predictions use the
script-named directories and reference filenames under the output directory,
independently of the nested input layout.
It generates predictions first, then reads the references.
It writes JSON and Markdown results with exact take-range precision/recall,
word-assignment agreement, classification changes and scores, and individual
mismatches for each recording. Agreement with an approved mapping is a regression
check, not an independent measurement of quality accuracy.
Machine predictions and comparison reports stay outside `benchmarks/`. The
first comparison report is preserved at `reports/initial/What Is Freedom
For.json` and `What Is Freedom For.md`. Preserve those measurements when tuning;
use fresh external destinations for new predictions and reports. Moving the
fixtures changes paths and fingerprints, not the initial accuracy measurements.

### Boundary refinement

`studio/tools/line/boundaries.py` searches one to three adjacent transcript
groups (up to 80 words) at individual word gaps, keeping approved script groups
fixed. Paths can skip chatter, merge fragments, split a restart, or move a cut
between readings. Repeated readings and local rewinds of up to three script
groups are allowed; transcript words always advance. This allows a flub tail to
remain with its abandoned attempt when the following reading returns to an
earlier script line. Normalized Exact links are anchors. The search keeps three
alternatives per position/script/count state
and emits up to four readings per window, so it is a bounded search rather than
an exhaustive global optimum.

The window text score is the sum of each reading's full normalized RapidFuzz
score times its normalized word count, capped at its script group's normalized
word count, divided by all normalized words in the window. Unmatched words earn
zero credit. Capping prevents extra chatter from increasing a reading's weight.
Literal abandoned prefixes of at least three words are also candidates when an
original boundary or following restart supports them; quality labels still use
the shared classifier.

Require at least five points of text improvement and five points after evidence
and costs. An existing reading's newly discarded edges must independently earn
five text points, so a strong neighboring repair cannot carry a weak crop along.
Eligible paths receive boundary evidence: up to two points per one-second pause,
one for sentence-ending punctuation, and half a point for an original transcript
cut. Subtract a quarter point per changed word gap. Evidence is measured relative
to the current path. Select non-overlapping windows by total added credit.

Reports include raw and adjusted gains, before/after readings, and the next
retained alternative's score margin. These margins are search diagnostics, not
confidence probabilities. Reference versions can conflict; report each result
without changing frozen mappings to manufacture agreement.

## Reruns, logs, and completion

For each approved script, find numbered `<Script>__Takes.v<N>.json` files and
select the highest numeric version, not the newest timestamp or alphabetically
last filename. If any version exists, skip generation entirely, even if v1 is
absent or the latest version is incomplete, malformed, or has stale sources.
Never regenerate, overwrite, or fall back to an older version to obtain success.
Only scripts with no numbered version get a new v1; then check that v1.

Search only the stage folder. Takes versions use positive, unpadded numbers
(`.v1.json`, `.v2.json`, etc.). If multiple files
share the highest number, fail with an ambiguity report. Preserve unversioned
legacy mappings and all earlier versions; unversioned files do not count as a
numbered version and are not silently migrated. An isolated `--output-dir` run
searches only that output directory.

Checking validates the selected mapping's schema, ranges, source fingerprints
and snapshots, and complete matching-transcript inventory; it recalculates
quality and checks the success condition below. Valid human grouping edits and
script discards are accepted. Old generator metadata alone does not fail a
review. Invalid latest files fail the check and remain preserved for inspection.

The command creates the stage folder and initial `LOG.md`, then uses the root
skill's `scripts/append-log.py` helper's `append_entry` function for append-only
run, input-validation, result, and invalidation entries. It records UTC times,
input/output inventories and fingerprints, algorithm metadata, generation/skip
decisions, selected versions, blocking lines, and the next action. On failure
it deletes the failed step's DONE, dependent future DONE markers, and containing
stage-level completion; it never archives markers or deletes work files.
Invalidation follows declared project inputs/dependencies transitively,
preserves demonstrably independent steps, and treats subsequent steps with
unknown dependencies conservatively as dependent. Extra human handoffs and
requested benchmark runs can use the existing logging helper directly.

**Success means every word of every approved script has at least one non-flub
reading across its matching transcripts, or has been explicitly discarded on
the script side by the user.** Exact and Partial links count as non-flub
coverage over their inclusive script word ranges; Flub links do not count.
Coverage is combined across recordings, so each recording need not cover the
whole script. Every approved script must have a valid latest numbered mapping
containing all its matching transcripts. An empty approved set or any failed
script is not success. Benchmark accuracy is a separate regression measure,
not a completion threshold.

If filming deliberately skipped a line, the user can open the mapping in Line,
right-click the script line, choose **Discard script**, and save a new mapping
version. This explicit script-side discard marks those words as OK without
changing the approved script or deleting its words. **Restore script** removes
the exception. Transcript-side discards never waive script coverage. Do not
automatically discard missing or Flub-only script passages to achieve success.

The normal stage command always checks the selected latest versions after any
necessary v1 generation. It reports `complete`, the exact selected paths and
version numbers, word coverage, and blocking script line numbers/text with
Missing or Flub-only reasons. It exits unsuccessfully if any script fails.
When reporting failure, show these blocking lines and give the user these steps:

1. In Line, choose **Open Line project…** and open the reported latest mapping.
2. For each blocking script line, find a usable reading and link the script and
   transcript groups. Split or merge unlinked groups if needed. Exact or Partial
   coverage clears the requirement. If the line was deliberately skipped at
   filming, right-click that script line and choose **Discard script** instead.
3. Save with the suggested `<Script>__Takes` name. Line writes the next numbered
   version in the stage folder. Rerun 3.3; it skips generation and checks that
   new highest version. If no usable reading exists and the line is still
   required, a filming pickup is needed.

For structural/source failures, report the validation error and selected path
instead of inventing uncovered lines. `--check-coverage <mapping-path> [...]`
remains available for explicit read-only diagnostics; normal stage completion
must use the automatically selected highest version for every script.
The command records those exact versions in the log and DONE output inventory
and writes DONE only for `complete: true`. On `studio status` and reruns, discover
the latest versions again: a newly added higher version invalidates an older
DONE selection even if the older mapping's bytes have not changed.

After verifying that condition, the command writes a JSON `DONE` containing the exact
approved-script, matching-transcript, and required-output inventories; SHA-256
fingerprints of those inputs/outputs (including chosen saved review versions);
this skill, the generator, the append-log helper, and the shared
Line adapters/actions/mapping implementation; algorithm metadata; and the UTC
completion time. Distinguish workflow-relative dependencies from project-relative
ones. Exclude the Studio root skill, benchmark fixtures/reports, logs, audio,
SRT, and DONE itself. Validate these inventories and fingerprints on rerun;
the command invalidates changed completion markers and their dependents before
writing a new successful marker. A failed run leaves no stage DONE.
