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

Explicit Line transcript repairs preserve these flat originals. Successful repairs are immutable candidates at `REPAIRS/<canonical stem>/vN/<same stem>.json`, published only by a final `COMMITTED.json` containing candidate and source byte hashes and a project-relative, forward-slash source path portable between machines. The shared `studio/transcript_versions.py` resolver selects the highest committed numeric version for each canonical lav stem; it ignores uncommitted/orphan candidates and reports malformed committed versions or changed provenance rather than falling back. Reports and arbitrary nested JSON are not transcripts. Earlier mappings keep their exact source paths and remain openable.

## Local repair

In Line, select one transcript group and choose **Repair transcription...** from its context menu. Line saves unsaved review work first, then launches the stage-local interpreter in the background. The progress window shows model loading/download and processing phases; **Cancel Repair**, Escape, or window-close requests cancellation while retaining the current review. The repair targets that group and its immediate neighbors within the same recording, with padded outer word anchors. Unknown local boundary times or ambiguous/unstable anchors cause refusal, not a guessed splice. Unaligned material outside the repair context is preserved.

The headless entry point is `scripts/repair_transcription.py` at the workflow root:

```text
<stage-python> ./scripts/repair_transcription.py "<Takes-file.json>" --project-root "<real-project-root>" --group 100
```

Group numbers are one-based global transcript display groups from the supplied saved mapping, not script lines, native segment indexes, or table rows. Native WhisperX transcript input is also supported, using its initial Line sentence groups, but does not generate Takes. Repairs use English large-v3, CPU int8, Silero, ten-second recognition chunks, and forced alignment without prompting from the old text. Shorter chunks help retain restarts that longer recognition chunks can collapse. Models use `--model-dir`, then `STUDIO_WHISPERX_MODEL_DIR`, then this skill's `.model-cache/`; uncached models may download. The original audio remains in place.

Changed normalized wording or any matched word boundary displaced by at least `--timing-threshold` seconds (default 0.5) counts as significant. Insignificant results produce diagnostics only. Significant repairs save a new coherent native transcript and freshly generated next-numbered Takes file, with no migration of manual links, grouping edits, or discards. Raw ASR, local/source alignment, audio excerpt, settings, and old/new comparison remain in the repair version directory. Publication occurs only after both outputs validate. Line offers **Load New Version** or **Keep Current**; keeping the current view does not undo publication or change latest-version selection for subsequent stage runs. Generation alone never records stage completion or approves acoustic accuracy.

## Transcription behavior

### Dependencies

Use Python 3.10 through 3.13 (3.13 recommended) to run `scripts/install_dependencies.py` in this skill directory. It creates the stage-local `.venv-tools`, pins WhisperX 3.8.6 and its compatible Torch stack, includes headless Line matching dependencies (RapidFuzz, num2words, python-docx) for explicit transcript repair and fresh Takes generation, and checks package consistency, CLI startup and FFmpeg on PATH without downloading models or transcribing. Add `--check` for read-only verification. A healthy installation is reused without reinstalling. Pip reuses its package cache where possible. Keep the shared root `.venv-tools` for common helpers; do not add its Python 3.14 binary packages to this Python 3.13 environment. The legacy root `.venv-whisperx` and its model cache are preserved, not required by the new installer.

Run new transcriptions with this stage's `.venv-tools/Scripts/python.exe -m whisperx` on Windows or `.venv-tools/bin/python -m whisperx` on macOS, supplying the agreed model/settings explicitly. Use an existing model-cache directory with WhisperX's `--model_dir` when appropriate; do not download models during dependency setup. No GPU or CUDA availability is implied by a successful dependency check.

Use the installed WhisperX CLI as the initial provider. Select the model and processing settings for each run through CLI arguments; record the CLI options, provider version, model, settings, source paths, and source SHA-256 fingerprints in `LOG.md`. When the user requests all intake WAVs, pass them together to one CLI invocation so the model is loaded once. If the model or processing settings have not been agreed, clarify them before processing audio rather than treating library defaults as an editorial decision.

Preserve repetitions, restarts, fillers, flubs, partial sentences, and pauses. Do not deduplicate readings, rewrite speech to match the approved script, or silently clean the transcript. Script matching, occurrence labels, and take selection belong to later stages. Generated transcripts are estimates until checked against the original audio.

