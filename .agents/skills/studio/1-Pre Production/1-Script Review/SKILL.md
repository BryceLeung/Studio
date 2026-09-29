---
name: studio-script-review
description: Run or resume Studio substage 1.1, planning script ideas interactively before writing Word candidates for human approval.
---

# 1.1 — Script Review

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Use this shared step skill when running a project.

## Inputs and work

Default intake for a new idea is an interactive discussion. Read the source material and any supplied script, then propose a small asset slate with the distinct angle, Reach/Trust/Traffic objective, and whether each piece needs filming or can be cut from footage. Discuss the slate with the user and refine it from their feedback. A proposed extract remains conditional until footage confirms that the exact wording and a usable opening and payoff exist. A project may be created during this discussion, before the slate is settled.

For a pickup child project, start from the specific parent asset that failed or needs revision. Discuss the smallest replacement slate, such as one new filmed take for an unusable extract. Record the parent project, source asset and version, failure reason, and replacement decision in the child's log. Keep the parent's approved set intact; the child has its own review and approval for any new filming script.

Do not draft full new scripts in chat, create or copy script files into the project, or advance to candidate review merely because an idea is promising or the user asked to create, run, or plan a project. Wait until the user explicitly asks to write scripts or names the scripts to draft; that request authorizes writing the selected scripts without another confirmation. When its OneDrive `1-Pre Production/1-Script Review/` folder is created, immediately create `Approved/` inside it, even if no candidate exists yet. After the writing request, use the agreed sources and decisions to write each filming candidate as a Word `.docx` directly in the step folder with a descriptive filename and no production-action prefix. Every `.docx` directly in the step folder is a script to be filmed. Record extract ideas in the discussion and `LOG.md`, but do not create extract documents during this step. Substage 1.3 creates extract documents from the approved scripts without separate human approval. Record the source, discussion decisions, filming candidates, and handoff in that folder's `LOG.md`.

## Endings and CTA pickups

Decide the ending for each proposed piece during script review. If a new spoken end-of-film CTA or alternate ending must be recorded, account for it as filmed material before the shoot. Include it in the main filming script when it is inseparable from that piece; use a separate `CTA Pickup - <destination>.docx` in the step folder when it is an optional or reusable ending. State which piece and destination it serves, the exact approved words, and where the pickup attaches. It receives human approval and a cue card like any other filming script. Treat an ending as an extract only when its exact words are already present in the flagship footage. A link, caption, end card, or other nonspoken CTA can be recorded as a packaging note in the relevant script and needs no filming pickup.

Choose CTAs by the piece's Reach, Trust, or Traffic objective. An emotional Reach ending may simply end on its payoff; do not add a traffic CTA by default. Keep alternate CTA pickups separate from the core ending so the edit can choose the right version for each destination.

## Approval and completion

`Approved/` is the human approval mechanism for filming scripts: Kristy or Bryce edits a root-level candidate in Word and moves it there to approve it; deleting a candidate rejects it. Pause for review while any candidate `.docx` remains directly in the step folder. If both the step folder and `Approved/` contain no `.docx`, remain incomplete and continue the interactive plan until the user asks for scripts. Extract ideas neither require approval nor keep this step open; an extract that fails in editing can be dropped.

Write `DONE` in the OneDrive step folder only when no candidate `.docx` remains directly in the step folder and at least one `.docx` exists in `Approved/`. The manifest must capture the approved set and contents and the empty-candidate condition so later edits, additions, moves, or deletions invalidate completion. Extract ideas in the log are outside the approval gate; substage 1.3 creates and checks extract documents from the approved scripts.

## Rerun

Validate the existing `DONE` as described in the root skill. Preserve approved documents and human edits. Resume from the current candidate and approval state; do not recreate candidates that remain valid or overwrite edited documents. Append each attempt, handoff, or invalidation to `LOG.md`.
