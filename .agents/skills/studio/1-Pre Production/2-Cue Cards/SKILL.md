---
name: studio-cue-cards
description: Run or resume Studio substage 1.2, turning approved Word scripts into editable Keynote cue cards.
---

# 1.2 — Cue Cards

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Use this step's project copy when running a project.

## Inputs and output

Input: every approved `.docx` from the project's OneDrive `1-Pre Production/1-Script Review/Approved/` folder. Create one editable Keynote `.key` presentation per approved script in the project's OneDrive `1-Pre Production/2-Cue Cards/` folder. Split the approved wording into readable speaking beats with large, high-contrast text suitable for a laptop display; preserve the approved script's wording. Keep the step's append-only `LOG.md` in the same folder.

## Completion

Success requires a verified deck for each approved script, readable slides, and complete script coverage. Record the approved documents and produced decks in the step's `DONE` manifest. If an approved script changes, validation invalidates 1.1, 1.2, and later steps.

## Rerun

Validate existing completion and compare the approved documents with the decks. Reuse valid decks. Review any human edits to an existing deck before changing it; do not discard them. Log the attempt, decisions, outputs, and any handoff or invalidation in `LOG.md`.
