---
name: studio
description: Manage Studio content projects through chat commands such as "studio create project", "studio run 1.1", "studio run", and "studio status". Route work through numbered project-stage skills and resumable OneDrive artifacts.
---

# Studio

Use this skill for chat requests beginning with `studio` and for clear requests to create, run, resume, or inspect a Studio project. This is a chat workflow, not a terminal CLI. The repository holds workflow instructions and scripts; a OneDrive folder added to the workspace holds project inputs, media, intermediate files, outputs, `LOG.md`, and `DONE`. Never put project media in Git.

## Roots and project identity

- Treat the repository containing this skill as the workflow root. Find the separate OneDrive project root among the workspace folders. If it is not mounted or its identity is ambiguous, ask for its location before creating or changing project data. Do not substitute a directory inside Git.
- Name project folders with sequential, stable numbers: `01-<Name>`, `02-<Name>`, and so on. Check both roots before assigning the next number; do not reuse a number or renumber an existing project.
- Each project has corresponding paths in both roots. The Git path contains skills and scripts. The OneDrive path contains working files. Add stage and substage directories only when their workflow is defined or run; do not scaffold future stages.
- The stage order is `1-Pre Production`, `2-Production`, `3-Post Production`, `4-Publish`, `5-Analyze`. Substages are numbered within their stage, such as `1-Pre Production/1-Script Review` and `1-Pre Production/2-Cue Cards`. An identifier such as `1.2` means stage 1, substage 2.

## Commands

- `studio create project "Name"`: resolve the next project number. Create the project root and only the first step, `1-Pre Production/1-Script Review`, in both roots. Copy the [1.1 skill](1-Pre%20Production/1-Script%20Review/SKILL.md) into the Git step folder as its `SKILL.md`. Create `Approved/` immediately in the OneDrive step folder, even if no candidate exists yet; put candidate Word documents directly in the step folder. Do not create later-stage folders. When creation follows an intake chat, carry the agreed source and candidate ideas into the project and its log.
- `studio run <stage.substage> [project]`: run or resume that specific defined step, such as `1.1` or `1.2`. Resolve the project from the command or unambiguous conversation context; ask if multiple projects remain possible. Validate prerequisite steps before running the requested step.
- `studio run [project]`: validate existing completion markers in order, then run the first incomplete step and continue sequentially until the project finishes, a skill is missing, a step fails, or human input or review is required. Do not silently skip an undefined step.
- `studio status [project]`: validate markers and report the current step, completed steps, stale or missing outputs, and the next required action. Validation may delete stale markers and append invalidation entries to logs.

The stage skills below are the source definitions in this repository. When a defined step is first created for a project, copy its source `SKILL.md` into the matching numbered Git project directory; keep any project-specific edits there. Read the project's step skill before running it. A step skill may describe interactive or judgment-based agent work; scripts are optional. Each step skill must state what it does, its expected inputs, its success condition, its output location, and how to rerun without duplicating or overwriting valid work. The step creates its own output, debug, and intermediate directory structure in the matching OneDrive path when needed.

## Logs, completion, and invalidation

- Each executable step has one human-readable, append-only `LOG.md` in its OneDrive step folder. Append an entry for every attempt, validation failure, human handoff, and invalidation. Record inputs, actions and decisions, outputs, result, and the next action. Preserve prior entries.
- Each completed step has a `DONE` file in its OneDrive step folder. `DONE` is a small machine-readable manifest of the step's declared input dependencies, required output files, and relevant directory conditions, with their expected paths and fingerprints. Include the step skill and script revision when changes to them would change the result. Exclude `LOG.md`, `DONE`, and incidental debug or temporary files from fingerprints. Use content hashes for small documents; a step may specify a practical fingerprint for large media.
- Write `DONE` only after checking the step's success condition and verifying its outputs. On `studio run` and `studio status`, validate completed steps in workflow order. If any declared dependency, output, or condition differs, delete that step's `DONE` and all subsequent `DONE` files, append the reason to the affected logs, and resume from the first invalid step. Keep existing work files for inspection; do not overwrite human edits blindly.
- This validation occurs when Studio is invoked, not continuously in the background. A `DONE` marker cannot undo an external action such as publishing; a rerun of such a step must reconcile the external result before acting again.
- A stage-level `DONE` may be written after all its defined substages are complete. It does not replace the substage markers used for resuming.

## Defined substages

- `1.1` — [Script Review](1-Pre%20Production/1-Script%20Review/SKILL.md)
- `1.2` — [Cue Cards](1-Pre%20Production/2-Cue%20Cards/SKILL.md)

Later steps are defined as the workflow evolves. Do not infer their detailed behavior from the five stage names.
