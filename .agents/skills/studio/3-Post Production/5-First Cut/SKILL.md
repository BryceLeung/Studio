---
name: studio-first-cut
description: Run or resume Studio substage 3.5, creating one Final Cut Pro FCPXML project per approved script with multicam take compounds and sentence auditions.
---

# 3.5 — First Cut

Read the Studio root skill for project resolution and logging rules. Resolve the
real OneDrive project root and pass each script's stage 3.4 `__Scores.v1.json`
file explicitly to the Python script. Use the dashboard Python environment and
an available FFprobe executable:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/5-First Cut/scripts/first_cut.py" --project-root "<real-project-root>" --scores "3-Post Production/4-Take Selection/<Script>__Scores.v1.json" [...other scripts' score files...] --padding-ms 50
```

The script owns prerequisite validation, input discovery from score/source
references, timing, camera selection, XML construction, logging, and verification.
Inputs are 1.1, 1.3, 2.1, 3.1, 3.3, and 3.4; word timings come from the source
snapshots in the mappings. Output is `3-Post Production/5-First Cut/<Script>.FCPXML`
and `REVIEW.json` recording input/output fingerprints and selected take ranges.
Extracts are retained as an input but no extract projects are created in v1.

The agreed behavior is one multicam per recording, one independent compound per
take, one audition per retained script group, and highest-scoring choice active.
Start each project on camA, flip cameras only at nonadjacent selected takes, and
retain lav audio. Consecutive non-flub readings share a frame-aligned midpoint
boundary. Other edges use configurable 50 ms padding rounded outward to frames
and constrained to shared camera/audio coverage. Never change reviewed mappings.
Alternative choices inherit their sentence's initial camera; manual audition
switches in Final Cut do not automatically recalculate cameras or neighboring trims.

FCPXML 1.10 uses the same hierarchy as the user's 1.14 temporary examples, with
portable relative media URLs pointing to stage 2.1. Missing timestamps, incomplete
coverage, or out-of-range speech fail explicitly. Matching outputs are reused;
differing existing outputs stop for inspection, preserving edits.

After generation, ask the user to import both files and review synchronization,
audition switching, camera switching, and continuous boundaries in Final Cut.
`DONE` remains absent until that import/playback review succeeds. This is the
initial success condition for an unfamiliar interchange workflow, pending the
user's review; XML generation alone must not be reported as stage completion.

Tests:

```text
python -m unittest discover -s ".agents/skills/studio/3-Post Production/5-First Cut/scripts" -p "test_first_cut.py" -v
```
