---
name: studio
description: Manage Studio content projects through chat commands such as "studio connect", "studio disconnect", "studio create project", "studio run 1.1", "studio run", and "studio status". Route work through numbered project-stage skills and resumable OneDrive artifacts.
---

# Studio

Use this skill for chat requests beginning with `studio` and for clear requests to connect, disconnect, create, run, resume, or inspect a Studio project. This is a chat workflow, not a terminal CLI. The repository holds shared workflow instructions and scripts. A OneDrive folder added to the workspace, or accessed through a workspace project link, holds every project folder, including its inputs, media, intermediate files, outputs, `LOG.md`, and `DONE`. Do not create or check in project folders or project links in Git.

## Roots and project identity

- Treat the repository containing this skill as the workflow root. Find the separate OneDrive project root among the workspace folders or from the targets of connected project links in the workflow root's `Connected Projects` subdirectory. Resolve connected projects to their real OneDrive paths before running stages. If the project root cannot be found or its identity is ambiguous, ask for its location before creating or changing project data. Do not substitute a directory inside Git.
- Name primary project folders with sequential, stable two-digit IDs: `01-<Name>`, `02-<Name>`, and so on. A pickup or revision of an existing idea is a separate child project beside its parent, named `<parent ID>.<next child number>-<parent name> <purpose>`; for example, `01.1-Love Compounds Too Pickup`. Use `01.2` for the next child of `01`, regardless of later primary projects. Check the OneDrive project root before assigning either number; do not reuse an ID or renumber an existing project. An explicitly requested available ID takes precedence over automatic numbering. A child requires an existing parent.
- Keep project folders only in OneDrive. Add stage and substage directories only when their workflow is defined or run; do not scaffold future stages.
- The stage order is `1-Pre Production`, `2-Production`, `3-Post Production`, `4-Publish`, `5-Analyze`. Substages are numbered within their stage, such as `1-Pre Production/1-Script Review` and `1-Pre Production/2-Cue Cards`. A one-digit prefix such as `1.2` is a stage/substage; a two-digit prefix such as `01.1` is a child project ID.

## Pickup and revision projects

Use a child project when an approved asset needs a new filming or editing pass after work has progressed, such as an extract that does not work in the footage and needs a new filmed take. Keep it scoped to the replacement or related pickup batch. In the child's first `LOG.md` entry, identify its parent project, the specific asset and version being replaced, why the pickup is needed, and the parent source files or edit it uses. Reference those files in place; do not copy the parent's scripts, footage, or outputs into the child merely to restart the workflow.

The child runs the same defined steps with its own candidates, `Approved/`, logs, and `DONE` markers. Its new scripts still require the user's explicit request before writing. A child does not add files to the parent's approved set or change the parent's completion markers. Child manifests fingerprint only the parent artifacts they actually depend on; a changed dependency invalidates the child, not the parent. Preserve earlier exports and store replacement outputs under the child with a clear link to the parent asset and version they supersede. Record the relationship in the parent log if needed, without treating that log entry as a reason to invalidate completed steps.

## Commands

For the connection script, use an available Python 3.9+ interpreter (`python` on this Windows machine, typically `python3` on macOS).

- `studio connect <project>`: use the exact project folder name and run `python "<workflow-root>/scripts/connect.py" connect "<project>"`. The script finds the synced `OneDrive/Books/Parent Like a Millionaire/Marketing/Studio` root using Windows OneDrive environment variables or the usual Windows/macOS locations, including macOS `~/Library/CloudStorage/OneDrive*`. It creates the `Connected Projects` subdirectory in the workflow root when missing, then creates a directory symlink with the project's folder name inside it; when Windows denies symlink creation because of missing privileges, it falls back to a directory junction. Several projects may be connected at once; reconnecting the same target is harmless. If the script reports several OneDrive roots or cannot find the root, ask for the intended local Studio project-root path and pass it with `--project-root`. Never replace an existing workspace entry or guess between roots. Connecting only adds the workspace link; it does not copy project data or edit `Studio.code-workspace`.
- `studio disconnect <project>`: run `python "<workflow-root>/scripts/connect.py" disconnect "<project>"` with the exact project folder name. Remove only that project's link in the workflow root's `Connected Projects` subdirectory, preserving the OneDrive folder and all its contents. An absent link is already disconnected. The script can remove broken project links and refuses files, real directories, and links to unrelated targets. If the link was created with an explicit `--project-root` outside the automatically detected locations, pass that same root when disconnecting; ask for it if unknown.
- `studio create project "Name"`: resolve the next primary project number, or use an available ID explicitly given by the user. Create the project root and only the first step, `1-Pre Production/1-Script Review`, in OneDrive. Create `Approved/` immediately in the step folder, even if it has no files yet. Do not create later-stage folders or write candidate scripts unless the user explicitly asks for them. When creation follows an intake chat, carry the agreed source and candidate ideas into the project's log.
- `studio create pickup <parent> "Purpose"`: resolve the parent project and next child ID, then create the child with the same initial 1.1 structure. This is also the route for natural-language requests to repair an asset within an existing idea. Record the parent and replacement scope in the child's log; do not modify the parent's approved files. If the user explicitly names an available child ID, use it.
- `studio run <stage.substage> [project]`: run or resume that specific defined step, such as `1.1` or `1.2`. Resolve the project from the command or unambiguous conversation context; ask if multiple projects remain possible. Validate prerequisite steps before running the requested step.
- `studio run [project]`: validate existing completion markers in order, then run the first incomplete step and continue sequentially until the project finishes, a skill is missing, a step fails, or human input or review is required. At 1.1, discuss a new asset slate interactively and stop before writing scripts unless the user explicitly requested that writing. Do not silently skip an undefined step.
- `studio status [project]`: validate markers and report the current step, completed steps, stale or missing outputs, and the next required action. Validation may delete stale markers and append invalidation entries to logs.

