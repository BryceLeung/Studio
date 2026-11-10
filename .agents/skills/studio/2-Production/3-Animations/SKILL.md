---
name: studio-animations
description: Run or resume Studio substage 2.3 interactively, discussing one registry asset at a time, sketching fast image previews, rendering Remotion candidates and saving reusable assets only on request, and waiting for human-approved videos.
---

# 2.3 - Animations

Use registry IDs as supplied: new projects use `A01`-`A99`; existing projects keep their legacy `ASSET-000001` IDs. Both formats are supported in asset arguments and folder names. Never renumber existing assets.

## Purpose and inputs

Create rendered videos for every active, kept `Graphic` or `Animation` in the current 1.4 Asset Registry. Use the canonical Markdown and JSON together, through the maintained registry parser. The approved Asset/Brief is the initial prompt; Why/Notes, linked script passages and extracts supply context. Exclude other types, rejected and inactive rows. The machine-readable dependency and output contract is [dependencies.json](dependencies.json).

Create animation candidates through authored animation techniques such as React, SVG, CSS and Remotion, including characters, backgrounds and objects. AI image generation and editing are permitted for static sketch exploration and design references through the sketch skill. Render requests do not authorize new image-generation calls or AI-generated video; they use the selected design and prepared resources through the authored Remotion workflow.

Quick sketches are static images, not animation renders. A connected image-generation/editing tool may be used for concepts and reference-guided revisions; direct programmatic drawing such as Pillow remains available for suitable tasks. Tool setup and credentials are not implied by this permission. Keep the exact prompt, tool/model when known and input references with each sketch version. An accepted AI sketch still needs explicit preparation into usable animation assets; do not promise that a flat image is automatically a layered rig.

This is an independent branch from 1.4, not a prerequisite of B-roll, footage intake or presenter postproduction. A missing or stale prerequisite blocks execution. A valid approved registry with no eligible assets is a successful empty selection, not a reason to invent graphics.

## Entry and preflight

Resolve the selected connected project to its real OneDrive directory using the root Studio skill. Run shared prerequisite validation before creating anything:

```text
python "<workflow-root>/scripts/validation.py" --project-root "<real-project-root>" --stage 2.3 --prerequisites
```

Use shared status to reuse current completion. For an incomplete or stale execution target, follow Studio's shared invalidation transition; never invalidate solely because prerequisite validation failed. Helper mutations also use the common invalidation and project mutation guard.

The maintained helper is `scripts/animations.py` relative to this skill. Commands below use `<helper>` for its absolute path. Execution helpers verify prerequisites; `context` and `check` are read-only, do not install tools, and do not generate candidates.

Use Python 3.10+ for the helpers, preferring this stage's `.venv-tools/bin/python` on macOS/Linux or `.venv-tools/Scripts/python.exe` on Windows when present. Do not silently use an older system Python.

```text
python "<helper>" context --project-root "<real-project-root>"
python "<helper>" init --project-root "<real-project-root>" --asset-id A10
```

Do not run a different stage to repair failed preflight. Do not install packages or download browsers during normal execution or status. Missing Node, dependencies or rendering browser is an explicit setup blocker. The shared renderer package is `renderer/`; its dependencies and lockfile are maintained in Git, with one installation serving all assets. Explicit setup uses `npm ci` and `npm run remotion -- browser ensure` from that directory.

## Directory setup

After successful preflight, create missing working directories for every currently eligible registry asset and the shared library below. Use the existing registry IDs, including legacy IDs, without renumbering. Keep this library inside stage 2.3, separate from the project's root-level `Assets/`. Preserve existing directories and files; setup is not permission to create artwork or render.

```text
2-Production/3-Animations/
  ASSETS/
    CHARACTERS/
    BACKGROUND/
    OBJECTS/
  A10/
    Animation.json
    request.01.md
    settings.json
    package.json
    tsconfig.json
    sketches/
      piggy-bank/
        v001/preview.png
    candidates/
      piggy-bank/
        v001/
        v002/
    src/Composition.tsx
    src/Studio.tsx
    references/
    snapshots/v001/
    renders/v001.mp4
    APPROVED/
  A11/
    ...
```

