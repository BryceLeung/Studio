---
name: studio-extracts
description: Run or resume Studio substage 1.3, creating optional verbatim video extract plans from approved filming scripts after Script Review.
---

# 1.3 — Extracts

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project resolution, logs, completion manifests, and invalidation. Run after substages 1.1 and 1.2 in workflow order. This step needs the approved Word scripts from `1-Pre Production/1-Script Review/Approved/`; it does not use unapproved drafts.

## Work and output

Review the extract ideas recorded during Script Review and the approved scripts. Create one Word `.docx` per viable, distinct cut directly in the project's OneDrive `1-Pre Production/3-Extracts/` folder. Use descriptive filenames without `FILM-` or `EXTRACT-` prefixes. Do not create another nested `Extracts/` folder. A proposed extract is an edit plan, not a new filming script, and needs no move to `Approved/` or separate human approval.

Each document must identify its approved source script and version, its Reach/Trust/Traffic aim, and an approximate duration. Give the exact spoken selections in playback order, marking every omission or jump cut; identify the intended opening, payoff, and any nonspoken end card or caption. Use only words present in the approved source. Do not silently add connective narration, a spoken CTA, or an alternate ending that would require filming. If an idea cannot form a coherent cut from approved wording, record that finding in `LOG.md` instead of fabricating lines.

No footage is required at this stage. Mark every extract as conditional: a later video editing step must match the selections to the recorded take and verify usable sound, image, opening, transitions, and payoff. It may revise or drop a cut that does not work in the footage. Do not claim timestamps or footage feasibility before seeing the recording.

Keep an append-only `LOG.md` in the step folder. Record the approved sources, selected and rejected extract ideas, output documents, and the next editing check. If there are no viable extract ideas, record the no-extract decision there.

## Completion and rerun

Success requires every selected extract document to be checked against its approved source for verbatim wording and clear cut boundaries. A documented no-extract result also satisfies the step. Write `DONE` only after this check. Its manifest must fingerprint the approved source scripts, this step skill, and every extract document; for a no-extract result, record the empty-output condition. Do not make footage verification or human extract approval a 1.3 completion gate.

On rerun, validate `DONE` and upstream markers in workflow order. If an approved source changes, invalidate 1.3 and recheck only extracts that depend on that source. Preserve existing documents for inspection, account for human edits, and do not overwrite valid or edited work blindly. Append the attempt and any invalidation to `LOG.md`.
