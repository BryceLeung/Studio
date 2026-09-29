---
name: studio-script-review
description: Run or resume Studio substage 1.1, planning script ideas interactively before writing Word candidates for human approval.
---

# 1.1 — Script Review

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Use this shared step skill when running a project.

## Inputs and work

Default intake for a new idea is an interactive discussion. Read the source material and any supplied script, then propose a small asset slate with the distinct angle, Reach/Trust/Traffic objective, and likely `FILM-` or `EXTRACT-` action for each piece. Discuss the slate with the user and refine it from their feedback. A proposed `EXTRACT-` remains conditional until footage confirms that the exact wording and a usable opening and payoff exist. A project may be created during this discussion, before the slate is settled.

For a pickup child project, start from the specific parent asset that failed or needs revision. Discuss the smallest replacement slate, such as one new `FILM-` take for an unusable `EXTRACT-`. Record the parent project, source asset and version, failure reason, and replacement decision in the child's log. Keep the parent's approved set intact; the child has its own review and approval.

Do not draft full new scripts in chat, create or copy script files into the project, or advance to candidate review merely because an idea is promising or the user asked to create, run, or plan a project. Wait until the user explicitly asks to write scripts or names the scripts to draft; that request authorizes writing the selected scripts without another confirmation. When its OneDrive `1-Pre Production/1-Script Review/` folder is created, immediately create `Approved/` inside it, even if no candidate exists yet. After the writing request, use the agreed sources and decisions to write each candidate as a Word `.docx` directly in the step folder. Prefix each filename with `FILM-` for a script requiring a new take or `EXTRACT-` for a verbatim selection to cut from flagship footage. State the same production action near the top of the document. Record the source, discussion decisions, candidates, and handoff in that folder's `LOG.md`.

## Endings and CTA pickups

Decide the ending for each proposed piece during script review. If a new spoken end-of-film CTA or alternate ending must be recorded, account for it as `FILM-` material before the shoot. Include it in the main `FILM-` script when it is inseparable from that piece; use a separate `FILM-CTA Pickup - <destination>.docx` when it is an optional or reusable ending. State which piece and destination it serves, the exact approved words, and where the pickup attaches. It receives human approval and a cue card like any other `FILM-` script. Mark an ending `EXTRACT-` only when its exact words are already present in the flagship footage. A link, caption, end card, or other nonspoken CTA can be recorded as a packaging note in the relevant script and needs no filming pickup.

Choose CTAs by the piece's Reach, Trust, or Traffic objective. An emotional Reach ending may simply end on its payoff; do not add a traffic CTA by default. Keep alternate CTA pickups separate from the core ending so the edit can choose the right version for each destination.

## Approval and completion

`Approved/` is the human approval mechanism: Kristy or Bryce edits a candidate in Word and moves it there in Finder to approve it; deleting a candidate rejects it. Preserve the `FILM-` or `EXTRACT-` prefix when moving it. Pause for review while any candidate `.docx` remains in the step root. If both the step root and `Approved/` contain no `.docx`, remain incomplete and continue the interactive plan until the user asks for scripts.

Write `DONE` in the OneDrive step folder only when no candidate `.docx` remains in the step root and at least one `.docx` exists in `Approved/`. The manifest must capture the approved set and contents and the empty-candidate condition so later edits, additions, moves, or deletions invalidate completion.

## Rerun

Validate the existing `DONE` as described in the root skill. Preserve approved documents and human edits. Resume from the current candidate and approval state; do not recreate candidates that remain valid or overwrite edited documents. Append each attempt, handoff, or invalidation to `LOG.md`.
