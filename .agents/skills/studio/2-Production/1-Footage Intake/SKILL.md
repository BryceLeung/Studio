---
name: studio-footage-intake
description: Run or resume Studio substage 2.1, validating and manifesting filmed camera and lavalier files dropped into the Production footage folder.
---

# 2.1 — Footage Intake

Read the Studio root skill at `.agents/skills/studio/SKILL.md` (relative to the repository root) for project resolution, logging, completion manifests, and invalidation. Run after substage 1.3 in workflow order.

## Purpose and inputs

This is the production drop folder. Each approved script is filmed with two cameras and one lavalier microphone, so every take yields exactly three files: two video angles and one audio track. Multiple takes of the same script are expected. After a shoot, the files are dropped directly into the project's OneDrive `2-Production/1-Footage Intake/` folder. This step verifies that the footage is complete and correctly named and records what was received. It does not edit, transcode, move, rename, or delete media.

Input: the approved `.docx` filming scripts in `1-Pre Production/1-Script Review/Approved/`, including each `CTA Pickup - <destination>.docx`. The `<Script>` tokens found in the footage must correspond to that approved set. Extracts shortlisted in substage 1.3 are cut from this footage and are never filmed as separate takes.

## Naming convention

Every media file placed directly in the step folder must be named:

`<Script>__T<nn>__<role>.<ext>`

A violation is fixed by renaming the file to the required form and rerunning this step; the convention itself is fixed and is not adjusted per shoot.

| Field | Values | Notes |
| --- | --- | --- |
| `<Script>` | The exact stem (filename without extension) of an approved `.docx` | Character-for-character, including spaces and capitalization, such as `Love Compounds Too`, `What Is Freedom For`, or `CTA Pickup - YouTube`. Must not contain two consecutive underscores. |
| `__` | A literal double underscore | Separates the fields. Script names already contain single spaces and hyphens, so `__` stays unambiguous. |
| `T<nn>` | `T01` through `T99` | Zero-padded two-digit take number. Takes begin at `T01`. |
| `<role>` | `camA`, `camB`, or `lav` | Lowercase. `camA` and `camB` are the two camera angles; `lav` is the single microphone track. Which physical angle is A and which is B is the project's choice and may stay undecided; the labels only need to be used consistently within a project. Record the assignment in the log once it is settled. |
| `<ext>` | `camA`/`camB`: `mp4` or `mov`; `lav`: `wav`, `mp3`, or `m4a` | Lowercase, and must match the role. |

Shared pattern: `^(?<script>.+?)__T(?<take>\d{2})__(?<role>camA|camB|lav)\.(?<ext>mp4|mov|wav|mp3|m4a)$`

Examples:

```text
Love Compounds Too__T01__camA.mp4
Love Compounds Too__T01__camB.mp4
Love Compounds Too__T01__lav.wav
Love Compounds Too__T02__camA.mp4
What Is Freedom For__T01__camA.mov
CTA Pickup - YouTube__T01__lav.wav
```

Enforced rules:

1. **Take completeness.** Every take present in the folder must contain exactly one `camA`, one `camB`, and one `lav`. A take with any role missing or duplicated is incomplete.
2. **Take numbering.** Takes begin at `T01` and are zero-padded. No two files may share the same take and role. Gaps are permitted only when the log explains the abandoned take.
3. **Approved-script match.** The `<Script>` token must equal an approved script name. Footage for a script with no approved `.docx` is a violation.
4. **Canonical forms.** Role tokens and extensions must be lowercase, and each extension must be valid for its role. Files are matched case-insensitively for detection but must be stored in the canonical form.
5. **Roles.** Only `camA`, `camB`, and `lav` are accepted. Any other role token is a naming violation, not a new role.

## Work

1. Read `Approved/` and build the approved script set from the `.docx` filenames.
2. Enumerate the files directly in the step folder, ignoring `LOG.md`, `DONE`, and any `working/` directory.
3. Match each media file against the shared pattern and the enforced rules. Collect every violation with the offending filename and the reason.
4. Group the valid files by script and take, and check each take's three roles.
5. Confirm that every approved script has at least one complete take. Record any script without footage, and its reason, for the log.
6. Fingerprint each media file. Use the byte size and full SHA-256 by default. For a file large enough that a full hash is impractical, record the byte size, the UTC modification time, and a sampled SHA-256 taken over the first and last 8 MiB, and state the method in the manifest.
7. Append the attempt, the counts, the violations, and the fingerprint method to `LOG.md`.

Do not rename, move, or delete any media file. When a violation is found, report the exact filename and the required name, let the human fix it, and rerun.

## Shared validator

Run [validate-footage.py](scripts/validate-footage.py) with the OneDrive project folder. It needs Python 3.10 or later and no third-party packages. It performs steps 1 through 6 above: it reads the approved set, matches every dropped file against the convention, groups the files into takes, checks the three roles, reports each violation with the offending filename and a specific reason, and prints a per-take table.

```text
python "2-Production/1-Footage Intake/scripts/validate-footage.py" "<OneDrive project folder>"
```

It exits nonzero while any violation remains, so it can gate completion. It never renames, moves, or deletes media. Useful options: `--write-manifest` records `DONE` once validation passes, `--accept-missing "<script>"` records a documented no-footage exception and may be repeated, `--overwrite` replaces an existing `DONE` after the change is confirmed, and `--json` prints the report as data. Files larger than 256 MB use a sampled hash by default; adjust with `--hash-threshold-mb` and `--sample-mb`, or force full hashing with `--full-hash`.

Follow the validator's report, but confirm that any `--accept-missing` entry has its reason in `LOG.md` before treating the step as complete.

## Completion

Success requires no naming violations, every take present complete with all three roles, and every approved script holding at least one complete take. A script with no footage satisfies this step only when `LOG.md` records an explicit human decision to drop or defer it. Any partial take, unknown role, mismatched extension, or unapproved script name leaves the step incomplete.

Write `DONE` in the OneDrive step folder only after the success condition is verified. Its manifest must record the dependencies (the root and this step skill), the inputs (the approved set with hashes and names), the outputs (every media file with its path, byte size, and hash, plus the fingerprint method used), and the conditions (the per-script take inventory, the role counts, the approved-set names, and a zero-violation condition so any later addition, rename, or removal invalidates completion).

## Rerun

Validate the upstream markers and this step's `DONE` in workflow order as described in the root skill. Compare the approved set with the manifest; if an approved script changed, invalidate 2.1 and later steps. Preserve every existing media file, and never overwrite or reorganize footage that a human placed. New drops are validated and added to the manifest; valid files already recorded stay untouched. Append each attempt, violation, handoff, and invalidation to `LOG.md`.
