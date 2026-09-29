---
name: studio-script-review
description: Run or resume Studio substage 1.1, developing Word script candidates and tracking human approval.
---

# 1.1 — Script Review

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Use this step's project copy when running a project.

## Inputs and work

Intake is interactive in chat. A request such as "take our last blog article and turn it into a script" starts idea and script development before the project is created. Use the agreed source material and idea decisions to develop candidate scripts. When there are candidates worth reviewing, create the numbered project. When its OneDrive `1-Pre Production/1-Script Review/` folder is created, immediately create `Approved/` inside it, even if there are no candidates yet. Write each candidate as a Word `.docx` directly in the step folder. Record the source, decisions, candidates, and handoff in that folder's `LOG.md`.

## Approval and completion

`Approved/` is the human approval mechanism: Kristy or Bryce edits a candidate in Word and moves it there in Finder to approve it; deleting a candidate rejects it. Pause for review while any candidate `.docx` remains in the step root. If both the step root and `Approved/` contain no `.docx`, remain incomplete and wait for new candidates.

Write `DONE` in the OneDrive step folder only when no candidate `.docx` remains in the step root and at least one `.docx` exists in `Approved/`. The manifest must capture the approved set and contents and the empty-candidate condition so later edits, additions, moves, or deletions invalidate completion.

## Rerun

Validate the existing `DONE` as described in the root skill. Preserve approved documents and human edits. Resume from the current candidate and approval state; do not recreate candidates that remain valid or overwrite edited documents. Append each attempt, handoff, or invalidation to `LOG.md`.
