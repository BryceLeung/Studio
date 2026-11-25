---
name: studio-animations
description: Run or resume Studio substage 2.3 interactively, discussing one registry asset at a time, sketching fast image previews, rendering Remotion candidates, saving reusable objects on request, storing requested generated sound effects in the shared sounds library, and waiting for human-approved videos.
---

# 2.3 - Animations

Use registry IDs as supplied: new projects use `A01`-`A99`; existing projects keep their legacy `ASSET-000001` IDs. Both formats are supported in asset arguments and folder names. Never renumber existing assets.

## Purpose and inputs

Create rendered videos for every active, kept `Graphic` or `Animation` in the current 1.4 Asset Registry. Use the canonical Markdown and JSON together, through the maintained registry parser. The approved Asset/Brief is the initial prompt; Why/Notes, linked script passages and extracts supply context. Exclude other types, rejected and inactive rows. The machine-readable dependency and output contract is [dependencies.json](dependencies.json).

Create animation candidates through authored animation techniques such as React, SVG, CSS and Remotion, including characters, backgrounds and objects. AI image generation and editing are permitted for static sketch exploration and design references through the sketch skill. Render requests do not authorize new image-generation calls or AI-generated video; they use the selected design and prepared resources through the authored Remotion workflow.

Quick sketches are static images, not animation renders. A connected image-generation/editing tool may be used for concepts and reference-guided revisions; direct programmatic drawing such as Pillow remains available for suitable tasks. Tool setup and credentials are not implied by this permission. Keep the exact prompt, tool/model when known and input references with each sketch version. An accepted AI sketch still needs explicit preparation into usable animation assets; do not promise that a flat image is automatically a layered rig.

This branch depends on 1.4 and is a prerequisite of First Cut (3.6), not of B-roll, footage intake, or earlier presenter postproduction stages. A missing or stale prerequisite blocks execution. A valid approved registry with no eligible assets is a successful empty selection, not a reason to invent graphics.

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
    SOUNDS/
  A10/
    Animation.json
    request.01.md
    settings.json
    package.json
    tsconfig.json
    scripts/
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

### Asset-specific scripts

Always create project-specific preparation, assembly, revision, verification and save helpers in the owning asset's `scripts/` directory in the resolved OneDrive project, such as `2-Production/3-Animations/A04/scripts/`. Helpers specific to a shared-library character, background, object or sound belong in that library item's `scripts/` directory, such as `2-Production/3-Animations/ASSETS/CHARACTERS/trump_girl/scripts/`. Create the directory only when a helper is needed. This rule applies to all script languages and subordinate sketch, render and save workflows; do not create new executable helpers in candidate versions or the workflow repository for asset-specific work. Preserve existing versioned script copies as provenance rather than moving or rewriting historical artifacts.

Only maintained, reusable workflow tooling belongs in this skill's repository `scripts/` directory. Project-local helpers must accept explicit paths for shared tooling they need, such as `--renderer-root`, rather than deriving the repository or renderer location from their own `__file__`. Pass `--project-root` explicitly and keep recorded project paths portable. Script placement does not bypass normal mutation, logging, rendering or approval gates.

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
8. Render an animation preview only when requested, using the maintained render workflow below. Inspect the work, follow the review handoff below, then stop for feedback. Continue revisions interactively. Move to the next animation only on the user's explicit instruction, not because a render succeeded or an object was saved. An instruction to move on is not final video approval; preserve pending approval status and the existing completion gate.

## Generated sound effects

- When the user requests a generated sound effect, check the stage-local `ASSETS/SOUNDS/` library for relevant existing versions. Publish every newly generated effect as a WAV in `ASSETS/SOUNDS/<sound-name>/vNNN/`, even if it was requested for only one animation. Do not leave the only copy in an animation's `references/`. Use a descriptive, portable name and a new version for revisions; never overwrite existing versions. Record how the sound was generated and any inputs or licensing that apply.
- The generation request authorizes this library write without a separate save request. It does not authorize adding the effect to a composition unless requested, nor does it authorize a video render or approval. The explicit save gate for visual objects is unchanged.
- When adding a cue to an animation, copy the selected version into that animation's `references/`, record the source library version, and use the local copy via `staticFile()`. Never reference the shared library live. Leave existing effects, snapshots and approvals in place unless the user requests a migration.

