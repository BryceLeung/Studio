---
name: studio-final-cut
description: Run Studio substage 3.7 to flatten edited First Cut FCPXML projects and layer approved animations on the timeline.
---

# 3.7 Final Cut

Use the Studio root skill's project resolution and prerequisite walk. First Cut
must be current with one edited, project-only `.fcpxmld` bundle or standalone
`.fcpxml` export per expected stem
in `3-Post Production/6-First Cut/APPROVED/`. Animation production (2.3) must also
be current, including for its valid empty selection. Run with FFprobe available:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/7-Final Cut/scripts/final_cut.py" --project-root "<real-project-root>"
```

Each project uses the first (active) pick in each exported audition, or the
direct compound reference if the audition was already finalized. Studio unwraps
the selected single-multicam compounds into the primary storyline without
reapplying automated camera or take selection. Connected subtitle titles are
visible on the main timeline. Unsupported compound structures or compound-level
effects stop conversion rather than silently losing edits; apply those effects
in 3.7. Keep Studio take names/markers so animation anchors remain identifiable.

Approved MP4s are imported into the `Animations` event. Eligible animation
versions appear as disabled video-only cutaways below the subtitles, initially
timed from the first visible subtitle inside the selected sentence trim.
By default, extracts inherit animations whose script anchor starts in a sentence
used in the edited timeline. A nonempty registry Extracts list restricts placement
to those extract IDs without changing full-film placement. Unmatched or ambiguous
anchors are reported in `REVIEW.json` and the assets remain available in the
browser. Review timing and enable the desired versions in Final Cut Pro.

Outputs are separate same-stem FCPXML files in `3-Post Production/7-Final Cut/`.
Identical outputs are reused. Differing outputs stop without replacement; add
`--regenerate` only after deciding to archive the affected generated artifacts.
The helper does not change the edited First Cut exports or approved animation
media. Old animation-bearing First Cut drafts are not automatically migrated.

Re-import and check selected takes, dialogue, camera switches, subtitle sync and
animation visibility. Render each reviewed project to a nonempty MOV, MP4, M4V,
MKV, AVI, WEBM, or MXF with the same stem in `7-Final Cut/APPROVED/`. Then invoke
the bundle-aware completion helper from the workflow root; it calls the shared
recorder. Generation alone is not approval. Changed inputs or generated projects
can require fresh renders.

```text
python "<workflow-root>/studio/first_cut_exports.py" --project-root "<real-project-root>" --stage 3.7
```
