---
name: studio-transcribe
description: Run or resume Studio substage 3.2, producing a verbatim word-timestamped JSON transcript and a companion SRT for each intake audio file. Completion criteria are pending WER design.
---

# 3.2 - Transcribe

Read the Studio root skill at `.agents/skills/studio/SKILL.md` for project resolution, prerequisite validation, and logging. This stage transcribes the original audio files from 2.1 Footage Intake. Its completion condition is deliberately deferred while the WER benchmark workflow is being designed.

## Inputs

Read every supported intake audio file directly from the resolved OneDrive project's `2-Production/1-Footage Intake/` folder. Under the 2.1 filename convention, these are the non-empty WAV files named:

```text
<Script>__C<nn>__lav.wav
```

Extension matching is case-insensitive. Use the intake audio in place; camera files and Audacity `.aup3` projects are not transcription inputs. Validate the intake through the existing 2.1 validator when running this stage. A script document and 3.1 sync JSON are not transcription inputs; timestamps refer to each source audio file independently.

## Outputs

For each input audio file, write two files with the same stem directly in the project's OneDrive `3-Post Production/2-Transcribe/` folder:

```text
<Script>__C<nn>__lav.json
<Script>__C<nn>__lav.srt
```

- JSON is the machine-readable transcript for downstream processing. Include the ordered verbatim words, word start/end timestamps in integer milliseconds, the source audio reference, and transcription provenance: provider, model, implementation version, and settings. Source paths are relative to the project root and use forward slashes. Times are relative to the original audio file's start, without applying sync offsets or removing pauses. Retain available provider diagnostics with their meaning identified; do not invent confidence values or timestamps for unaligned words.
- SRT is the companion for listening and text correction in a subtitle editor. Generate it from the same transcription, using standard SRT timecodes and chronological sections suitable for playback. Subtitle sections do not define script sentences or take boundaries.

## Transcription behavior

Use the installed WhisperX environment as the initial provider. Keep the stage's input/output contract independent of that provider so benchmark comparisons can use other engines later. Record the actual model and settings for each run. If the model or processing settings have not been agreed, clarify them before processing audio rather than treating library defaults as an editorial decision.

Preserve repetitions, restarts, fillers, flubs, partial sentences, and pauses. Do not deduplicate readings, rewrite speech to match the approved script, or silently clean the transcript. Script matching, occurrence labels, and take selection belong to later stages. Generated transcripts are estimates until checked against the original audio.

## Execution and reruns

Create the OneDrive stage folder and its `LOG.md` when executing the stage. Append each attempt, selected inputs, provider/settings, output filenames, failures, and next action using the root skill's append-log helper. Record source fingerprints and run provenance in the JSON or log so outputs can be associated with the audio and settings that produced them.

Reuse an existing output pair only when its recorded source fingerprint and provider/settings match the requested run. Report missing, stale, or failed outputs by exact source filename. Preserve existing work and human corrections; clarify replacement before overwriting edited files. Keep corrections for the human benchmark reference separately from the original engine outputs so WER can compare the unchanged hypothesis with the verified reference.

## Completion deferred

No DONE condition is defined yet. Do not set WER scoring rules, acceptance thresholds, or a golden-reference procedure in this stage definition. Do not write `DONE`, claim the stage is complete, or continue automatically past 3.2. Report generated outputs and any failures, with WER design and the completion condition still pending.
