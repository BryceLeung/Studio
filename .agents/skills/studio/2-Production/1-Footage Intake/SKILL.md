---
name: studio-footage-intake
description: Run or resume Studio substage 2.1 by checking that every approved script has a non-empty camera/audio footage set.
---

# 2.1 — Footage Intake

After resolving the project and preparing the intake directory as described below, use the root skill's shared prerequisite check for normal execution. After the footage validator succeeds, invoke the shared completion recorder with `--stage 2.1`; it rechecks the complete footage groups and records the declared inventories. Do not write `DONE` by hand.

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Run after substage 1.3 in workflow order.

## Purpose and inputs

This step checks the footage dropped directly into the project's `2-Production/1-Footage Intake/` folder. It does not inspect media contents or rename, move, hash, or delete footage. Execution failures archive this step's and its dependents' completion markers using the shared graph helper; originals stay in place. The stage has no generator-owned outputs eligible for regeneration archiving.

The approved-script set is the stems of the `.docx` files in `1-Pre Production/1-Script Review/Approved/`, including CTA pickups.

## Directory preparation

On an explicit stage 2.1 run, resolve and verify the real OneDrive project first, then create `2-Production/1-Footage Intake/` and any missing parent directory within that project. Preserve existing directories and their contents; if a required path is a file or cannot be created, report the error and stop. This idempotent preparation happens before prerequisite validation so even a rejected preflight can be recorded through the shared append-only `LOG.md` helper in the intake folder.

Do not create placeholder media, an `Approved/` folder, future stage folders, or a `DONE` marker. Do not create or repair upstream folders. Directory preparation is not stage completion and does not waive prerequisites: if preflight fails, log and report the blocker without running footage validation or claiming footage is missing. Read-only status and audit commands must not create directories.

## Expected filenames

Each footage set uses one clip number (`C01`, `C02`, and so on) shared by these three source clips:

```text
<Script>__C<nn>__camA.<ext>
<Script>__C<nn>__camB.<ext>
<Script>__C<nn>__lav.<ext>
```

`<Script>` must exactly match an approved `.docx` stem. `camA` and `camB` must use `.mov` or `.mp4`; `lav` must use `.wav`. Extension matching is case-insensitive. Multiple clip sets are allowed. A take is an individual performance of a script passage within the synchronized source clips; `C<nn>` identifies the source clip set, not that performance.

## Validation

Running stage 2.1 means running [validate-footage.py](scripts/validate-footage.py) with the resolved OneDrive project folder:

```text
python "<workflow-root>/.agents/skills/studio/2-Production/1-Footage Intake/scripts/validate-footage.py" "<OneDrive project folder>"
```

For every approved script, the validator requires at least one `C<nn>` clip set containing `camA`, `camB`, and `lav`, with all three files larger than zero bytes and using the allowed extension for their role. Files with unsupported extensions, such as Audacity `.aup3` project files, are ignored and do not count toward a complete set. Other files that do not match the expected filenames are also ignored, except that likely single-underscore separator mistakes are reported. The command exits nonzero and lists each problem when validation fails.

Treat the validator as the stage's source of truth:

1. Run it as written; do not replace its checks with ad hoc inspection.
2. Report `PASS` only when it exits successfully. Report `FAIL` when it exits nonzero, preserving its meaningful error messages and exact filenames in the response.
3. If the validator cannot run because Python or another execution prerequisite is unavailable, report that execution error as the stage result. Do not infer a pass or substitute a different runtime.
4. Do not edit, relax, reinterpret, or work around the validator during a stage run. Change it only when the user explicitly asks to change the validator.
5. Never rename or otherwise modify footage to make validation pass unless the user explicitly requests that separate action.

When footage validation reports a missing or incomplete set, tell the user the resolved intake folder and the concrete filenames expected for each affected approved-script stem, not just the generic template or `MISSING` label. If no recognized set exists for a script, show this first-set example with its exact stem:

```text
<exact approved-script stem>__C01__camA.mov (or .mp4)
<exact approved-script stem>__C01__camB.mov (or .mp4)
<exact approved-script stem>__C01__lav.wav
```

Replace the placeholder with each affected script's actual stem. If recognized partial sets exist, list their actual `C<nn>` numbers and which roles are missing or empty, with the corresponding expected filenames; do not require users to replace a valid clip number with `C01`. Explain that all three non-empty files must share the exact script stem and clip number, with double underscores around the clip number and the allowed role-specific extensions. Keep the validator's errors visible, including suggested separator corrections. Unsupported or otherwise unrecognized filenames do not satisfy the requirement; do not silently rename them or count them as valid footage. If the approved-script set cannot be read, report that blocker and the generic naming convention without inventing script names.

## Completion and reruns

Completion requires a successful validator run: every approved script has a non-empty `C<nn>` camA/camB/lav set, and no file has a reported validation problem. Append the validator's pass/fail result and its messages to the step's `LOG.md`. After passing validation, use the shared recorder to fingerprint the approved scripts, stage implementation, and all recognized intake media, not just one representative set per script. On failure, do not record completion; report the listed corrections and stop. The footage validator itself does not write `DONE`.

On rerun, validate upstream markers first, rerun the validator, and compare the approved-script set, naming convention, stage-specific skill and validator revisions, and recorded files with `DONE`. Invalidate this and later completion markers when those dependencies change or validation fails. Preserve all footage files.
