# Line project format

Line! saves each review under its OneDrive project's `studio/line/` directory as
`<name>.v001.json`, `v002.json`, and so on. The save dialog suggests the loaded
script's file name suffixed with `__Takes` (for example `freedom-c01__Takes`),
which produces `freedom-c01__Takes.v001.json`. Each save creates a new file. A
Line project contains immutable source snapshots, editable word groupings, links,
and discarded transcript groupings. Source files are never edited.

## Schema

The schema is `studio.line.mapping`. Version `2` maps one script to one
transcript; version `3` supports multiple recordings (described below).
Every grouping, link, and discard uses an **inclusive,
zero-based word range**. The same link format applies to ordinary sentence-sized
readings and manually split or merged readings.

```json
{
  "schema": "studio.line.mapping",
  "schema_version": 2,
  "mapping_name": "freedom-c01",
  "version": 2,
  "created_at": "2026-10-12T21:00:00+00:00",
  "parent": "studio/line/freedom-c01.v001.json",
  "sources": {
    "script": {
      "source": {"path": "Approved/Script.txt", "sha256": "<SHA-256>", "segmentation_version": "line-sentences-v1", "kind": "script", "format": "text"},
      "title": "Script",
      "words": [
        {"index": 0, "text": "What", "separator": ""},
        {"index": 1, "text": "is", "separator": " "},
        {"index": 2, "text": "freedom?", "separator": " "}
      ],
      "rows": [{"id": "<script sentence ID>", "line": 1, "text": "What is freedom?", "word_start": 0, "word_end": 2}]
    },
    "transcript": {
      "source": {"path": "Transcript.json", "sha256": "<SHA-256>", "segmentation_version": "line-sentences-v1", "kind": "transcript", "format": "word-json"},
      "title": "Transcript",
      "audio": null,
      "words": [
        {"index": 0, "text": "What", "start_ms": 1000, "end_ms": 1100},
        {"index": 1, "text": "is", "start_ms": 1100, "end_ms": 1200},
        {"index": 2, "text": "freedom?", "start_ms": null, "end_ms": null}
      ],
      "rows": [{"id": "<transcript sentence ID>", "line": 1, "text": "What is freedom?", "word_start": 0, "word_end": 2, "start_ms": 1000, "end_ms": null}]
    }
  },
  "groupings": {
    "script": [{"word_start": 0, "word_end": 2}],
    "transcript": [{"word_start": 0, "word_end": 2}]
  },
  "links": [{"script": {"word_start": 0, "word_end": 2}, "transcript": {"word_start": 0, "word_end": 2}, "classification": "exact"}],
  "discarded": []
}
```

The example uses placeholders for hashes and sentence IDs. Actual source row
IDs are `<kind>:<full SHA-256>:<segmentation version>:<one-based line number>`.
They identify the deterministic starting segmentation; links do not depend on
them. Paths use forward slashes relative to the project root. Loading verifies
source hashes, words, timings, and rows against the original files. Version 1
review files are not supported; start a new review from their sources.

## Initial groupings and review

### Multiple recordings (version 3)

Multi-recording reviews retain the same mapping name, version, creation time,
and parent fields. Instead of v2's `sources`, `groupings`, `links`, and
`discarded` fields, they contain:

- `script`: one canonical script source object.
- `script_groupings`: the editable script word groups shared by all recordings.
- `recordings`: an ordered list of objects containing `transcript` (canonical
  source), `groupings`, `links`, and `discarded`.

Every recording uses its own original zero-based transcript word indexes and
timestamps. Script ranges refer to the shared script. Each recording is
validated using the v2 rules and source fingerprints; duplicate source paths
are rejected. Groups cannot cross recording boundaries. The combined UI uses
temporary concatenated indexes internally, which are converted back to local
ranges on save. Coverage considers all recordings, regardless of collapsed
sections. Collapse state is a temporary viewing preference.

Line also opens `studio.find-takes` version 1 files directly, validating each
recording's sources and mappings. Reviews are saved as new Line files under
`studio/line/`; Find Takes input files are preserved.

### Grouping operations

Initial groupings come from sentence segmentation. Word scripts include only
paragraphs after the `Spoken script` heading. Script words retain whitespace
before each word so a merged group can display original line breaks. Transcript
groups start from ordered ASR words, independently of ASR segment boundaries.
Terminal `.`, `!`, `?`, and `…` punctuation creates an initial boundary; common
abbreviations and initials are retained. A final fragment is kept.

The WhisperX adapter reads per-segment `words` when available, retaining
unaligned entries and text from segments without word alignment. Otherwise it
reads `word_segments`. WhisperX seconds are rounded to integer milliseconds.
Provider-independent JSON `words` use integer milliseconds via
`start_ms`/`end_ms` or `start`/`end`; `timestamp_unit` or `time_unit` may specify
seconds or milliseconds. Missing timing remains `null`. Group timing comes from
its first and last source words. Times refer to the original audio without a
sync offset.

Groupings cover every source word once, in order, without gaps or overlap.
Right-click one unlinked group to split it between words. Shift-click adjacent
unlinked groups, then right-click to merge them. Linked groups must be unlinked
before regrouping. Splitting a discarded group keeps both children discarded;
merging discarded groups keeps the result discarded. Restore discarded groups
before merging them with unreviewed groups.

Each link connects one script group to one transcript group. Many transcript
groups may link to the same script group; a transcript group may link only once.
Quality is inferred from group text: literal equality is `exact`, a shorter
transcript text is `flub`, and other equal/longer text is `partial`. The former
`good` quality value is accepted as `partial` in version 2. Coverage uses the
best reading for each script group: `exact` outranks `partial`, which outranks
`flub`; an unlinked group is `missing`. Transcript groups are `linked`,
`discarded`, or `unreviewed`.

Closing a source panel retains its source, groupings, and review state.
Reopening the same input restores them. A dirty review must be saved or reset
before loading another Line project. Saving always creates a new version.

## Shared actions

Registered actions accept `(project, state, ...)`, with `state` from
`mapping.new_state()`. The desktop and CLI use `studio.actions.invoke()` for
common logging and project-lock checks.

| Action | Additional arguments |
| --- | --- |
| `line.load_script`, `line.load_transcript`, `line.load_mapping` | `path` |
| `line.add_transcripts` | `paths`: nonempty list of project file paths; preserves existing review and skips unchanged duplicates |
| `line.close_script`, `line.close_transcript`, `line.reset` | None |
| `line.select` | `panel`, zero-based grouping `index` or `null` |
| `line.navigate` | `panel`, integer `delta` |
| `line.jump` | `panel`, one-based grouping `line` |
| `line.split` | `panel`, `boundary`: zero-based first word of new right group; acts on selected group |
| `line.merge` | `panel`, contiguous zero-based `indexes` list |
| `line.link` | None; quality is inferred from selected grouping texts |
| `line.unlink`, `line.discard`, `line.restore` | None; selected transcript grouping |
| `line.save` | `name` |

Matching algorithms can load canonical sources through `sources.load_script()`
and `sources.load_transcript()`, set initial groupings with
`mapping.initial_groupings()`, populate word-range links and discards, and save
through `mapping.save_version()`.
