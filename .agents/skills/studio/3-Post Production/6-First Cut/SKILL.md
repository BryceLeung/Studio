---
name: studio-first-cut
description: Run or resume Studio substage 3.6, creating Final Cut Pro projects for approved full scripts or extracts with multicam take compounds and sentence auditions.
---

# 3.6 — First Cut

Normal generation uses the root skill's shared prerequisite walk. After editing,
rendering, and moving all expected movies into `APPROVED/`, invoke the shared
completion recorder with `--stage 3.6`. It validates project/movie inventories
and current input fingerprints before writing `DONE`; generation alone never
records completion. Do not hand-author the marker.

Read the Studio root skill for project resolution and logging rules. Resolve the
real OneDrive project root and pass each script's stage 3.4 `__Scores.v1.json`
file explicitly to the Python script. Validate stage 3.5 Face Tracking completion
first; current landscape XML does not yet consume its face paths. Use the dashboard Python environment and
an available FFprobe executable:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/6-First Cut/scripts/first_cut.py" --project-root "<real-project-root>" --scores "3-Post Production/4-Take Selection/<Script>__Scores.v1.json" [...other scripts' score files...] --padding-ms 50
```

Approved extract projects:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/6-First Cut/scripts/extracts.py" --project-root "<real-project-root>" --shortlist "1-Pre Production/3-Extracts/APPROVED/Short-Form Excerpt Shortlist.md" --scores "3-Post Production/4-Take Selection/<Script>__Scores.v1.json" [...other scripts' score files...] --generate --padding-ms 50
```

Omit `--generate` for read-only sentence/take matching. Add `--omit-discarded`
only with explicit authorization to omit included script-side discards; report
unresolved matches instead of guessing. The user authorized omitting sentence
109 from extract 6 in project `01-Love Compounds Too` on 2026-10-13.

The Python scripts own input validation, timing, camera logic, XML construction,
logging, and output verification. Full-script files and `REVIEW.json` are written
under `3-Post Production/6-First Cut/`; extract FCPXML projects and
`Extract Mappings.json` go in its `Extracts/` subdirectory. Differing existing
outputs stop for inspection; preserve human edits. Neither operation changes
the approved scripts, shortlist, source mappings, or scores.

The generator includes draft, editable static Basic Titles inside every take
compound, using its mapped transcript word times, heavy Arial Black lettering,
and the mockup's warm-yellow active-word highlight. Long phrases use a bounded
paragraph layout and explicit word-boundary line breaks, with oversized single
words scaled down. These titles are not the final editorial subtitle review.
Import into Final Cut to
check title rendering, frame timing, legibility, and audition switching.

For an isolated preview before 3.5 has completed, use
`scripts/preview_subtitles.py` with `--project-root`, `--shortlist`, all
`--scores` files, `--extract 1`, and a new `--output` path in the project's
existing `3-Post Production/0-Tmp/` folder. This writes one new XML and logs
the trial in 0-Tmp, without replacing First Cut exports or satisfying 3.6.

Deliver the generated projects for editing and rendering. Human approval is
accomplished by moving new, non-empty rendered movie files into
`3-Post Production/6-First Cut/APPROVED/`. Keep each project's filename stem:
`A.FCPXML` is satisfied by `APPROVED/A.mov`, `APPROVED/A.mp4`, or another supported
movie extension. Match stems and extensions case-insensitively. Supported movie
extensions are MOV, MP4, M4V, MKV, AVI, WEBM, and MXF. Require a render for every
full-script score input and every generated full-script or extract FCPXML project.
Leave the XML files in their generation folders; XML in `APPROVED/` does not
satisfy completion. Moving a rendered movie into `APPROVED/` is the user's
assertion that it has been edited and rendered; validation does not compare
file creation times or attempt to judge the edit.

Record current input fingerprints (including the generated project inventories)
and the complete approved movie inventory in `DONE`. Movies use the shared
media fingerprint (size and integer modification seconds). `REVIEW.json` and
`Extract Mappings.json` remain generation provenance, not required approval
outputs. The generators still do not write `DONE` themselves. Validation is
read-only and never moves movies or XML files on the user's behalf.

Default reruns reuse identical generated files and refuse differing files.
After a deliberate regeneration decision, add `--regenerate` to full-script or
extract generation: it prepares replacement XML/reports first, archives only the
selected old generated files into the pending `STALE/N/` slot, and publishes the
replacements. Approved edited movies are protected and remain in place; they are
never moved by XML generation. New/changed generation and execution failures use
the common graph invalidator. Reusing identical XML/reports does not invalidate
an otherwise current completion marker. Mapping-only and subtitle previews
remain outside canonical invalidation.

Preserved movies are not automatically renewed approval after generation inputs
change. The validator checks the previous active or archived render completion;
previously approved projects need a fresh movie identity when the recorded input
set or fingerprints change. Replacing the movie with a newly edited/rendered
export satisfies that gate. Pure workflow-code changes with identical generation
inputs do not by themselves require a different movie. No file creation-time
comparison or edit-quality judgment is performed.

Tests:

```text
python -m unittest discover -s ".agents/skills/studio/3-Post Production/6-First Cut/scripts" -p "test_*.py" -v
```
