---
name: studio-animations
description: Run or resume Studio substage 2.3, creating Remotion graphics and animations from approved asset briefs, refining them through chat, and waiting for human-approved videos.
---

# 2.3 - Animations

## Purpose and inputs

Create rendered videos for every active, kept `Graphic` or `Animation` in the current 1.4 Asset Registry. Use the canonical Markdown and JSON together, through the maintained registry parser. The approved Asset/Brief is the initial prompt; Why/Notes, linked script passages and extracts supply context. Exclude other types, rejected and inactive rows. The machine-readable dependency and output contract is [dependencies.json](dependencies.json).

This is an independent branch from 1.4, not a prerequisite of B-roll, footage intake or presenter postproduction. A missing or stale prerequisite blocks execution. A valid approved registry with no eligible assets is a successful empty selection, not a reason to invent graphics.

## Entry and preflight

Resolve the selected connected project to its real OneDrive directory using the root Studio skill. Run shared prerequisite validation before creating anything:

```text
python "<workflow-root>/scripts/validation.py" --project-root "<real-project-root>" --stage 2.3 --prerequisites
```

Use shared status to reuse current completion. For an incomplete or stale execution target, follow Studio's shared invalidation transition; never invalidate solely because prerequisite validation failed. Helper mutations also use the common invalidation and project mutation guard.

The maintained helper is `scripts/animations.py` relative to this skill. Commands below use `<helper>` for its absolute path. Execution helpers verify prerequisites; `context` and `check` are read-only, do not install tools, and do not generate candidates.

```text
python "<helper>" context --project-root "<real-project-root>"
python "<helper>" init --project-root "<real-project-root>" --asset-id ASSET-000010
```

Do not run a different stage to repair failed preflight. Do not install packages or download browsers during normal execution or status. Missing Node, dependencies or rendering browser is an explicit setup blocker. The shared renderer package is `renderer/`; its dependencies and lockfile are maintained in Git, with one installation serving all assets. Explicit setup uses `npm ci` and `npm run remotion -- browser ensure` from that directory.

## Working files and initial cut

Each eligible asset gets its own working directory:

```text
2-Production/3-Animations/ASSET-000010/
  Animation.json
  request.01.md
  settings.json
  src/Composition.tsx
  references/
  snapshots/v001/
  renders/v001.mp4
  APPROVED/
```

`init` snapshots the approved request and creates an editable scaffold without replacing existing human work. The scaffold is not an interpretation of the brief: the agent must author the requested visual before presenting a first cut.

1. Read the complete asset context and inspect existing requests, source, references, renders and approval files.
2. Turn the approved brief into a short visual sequence. Make sensible production choices without an unnecessary questionnaire; ask only when missing facts or delivery requirements materially affect the result.
3. Author `src/Composition.tsx` as a default-exported React component. Keep local code imports inside `src/`. Use React, Remotion, CSS and SVG for charts, diagrams, motion graphics and illustrative cartoons. Remotion animates authored artwork; it is not a generative image model.
4. Put local media, fonts and other visual resources in `references/`; reference them with Remotion's `staticFile()`. Do not use unpinned remote media or import project code from elsewhere. Preserve attribution/licensing information with resources.
5. Record width, height, fps, durationInFrames and optional component `props` in `settings.json`. Initial defaults are 1920x1080, 30 fps, 150 frames, opaque H.264 MP4 without added audio. Adjust to the passage and known delivery requirements rather than treating those defaults as creative instructions.
6. Render through the maintained helper:

```text
python "<helper>" render --project-root "<real-project-root>" --asset-id ASSET-000010
```

The helper snapshots source, references, requests, context and settings, then creates a numbered render without overwriting previous versions. It records exact render identity and renderer fingerprints. Reuse an unchanged current render instead of generating a duplicate.

Inspect the resulting video for legibility, composition, timing, motion, clipping, factual accuracy and correspondence to the script. Technical validation is not a substitute for visual review. Present the first cut and stop for user feedback; never call an untouched title scaffold a finished interpretation.

For graphs and charts, use supplied facts or explicitly agreed assumptions. Identify illustrative data and return assumptions. Do not invent financial outcomes, imply guaranteed growth or disguise a conceptual curve as measured data.

## Iterative chat revisions

Record each substantive requested change before editing the composition:

```text
python "<helper>" revise --project-root "<real-project-root>" --asset-id ASSET-000010 --request "Slow the deposits, retain the piggy bank longer, and simplify the graph labels."
```

Use `--request-file` for longer exact requests. The helper preserves numbered `request.NN.md` history. Update source, resources or settings and render the next version. Preserve prior render snapshots and candidate videos; do not overwrite a version to conceal iteration. Keep production refinements local to 2.3; reconcile a changed fundamental purpose, type or source applicability through 1.4 instead of silently changing its approved request.

A revised request, changed source, settings, resources or relevant approved registry fields makes previous approval noncurrent. Keep old approved files in place, report the mismatch and let the human replace their selection. Do not move or delete approval files automatically.

## Human approval and completion

Only the user moves the chosen rendered MP4 into that asset's `APPROVED/`. Do not offer an automatic approval command, perform the move, or infer approval from a positive chat comment. The video can be renamed; exact-byte matching identifies its recorded render.

Every currently eligible asset must have exactly one current approved video. A file must match a registered render and its current request/source/settings/resource provenance, and probe as usable video with the recorded technical settings. Empty, corrupt, unregistered, altered, obsolete and ambiguous approvals do not count. Missing or extra approval files are actionable review failures, not reasons to select arbitrarily. Historical asset directories never expand the current eligible set.

```text
python "<helper>" check --project-root "<real-project-root>"
python "<helper>" complete --project-root "<real-project-root>"
```

`complete` supplies stage-owned evidence to the shared recorder. Generic inventory-only completion is refused. Never author `DONE` directly or bypass human approval. Status watches the current registry pair and complete nested asset inventories, including additions/removals, and exact video identity. It can detect a modified approved movie even if its size and modification time are preserved.

## Reruns, preservation and safety

- Reuse current assets and approvals. An unrelated registry change may invalidate the stage marker without requiring every animation to be regenerated.
- Preserve briefs, human edits, source, references, snapshots, previous renders and `APPROVED/`. This initial version has no automatic project artifact deletion or regeneration sweep.
- Keep mutations and rendering serial in this initial stage. Do not parallelize helper calls or delegate workers with shared write access. Shared publication, logs and completion remain coordinator-owned.
- Record attempts, failures, revisions and handoffs in the normal append-only stage `LOG.md`, using maintained helpers. Do not rewrite logs or fabricate completion evidence.
- Generated TypeScript is executable code. The renderer does not sandbox composition code or constrain Python/Node filesystem access. Kilo permissions are not an OS security boundary. Use reviewed code, project-specific access, approval for dependency changes and an OS-isolated renderer when stronger containment is required.
- The pinned patched Zod override avoids a known dependency advisory but differs from Remotion's expected version. This stage uses plain JSON settings rather than schema-generated editor controls; do not assume untested Zod editor integrations are compatible.

## Verification

Run `python -m unittest discover -s "<skill-root>/scripts" -p "test_*.py"` for lifecycle and approval checks. From `renderer/`, run `npm test` and `npm run test:smoke`; the latter renders and probes an isolated temporary clip, verifies non-overwrite behavior, and removes its test files. Tests must never approve or mutate a connected production project.
