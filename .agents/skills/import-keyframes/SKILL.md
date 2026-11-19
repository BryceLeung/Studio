---
name: import-keyframes
description: Import a labelled contact sheet (3 columns by 2 rows by default) into individual keyframe PNGs when the user says "import keyframes". Read filenames from labels with local OCR, skip empty slots, and validate visibility and transparency before publishing.
---

# Import Keyframes

Use this skill for an explicit `import keyframes` request. Resolve the supplied sheet and its owning character from the current context. Use [Studio](../studio/SKILL.md) project resolution rules for connected project assets. Ask only if the exact source or owning character is ambiguous. This is a local image import, not a numbered stage run, render, library promotion, or approval.

## Standard Location

Use the owning character's `contact sheets/` folder under the project's shared animation library. Keep source sheets directly in `2-Production/3-Animations/ASSETS/CHARACTERS/<character>/contact sheets/` and write cropped PNGs to a new `<batch_slug>_import/` subfolder there. Use a short, descriptive lowercase snake_case batch slug, such as `happy_flap_import` for the happy-flap poses. If that folder already exists, use the next unused numbered suffix (for example, `happy_flap_import_02`) rather than mixing batches or overwriting files. If a sheet is supplied elsewhere, read it in place and use the same standard output location; do not move or rename existing sheets or imports.

Do not import directly into an animation's `references/`, an approval folder, or the character's main art folder. If an animation later uses selected poses, copy those poses into its `references/` as part of assembly so the composition does not depend on live shared files.

## Command

Requires Python 3.10+, Pillow, and Tesseract with English language data. On Windows, prefer the skill-local `.agents/skills/import-keyframes/.venv-tools/Scripts/python.exe` when present; the importer checks the skill's `.venv-tools/Tesseract-OCR/tesseract.exe` before searching PATH. Otherwise use the shared `.venv-tools/Scripts/python.exe` with Tesseract on PATH. On macOS, use the command below with Tesseract on PATH. Run from the workflow root. Do not install missing dependencies on an import request.

```sh
"./.venv-tools/bin/python" "./.agents/skills/import-keyframes/scripts/import_keyframes.py" "<sheet image>" --output-dir "<project-root>/2-Production/3-Animations/ASSETS/CHARACTERS/<character>/contact sheets/<batch_slug>_import" --dry-run
```

1. Dry-run first. The importer expects a single image with an equal 3-column, 2-row grid by default (slots 1-3 on top and 4-6 below), with a single `.png` filename label beneath each populated drawing. For older 2-column, 3-row sheets, pass `--grid 2x3` on both the dry run and the actual import. It reads the labels locally, removes the label areas, and skips unlabeled blank slots; visible art without a readable, unambiguous label is an error. Compare the OCR names with the sheet (and, when available, the filenames in the `draw keyframes` prompt). Do not silently guess spelling or import an incorrect name.
2. The importer rejects invisible artwork, duplicate or unsafe names, existing output files, and crops without usable transparency. It checks actual pixel alpha, including clear upper margins, rather than just whether an alpha channel exists. It flags possible clipping; it does not guarantee a clean matte, remove a background, or judge the poses. `--allow-opaque` is only for explicitly requested reference crops and preserves the background with a warning; never use it to pass sprites off as compositing-ready.
3. If the batch passes and the user requested an actual import, rerun without `--dry-run`. Preserve the source sheet and review the outputs. Report written filenames, empty slots, and warnings. If publishing under a watched Studio project path, use the common mutation/invalidation and append-only logging workflow for the owning substage. Imports do not complete the stage. On a failed check, report the issue without writing the batch.
