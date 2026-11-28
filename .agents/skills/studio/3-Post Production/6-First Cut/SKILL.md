---
name: studio-first-cut
description: Run or resume Studio substage 3.6, creating Final Cut Pro projects for approved full scripts or extracts with multicam take compounds and sentence auditions.
---

# 3.6 — First Cut

Normal generation uses the root skill's shared prerequisite walk. After editing
and exporting the expected projects into `APPROVED/`, run this completion helper
from the workflow root using the shared interpreter:

```text
python "<workflow-root>/studio/first_cut_exports.py" --project-root "<real-project-root>" --stage 3.6
```

It gathers bundle-aware evidence and calls the shared recorder, which validates
the draft and edited project inventories and current fingerprints before writing `DONE`.
Generation alone never records completion. Do not hand-author the marker.

Read the Studio root skill for project resolution and logging rules. Resolve the
real OneDrive project root and pass each script's highest numbered stage 3.4 `__Scores.v<N>.json`
file explicitly to the Python script. Validate stage 3.5 Face Tracking completion
first; landscape exports do not consume its face paths. Use the dashboard Python environment and
an available FFprobe executable:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/6-First Cut/scripts/first_cut.py" --project-root "<real-project-root>" --scores "3-Post Production/4-Take Selection/<Script>__Scores.v<N>.json" [...other scripts' latest score files...] --padding-ms 50
```

On an extract-only rerun, skip the full-script command when the required full-script drafts already exist. Leave those drafts and `REVIEW.json` in place. If preflight finds no generation work and only human review or completion remains, do not invalidate merely to rerun a generator.

Approved extract projects:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/6-First Cut/scripts/extracts.py" --project-root "<real-project-root>" --shortlist "1-Pre Production/3-Extracts/APPROVED/Short-Form Excerpt Shortlist.md" --scores "3-Post Production/4-Take Selection/<Script>__Scores.v<N>.json" [...other scripts' latest score files...] --generate --padding-ms 50
```

Omit `--generate` for read-only format and sentence/take matching. Add `--omit-discarded`
only with explicit authorization to omit included script-side discards; report
unresolved matches instead of guessing. The user authorized omitting sentence
109 from extract 6 in project `01-Love Compounds Too` on 2026-10-13.

Each approved extract declares `- **Formats:** Portrait`, `Landscape`, or
`Portrait and Landscape` (also `Both`). The reader accepts equivalent ratios
and existing `Versions` fields, plus explicit numbered ranges such as
“Extracts 1–4 retain their vertical presentation.” Missing or conflicting
requirements stop generation rather than defaulting to a format. Do not use a
batch-wide format flag. Generation writes only the missing required variants,
preserves existing drafts and approvals, and extends each affected format's
`Extract Mappings.json` without replacing its existing per-cut entries.

Portrait generation uses the fingerprinted stage 3.5 index and camera paths
and writes 1080x1920 projects under `3-Post Production/6-First Cut/Extracts/Portrait/`.
Both camera angles are reframed inside the multicam, preserving take auditions,
camera switching, and the original source dimensions, media, audio and sync.

Portrait framing fills the canvas with the full camera height using explicit
Fit plus a relative transform enlargement (316.0493827% for 16:9 to 9:16).
This combination was verified in Final Cut; implicit Fill and native-pixel
sizing with None did not work in the imported multicam structure. Camera
asset-clips explicitly use the portrait format; original asset formats remain
unchanged.

The standard portrait workflow uses locked crops. The user approved this
behavior in Final Cut on 2026-10-18. The generator explicitly selects locked
motion and records `portrait.motion: "locked"` in its generation report; normal
run/resume commands must not opt into tracked animation.
Each extract locks one editable crop per camera and recording for its entire
timeline. Stage 3.5 face detections choose a robust median horizontal center;
they do not animate the crop. Use active takes as the framing reference, with
all takes as fallback for a recording present only in alternatives. Deduplicate
overlapping source samples and exclude unused source time. Short trims without
a detector sample use the preceding source sample, or the first leading one.
Prefer tracked detections to held boxes, then use the frame center if none are
available. No per-word or per-sentence resets, pan keyframes, vertical tracking,
or zoom animation are emitted. Different cameras and recordings retain their
own positions. Camera-angle cut decisions are unchanged. Stage 3.5 outputs
remain unchanged.

