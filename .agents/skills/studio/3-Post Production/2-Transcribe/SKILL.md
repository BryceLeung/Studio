---
name: studio-transcribe
description: Run or resume Studio substage 3.2, producing a word-timestamped JSON transcript per intake lav WAV and reusing valid existing outputs.
---

# 3.2 - Transcribe

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project resolution, prerequisite validation, and logging. This stage transcribes the original audio files from 2.1 Footage Intake. Completion is based on the JSON output inventory, not a WER threshold.

## Inputs

Read every supported intake audio file directly from the resolved OneDrive project's `2-Production/1-Footage Intake/` folder. Under the 2.1 filename convention, these are the non-empty WAV files named:

```text
<Script>__C<nn>__lav.wav
```

Extension matching is case-insensitive. Use the intake audio in place; camera files and Audacity `.aup3` projects are not transcription inputs. Validate the intake through the existing 2.1 validator when running this stage. A script document and 3.1 sync JSON are not transcription inputs; timestamps refer to each source audio file independently.

## Outputs

For each input audio file, write at least these two files with the same stem directly in the project's OneDrive `3-Post Production/2-Transcribe/` folder:

```text
<Script>__C<nn>__lav.json
<Script>__C<nn>__lav.srt
```

- JSON is the native WhisperX JSON output. Preserve its schema, word ordering, timestamps, and available diagnostics. The source is the intake WAV with the same filename stem; record its project-relative path and SHA-256 in `LOG.md`. Native timestamps are in seconds relative to the original audio file's start, without applying sync offsets or removing pauses. Do not invent confidence values or timestamps for unaligned words.
- SRT is the companion for listening and text correction in a subtitle editor. Generate it from the same transcription, using standard SRT timecodes and chronological sections suitable for playback. Subtitle sections do not define script sentences or take boundaries.
- When the WhisperX CLI's `all` output format is used to produce JSON and SRT together, retain its additional native output formats in the stage folder.

## Transcription behavior

Use the installed WhisperX CLI as the initial provider. Select the model and processing settings for each run through CLI arguments; record the CLI options, provider version, model, settings, source paths, and source SHA-256 fingerprints in `LOG.md`. When the user requests all intake WAVs, pass them together to one CLI invocation so the model is loaded once. If the model or processing settings have not been agreed, clarify them before processing audio rather than treating library defaults as an editorial decision.

Preserve repetitions, restarts, fillers, flubs, partial sentences, and pauses. Do not deduplicate readings, rewrite speech to match the approved script, or silently clean the transcript. Script matching, occurrence labels, and take selection belong to later stages. Generated transcripts are estimates until checked against the original audio.

## Execution and reruns

Create the OneDrive stage folder and its `LOG.md` when executing the stage. Append each attempt, selected inputs, provider/settings, output filenames, failures, and next action using the root skill's append-log helper. Record source fingerprints and run provenance in the JSON or log so outputs can be associated with the audio and settings that produced them.

Before invoking WhisperX, run the completion check below. If it reports `reused` or `adopted`, do **not** retranscribe or overwrite the existing JSON. Existing JSON is adopted against the current intake WAV by matching stem, then the WAV and JSON SHA-256 hashes are recorded in `DONE`. This first adoption records current state; a filename alone cannot prove how a historical JSON was originally generated. After adoption, unchanged WAV/JSON hashes reuse the existing `DONE` unchanged. If an audio hash changes while its JSON hash does not, refuse to treat that JSON as a current transcription. Report missing or stale outputs by exact source filename. Preserve existing work and human corrections; clarify replacement before overwriting edited files. Keep corrections for the human benchmark reference separately from the original engine outputs so WER can compare the unchanged hypothesis with the verified reference.

## Completion and reruns

Run the completion check using Python 3.9+ after validating 2.1 and its `DONE`:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/2-Transcribe/scripts/check_transcripts.py" --project-root "<real-project-root>"
```

**Done means each non-empty intake `<Script>__C<nn>__lav.wav` has a same-stem, non-empty, parseable `.json` file directly in `3-Post Production/2-Transcribe/`, with no unmatched transcript JSON.** The script never runs WhisperX. The companion `.srt` is still generated for new transcription runs, but its presence is not a completion gate. On success write `DONE` with exact full WAV/JSON SHA-256 fingerprints and this step's skill/script and intake-validator fingerprints; exclude `LOG.md`, `DONE`, SRT, and comparison artifacts. On an unchanged rerun, leave both outputs and `DONE` intact. When an input/output changes, invalidate the step and dependent completion markers and recheck; changed audio with unchanged JSON needs retranscription before a new `DONE`. Missing, invalid, or extra JSON fails without overwriting files. Record every attempt and invalidation in append-only logs.
If changed audio invalidates `DONE` while its JSON is unchanged, keep a small `Stale Transcripts.json` guard with that JSON hash. The checker must not readopt that same JSON on a later run after `DONE` has been deleted. Remove the guard only when replacement JSON differs and the inventory passes; never use it to overwrite a transcript.

WER benchmarking is separate from this inventory-based completion criterion. Do not invent a WER threshold or alter original WhisperX transcripts to satisfy one.
An automatic `studio run` may continue to 3.3 once the 3.2 `DONE` is valid.

Tests:

```text
python -m unittest discover -s ".agents/skills/studio/3-Post Production/2-Transcribe/scripts" -p "test_*.py" -v
```