## Review handoff

Use this rule when handing back authored or revised Remotion work, including isolated object candidates. It does not apply to text proposals, scaffold setup, static sketches, save-only actions or status checks.

**Every animation change without a new render requires a live Remotion Studio handoff by default.** Start the [single review server](#opening-remotion-studio), or reuse it if it already serves the exact updated composition. If another review is active, follow the chat prompt flow below instead of switching automatically. Verify that the updated composition has built and that the server still serves it immediately before sending a ready link. Label that clickable link with the asset ID. Do this without waiting for a separate preview request and without rendering an MP4 unless the user chooses Background Render.

Use the same Studio handoff after a render unless the user chose Background Render or Abort. Background Render is an MP4-only handoff; never open or switch Studio for it. Otherwise, if Studio cannot be verified for the exact composition, report the blocker. A successful build alone, an earlier MP4, or a link to another composition does not satisfy the live handoff.

If an MP4 is available, also link to that exact playable file, including an inspection render or a reused current render. Keep it available and label inspection-only or isolated videos as such. A link is not registration or approval. If only a historical MP4 is available and the matching composition cannot be served, link the MP4 and explain why Studio was not switched.

**Every MP4 presented in chat MUST have a clickable Markdown playback link to that exact file.** This applies to new renders, existing or approved videos, inspection previews, isolated candidates, and read-only requests such as showing the latest version. In Kilo/VS Code, use a verified loopback HTTP URL. A `file://` URI, bare path, or code span does not satisfy this requirement. Presenting an existing MP4 does not authorize a new render or require switching Remotion Studio.

1. Locate the exact MP4 and confirm its current path, including `APPROVED/` if approval moved it. Serve from the narrowest media-only directory containing the file. Python's static server exposes all contents of that directory, so do not point it at a project root or a directory containing source, other non-media files, or links outside it. If the directory is unsuitable, use a single-file serving method or report that playback could not be served safely.
2. Start a dedicated tracked **session-scoped** background process for each distinct MP4, even if another video in the same directory already has a server. Label it with the asset and version and bind it to `127.0.0.1` only. Use port `0` to let the OS allocate a free port and read the actual port from the startup output. Within the same session, reuse a server only for the exact MP4 it was launched for. Never stop, repurpose, or reuse another video's or another session's server to present a new video. Keep each process running after the reply; use the background process tool's default lifetime (no `persistent` or `inherit`) so it is automatically closed when its owning session ends. This is separate from the Remotion Studio server on port `3000`.

   ```text
   python -u -m http.server 0 --bind 127.0.0.1 --directory "<exact-media-directory>"
   ```

   Use `python3` if that is the local Python command. No dependency installation is needed.
3. Verify that the listener belongs to the tracked server and that the exact URL returns HTTP 200 with `Content-Type: video/mp4` and a `Content-Length` matching the selected file (for example, with `curl -I`, or `curl.exe -I` on Windows). A response from another listener or for another `v001.mp4` is not verification. Percent-encode special characters in the filename. If a verified clickable link cannot be provided, report the blocker instead of presenting a path or unverified URL as playback.

In the final chat reply, use normal Markdown links. MP4 playback links expire when their owning session ends; if a user asks to see the same video in a new session, start a new server and verify its URL. For Studio, link the separately verified HTTP URL printed by the launcher. Replace these illustrative targets with the actual ones:

```markdown
[Watch A13 v001 (MP4)](http://127.0.0.1:8765/v001.mp4)
[Open the current Remotion review](http://localhost:3000)
```

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

Run `scripts/review_studio.py` from the workflow root with the selected stage interpreter in a tracked persistent background process labeled `Studio 2.3 review`. Chat replies are the default. Set `ready.pattern` to `Studio review` so the tool returns on either `Studio review ready:` or `Studio review waiting:`. Do not wait exclusively for `Studio review ready:`: the user may need to make a choice before the server can start. Inspect the returned output or the tracked process's logs before continuing. The process card is not an input terminal.

```text
python "./.agents/skills/studio/2-Production/3-Animations/scripts/review_studio.py" --project-root "<real-project-root>" --asset-id A13 --candidate "529-girl-power-up/v012"
```

If the process reports `Studio review waiting:`, use Kilo's `question` tool in the same turn. Name the requested review and, if known, the current review. Offer these choices in this order:

1. **Take Over** (`t`): replace the managed review after the current operation finishes.
2. **Background Render** (`b`): render the exact requested target without opening or switching Studio; return a clickable MP4 link when ready.
3. **Abort** (`a`): cancel only this request, leaving the active review alone.

The pending request waits for a choice even if Studio becomes free. Do not move on or ask the user to type into the process card. Send the selection to the same pending request using the handle printed after `Reply handle:` (omit the terminating period):

```text
python "./.agents/skills/studio/2-Production/3-Animations/scripts/review_studio.py" --reply "<handle>" <t|b|a>
```

Do not include the handle in the chat question. After a reply, check the same tracked process for ready, aborted, cancelled for Background Render, or takeover queued. If the process no longer accepts a reply, check its output before doing anything else. Do not start a duplicate or send `--stop` in response to a failed reply. If an accepted Take Over is still waiting, report it as pending, not ready. If the question tool is unavailable, ask for the choice in chat and continue with the same tracked process when the user responds.

After `Studio review cancelled for Background Render.`, start one **session-scoped** tracked process (not persistent) with the original target arguments and `--background-render`. Use `Studio playback ready:` for readiness and allow time for the render. Keep this same process running: it serves only the verified MP4 on a dedicated loopback port and prints the URL for a clickable Markdown link. If rendering is still in progress, follow that same process until ready; do not restart it or open Studio. Full animations use the maintained registered render workflow (including reuse). Candidate renders are inspection-only, use a temporary copy of the exact candidate, never replace its existing preview, and expire with the playback process.

Omit `--candidate` only for a full animation. The helper verifies the target, reuses the same managed review on port `3000` or starts it when the port is free, and prints the verified URL when ready. Use `--terminal` only in a real foreground terminal to get the keyboard menu; Background Render from that menu serves playback until the foreground process ends. Use `--stop` by itself to explicitly release the managed review when done. The helper does not open a browser, install dependencies, or stop unmanaged listeners.

If a legacy review still occupies port `3000`, identify it by its tracked `Studio 2.3 review` label and verified project source, then stop it at its source once. Do not stop an unrelated process or fall back to another port. Use the Python helper for new launches, not the legacy `animations.py studio` command.

Studio edits save directly to source and make older renders differ from the working version, without revoking their approvals. Test the generated controls by changing and resetting one value, and check timeline timing after restructuring. Keep previous candidates and snapshots. A manual Studio export counts for completion when the human places the chosen full-animation MP4 in `APPROVED/`; it does not gain render provenance.

For graphs and charts, use supplied facts or explicitly agreed assumptions. Identify illustrative data and return assumptions. Do not invent financial outcomes, imply guaranteed growth or disguise a conceptual curve as measured data.

## Iterative chat revisions

Record each substantive requested change before editing the composition:

```text
python "<helper>" revise --project-root "<real-project-root>" --asset-id A10 --request "Slow the deposits, retain the piggy bank longer, and simplify the graph labels."
```

Use `--request-file` for longer exact requests. The helper preserves numbered `request.NN.md` history. Update only the source, resources or settings requested by the user; render the next version only when requested. Sketch-only changes use the sketch skill's lightweight image versioning and log record, not canonical composition revisions or render snapshots. Object-only changes follow the candidate versioning and explicit save gates above. Preserve sketches, prior render snapshots and candidate videos; do not overwrite a version to conceal iteration. Keep production refinements local to 2.3; reconcile a changed fundamental purpose, type or source applicability through 1.4 instead of silently changing its approved request.

A creative revision to requests, source, settings, resources or the renderer does not revoke earlier approvals. For registered provenance checks, compare each render with its immutable snapshot, not the latest working composition. If the registry brief or linked passage changes, flag it for human review. The folder-based completion check cannot establish whether an existing approved video matches a changed brief. Never move or delete earlier approvals automatically.

## Human approval and completion

Positive human feedback such as "I like this animation" or "approve this" about the unambiguously selected full-animation preview authorizes moving that exact registered MP4 into the asset's `APPROVED/`. Resolve an ambiguous asset or version before acting; never guess the latest render. This does not apply to sketches, isolated object candidates, unregistered Studio exports, technical render success or an instruction to move on. Approval does not render, save objects, advance to the next asset or complete the stage automatically.

Use the maintained helper with the exact render number and verbatim feedback:

```text
python "<helper>" approve --project-root "<real-project-root>" --asset-id A10 --version 3 --request "I like this animation"
```

The helper moves `renders/v003.mp4` to `APPROVED/v003.mp4`, preserves snapshots and other approved versions, and records the decision, timestamp, render identity and registry context in `Animation.json` and the stage log. It never overwrites a destination. Repeated approval of the same version and context is a no-op. Explicitly reapproving an older version after compatibility review records acceptance of the current registry context without altering its original render provenance. Files can be renamed; exact-byte matching identifies their render. Existing manually moved approvals remain supported, using their original render registry context when no chat approval record exists.

Every currently eligible asset must have at least one non-empty, regular MP4 directly in its `APPROVED/` folder. The human's placement of the chosen MP4 is the approval signal. Registration, snapshots and playback probing are not completion requirements. Multiple approved files and a valid empty registry selection are allowed. `check` returns the approved paths for each eligible asset. `check-provenance` is an optional, stricter diagnostic for registered renders and does not affect `DONE`. Downstream FCPX intake and audition assembly are outside this stage.

```text
python "<helper>" check --project-root "<real-project-root>"
python "<helper>" complete --project-root "<real-project-root>"
```

`complete` supplies stage-owned evidence to the shared recorder. Never author `DONE` directly or bypass human approval. Status watches the current registry pair and the approved MP4 paths and byte hashes, including additions, removals and replacements. It does not scan working compositions, renderer caches, candidate files or render snapshots for completion.

## Reruns, preservation and safety

- Reuse current assets and approvals. An unrelated registry change may invalidate the stage marker without requiring every animation to be regenerated.
- Preserve briefs, human edits, source, references, sketches, object candidates, shared library versions, snapshots, previous renders and `APPROVED/`. Resume the recorded interactive handoff without replaying completed work or creating an automatic first cut. This stage has no automatic project artifact deletion or regeneration sweep.
- Keep mutations and rendering serial in this initial stage. Do not parallelize helper calls or delegate workers with shared write access. Shared publication, logs and completion remain coordinator-owned.
- Record attempts, failures, revisions and handoffs in the normal append-only stage `LOG.md`, using maintained helpers. Do not rewrite logs or fabricate completion evidence.
- Generated TypeScript is executable code. The renderer does not sandbox composition code or constrain Python/Node filesystem access. Kilo permissions are not an OS security boundary. Use reviewed code, project-specific access, approval for dependency changes and an OS-isolated renderer when stronger containment is required.
- The pinned patched Zod override differs from Remotion's expected version. Prefer Remotion `InteractivitySchema` controls or inferred composition props; do not assume untested Zod integrations are compatible.

## Verification

Run `python -m unittest discover -s "<skill-root>/scripts" -p "test_*.py"` for lifecycle and approval checks. From `renderer/`, run `npm test` and `npm run test:smoke`; the latter renders and probes an isolated temporary clip, verifies non-overwrite behavior, and removes its test files. Tests must never approve or mutate a connected production project.
