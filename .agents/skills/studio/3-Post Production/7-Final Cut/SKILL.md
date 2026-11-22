---
name: studio-final-cut
description: Run Studio substage 3.7 to finalize take auditions, lift subtitles, and layer approved animations on the timeline.
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
direct compound reference if the audition was already finalized. Studio retains
the selected compounds in the primary storyline with their editable multicam
clips inside. It lifts the subtitle titles onto the project timeline, clipping
them to the approved trims without reselecting takes or cameras. Unsupported
compound structures and retimes that prevent safe subtitle lifting stop
conversion. Keep Studio take names/markers so animation anchors remain identifiable.

For a presenter-only review of one approved export before the full-stage gates
are met, add `--preview "<approved-export-stem>"`. This writes a separate FCPXML
in `3-Post Production/0-Tmp/` without replacing differing previews or changing
the canonical Final Cut, approvals, or completion status. It omits animations.

Approved MP4s are imported into the `Animations` event. Eligible animation
versions appear as disabled video-only cutaways below the subtitles, initially
timed from the first visible subtitle inside the selected sentence trim. Each
cutaway keeps its full usable duration at the project frame rate, even across
later cuts. A cutaway that runs past the last presenter clip can extend the
project duration without moving or trimming the presenter clips.
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