The crop is clamped at source edges. Source-path held/centered samples and
coverage holds remain listed in `portrait.warnings` for review; they describe
the evidence used for framing, not camera animation. Takes with sampled face
boxes outside the locked crop get `face_outside_locked_crop` warnings for
manual review. Unusually large subject movement may require a deliberate
reframe. The legacy tracked helper is available only through the Python
`motion="tracked"` argument; it emits time/value-only position keyframes
because FCP rejected explicit interpolation attributes on that parameter.
Sparse/malformed paths, missing camera sources, stale media, rotated footage,
non-square pixels, or dimension mismatches still stop generation.
Portrait draft titles use smaller type, narrower margins, and a lower-frame
position independently of camera motion.

Review portrait framing, subject movement, subtitle placement, both
camera angles, and alternate takes in Final Cut before accepting these edits.
Automated XML checks do not establish visual framing or title-rendering quality.

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
If frame rounding collapses a word's highlight interval, share the preceding
word's interval, or the following word's interval when there is no predecessor.
Both words are highlighted together and kept in the same displayed phrase,
even across an original phrase boundary. A word with no usable neighbour gets
one frame, shifted earlier if needed to remain within its take. Source
transcript timestamps and text remain unchanged.
Import into Final Cut to
check title rendering, frame timing, legibility, and audition switching.

First Cut is the presenter edit only and does not depend on animation production.
Animation intake and placement run in [3.7 Final Cut](../7-Final%20Cut/SKILL.md).

For an isolated preview before 3.5 has completed, use
`scripts/preview_subtitles.py` with `--project-root`, `--shortlist`, all
`--scores` files, `--extract 1`, and a new `--output` path in the project's
existing `3-Post Production/0-Tmp/` folder. This writes one new XML and logs
the trial in 0-Tmp, without replacing First Cut exports or satisfying 3.6.

Edit the generated projects in Final Cut Pro. Finalize the take choices, trims,
camera switches, and dialogue edit. You can leave an audition open with the
desired take active; Final Cut Pro exports that pick first. Export each edited
project as a single-project `.fcpxmld` bundle or standalone `.fcpxml` into
`3-Post Production/6-First Cut/APPROVED/` with the same filename stem as the
generated project. Keep one export per stem. Studio reads and fingerprints
`Info.fcpxml` directly inside each bundle. Keep the generated drafts in their
generation folders. No movie render is required here. The edited export is the
input to 3.7.

Record current input fingerprints (including the generated project inventories)
and the complete edited-export inventory in `DONE`. Source media, sync checks,
and previews use the shared seconds-based identity; saved legacy
`mtime_ns` values remain readable after integer division by 1,000,000,000.
`REVIEW.json` and `Extract Mappings.json` are generation provenance, not approval
outputs. Studio fingerprints existing extract mapping reports as completion inputs.
The generators still do not write `DONE` themselves. Validation is
read-only and never moves movies or XML files on the user's behalf.

Default extract reruns preserve existing drafts and flag changed or unverified
generation evidence for inspection. Full-script reruns refuse differing files.
After a deliberate regeneration decision, add `--regenerate` to full-script or
extract generation: it prepares replacement XML/reports first, archives only the
selected old generated files into the pending `STALE/N/` slot, and publishes the
replacements. Approved edited XML exports are protected and remain in place; they are
never moved by XML generation. New/changed generation and execution failures use
the common graph invalidator. Reusing identical XML/reports does not invalidate
an otherwise current completion marker. Mapping-only and subtitle previews
remain outside canonical invalidation.

Stage 3.6 keeps one `DONE`. Completion checks all required variants against the
approved shortlist, plus the full scripts. A required draft cannot be dropped
by deleting it. A matching approved export can carry forward when its draft,
applicable generation inputs, and extract selection are unchanged. New drafts
need human-approved exports. A changed cut needs its own renewed review and
export. If legacy evidence cannot establish an unchanged cut, report that cut
specifically, without replacing it or treating the whole approval set as stale.
Legacy approved movies are left in place but are not the 3.7 handoff.

Tests:

```text
python -m unittest discover -s ".agents/skills/studio/3-Post Production/6-First Cut/scripts" -p "test_*.py" -v
```
