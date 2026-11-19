---
name: import-keyframes
description: Import a labelled 2-column by 3-row contact sheet into individual keyframe PNGs when the user says "import keyframes". Read filenames from labels with local OCR, skip empty slots, and validate visibility and transparency before publishing.
---

# Import Keyframes

Use this skill for an explicit `import keyframes` request. Resolve the supplied sheet and the intended output location from the current context. Use [Studio](../studio/SKILL.md) project resolution rules for connected project assets. Ask only if the exact source or destination is ambiguous. Import to a new working folder, not into approvals or over existing character art. This is a local image import, not a numbered stage run, render, library promotion, or approval.

## Command

Requires Python 3.10+, Pillow (included in the shared `.venv-tools`), and Tesseract with English language data on PATH. Do not install missing dependencies on an import request. On macOS, run this from the workflow root (on Windows use the shared `.venv-tools/Scripts/python.exe`):

```sh
"./.venv-tools/bin/python" "./.agents/skills/import-keyframes/scripts/import_keyframes.py" "<sheet image>" --output-dir "<working folder>" --dry-run
```

1. Dry-run first. The importer expects a single image with an equal 2-column, 3-row grid, in reading order, with a single `.png` filename label beneath each populated drawing. It reads the labels locally, removes the label areas, and skips unlabeled blank slots; visible art without a readable, unambiguous label is an error. Compare the OCR names with the sheet (and, when available, the filenames in the `draw keyframes` prompt). Do not silently guess spelling or import an incorrect name.
2. The importer rejects invisible artwork, duplicate or unsafe names, existing output files, and crops without usable transparency. It checks actual pixel alpha, including clear upper margins, rather than just whether an alpha channel exists. It flags possible clipping; it does not guarantee a clean matte, remove a background, or judge the poses. `--allow-opaque` is only for explicitly requested reference crops and preserves the background with a warning; never use it to pass sprites off as compositing-ready.
3. If the batch passes and the user requested an actual import, rerun without `--dry-run`. Preserve the source sheet and review the outputs. Report written filenames, empty slots, and warnings. If publishing under a watched Studio project path, use the common mutation/invalidation and append-only logging workflow for the owning substage. Imports do not complete the stage. On a failed check, report the issue without writing the batch.
