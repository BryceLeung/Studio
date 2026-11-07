---
name: studio-animation-render-object
description: On an explicit render request, create a versioned Remotion rendering of a Studio 2.3 character, background or object, optionally based on a selected AI-assisted or authored sketch. Does not invoke image generation, create sketches or automatically save to the shared library.
---

# Render Animation Object

## Scope and gate

Use when the user explicitly asks to render a character, background or object in Remotion, or revise an existing Remotion candidate. "Sketch" and quick image preview requests belong to [Sketch Animation Asset](../sketching/SKILL.md), not this skill. Read the parent [Animations skill](../SKILL.md), its dependencies and [Studio authoring](../references/studio-authoring.md). Follow Studio project resolution, interpreter selection, prerequisite validation, invalidation and append-only logging rules. Stop on failed preflight without repairing another stage. A stage run, text proposal, sketch request or positive sketch feedback does not authorize rendering.

Identify the current registry asset, exact request and selected sketch, earlier candidate or library version if any. A sketch, including an AI-generated or AI-edited sketch, is a design reference, not an existing Remotion candidate; no sketch is required if the user requests a render directly. Ask only if the target or requested choice is genuinely ambiguous. Create only the requested object, using authored React, SVG, CSS and Remotion. This skill does not invoke image generation; use the sketch skill for that separate action. Selected AI sketches may be inspected and copied as design references with their provenance retained. Identify preparation needed to turn flat artwork into editable parts or a rig, and report unsupported steps rather than promising automatic conversion or passing off a static image as an articulated character. No full-animation assembly, library promotion or approval is implied.

## Candidate workflow

1. Inspect existing candidates and user decisions. Choose a descriptive stable object name; it must be a single directory name, not an absolute path or path traversal. Store new work only under `2-Production/3-Animations/<registry-id>/candidates/<object-name>/vNNN/`. Allocate the next unused version above existing versions, never overwrite or recycle an earlier version, and keep writes serial.
2. Create real, independent files in that version: `src/Composition.tsx`, `src/Studio.tsx`, `references/`, `settings.json` and `provenance.json`. Do not use symlinks or hardlinks to canonical animation source, approvals, other candidates or library resources. Copy any reused source/resources locally and preserve licensing information. Keep code imports inside the candidate's `src/` and use `staticFile()` for resources in its `references/`.
3. Record in `provenance.json` the registry ID, object name, exact user request, project-relative candidate path, selected sketch and parent candidate/library version if any, resources and attribution. Preserve the sketch in place; copy any needed design references into the candidate. Use project-relative paths without `..`; do not embed machine-specific paths. Explain what was authored versus reused. This is working provenance, not a completion manifest.
4. Author the requested visual, not a title scaffold. Default-export its React component and register exactly one composition in `src/Studio.tsx`. Keep parts independently editable and named. Use the existing renderer templates as structural references, not as completed artwork. Set matching width, height, fps and durationInFrames in Studio metadata and `settings.json`; use agreed delivery settings, otherwise parent-stage defaults. MP4 previews are opaque; retain editable source for later reuse rather than treating the preview as a transparent sprite.
5. An explicit object render request authorizes creating the Remotion candidate and rendering it with the isolated commands below, not `animations.py render`, which registers canonical animation videos. A source-only revision does not authorize another render unless requested. Do not replace the animation's composition temporarily to render an object. Preserve previous versions and preview files. If the user requests another revision after delivery, allocate a new candidate version.
6. Inspect any preview for appearance, clipping, motion and correspondence to the request. Report the candidate path, what changed, reused resources and any limitations. Append the request, version, actions, render/check outcome and next handoff to the stage `LOG.md` using the shared helper. Stop for feedback. Do not automatically save, assemble, render the full animation, advance to another animation or record completion.

## Isolated preview commands

`<stage-root>` is the workflow repository's `.agents/skills/studio/2-Production/3-Animations`; `<candidate>` is the resolved candidate version directory. These are existing Node interfaces, not Python helper subcommands. Execute from the workflow repository root with quoted paths and normal Node execution approval. Do not install dependencies or browsers automatically. Missing setup is a blocker.

```text
node "<stage-root>/renderer/render.mjs" --source-dir "<candidate>/src" --public-dir "<candidate>/references" --settings "<candidate>/settings.json" --inspect
node "<stage-root>/renderer/render.mjs" --source-dir "<candidate>/src" --public-dir "<candidate>/references" --settings "<candidate>/settings.json" --output "<candidate>/preview.mp4"
node "<stage-root>/renderer/probe.mjs" "<candidate>/preview.mp4"
```

`--inspect` executes composition code and uses the installed browser; it is not static inspection. Its returned dimensions, fps and duration must match the candidate settings before rendering. Correct new candidate settings if needed; never silently change agreed timing or dimensions. The renderer refuses existing output files. Preserve a failed attempt's artifacts; use a new version or unused preview filename for a retry rather than deleting outputs. Technical probing does not replace visual review.

The renderer is not an OS sandbox. Review executable code and confine inputs and outputs deliberately. These previews are exploratory candidates, not registered animation videos and never final approvals. Candidate publication must follow the parent stage's normal invalidation/logging rules; the direct Node renderer does not perform them.

## Success and reruns

Success for a render request is the editable Remotion candidate and its visually reviewed, probed video handed back to the user. A source-only revision is reported as unrendered, not a successful render. Reuse unchanged candidates when appropriate, but never infer library acceptance. Saving is a separate explicit action handled by [Save Animation Object](../library-save/SKILL.md). No `DONE` is written by this skill.
