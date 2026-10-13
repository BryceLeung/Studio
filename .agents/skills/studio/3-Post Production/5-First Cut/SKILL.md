---
name: studio-first-cut
description: Run or resume Studio substage 3.5, creating Final Cut Pro projects for approved full scripts or extracts with multicam take compounds and sentence auditions.
---

# 3.5 — First Cut

Read the Studio root skill for project resolution and logging rules. Resolve the
real OneDrive project root and pass each script's stage 3.4 `__Scores.v1.json`
file explicitly to the Python script. Use the dashboard Python environment and
an available FFprobe executable:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/5-First Cut/scripts/first_cut.py" --project-root "<real-project-root>" --scores "3-Post Production/4-Take Selection/<Script>__Scores.v1.json" [...other scripts' score files...] --padding-ms 50
```

Approved extract projects:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/5-First Cut/scripts/extracts.py" --project-root "<real-project-root>" --shortlist "1-Pre Production/3-Extracts/Short-Form Excerpt Shortlist.md" --scores "3-Post Production/4-Take Selection/<Script>__Scores.v1.json" [...other scripts' score files...] --generate --padding-ms 50
```

Omit `--generate` for read-only sentence/take matching. Add `--omit-discarded`
only with explicit authorization to omit included script-side discards; report
unresolved matches instead of guessing. The user authorized omitting sentence
109 from extract 6 in project `01-Love Compounds Too` on 2026-10-13.

The Python scripts own input validation, timing, camera logic, XML construction,
logging, and output verification. Full-script files and `REVIEW.json` are written
under `3-Post Production/5-First Cut/`; extract FCPXML projects and
`Extract Mappings.json` go in its `Extracts/` subdirectory. Differing existing
outputs stop for inspection; preserve human edits. Neither operation changes
the approved scripts, shortlist, source mappings, or scores.

Deliver generated files for Final Cut import/playback review; do not claim stage
completion or write `DONE` before that review succeeds.

Tests:

```text
python -m unittest discover -s ".agents/skills/studio/3-Post Production/5-First Cut/scripts" -p "test_*.py" -v
```