`init` snapshots the approved request and creates a Studio-ready scaffold with a registered entry point, named interactive SVG layer and portable project configuration. Run it serially for each eligible asset on setup, including existing assets: it creates a missing `sketches/` directory without rewriting existing files or replacing sketch content. Create missing library and candidate directories through permitted filesystem operations, not invented helper flags. Every new graphic/animation should retain the Studio-ready format. A scaffold is setup only, not artwork, a visual proposal or a finished animation. Sketch versions, candidate versions, snapshots and rendered files in the example appear only when requested work produces them; do not populate placeholder versions. Preserve historical sketches stored as candidates; do not migrate or rename earlier work automatically.

## Interactive workflow

This stage has no automatic first-cut flow. A `studio run 2.3` request authorizes preflight, directory setup, source-context presentation and a text proposal only. It does not authorize creating objects, assembling a composition, rendering a preview, saving reusable assets or proceeding through all animations unattended.

Route requests by meaning, without automatically chaining actions:

- **Sketch:** follow [Sketch Animation Asset](sketching/SKILL.md) for a fast static image in this asset's `sketches/<subject>/vNNN/`, using connected AI generation/editing or appropriate programmatic drawing. No Remotion setup, video render or frame extraction. A quick image preview or ambiguous bare "preview" defaults to this lightweight path; it does not authorize setting up a provider or silently replacing a requested AI sketch with a coded approximation.
- **Render:** follow [Render Animation Object](object-authoring/SKILL.md) for a requested Remotion object candidate in `candidates/<object-name>/vNNN/`. If the request explicitly targets the whole animation instead, use the maintained full-animation render workflow below. Use a selected sketch as a design reference without moving or promoting it.
- **Save:** follow [Save Animation Object](library-save/SKILL.md) to copy a selected candidate into the common versioned `ASSETS` library. Saving does not sketch or render. A sketch alone is not a reusable candidate and must not be silently promoted.

These are subordinate workflows, not additional numbered stages, and do not replace the final animation approval gate.

1. Work on one asset at a time. Resume the asset already under discussion; otherwise start with the first unfinished eligible asset in registry order. Reuse current approved work rather than recreating it. Read its requests, source, references, sketches, candidates, renders and approvals before proposing changes.
2. Show the linked script passage, stage directions, registry brief and Why/Notes, plus relevant extract context. Quote original material accurately and distinguish it from new production decisions. Identify missing context rather than inventing script notes.
3. Suggest the visual treatment in text: composition, action, approximate timing, characters, background and objects, including reusable library candidates. Then stop for user direction. Do not author artwork or render a first cut automatically.
4. Sketch or render only the requested subject using the corresponding skill. Sketches live in `sketches/` and Remotion candidates in `candidates/`; retain separate version histories and trace the selected sketch in render provenance. A sketch request does not authorize Remotion rendering or production-ready rigging. Neither object workflow authorizes rendering the whole animation or saving to the library.
5. Stop after presenting each requested sketch or candidate for feedback. Apply requested revisions as new versions in the appropriate folder; do not silently overwrite earlier work or assume positive feedback means render or save. Record decisions and sketch/candidate paths in the append-only stage log so another session can resume the same discussion.
6. Only when the user explicitly asks to save an object, copy the selected version into `ASSETS/CHARACTERS/<name>/vNNN/`, `ASSETS/BACKGROUND/<name>/vNNN/` or `ASSETS/OBJECTS/<name>/vNNN/` as appropriate. Preserve the original candidate and existing library versions. If the selected version is ambiguous, resolve that before copying. Retain source, required resources, provenance and any attribution/licensing information with the saved version. Saving an object is not animation approval.
7. Assemble or revise the animation only when requested, using the agreed objects and treatment. Freeze selected library resources into this animation's `references/`, and copy reusable code into its own `src/`; record the source library version. Never import live code or reference mutable files from the shared library or another animation. Later library updates must not silently change an existing animation.
8. Render an animation preview only when requested, using the maintained render workflow below. Inspect it and present it for feedback, then stop. Continue revisions interactively. Move to the next animation only on the user's explicit instruction, not because a render succeeded or an object was saved. An instruction to move on is not final video approval; preserve pending approval status and the existing completion gate.