Resolve primary and child projects by their full ID or exact folder name. If a title could mean both a parent and one or more children, ask which one to run or inspect.

The stage skills below are shared workflow definitions in this repository. Read the relevant source skill before running a step. Record project-specific decisions in the OneDrive step `LOG.md` or its working artifacts; update the shared source skill when the workflow itself changes. A step skill may describe interactive or judgment-based agent work; scripts are optional. Each step skill must state what it does, its expected inputs, its success condition, its output location, and how to rerun without duplicating or overwriting valid work. The step creates its own output, debug, and intermediate directory structure in the matching OneDrive path when needed.

## Logs, completion, and invalidation

- Each executable step has one human-readable, append-only `LOG.md` in its OneDrive step folder. Append an entry for every attempt, validation failure, human handoff, and invalidation. Record inputs, actions and decisions, outputs, result, and the next action. Preserve prior entries.
- Append entries with `python "<workflow-root>/scripts/append-log.py" "<step-folder>/LOG.md" --title "<heading>" --item "<bullet>" [--item "<bullet>" ...]`. Run it directly against the connected project, requesting external-path approval when required. Do not copy the log into the repository, rewrite the whole file, or use shell redirection. The helper performs one append-only write and refuses non-`LOG.md` targets.
- Each completed step has a `DONE` file in its OneDrive step folder. `DONE` is a small machine-readable manifest of the step's declared input dependencies, required output files, and relevant directory conditions, with their expected paths and fingerprints. Include the step skill and script revision when changes to them would change the result. Exclude the shared Studio root skill (`.agents/skills/studio/SKILL.md`), `LOG.md`, `DONE`, and incidental debug or temporary files from fingerprints. Adding stages or changing the overall workflow must not invalidate completed stages. Use content hashes for small documents; a step may specify a practical fingerprint for large media.
- When validating older `DONE` manifests, remove any legacy dependency entry for the shared Studio root skill and log the migration. A changed hash for that file is not an invalidation reason. Continue checking the stage's own instructions, scripts, inputs, outputs, and directory conditions as declared.
- Write `DONE` only after checking the step's success condition and verifying its outputs. On `studio run` and `studio status`, validate completed steps in workflow order. If any declared dependency, output, or condition differs, delete that step's `DONE` and all subsequent `DONE` files, append the reason to the affected logs, and resume from the first invalid step. Keep existing work files for inspection; do not overwrite human edits blindly.
- This validation occurs when Studio is invoked, not continuously in the background. A `DONE` marker cannot undo an external action such as publishing; a rerun of such a step must reconcile the external result before acting again.
- A stage-level `DONE` may be written after all its defined substages are complete. It does not replace the substage markers used for resuming.

## Defined substages

- `1.1` — [Script Review](1-Pre%20Production/1-Script%20Review/SKILL.md)
- `1.2` — [Cue Cards](1-Pre%20Production/2-Cue%20Cards/SKILL.md)
- `1.3` — [Extracts](1-Pre%20Production/3-Extracts/SKILL.md)
- `2.1` — [Footage Intake](2-Production/1-Footage%20Intake/SKILL.md)
- `3.1` — [Sync](3-Post%20Production/1-Sync/SKILL.md)
- `3.2` - [Transcribe](3-Post%20Production/2-Transcribe/SKILL.md)

Stage 3.2 currently has no completion condition, pending design of the WER benchmark workflow. Generate its outputs when requested, but do not write its `DONE`, claim completion, or continue automatically past it until the user defines that condition.

Later steps are defined as the workflow evolves. Do not infer their detailed behavior from the five stage names.
