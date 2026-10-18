---
name: studio-take-selection
description: Run or resume Studio substage 3.4, scoring every mapped take from 3.3 by continuity first and recency second.
---

# 3.4 — Take Selection

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project
resolution, logging, and completion manifests. Resolve the project and call the
script below with its latest mapping files; do not score takes manually.

## Inputs and outputs

Inputs are the mapping JSON files from `3-Post Production/3-Find Takes/`.
Preserve reviewed mappings and their script/transcript word ranges. The
existing formats are Find Takes schema v1 and Line mapping schemas v2/v3;
they contain source word snapshots, transcript timings, and take links.

The output is JSON under `3-Post Production/4-Take Selection/` that assigns a
numerical score to each take. Higher scores rank above lower scores; the
highest-scoring candidate for a script passage is the one to select. Retain
enough source identity and the original inclusive, zero-based word ranges to
associate each score with its input take, including across recordings.

Write one `<Script>__Scores.v1.json` per script. Each scored take retains the
source mapping's project-relative path (including its saved version) and SHA-256,
script and transcript source references, and both original word ranges. A
derived `take_key` hashes that identity for convenience; it does not add IDs to
3.3 or remain the same after a mapping/source revision. Include the original
classification, `continuity_sentences`, `recording_number`, `recency_rank`, and
final integer `score`. Score comparisons are within one script's output.

## Criterion 1: continued reading

The strongest stated evidence of quality is that the speaker kept going through
the script without stopping or restarting. The user confirmed that "kept rolling
and didn't cut" means continuing through subsequent script lines, rather than
whether the camera remained recording.

The user defines continuation as: "the next sentence in the script has a
non-flub candidate take that continues on from this take". Use the approved
script group order and chronological transcript takes to identify that next
sentence. A run stays within one recording. Do not use elapsed pauses as a
criterion or invent a pause threshold.

Every take in the same uninterrupted run shares a continuity score based on the
whole run's length. Do not award different scores according to the number of
sentences remaining after each take. Continuity is evidence of quality, not
proof that the wording or delivery is good.

The user confirmed these rules:

- Only Exact and Partial takes participate in runs. Flubs remain scored takes,
  but receive zero continuity points and break the run.
- The next take must read the immediately following script sentence and follow
  directly in transcript word order. Discarded or unmapped speech between takes
  breaks the run. A repeat, rewind, skipped script sentence, or recording
  boundary also ends the run.
- The continuity score is the number of script sentences in the entire run.
  For example, every take in an eight-sentence run scores 8. An isolated
  non-Flub take scores 1. Do not convert this criterion to a 0–100 scale.

The user's editorial goal is to favor longer shots. Sentence count is the
agreed measure of continuous mapped reading, not elapsed shot duration. Choosing
the highest score independently for each sentence may still switch between
runs. Apply the approved recency precedence below when run lengths tie; an
additional preference to stay in the currently selected run has not been defined.

Keep the existing Exact/Partial/Flub labels intact. The known imperfect Partial
mapping in the Find Takes benchmark remains a reviewed input; selection scoring
does not authorize changing its boundaries. Exact and Partial have equal scoring
weight. There is no wording-quality or delivery-quality bonus in version 1;
labels only determine whether a take can participate in a continuous run.

## Criterion 2: prefer later takes when run lengths tie

The user considers later takes more likely to be polished. Among candidates for
the same script passage with equal continuity run lengths, give precedence to
the later take. This is a secondary criterion: a later take from a shorter run
must not outrank an earlier take from a longer run because of recency alone.

The user confirmed chronological ordering across recordings:

1. Compare the recording number in the transcript source filename numerically.
   Every C02 take comes after every C01 take, regardless of timestamps within
   those separate recordings.
2. Within the same recording, compare transcript word order. The take with the
   larger `transcript.word_start` is later.

For example, a seven-sentence C02 run loses to an eight-sentence C01 run. If both
runs contain eight sentences, the C02 candidate wins. Between equal-length
candidate runs in C02, the later reading of the script passage wins.

Keep the whole-run sentence count as the continuity component. Any final
numerical encoding of recency must preserve this ordering exactly. Do not infer
recording chronology from local timestamps or filesystem modification times.

## Numerical encoding

For each script, let `N` be the number of input takes across all recordings.
Sort takes by numeric recording number, then local `transcript.word_start`,
and assign one-based `recency_rank` values from 1 to N. Compute:

```text
score = continuity_sentences * (N + 1) + recency_rank
```

This integer encoding guarantees that one extra continuity sentence outweighs
every possible recency difference. The continuity component remains the whole
run's sentence count. Flubs have zero continuity but still receive a score from
their recency rank; every non-flub ranks above every flub. No floating-point
fractions or 0–100 normalization are used.

## Run

Find the highest positive numeric `<Script>__Takes.v<N>.json` version for every
approved script in `3-Post Production/3-Find Takes/`. Pass those exact files,
one per script, explicitly to this command using the dashboard Python or
another Python 3.9+ interpreter with Studio's existing dependencies installed:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/4-Take Selection/scripts/score_takes.py" --project-root "<real-project-root>" "3-Post Production/3-Find Takes/<Script>__Takes.v<N>.json" [...other latest mapping paths...]
```

The script validates the supplied inventory, 1.1 approval, 3.3 completion
fingerprints, current script/transcript snapshots and coverage, mapping schemas,
and recording chronology. It preserves saved labels and ranges. The reviewed
script grouping order defines the sentence sequence, as in 3.3. Ambiguous or
missing latest versions, unsupported schemas, and unparseable or duplicate
recording numbers fail explicitly. It never falls back to an older mapping.

`--output-dir <separate-directory>` runs an isolated trial with its own log and
no project `DONE` writes or invalidation. It still validates the complete latest
mapping inventory and actual sources, but does not require upstream DONE markers.
Do not use an upstream input directory as the output directory.

## Completion and reruns

Normal project execution validates prerequisites through the shared JSON graph, then submits the stage-owned manifest to the common `record_done` API after checking persisted scores. Do not write `DONE` by hand. Isolated output trials keep their existing trial semantics and never record project completion.

**Done means every take identified by 3.3 has exactly one finite numerical
score associated with its original source and word ranges**, including Flubs.
An empty or partially scored input set fails. The script verifies the persisted
JSON and writes `DONE` only after the full inventory passes. Missing or explicitly
discarded script passages are not invented as takes.

The script handles append-only logging through Studio's log helper, fingerprints
the exact selected mappings, sources, scoring implementation and skill, and
archives failed/stale completion markers using Studio's common graph helper.
It does not fingerprint the shared Studio root skill. Matching output files are
preserved on rerun. If an existing output differs, the script fails and preserves
it for inspection before replacement; do not silently overwrite human edits.
After a deliberate replacement decision, `--regenerate` computes the complete
replacement set first, archives selected old score JSON into the pending
`STALE/N/` slot, then publishes and verifies the new scores. It never moves Takes
mappings or source inputs. Isolated trials do not mutate canonical archives.
This stage produces scores, not an editing timeline, and does not imply 3.2
completion.

Run the behavioral tests with:

```text
python -m unittest discover -s ".agents/skills/studio/3-Post Production/4-Take Selection/scripts" -p "test_score_takes.py" -v
```
