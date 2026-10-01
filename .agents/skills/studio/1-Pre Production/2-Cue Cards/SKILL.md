---
name: studio-cue-cards
description: Run or resume Studio substage 1.2, turning approved Word scripts into editable PowerPoint cue cards.
---

# 1.2 — Cue Cards

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Use this shared step skill when running a project.

## Inputs and output

Input: every approved `.docx` filming script from the project's OneDrive `1-Pre Production/1-Script Review/Approved/` folder. Create one editable PowerPoint `.pptx` presentation per approved script in the project's OneDrive `1-Pre Production/2-Cue Cards/` folder. Use standard editable text boxes so the decks open across PowerPoint-compatible platforms. Split the approved wording into readable speaking beats with large, high-contrast text suitable for a laptop display; preserve the approved script's wording and line breaks. When successive script sentences appear on separate lines, keep them on separate lines if they share a slide. Keep the speaking text at one font size within each deck and center its text box vertically. If text overflows, move the excess to the next slide instead of reducing the font. Extracts are created in substage 1.3 and require no cue cards. Keep the step's append-only `LOG.md` in the same folder.

Approved `CTA Pickup -` documents are filming scripts: create a separate deck for each one, label its destination and attachment point, and preserve the approved CTA wording. Do not put an optional pickup on the flagship's core cue cards.

## Shared generators

Use [make-plans.py](scripts/make-plans.py) with the OneDrive project folder to prepare cue text, then [build-cue-cards.ps1](scripts/build-cue-cards.ps1) with `-ProjectPath` to create and render the editable decks on a Windows machine with PowerPoint. The scripts accept any Studio project with approved Word scripts. Generated plans and renders stay in the project's `2-Cue Cards/working/` folder. The builder refuses to replace an existing deck unless `-Overwrite` is supplied after reviewing it for human edits. Check and label CTA pickup decks according to their approved destination and attachment point.

## Completion

Success requires a verified deck for each approved script, readable slides, and complete script coverage. Record the full approved set and the produced decks in the step's `DONE` manifest. If an approved document changes, validation invalidates 1.1, 1.2, and later steps.

## Rerun

Validate existing completion and compare the approved documents with the decks. Reuse valid decks. Review any human edits to an existing deck before changing it; do not discard them. Log the attempt, decisions, outputs, and any handoff or invalidation in `LOG.md`.