## Execution and reruns

Create the OneDrive stage folder and its `LOG.md` when executing the stage. Append each attempt, selected inputs, provider/settings, output filenames, failures, and next action using the root skill's append-log helper. Record source fingerprints and run provenance in the JSON or log so outputs can be associated with the audio and settings that produced them.

Before invoking WhisperX, run the completion check below. If it reports `reused` or `adopted`, do **not** retranscribe or overwrite the existing JSON. Existing JSON is adopted against the current intake WAV by matching stem, then its media identity (byte size and integer modification second) and the JSON SHA-256 hash are recorded in `DONE`. This first adoption records current state; a filename alone cannot prove how a historical JSON was originally generated. After adoption, unchanged WAV/JSON fingerprints reuse the existing `DONE` unchanged. If the audio identity changes while its JSON hash does not, refuse to treat that JSON as a current transcription. Report missing or stale outputs by exact source filename. Preserve existing work and human corrections; clarify replacement before overwriting edited files. Keep corrections for the human benchmark reference separately from the original engine outputs so WER can compare the unchanged hypothesis with the verified reference.

When retranscription is deliberately selected, use the root skill's `prepare-regeneration --stage 3.2` command before invoking WhisperX. Pass each existing native artifact for the selected source stem with repeated `--file` arguments (`.json`, `.srt`, `.vtt`, `.tsv`, `.txt` as present). It archives those files into the exact pending invalidation slot and preserves the old completion provenance. Never select WAV inputs, `Stale Transcripts.json`, or human benchmark/reference corrections. For a first generation with no existing artifacts, the helper accepts no `--file` arguments after this step's folder has been created. Run WhisperX only after preparation succeeds; then run the normal checker to validate replacements and record completion.

## Completion and reruns

The normal checker uses the shared ancestor walk before any project mutation and the shared `record_done` API for both reuse and new completion. It retains ownership of stale-transcript provenance and its success rule. Do not bypass it with a hand-written marker or a generic inventory-only record.

Run the completion check using Python 3.9+ after validating 2.1 and its `DONE`:

```text
python "<workflow-root>/.agents/skills/studio/3-Post Production/2-Transcribe/scripts/check_transcripts.py" --project-root "<real-project-root>"
```

**Done means each non-empty intake lav WAV in this step's `dependencies.json` has a same-stem, non-empty, parseable `.json` file directly in its declared output folder, with no unmatched transcript JSON.** The script never runs WhisperX. The companion `.srt` is still generated for new transcription runs, but its presence is not a completion gate. On success record `DONE` with the shared writer; exclude `LOG.md`, `DONE`, SRT, and comparison artifacts. On an unchanged rerun, leave both outputs and `DONE` intact. Execution-time invalidation uses the JSON graph, archiving this and dependent markers under their own `STALE/N/DONE` while preserving independent Sync completion. Changed audio with unchanged JSON needs retranscription before a new `DONE`. Missing, invalid, or extra JSON fails without overwriting files. Record every attempt and invalidation in append-only logs.
The checker additionally fingerprints committed repair transcripts and publication markers, retaining canonical flat outputs for inventory and historical audio pairing, and records the effective transcript selection. A new committed repair or changed candidate/marker makes old completion stale. Run this normal checker to adopt successful repairs before 3.3; repair generation itself neither runs stages nor writes DONE. Canonical original/audio stale guards remain enforced even when a repair exists.
If changed audio invalidates `DONE` while its JSON is unchanged, keep a small `Stale Transcripts.json` guard with that JSON hash. The checker must not readopt that same JSON on a later run after `DONE` has been archived. Remove the guard only when replacement JSON differs and the inventory passes; never use it to overwrite a transcript.

WER benchmarking is separate from this inventory-based completion criterion. Do not invent a WER threshold or alter original WhisperX transcripts to satisfy one.
An automatic `studio run` may continue to 3.3 once the 3.2 `DONE` is valid.

Tests:

```text
python -m unittest discover -s ".agents/skills/studio/3-Post Production/2-Transcribe/scripts" -p "test_*.py" -v
```
