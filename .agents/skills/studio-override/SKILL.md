---
name: studio-override
description: Manually preview and explicitly confirm acceptance of existing outputs for previously completed Studio substages via "studio override <stage.substage> [project]" or "studio override all [project]". Never invoked automatically by run or status.
---

# Studio Override

Use only for an explicit manual request for `studio override <stage.substage> [project]` or `studio override all [project]`. Read the [Studio root skill](../studio/SKILL.md) for project identity and normal permissions. This skill accepts existing content by user attestation; it does not execute a stage or generate, regenerate, move, or archive outputs, and does not create approval folders. Never invoke it as a repair for `studio run`, `studio status`, validation failures, or ordinary tool requests. There is no general `--yes`, implicit consent, or automatic override.

## Resolve Scope

- Resolve the requested project by exact full project ID or exact folder name, or use an unambiguous existing project root established in the current context. Resolve a connected project link to its real project path. Do not guess between connected projects, use a title match to choose one, create a project, or substitute a repository directory. Ask for the exact project when ambiguous, and stop until resolved.
- Select exactly one defined `stage.substage`, or `all`. Do not infer a whole stage from a stage number or silently widen the scope. Use the declared dependency definitions, not a separate hard-coded dependency list.
- Use the reason supplied in the request or established context. If none was supplied, use `User requested manual acceptance of existing outputs`, label it as the default, and show it in the confirmation preview; do not invent a more specific justification or add a separate reason question. Keep the same project, stage selection, and confirmed reason for preview and apply.
- Use a Python 3.10+ environment and the shared `scripts/stage_override.py` interface below. Do not replace a missing or failing helper with manual marker edits or normal completion recording.

## Preview and Confirmation

Run the read-only preview against the resolved real project root:

```sh
python "<workflow-root>/scripts/stage_override.py" preview --project-root "<real-project-root>" --stage <stage.substage|all> --reason "<user reason>"
```

The helper emits a warning, selected stages, explicitly skipped never-completed stages for `all`, impacts, `plan_id`, and `can_apply`. Preview must not write logs, markers, folders, or outputs. Display its warning and plan to the user, including the exact project, reason, selected stages in dependency order, skipped stages, accepted files, waived checks, and marker archival/invalidation impacts. If those details are unavailable, stop rather than invent them or bypass the preview.

Always communicate this warning before confirmation:

> This marks existing outputs valid without rerunning the stage success check or verifying that they match changed inputs or code. It may accept incorrect work. No outputs are regenerated or moved.

Warn explicitly when applying will archive dependent completion markers outside the selected scope, including containing stage aggregate markers. Only markers are archived, not their outputs. For `all`, selected descendants are reasserted later in dependency order; unselected dependents remain invalidated.

If `can_apply` is false, report all blockers and stop without changes. Otherwise use the question tool to request explicit confirmation of this displayed preview, with clear apply and cancel choices. Confirmation is mandatory even if the initial request already said to apply, confirmed, or supplied a yes-like option. Tool permission approval alone is not this confirmation. If the question tool is unavailable, stop without applying. A cancellation, refusal, or absence of an answer means no changes; do not run apply, append logs, or invalidate markers.

## Apply the Confirmed Plan

Only after affirmative confirmation through the question tool, run:

```sh
python "<workflow-root>/scripts/stage_override.py" apply --project-root "<real-project-root>" --stage <stage.substage|all> --reason "<same user reason>" --confirm "<preview plan_id>" --actor "<user identity or explicit user confirmation>"
```

Use an established user identity if available; otherwise record `explicit user confirmation` as the actor. Never invent an identity. Preserve normal tool permissions and external-path approval for both commands; confirmation does not grant filesystem permission. Do not alter arguments, omit the plan ID, accept a stale plan, or auto-retry changed scope after confirmation. If the plan no longer applies, obtain a fresh preview and a new question-tool confirmation.

## Eligibility and Effects

- A selected stage must have prior completion evidenced by an active or archived `DONE`; a never-completed stage cannot be overridden. `all` selects only previously completed, defined stages in dependency order and explicitly reports never-completed stages as skipped, not successful. Existing archived `DONE` files are left untouched.
- Existing required files and declared inventory floors must be present. Nonselected prerequisites must be current. Missing files or stale prerequisites outside the selection block the entire apply during preflight, before any writes. An override cannot fabricate evidence, replace missing outputs, or use an archived output as a current file.
- The shared writer archives any active selected `DONE`, even if currently valid, and creates a fresh snapshot with an override attestation. It assumes existing content is valid and waives the stage's human, domain, and history checks; it does not claim those checks passed. It records current declared inputs, outputs, full watched inventories, and workflow files so normal status honors the override only until any of them change.
- Use only the helper and shared writer for mutations. They invalidate descendant completion and containing stage aggregate markers through the common invalidator. No separate dependency walk, hand-authored `DONE`, marker deletion, or output archiving is permitted. For `all`, later selected stages are reasserted in dependency order after earlier invalidations.
- The helper logs the reason, scope, accepted files, and waived checks with the attestation. Do not add independent log writes, stage executions, approval moves, or output modifications as part of this workflow.

## Results and Recovery

Report the applied scope and any invalidated out-of-scope dependent markers, and reiterate that outputs were not regenerated or moved. A successful override is an attestation about existing content, not a verified rerun or an external approval.

Preflight failure must leave the project unchanged. Unexpected I/O failure after writing begins can produce a partial result: report the helper's applied and remaining stages and stop. Never claim rollback, full success, or automatic continuation. Recovery requires a fresh read-only preview of the remaining intended scope, renewed warning, and a new question-tool confirmation before any further apply.