## Requested composition and rendering

These technical instructions apply only after the corresponding interactive request, not automatically on stage entry.

Author `src/Composition.tsx` as a default-exported React component. Preserve `src/Studio.tsx`, registering exactly one composition. Keep local code imports inside `src/`. Use React, Remotion, CSS and SVG for charts, diagrams, motion graphics and illustrative cartoons. Build independently editable elements as named timeline nodes and expose useful controls for the particular brief. Read [Studio authoring](references/studio-authoring.md) before composing or restructuring these controls. Remotion animates authored artwork; it is not a generative image model.

Put local media, fonts and other visual resources in `references/`; reference them with Remotion's `staticFile()`. Do not use unpinned remote media or import project code from elsewhere. Preserve attribution/licensing information with resources.

Set the composition metadata in `src/Studio.tsx`. Initial defaults are 1920x1080, 30 fps, 150 frames, opaque H.264 MP4 without added audio. Adjust to the agreed treatment, passage and known delivery requirements. The render helper resolves this same Studio composition and captures its width, height, fps and duration into `settings.json` before snapshotting. Optional `settings.json` component `props` remain explicit overrides; avoid duplicating editable controls there and in source. A Studio entry is required; unsupported older layouts must not be silently converted or rendered.

Render requested animation previews through the maintained helper:

```text
python "<helper>" render --project-root "<real-project-root>" --asset-id A10
```

The helper snapshots source, references, requests, context and settings, then creates a numbered render without overwriting previous versions. It records exact render identity and renderer fingerprints. Reuse an unchanged current render instead of generating a duplicate.

Inspect each requested preview for legibility, composition, timing, motion, clipping, factual accuracy and correspondence to the script. Technical validation is not a substitute for visual review. Present the preview and stop for user feedback; never call an untouched title scaffold a finished interpretation. Object candidates are exploratory working assets, not registered final animation videos. The Python helper has no object-candidate render or library-save command: follow the object skill's existing isolated Node renderer interface and the save skill's verified copy workflow. Do not invent helper commands, register an object preview as the completed animation, or overwrite the canonical composition to render an isolated object. Direct Node execution retains its normal approval requirements.

## Opening Remotion Studio

Open the selected asset with the maintained helper when the user wants visual editing:

```text
python "<helper>" studio --project-root "<real-project-root>" --asset-id A10
```

This starts a foreground local server, opens the browser, and chooses a free port. Optional `--port 3000` selects a port; `--no-open` leaves the browser closed. Return the actual URL printed by the server. The launcher uses the asset as its editable project root and the shared renderer installation for dependencies. It does not install packages. Missing entry/configuration files are an error; no migration or automatic retrofit is supported.

Studio edits save directly to source and make older renders differ from the working version, without revoking their approvals. Test the generated controls by changing and resetting one value, and check timeline timing after restructuring. Keep previous candidates and snapshots. A Studio export is an exploratory video until rendered and registered through `animations.py render`; it does not bypass the human approval gate. Include Studio readiness when handing off a requested animation preview; do not open a separate browser/server for every asset automatically.

For graphs and charts, use supplied facts or explicitly agreed assumptions. Identify illustrative data and return assumptions. Do not invent financial outcomes, imply guaranteed growth or disguise a conceptual curve as measured data.

## Iterative chat revisions

Record each substantive requested change before editing the composition:

```text
python "<helper>" revise --project-root "<real-project-root>" --asset-id A10 --request "Slow the deposits, retain the piggy bank longer, and simplify the graph labels."
```

Use `--request-file` for longer exact requests. The helper preserves numbered `request.NN.md` history. Update only the source, resources or settings requested by the user; render the next version only when requested. Sketch-only changes use the sketch skill's lightweight image versioning and log record, not canonical composition revisions or render snapshots. Object-only changes follow the candidate versioning and explicit save gates above. Preserve sketches, prior render snapshots and candidate videos; do not overwrite a version to conceal iteration. Keep production refinements local to 2.3; reconcile a changed fundamental purpose, type or source applicability through 1.4 instead of silently changing its approved request.

