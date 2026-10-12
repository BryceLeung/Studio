# Find Takes benchmarks

These fixtures contain approved scripts, original transcript JSON files, and
human-reviewed mappings. Each case has its own folder. The current nesting is
intentional and is recorded in [manifest.json](manifest.json):

```text
benchmarks/
  manifest.json
  README.md
  What Is Freedom For/
    What Is Freedom For/
      What Is Freedom For.docx
      What Is Freedom For__C01__lav.json
      What Is Freedom For__C02__lav.json
      What Is Freedom For__Takes.json
    Love Compounds Too/
      Love Compounds Too.docx
      Love Compounds Too__C01__lav.json
      Love Compounds Too__Takes.json
```

The manifest's `directory` is relative to `benchmarks/`; `name` is the script
name used by `--case` and output folders. When `directory` is absent, the runner
uses `name` as the case directory for compatibility with earlier flat fixtures.
Each case's files remain adjacent. Original project-relative source paths in a
review are resolved to these adjacent files in memory. Source fingerprints and
word snapshots are still verified; the reference JSON is never rewritten.

## Reviewed references

| Case | Recordings | Reference | Reviewed takes |
| --- | --- | --- | --- |
| What Is Freedom For | C01, C02 | Approved Find Takes schema v1 output | 28 + 27 |
| Love Compounds Too | C01 | Human-edited Line schema v2, review v001, copied as `Love Compounds Too__Takes.json` | 138 |

What Is Freedom For includes the transcript boundary repairs and the C02 local
rewind correction that keeps "having..." with its abandoned reading before
"He wanted a grandchild...". The current algorithm reproduces all 55 takes and
838 word assignments in this case.

Love Compounds Too retains the user's edited v001 as the reviewed reference.
It has 108 Exact, 27 Partial, and 3 Flub takes. The original generated mapping
had 137 takes; the review adds one Partial take through the correction below.
Source words, timestamps, and all script groupings are unchanged, as are the
other 136 take mappings and their labels.

## Known imperfect Partial match: "then another"

In Love Compounds Too C01, around 3:40, two consecutive script groups (59 and
60) both say **"Then another one."** The speaker instead says
**"then another, then another."**, omitting "one" from both readings.

This combines changed wording with an ambiguous boundary between two identical,
very short script lines. The human review interprets the two spoken phrases as
the two consecutive script lines. It is the best reviewed mapping we have, but
the readings remain imperfect matches; neither qualifies as Exact.

The table uses inclusive, zero-based word indexes. Group numbers above are
one-based, as shown in Line.

| Version | Transcript words and reading | Script words | Treatment |
| --- | --- | --- | --- |
| Original generated mapping | 491-493: "then another, then" | 431-433: second "Then another one." | One Partial take |
| Original generated mapping | 494: "another." | None | Discarded |
| Reviewed v001 | 491-492: "then another," | 428-430: first "Then another one." | Partial |
| Reviewed v001 | 493-494: "then another." | 431-433: second "Then another one." | Partial |

The reviewed split recovers the discarded word and separates the two phrases.
Both shortened readings score about 85.71/100 using the current normalized
RapidFuzz comparison. Exact still requires identical normalized wording;
Partial uses the existing threshold of 75. The human decision here concerns
the boundaries and assignment to repeated script lines, not a label override.

**Current policy:** retain the edited mapping as the reference and leave this
case for manual review. Keep the mismatch visible in normal benchmark metrics.
Do not omit these ranges, relax Exact, or tune the algorithm specifically to
reproduce this single edit. Revisit automation if more reviewed examples support
a general improvement that also holds up across the other cases.

With algorithm v5 and boundary refinement v2, the current result is:

- 136 matching take ranges out of 138 reviewed takes; 137 predicted takes.
- 1110 of 1113 word assignments agree; differences are words 491, 492, and 494.
- All 136 matching take ranges retain the reviewed quality labels.

This is an accepted known miss. Regression tests verify that it remains visible
and localized; a future general improvement can update these expectations after
review without changing the frozen reference to fit the algorithm.

## Run and interpret

From the repository root, use the dashboard Python environment or another
interpreter with the dependencies listed in [the stage skill](../SKILL.md):

```text
python ".agents/skills/studio/3-Post Production/3-Find Takes/scripts/benchmark.py" --output-dir <new-directory>/predictions --report <new-directory>/report.json
```

The default run evaluates both script cases and all three recordings. Add
`--case "Love Compounds Too"` or `--case "What Is Freedom For"` to select one.
Predictions are written to `<output-dir>/<Script>/<reference-filename>`, without
repeating the fixture's outer folder. JSON and Markdown reports stay outside
`benchmarks/`, as do generated predictions. Use a fresh destination for each run.

Add `--all-versions --boundary-proposals` to compare the initial alignment pass
with the complete boundary refinement. This writes separate `baseline/` and
`preview/` outputs and proposal diagnostics. There is currently one approved
reference per script; optional `reviews` inventories can retain older versions.

Every prediction and boundary search finishes before any reference mapping is
read. A generated mapping becomes a reference only after explicit user approval.
Review JSON fingerprints normalize CRLF to LF for checkout portability; native
script and transcript fingerprints verify exact bytes. Labels are compared as
saved, without recalculation. Similarity scores are not probabilities, and
agreement with an approved mapping is a regression check rather than an
independent estimate of accuracy on new recordings.

Preserve the historical [initial JSON report](<../reports/initial/What Is Freedom For.json>)
and [initial Markdown report](<../reports/initial/What Is Freedom For.md>).
Their descriptions and measurements reflect the original benchmark inventory,
not the current expanded set. Adding a case or changing folder paths does not
rewrite those earlier measurements.
