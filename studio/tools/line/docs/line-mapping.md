# Line! mapping format

Line! stores dashboard files under each OneDrive project’s `studio/` directory.
Mapping versions are `studio/line/<name>.v001.json`, `v002.json`, and so on. Saving
creates a new file exclusively; an existing version is never overwritten.
Pipeline scripts, transcripts, and audio remain in their original locations.

## Schema

Manual review and matching algorithms use the same `studio.line.mapping` schema,
currently version `1`. Each document maps one script to one transcript. Several
transcript sentences may link to the same script sentence; each transcript
sentence may link to only one script sentence.

```json
{
  "schema": "studio.line.mapping",
  "schema_version": 1,
  "mapping_name": "freedom-c01",
  "version": 2,
  "created_at": "2026-10-11T21:00:00+00:00",
  "parent": "studio/line/freedom-c01.v001.json",
  "sources": {
    "script": {
      "source": {
        "path": "1-Pre Production/1-Script Review/Approved/What Is Freedom For.docx",
        "sha256": "<64 lowercase hexadecimal characters>",
        "segmentation_version": "line-sentences-v1",
        "kind": "script",
        "format": "docx-spoken-script"
      },
      "title": "What Is Freedom For",
      "rows": [
        {"id": "<script sentence ID>", "line": 1, "text": "What is freedom for?"}
      ]
    },
    "transcript": {
      "source": {
        "path": "3-Post Production/2-Transcribe/What Is Freedom For__C01__lav.json",
        "sha256": "<64 lowercase hexadecimal characters>",
        "segmentation_version": "line-sentences-v1",
        "kind": "transcript",
        "format": "whisperx-json"
      },
      "title": "What Is Freedom For__C01__lav",
      "audio": "2-Production/1-Footage Intake/What Is Freedom For__C01__lav.wav",
      "words": [
        {"index": 0, "text": "What", "start_ms": 1000, "end_ms": 1100},
        {"index": 1, "text": "is", "start_ms": 1100, "end_ms": 1200},
        {"index": 2, "text": "freedom", "start_ms": null, "end_ms": null},
        {"index": 3, "text": "for?", "start_ms": 1500, "end_ms": 1800}
      ],
      "rows": [
        {
          "id": "<transcript sentence ID>",
          "line": 1,
          "text": "What is freedom for?",
          "word_start": 0,
          "word_end": 3,
          "start_ms": 1000,
          "end_ms": 1800
        }
      ]
    }
  },
  "links": [
    {
      "script_id": "<script sentence ID>",
      "transcript_id": "<transcript sentence ID>",
      "classification": "exact"
    }
  ],
  "discarded": []
}
```

The example uses placeholders for fingerprints and IDs. Actual IDs are
`<kind>:<full SHA-256>:<segmentation version>:<one-based line number>`.
The source fingerprint hashes the exact file bytes used by the input adapter.
Paths use forward slashes and are relative to the project root. Reads and writes
are confined to that project, including resolution of symlinks.

The `sources` objects contain the immutable sentence segmentation and normalized
transcript words used for review. Loading verifies the source files still exist,
their fingerprints match, their sentence rows and word timings match the named
segmentation version, and every link/discard reference is valid. Editing a source
file makes its old mapping unavailable until the original source is restored;
use a fresh review for changed inputs. Unsupported schema or segmentation
versions produce an error.

## Inputs and sentence boundaries

Word scripts include only the paragraphs after the `Spoken script` heading.
Titles and notes before that heading are excluded. Plain text scripts and JSON
scripts containing `spoken_script` or `text` are also supported. Script sentence
boundaries preserve paragraph and line breaks. Transcript sentences are formed
from the ordered source words, independently of subtitle/ASR segment boundaries.
Terminal `.`, `!`, `?`, and `…` punctuation creates a sentence boundary; common
abbreviations and initials are retained. A final fragment remains a row. This is
a deterministic initial segmentation rule, rather than a take detector.

The WhisperX adapter reads per-segment `words` when available, retaining unaligned
entries and text from segments without word alignment. Otherwise it reads
`word_segments`. WhisperX seconds are rounded to integer milliseconds. The
provider-independent JSON `words` contract uses integer milliseconds, either
`start_ms`/`end_ms` or `start`/`end`; `timestamp_unit` or `time_unit` can explicitly
specify `seconds` or `milliseconds`. Missing timing remains `null`; no timing is
invented from an ASR segment. Alignment score and confidence diagnostics remain
separately named fields when supplied.

Word indexes start at zero and include unaligned words. Sentence `word_start`
and `word_end` are **inclusive**. Sentence timing comes from the first and last
source words; an unaligned boundary has a `null` time even if interior words
align. Times refer to the original audio with no sync offset applied.

An explicit `source_audio` string or `{ "path": "..." }` object is normalized
to the project-relative `audio` path. If absent, Line! looks for the WAV with the
transcript’s stem in `2-Production/1-Footage Intake/`. If no unique WAV exists,
`audio` is `null`.

## Review behavior

Links contain only a script ID, transcript ID, and `exact`, `partial`, or `flub`
classification. Legacy `good` values are accepted and load as `partial`.
The link action infers quality using literal text equality (`exact`), a shorter
transcript character count (`flub`), or any other equal/longer text (`partial`).
There is no separate link schema or per-link provenance label
for human and algorithm results. Algorithms can obtain the same canonical
source objects through `sources.load_script()` and `sources.load_transcript()`,
populate links/discarded, and save through `mapping.save_version()`.

Coverage is calculated per script sentence from its best linked reading:
`exact` outranks `partial`, which outranks `flub`; a sentence without a link is
`missing`. Each transcript sentence is separately `linked`, `discarded`, or
`unreviewed`. Loading or saving never marks unseen sentences reviewed.

Discarding a transcript sentence removes its links. Restoring it makes it
unreviewed; the old links are not restored. Closing a source panel retains its
source and review state, and reopening the same file restores that state.
Changing an existing source when there are changes or links requires an
explicit Reset. Reset clears the entire current review; saved versions remain
available to load. A dirty review must be saved or explicitly reset before
loading another mapping.

## Shared actions

Registered actions accept `(project, state, ...)`, where `project.root` is the
project directory and `state` comes from `mapping.new_state()`. They mutate and
return that state, except `line.save`, which returns the new project-relative
mapping path. `studio.actions.invoke()` supplies common logging and project-lock
checking to the desktop interface, command line, and agent callers.

| Action | Additional arguments |
| --- | --- |
| `line.load_script`, `line.load_transcript`, `line.load_mapping` | `path` |
| `line.close_script`, `line.close_transcript`, `line.reset` | None |
| `line.select` | `panel`, zero-based `index` or `null` |
| `line.navigate` | `panel`, integer `delta` |
| `line.jump` | `panel`, one-based `line` |
| `line.link` | None; quality is inferred from selected sentence texts |
| `line.unlink`, `line.discard`, `line.restore` | None; selected transcript sentence |
| `line.save` | `name` |

`line.unlink` removes the selected transcript reading’s link, which also allows
that reading to be reassigned to another script sentence. All review actions use
the same state and persistence contract regardless of caller.