A creative revision to requests, source, settings, resources or the renderer does not revoke earlier approvals. Validate each approved render against its immutable snapshot, not the latest working composition. Changed relevant registry context (including brief, purpose and linked passage) requires human compatibility review: retain old files, but exclude them from the current eligible set until the user explicitly reapproves the exact version for the new context, or approves a replacement. Record a registry-context revision before completing the stage. Never move or delete earlier approvals automatically.

## Human approval and completion

Positive human feedback such as "I like this animation" or "approve this" about the unambiguously selected full-animation preview authorizes moving that exact registered MP4 into the asset's `APPROVED/`. Resolve an ambiguous asset or version before acting; never guess the latest render. This does not apply to sketches, isolated object candidates, unregistered Studio exports, technical render success or an instruction to move on. Approval does not render, save objects, advance to the next asset or complete the stage automatically.

Use the maintained helper with the exact render number and verbatim feedback:

```text
python "<helper>" approve --project-root "<real-project-root>" --asset-id A10 --version 3 --request "I like this animation"
```

The helper moves `renders/v003.mp4` to `APPROVED/v003.mp4`, preserves snapshots and other approved versions, and records the decision, timestamp, render identity and registry context in `Animation.json` and the stage log. It never overwrites a destination. Repeated approval of the same version and context is a no-op. Explicitly reapproving an older version after compatibility review records acceptance of the current registry context without altering its original render provenance. Files can be renamed; exact-byte matching identifies their render. Existing manually moved approvals remain supported, using their original render registry context when no chat approval record exists.

Every currently eligible asset must have at least one valid approved video matching current registry context; multiple distinct approved versions are allowed. A file must match a ready registered render, retain its intact snapshot/provenance and probe as usable video with that snapshot's technical settings. Empty, corrupt, unregistered, altered and duplicate approved files fail validation. Byte-identical render versions share one approved file and one editorial option, while retaining separate render and approval histories; merely rendering identical bytes does not reapprove them for changed context. For legacy manual approvals matching multiple identical renders, use the earliest render's registry context until explicitly approved through the helper. Context-obsolete files stay in place but do not count toward completion or appear in `check`'s eligible path lists; report exclusions when reviewing an asset. Historical asset directories never expand the current eligible set. `check` returns an asset-to-list-of-approved-paths mapping, not one chosen default. Downstream FCPX intake and audition assembly are outside this stage.

```text
python "<helper>" check --project-root "<real-project-root>"
python "<helper>" complete --project-root "<real-project-root>"
```

`complete` supplies stage-owned evidence to the shared recorder. Generic inventory-only completion is refused. Never author `DONE` directly or bypass human approval. Status watches the current registry pair and complete nested asset inventories, including additions/removals, and exact video identity. It can detect a modified approved movie even if its size and modification time are preserved.

## Reruns, preservation and safety

- Reuse current assets and approvals. An unrelated registry change may invalidate the stage marker without requiring every animation to be regenerated.
- Preserve briefs, human edits, source, references, sketches, object candidates, shared library versions, snapshots, previous renders and `APPROVED/`. Resume the recorded interactive handoff without replaying completed work or creating an automatic first cut. This stage has no automatic project artifact deletion or regeneration sweep.
- Keep mutations and rendering serial in this initial stage. Do not parallelize helper calls or delegate workers with shared write access. Shared publication, logs and completion remain coordinator-owned.
- Record attempts, failures, revisions and handoffs in the normal append-only stage `LOG.md`, using maintained helpers. Do not rewrite logs or fabricate completion evidence.
- Generated TypeScript is executable code. The renderer does not sandbox composition code or constrain Python/Node filesystem access. Kilo permissions are not an OS security boundary. Use reviewed code, project-specific access, approval for dependency changes and an OS-isolated renderer when stronger containment is required.
- The pinned patched Zod override differs from Remotion's expected version. Prefer Remotion `InteractivitySchema` controls or inferred composition props; do not assume untested Zod integrations are compatible.

## Verification

Run `python -m unittest discover -s "<skill-root>/scripts" -p "test_*.py"` for lifecycle and approval checks. From `renderer/`, run `npm test` and `npm run test:smoke`; the latter renders and probes an isolated temporary clip, verifies non-overwrite behavior, and removes its test files. Tests must never approve or mutate a connected production project.
