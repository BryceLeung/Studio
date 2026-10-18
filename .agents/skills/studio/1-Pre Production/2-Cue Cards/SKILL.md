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

With Python 3.10 or later, install [the Python dependencies](scripts/requirements.txt) in the active environment. Run [make-plans.py](scripts/make-plans.py) with the OneDrive project folder to prepare cue text, then [build-cue-cards.py](scripts/build-cue-cards.py) with the same project folder to create editable decks. Both scripts run on Windows, macOS, and Linux without PowerPoint. Generated plans stay in the project's `2-Cue Cards/working/` folder. The builder refuses to replace an existing deck unless `--overwrite` is supplied after reviewing it for human edits. Visual inspection is optional, not a completion gate. The Python builder does not render slides. Check and label CTA pickup decks according to their approved destination and attachment point.

## Completion

Normal execution uses the root skill's shared prerequisite check. After producing the complete canonical deck set, invoke its shared completion recorder with `--stage 1.2`; do not write `DONE` by hand or record a preview output directory as canonical completion.

Success requires the same-stem `.pptx` file to exist for each approved script; no human approval or slide-rendering check is required. Record fingerprints of the full approved input set and the produced decks in the step's `DONE` manifest. The shared validator and this step's `scripts/validation.py` check the file inventory and recorded fingerprints without opening PowerPoint. If an approved document changes, its saved input fingerprint no longer validates.

## Rerun

Validate existing completion and compare the approved documents with the decks. Reuse valid decks. Review any human edits to an existing deck before changing it; do not discard them. Log the attempt, decisions, outputs, and any handoff or invalidation in `LOG.md`.

Unchanged plans and decks are reused. Changed existing plans require `make-plans.py --overwrite`, and deck replacement requires `build-cue-cards.py --overwrite`. Canonical replacement uses the common regeneration helper to archive only selected `working/*.json` and deck files into the pending `STALE/N/` slot before publishing replacements; deck rendering is staged first. Redirected preview output does not invalidate or archive canonical work. Execution failures archive affected completion markers through the common graph helper, without moving work files merely because the run failed.
