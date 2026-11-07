---
name: studio-animation-save-object
description: On an explicit save request, copy a selected Studio 2.3 object candidate into the versioned shared ASSETS library without overwriting candidates, changing animations or approving videos.
---

# Save Animation Object

## Scope and gate

Use only when the user explicitly asks to save a selected character, background or object to the shared animation library. Positive feedback, rendering success and instructions to move to the next animation are not save requests. Read the parent [Animations skill](../SKILL.md) and follow normal project resolution, prerequisite validation, invalidation, permissions and append-only logging rules. There is no `animations.py` library-save command; do not invent one or bypass filesystem approvals.

## Save workflow

1. Resolve the selected `candidates/<object-name>/vNNN/` within the current animation. Check the actual source/resources and provenance, not just a displayed preview. If the chosen candidate or object is ambiguous, obtain the user's selection before writing. A rendered preview is optional for saving editable artwork; never fabricate a visual review or render provenance.
2. Select the appropriate stage-local category: `ASSETS/CHARACTERS`, `ASSETS/BACKGROUND` or `ASSETS/OBJECTS`. Retain an established name/category unless the user asks to change it. New object names must be single safe directory names. Keep this library separate from the project's root-level `Assets/`.
3. Allocate the next unused `ASSETS/<category>/<name>/vNNN/` version, above all existing versions. A library version is independent of its originating candidate's version. Never overwrite, merge into, move or delete an existing candidate or library version. Keep operations serial and refuse collisions. If a prior save already contains this exact candidate and unchanged content, report and reuse that saved version rather than creating a duplicate.
4. Copy the selected candidate's authored source, settings, local resources, provenance, licensing and available preview into new independent files under the chosen library version, using permitted filesystem operations. Reject symlinks/hardlinks or path escapes that alias canonical files or outside resources. Do not copy unrelated animation files, approval directories or completion markers. Preserve the candidate unchanged.
5. Add save provenance to the new library version's `provenance.json`: selected project-relative candidate path, originating registry ID, destination library version, explicit user save instruction and copied file inventory. Retain original request, parent-version and attribution information. Use portable project-relative paths, not absolute machine paths. This working provenance does not confer stage completion or approval.
6. Verify the copied file inventory and content against the selected candidate, accounting for the intentional provenance addition. Confirm local imports/resources remain self-contained. Publish no successful-save claim until the copy is complete and checked. If copying fails, preserve and report the partial version as incomplete in the stage log; do not treat it as reusable or merge a retry into it. Retry to a new unused version.
7. Append the selected candidate, destination, verification result and next handoff to the stage `LOG.md` through the maintained logging helper. Return the saved path and stop. Do not automatically assemble, render, update consumers or advance to the next animation.

## Reuse and completion

Success is a verified independent library copy with traceable provenance, while the original candidate and earlier library versions remain unchanged. No render or `DONE` is required or written by this skill. A saved object is not an approved final animation.

When the user later requests assembly, freeze the selected library version's resources into the consuming animation's `references/` and code into its own `src/`. Record the selected library version; never use live imports, aliases or mutable links into the library. Updating the library must not silently change existing animations. Rendered videos still require the parent stage's registered render workflow and human `APPROVED/` gate.
