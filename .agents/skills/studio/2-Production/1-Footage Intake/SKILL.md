---
name: studio-footage-intake
description: Run or resume Studio substage 2.1 by checking that every approved script has a non-empty camera/audio footage set.
---

# 2.1 — Footage Intake

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Run after substage 1.3 in workflow order.

## Purpose and inputs

This step checks the footage dropped directly into the project's `2-Production/1-Footage Intake/` folder. It does not inspect media contents or rename, move, hash, or delete files.

The approved-script set is the stems of the `.docx` files in `1-Pre Production/1-Script Review/Approved/`, including CTA pickups.

## Expected filenames

Each footage set uses one take number and these three filenames:

```text
<Script>__T<nn>__camA.<ext>
<Script>__T<nn>__camB.<ext>
<Script>__T<nn>__lav.<ext>
```

`<Script>` must exactly match an approved `.docx` stem. `camA` and `camB` must use `.mov` or `.mp4`; `lav` must use `.wav`. Extension matching is case-insensitive. Multiple takes are allowed.

## Validation

Running stage 2.1 means running [validate-footage.py](scripts/validate-footage.py) with the resolved OneDrive project folder:

```text
python "<workflow-root>/.agents/skills/studio/2-Production/1-Footage Intake/scripts/validate-footage.py" "<OneDrive project folder>"
```

For every approved script, the validator requires at least one take containing `camA`, `camB`, and `lav`, with all three files larger than zero bytes and using the allowed extension for their role. Files with unsupported extensions, such as Audacity `.aup3` project files, are ignored and do not count toward a complete set. Other files that do not match the expected filenames are also ignored, except that likely single-underscore separator mistakes are reported. The command exits nonzero and lists each problem when validation fails.

Treat the validator as the stage's source of truth:

1. Run it as written; do not replace its checks with ad hoc inspection.
2. Report `PASS` only when it exits successfully. Report `FAIL` when it exits nonzero, preserving its meaningful error messages and exact filenames in the response.
3. If the validator cannot run because Python or another execution prerequisite is unavailable, report that execution error as the stage result. Do not infer a pass or substitute a different runtime.
4. Do not edit, relax, reinterpret, or work around the validator during a stage run. Change it only when the user explicitly asks to change the validator.
5. Never rename or otherwise modify footage to make validation pass unless the user explicitly requests that separate action.

## Completion and reruns

Append the validator's pass/fail result and its messages to the step's `LOG.md`. After a passing validation, write `DONE` according to the Studio root skill, recording the approved scripts and the three files in at least one complete set per script. On failure, do not write `DONE`; report the listed corrections and stop. The validator itself does not write `DONE`.

On rerun, validate upstream markers first, rerun the validator, and compare the approved-script set and recorded files with `DONE`. Invalidate this and later completion markers when those inputs change. Preserve all footage files.
