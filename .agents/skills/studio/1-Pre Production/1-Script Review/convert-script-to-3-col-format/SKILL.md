---
name: studio-convert-script-to-3-col-format
description: Convert a Studio stage 1.1 Word script to ID, Spoken Dialog, and Stage Directions columns when asked to "convert script to 3-col format", without running the whole stage or approving the result.
---

# Convert Script to 3-Col Format

This is a conversion-only action within stage 1.1, not a new numbered substage. Trigger on `convert script to 3-col format` or `studio convert script to 3-col format`, with the source script, project, and optional output filename supplied in the request or established context. Creating or discussing this skill does not authorize conversion of project documents.

Read the [Studio root skill](../../../SKILL.md) for real OneDrive project resolution and the [Script Review skill](../SKILL.md) for the canonical layout and revision rules. Do not invoke `studio run 1.1`, start the full script-review workflow, write new dialogue, or advance to another stage. Use the current agent's normal permissions; this action does not implicitly create an Agent Manager session or grant runner permissions.

## Resolve the source

Resolve the selected project to its real OneDrive directory using the root skill's maintained resolver. Work with an existing `.docx` in `1-Pre Production/1-Script Review/`, normally a root-level candidate or an explicitly selected file in `DRAFTS/`. Honor an explicitly named file and version. If no source was named, use a root candidate only when exactly one exists; otherwise list the candidates and ask which script/version to convert. Never guess the latest revision, select a stored draft implicitly, or convert every draft as an implicit batch.

If the user explicitly selects a source in `Approved/`, keep it read-only and require an explicit output filename/path outside `Approved/`; ask for that destination if absent. Generated drafts belong in the stage root for pending review or in `DRAFTS/` for retention, never in approval or archive directories. Do not move, replace, or delete any source, approved document, or earlier revision. Creating an output beside a selected `DRAFTS/` source leaves it unapproved and outside the root review queue.

Conversion is allowed before stage 1.1 is complete and is not gated on its `DONE`. Do not require the whole stage to pass merely to format an unapproved draft.

## Convert with the existing writer

Use Python 3.9+ with the existing stage 1.1 `scripts/requirements.txt` dependencies. Do not install missing dependencies without normal permission approval or create another DOCX writer. From the workflow repository root, run the maintained wrapper, which reuses `write-script.py`:

```text
python3 "./.agents/skills/studio/1-Pre Production/1-Script Review/scripts/convert-script.py" "<real-source.docx>" --to three-column
```

For a requested output name or path:

```text
python3 "./.agents/skills/studio/1-Pre Production/1-Script Review/scripts/convert-script.py" "<real-source.docx>" --to three-column --output "<output.docx>"
```

Use the root skill's interpreter conventions on other platforms or with an existing virtualenv. Without `--output`, let the helper choose the next unused `<script>.v<N>.docx` beside the source. A bare output filename is relative to the source folder. Never overwrite an existing file, silently substitute another explicitly requested output, or write into `Approved/`.

The canonical columns are `ID` (7%), `Spoken Dialog` (65%), and `Stage Directions` (28%). Enter and Shift+Enter split logical paragraphs. Standalone bracketed directions attach to the preceding dialogue; leading directions attach to the first following dialogue. Strip only the outer square-bracket delimiters in the direction column, preserving inner brackets and formatting. One-column conversion through the parent skill restores the outer brackets. Inline bracketed phrases remain spoken. Assign sequential `L01`, `L02`, etc., in reading order. Preserve front matter, formatting, hyperlinks, and supported resources through the helper rather than reconstructing text. Unsupported structures or changed comment selections (other than direction delimiter removal/restoration) must fail explicitly, not be flattened or discarded.

## Verify and hand off

After a successful conversion, inspect the output with the maintained read-only reader:

```text
python3 "./studio/script_docx.py" "<real-output.docx>" --format json
```

Check the ordered dialogue and directions, sequential IDs, and source/output paths. The helper already verifies spoken content before publishing. A directions-only document may be converted without losing content, but report that it cannot satisfy the filming-script approval gate.

Append the conversion attempt and result to the stage's `LOG.md` using the root skill's `scripts/append-log.py`; the converter itself does not append logs. Report the new output path and that human review/approval remains separate. Do not move the output into `Approved/`, write or record `DONE`, invalidate completion, or regenerate downstream outputs. Any root `.docx` blocks stage 1.1 completion; only retained drafts inside `DRAFTS/` are excluded from completion checks. Do not silently move earlier revisions to clear that gate. Validation may report a prior completion stale while root candidates remain, but this conversion-only action does not archive or rewrite markers.

Each explicit conversion can create a new revision, including normalization of an edited three-column draft. On a retry, inspect any output already produced by that attempt before generating another version. Reuse a verified successful result rather than repeatedly invoking the converter. On failure, preserve existing files and report the diagnostic; do not repair unsupported Word structures or revise the script without authorization. One-column conversion remains available through the parent skill, but is not performed by this three-column action.
