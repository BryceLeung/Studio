# Studio

Studio's workflow definitions live under `.agents/skills/studio/`. The native
Python dashboard provides a container for project tools; **Line!** is its first
tool, for mapping transcript sentences to approved script sentences.

## Start the dashboard

The local environment is `.venv-dashboard`. From this repository on macOS:

```sh
.venv-dashboard/bin/python -m studio
```

To open a specific synced project:

```sh
.venv-dashboard/bin/python -m studio gui --project "01-Love Compounds Too"
```

Use **Project → Available Projects** or **Open Project** to choose a project.
Projects are discovered through `Connected Projects/` and the usual macOS and
Windows OneDrive locations. An explicit full project path also works. Selecting
a workspace link resolves it to its OneDrive destination.

For a fresh environment, use Python 3.9 or newer (3.11 or newer recommended):

```sh
python3 -m venv .venv-dashboard
.venv-dashboard/bin/python -m pip install --upgrade pip
.venv-dashboard/bin/python -m pip install -e .
```

On Windows, substitute `python` for `python3` and
`.venv-dashboard\Scripts\python.exe` for `.venv-dashboard/bin/python`.
The GUI uses PySide6 and the operating system's desktop window system.

## Review with Line!

1. Load an approved Word script. Only the content after **Spoken script** becomes
   sentence rows; the title and filming instructions are excluded.
2. Load a word-timestamped transcript JSON from `3-Post Production/2-Transcribe/`.
   Existing WhisperX files are supported; source text and files remain intact.
3. Select one script row and one transcript row. **Link** infers quality:
   identical text is **Exact** (green), shorter transcript text is **Flub** (red),
   and other text of equal or greater character length is **Partial** (yellow).
   Comparisons preserve case, punctuation, and spacing. A script sentence can
   have many readings; each transcript sentence links to at most one script sentence.
4. The same button becomes **Unlink** when the transcript sentence has a link.
   **Discard** excludes a transcript sentence and removes its link; the button
   becomes **Restore** for discarded sentences. Link and Discard advance to the
   next transcript sentence, stopping at the end. Unlink and Restore stay put.
5. Save a mapping with a name. Every save creates a new numbered JSON version.
   Load an existing mapping to continue reviewing it.

Closing an individual source unloads its panel while retaining the current
mapping. Reopening the same source restores it. **New Review** explicitly
clears both sources and the mapping; unsaved work prompts for save/discard/cancel.
Changing projects or closing the window also offers to save unsaved work.

Coverage uses the best established link for each script sentence:
exact → partial → flub → missing. Older saved `good` links load as `partial`.
Missing means no link has been established.
Transcript review states are linked, discarded, and unreviewed. Source offsets
remain available in mappings; audio playback and algorithm comparisons are
later additions.

Use **Line! → Keyboard mappings…** to edit shortcuts. Changes take effect
immediately and persist across restarts. Clear a field to disable its shortcut,
or choose **Restore defaults**. Defaults are:

| Control | Key |
| --- | --- |
| Up / down on script | A / Z |
| Link / unlink | Space |
| Up / down on transcript | K / M |
| Discard / restore | Backspace |

## Project state

All project files created by the dashboard are under the project's OneDrive
`studio/` directory:

```text
01-Love Compounds Too/
├── 1-Pre Production/…
├── 2-Production/…
├── 3-Post Production/…
└── studio/
    ├── dashboard.lock
    ├── .dashboard.lock.guard
    ├── LOG.md
    └── line/
        ├── Love Compounds Too.v001.json
        └── Love Compounds Too.v002.json
```

The log records meaningful action names, UTC times, success/failure, and a short
description supplied by each tool. Line descriptions include loaded filenames and
sentence counts, one-based sentence numbers for edits, link classifications, and
the actual versioned path returned by saves. Full inputs and returned state are
not written to the log. Actions without a formatter use a compact generic summary;
each summary is limited to 600 characters. Frequent selection/navigation actions
do not fill the log. Mapping files contain source
paths and SHA-256 fingerprints, sentence IDs, words and offsets, links, and
discarded IDs. Inputs are verified when loading or saving mappings so changed
source files cannot silently reuse an old review. See
[the mapping contract](studio/tools/line/docs/line-mapping.md).

The dashboard holds a project lock while open. Another local process is refused;
a confirmed dead local process can be recovered automatically. A lock from
another machine is refused until that dashboard closes and OneDrive syncs.
OneDrive file synchronization cannot guarantee atomic locking between machines,
so wait for sync before opening a project on another machine. Graceful shutdown
removes `dashboard.lock`; `.dashboard.lock.guard` stays as local lock infrastructure.

## Shared actions and CLI

GUI controls, shortcuts, and CLI calls dispatch the same registered functions.
List projects and action signatures:

```sh
.venv-dashboard/bin/python -m studio projects
.venv-dashboard/bin/python -m studio actions
```

For example, create a versioned link from two selected rows:

```sh
.venv-dashboard/bin/python -m studio action line.link \
  --project "01-Love Compounds Too" \
  --script "1-Pre Production/1-Script Review/Approved/Love Compounds Too.docx" \
  --transcript "3-Post Production/2-Transcribe/Love Compounds Too__C01__lav.json" \
  --script-line 1 --transcript-line 3 \
  --save "Love Compounds Too"
```

The line numbers in this example are illustrative; review the content before
using the result as a benchmark. Pass `--mapping studio/line/<name>.v001.json`
to start with an existing mapping. CLI state is temporary unless `--save` or
`line.save` is requested. CLI actions also acquire the project lock; close its
dashboard before running a separate CLI writer.

Python callers register the actions once and retain their own in-memory state:

```python
from pathlib import Path
from studio.actions import invoke
from studio.project import Project, ProjectLock
from studio.tools import register_actions
from studio.tools.line.mapping import new_state

register_actions()
project = Project(Path("/path/to/OneDrive/Books/Parent Like a Millionaire/Marketing/Studio/01-Love Compounds Too"))
state = new_state()
with ProjectLock(project) as lock:
    invoke("line.load_script", project, state,
           path="1-Pre Production/1-Script Review/Approved/Love Compounds Too.docx",
           _lock=lock)
```

## Repository layout

```text
studio/
├── __main__.py       # GUI/CLI entry point
├── dashboard.py      # Native project window and tool container
├── project.py        # OneDrive discovery and project locking
├── session.py        # Shared project open/close actions
├── actions.py        # Action registry and append-only logging
└── tools/
    └── line/
        ├── ui.py     # Native sentence panels and controls
        ├── actions.py
        ├── sources.py
        └── mapping.py
```

Add tools to `studio/tools/` and register their widget in `studio/tools/__init__.py`.
Keep their project state in a sibling directory of `studio/line/` in OneDrive.
