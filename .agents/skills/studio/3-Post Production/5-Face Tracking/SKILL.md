---
name: studio-face-tracking
description: Run Studio substage 3.5, detecting the presenter's face in camera ranges used by approved short-form extracts and writing reusable source-time face paths.
---

# 3.5 - Face Tracking

Read the Studio root skill for project resolution, logs, completion, and
invalidation. This stage makes data for later portrait reframing; it does not
change 3.3 take mappings, 3.4 scores, or 3.6 First Cut projects.

## Inputs and scope

Use the approved shortlist from 1.3, latest reviewed 3.3 Takes files and their
word-timestamped lav transcripts, 3.1 Sync offsets, and 2.1 camera footage.
1.1 approval and the relevant upstream `DONE` files must validate. Do not use
3.4 rankings: analyze **every mapped take alternative** overlapping approved
extract passages, except script-side discarded groups. Match passages verbatim
and in unique source order; an extract that crops a reviewed script group or
has a group with no mapped take requires review. Convert each take's lav times
to camA/camB source times using the signed 3.1 offsets; merge overlapping
camera intervals. Analyze both angles even when the initial cut prefers one.
Do not scan the rest of the recording. An approved shortlist with no approved
extracts has a validated no-op output; it needs no face model.

## Setup and run

Use only `.venv-tools/` inside this substage's repository directory:
`.agents/skills/studio/3-Post Production/5-Face Tracking/.venv-tools/`.
Create it with Python 3.10+ (3.13 recommended) and install this stage's
`requirements.txt`. Do not use the dashboard, Sync, home-directory, or tmp
environments. The command automatically relaunches with the stage-local Python
before importing stage dependencies, or stops with setup instructions if absent.
On macOS/Linux, setup and execution are:

```text
python3.13 -m venv "<stage-repo-directory>/.venv-tools"
"<stage-repo-directory>/.venv-tools/bin/python" -m pip install -r "<stage-repo-directory>/requirements.txt"
"<stage-repo-directory>/.venv-tools/bin/python" "<stage-repo-directory>/scripts/face_tracking.py" --project-root "<real-project-root>"
```

On Windows, use an available Python 3.10+ interpreter for creation and
`.venv-tools/Scripts/python.exe` instead of `.venv-tools/bin/python`.
`<stage-repo-directory>` is the repository directory containing this skill,
not a OneDrive project folder. Reuse the existing environment on subsequent
runs; do not recreate it or reinstall packages unless dependencies need repair.
Both the environment and its model cache are Git-ignored.

The first run downloads OpenCV Zoo's MIT-licensed YuNet 2023mar ONNX model from a pinned
revision into `.venv-tools/model-cache/face_detection_yunet_2023mar.onnx`
after SHA-256 verification; subsequent runs use the cached model offline.
Video is never sent to a remote service. A bad
model, unavailable decoder, unsupported source, or failed timestamp validation
stops the stage instead of producing misleading paths. There are no alternative
model-cache locations: `LOCALAPPDATA`, `XDG_CACHE_HOME`, `HF_HOME`, and `TMPDIR`
do not select the model path. Partial downloads are created in that same local
model-cache directory. Do not search or migrate old external caches automatically.
Deleting `.venv-tools/` also deletes its cached model.

The script samples decoded camera frames at about 4 Hz, runs face detection
on a width-at-most-640 preview, and follows the solo presenter's face by
position and size. Coordinates refer to the decoded image, normalized to
0..1. Each frame's source-media `time_ms`, normalized `[center_x, center_y,
width, height]` box or null, detection confidence or null, and status
(`tracked`, `held`, `centered`) are preserved. A missing face holds the last
position within the current take interval; if none exists, it remains centered.
Warnings list every non-tracked sample. The source media remain unchanged.
OpenCV timestamp/seek semantics should be reviewed on unusual VFR or rotated
sources before treating these paths as approved portrait crops.

## Outputs and completion

Normal project execution validates prerequisites through the shared JSON graph and submits its path/index manifest to the common `record_done` API. The stage retains its required-range and saved-path checks, including the approved no-extract case. Do not write `DONE` by hand.

Write one `<Script>__C<nn>__camA__FacePath.v1.json` and corresponding camB JSON
for each camera source used by an approved extract, in the project's OneDrive
`3-Post Production/5-Face Tracking/`. Every JSON records source-second ranges
as millisecond pairs, camera role, media identity (size and whole integer `mtime_s` seconds), declared
input/dependency fingerprints, algorithm sampling/padding and verified model
SHA-256, dimensions, face samples, and warnings. `Face Paths.json` inventories
the exact path files (also written for the no-extract case). The append-only
`LOG.md` records each attempt, outcome, and next review action.

`DONE` requires validated upstream files, full extract/take/angle coverage,
persisted non-empty paths for every required source and a fingerprinted index.
Warnings from zero or uncertain face detections do not prevent computational
completion; they require review when constructing the portrait edit. A missing
model, invalid input, or unreadable required frames prevents completion.
The script reuses unchanged existing path files, refuses changed files instead
of overwriting them, checks input/source/dependency fingerprints, and
archives dependent `DONE` markers through the shared graph on changed completion or failure. The
no-extract condition writes an index and `DONE` without a detector download.
Validate this stage's `DONE` before starting 3.6 First Cut. Face framing is
independent of 3.4 take selection.

Media identity comparisons use byte size and whole modification seconds; saved
legacy `mtime_ns` values are normalized with integer division by 1,000,000,000.
Only media identities in saved headers/indexes are normalized. Generator and
other dependency SHA-256 checks remain exact and are never bypassed.

Use `--regenerate` only after deliberately choosing replacement of stale saved
paths. Required analysis is prepared before archiving the selected old path/index
files into the pending `STALE/N/` slot. Valid unchanged paths remain in place;
camera originals, approved shortlist, Takes reviews, and approved movies are
never moved. Failure alone invalidates markers, not work files.

Tests:

```text
"<stage-repo-directory>/.venv-tools/bin/python" -m unittest discover -s "<stage-repo-directory>/scripts" -p "test_*.py" -v
```
