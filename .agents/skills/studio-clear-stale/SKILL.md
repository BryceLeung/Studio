---
name: studio-clear-stale
description: Delete a Studio project's STALE archive directories on an explicit studio clear stale request for one stage, one substage, or all stages.
---

# Studio Clear Stale

Use only when the user explicitly requests clearing stale archives. Read the [Studio root skill](../studio/SKILL.md) to resolve the real OneDrive project; use the established project when unambiguous and ask for its identity otherwise. Creating this skill or running/status-checking a stage does not authorize cleanup.

Accept a substage (`3.2`), a whole stage (`3`), or `all`. A whole stage includes its own `STALE` directory and those of its numbered substages; `all` includes every existing numbered stage and substage, including ones without a workflow definition. Scope `all` to the selected project, never all connected projects.

Run with Python 3.9+:

```text
python3 "<workflow-root>/scripts/clear_stale.py" --project-root "<real-project-root>" --stage <stage|stage.substage|all>
```

The helper removes only those exact `STALE` directories and their contents. This includes archived outputs, archived `DONE` records, and `PENDING.json`. Keep active `DONE`, logs, current outputs, source files, and stale-transcript guards intact. Do not run validation, invalidation, regeneration, or log appends as part of cleanup. Missing archives are a successful no-op. The explicit cleanup request authorizes deletion; do not add another conversational confirmation. Normal filesystem/tool approvals still apply.

Use the helper rather than a recursive project-wide glob or hand-built deletion command. It preflights selected paths and refuses linked stage or archive directories; it never follows symlinks into other folders. `--dry-run` lists the same deletion scope without changes when a preview is requested.

Report which project and archives were cleared, or that none existed. If deletion fails after some directories were removed, report the removed paths and error; do not claim rollback or automatically broaden the scope. Clearing archives discards historical provenance and cannot make a stage current. A later run must pass its normal checks.
