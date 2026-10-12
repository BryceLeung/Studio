# Takes JSON and baseline algorithm

The schema is `studio.find-takes`, version 1. Every file covers one script and
all its transcript files. `script` is the canonical Line source object including
words, immutable starting sentence rows, a project-relative source path, and the
SHA-256 of the original bytes. `script_groupings` starts from those rows.

Each `recordings` item contains:

- `transcript`: the canonical Line source object, including ordered words,
  integer millisecond timings (or null), original sentence rows, fingerprint,
  and optional project-relative audio path.
- `groupings`: a complete partition of transcript words, in order, with no gaps
  or overlaps. It may differ from the original sentence rows.
- `links`: objects with `script`, `transcript`, and `classification` fields,
  exactly as in Line v2. Both ranges contain only `word_start` and `word_end`,
  inclusive and zero-based. A transcript group links at most once; a script
  group may have multiple readings in each recording and across recordings.
- `discarded`: unlinked transcript group ranges. Every group is linked or
  discarded by the initial algorithm.
- `match_scores`: diagnostics keyed by transcript range. `similarity` is the
  span matcher's normalized token-distance score (0–1). `quality_score` is the
  full-sentence RapidFuzz score (0–100) used for classification. Neither score
  is a probability. `partial_prefix` identifies an abandoned
  literal prefix used to hold the script cursor.
- `boundary_analysis` records the searched windows,
  selected regroupings, score gains, and retained-alternative margins. New take
  ranges have `boundary_proposal: true` in `match_scores`, with a recalculated
  `quality_score`; they do not inherit the old span's token-distance diagnostic.
  Top-level `algorithm.boundary_refinement` fingerprints this pass and records
  its settings. See the skill's boundary trial section for the scoring rules.

One ordinary link looks like:

```json
{"script": {"word_start": 256, "word_end": 280}, "transcript": {"word_start": 300, "word_end": 324}, "classification": "partial"}
```

There are no special merge or split link types. Source word times supply edit
times; no synchronization offsets are applied. Classification uses Line's shared
NFKC normalization, casefolding, punctuation removal, and whitespace collapsing.
Identical normalized wording is exact. Other readings use full-sentence
`rapidfuzz.fuzz.ratio`: scores ≥75 are partial and <75 are flub.
Digit forms expand to English with `num2words`; hyphens become spaces, so `31`
and `thirty-one`, or `father-in-law` and `father in law`, compare identically.
Ordinals use their ordinal reading. Decimals and explicit leading zeros keep
their individual digits, and symbols such as currency signs are preserved.
Empty or punctuation-only content scores zero.
Source text and indexes remain intact. This quality score is independent of the
span matcher's candidate score. Line recalculates labels when opening a review,
marking changed labels as unsaved updates. Benchmark comparisons read the saved
human reference labels directly and do not recalculate or rewrite them.

## Span matcher (unchanged by quality scoring)

Two cursors track the next transcript word and expected script grouping. For
each candidate script group within three groups behind or ahead, compute token
Levenshtein distances to possible transcript prefixes, up to 1.4 times the
script length plus four words. Normalize NFKC, case, and punctuation for scoring
without changing source text or indexes. Retain candidates with similarity at
least 0.72; subtract 0.012 per group of cursor distance when choosing a winner.

An exact prefix of at least three words, shorter than the script group and
ending on an original transcript grouping boundary, is a partial-take candidate.
Its score is 0.70 plus 0.20 times the fraction of the script read. A complete
match can still outscore and absorb that prefix; the first benchmark exposes
this behavior on a restart.

If the local window has no candidate, search the whole script and accept only
a complete candidate with similarity at least 0.90. If no candidate qualifies,
advance one transcript word. Otherwise emit the chosen span, advance the
transcript cursor to its end, and move the script cursor to the next script
group (or hold it on a partial prefix). Revisiting earlier groups captures
repeated readings; looking ahead allows omitted script passages.

Finally, put grouping boundaries at all take endpoints and retain the original
boundaries in unmatched passages. Remove original boundaries inside a take.
That yields the same result format for normal, split, and merged groupings.
Unmatched groups become discarded. The first baseline's opening chatter,
restart absorption, and final-word boundary drift remain visible for tuning.

The top-level `algorithm` object fingerprints the generator and shared Line
implementation and records every setting, including the Exact equality rule,
the Partial threshold, the RapidFuzz method, and installed RapidFuzz/num2words
versions. Source changes, inventory changes,
or algorithm changes invalidate reuse. Generation completion and matching
accuracy are separate: missing coverage and discarded passages need review.
