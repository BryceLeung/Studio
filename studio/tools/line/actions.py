"""Line! actions shared by the desktop interface, command line, and agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from studio.actions import action
from studio.fingerprints import matches

from . import mapping, sources
from . import combined
from .quality import classify as infer_classification


def _describe_line(operation: str):
    """Keep Line's audit wording beside the actions that own its state."""
    def describe(inputs: dict, result: Any) -> str:
        state = inputs.get("state", {})
        selected = state.get("selected", {})

        def group_number(panel: str) -> str:
            index = selected.get(panel)
            return "unselected" if index is None else str(index + 1)

        if operation in ("load_script", "load_transcript"):
            panel = operation.removeprefix("load_")
            source = result[panel]
            return f"Loaded {panel}: {Path(source['source']['path']).name} ({len(result['groupings'][panel])} word groupings)"
        if operation == "add_transcripts":
            return "Added transcript files: " + ", ".join(Path(path).name for path in inputs["paths"])
        if operation in ("close_script", "close_transcript"):
            return f"Closed {operation.removeprefix('close_')} panel"
        if operation == "link":
            transcript = _selected_row(result, "transcript")
            classification = mapping.linked_group(result, "transcript", transcript)["classification"]
            return f"Linked script group {group_number('script')} to transcript group {group_number('transcript')} as {classification}"
        if operation in ("unlink", "discard", "restore"):
            verb = {"unlink": "Unlinked", "discard": "Discarded", "restore": "Restored"}[operation]
            panel = inputs.get("panel", "transcript")
            return f"{verb} {panel} group {group_number(panel)}"
        if operation in ("split", "merge"):
            return f"{operation.title()} {inputs['panel']} word grouping"
        if operation == "load_mapping":
            description = f"Loaded Line project: {Path(inputs['path']).name} (links: {len(result['links'])}, discarded: {len(result['discarded'])})"
            if result.get("_quality_updates"):
                description += f"; quality labels recalculated: {result['_quality_updates']}"
            return description
        if operation == "save":
            return f"Saved: {result}"
        if operation == "reset":
            return "Reset sources and Line project"
        raise ValueError(f"No Line description for {operation}")

    return describe


def _root(project: Any) -> Path:
    return Path(project.root)


def _panel(panel: str) -> str:
    if panel not in mapping.PANELS:
        raise ValueError("Panel must be ‘script’ or ‘transcript’.")
    return panel


def _selected_row(state: dict, panel: str) -> dict:
    panel = _panel(panel)
    source = state.get(panel)
    index = state["selected"].get(panel)
    if not source or index is None:
        raise ValueError(f"Select a {panel} word grouping first.")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(state["groupings"][panel]):
        raise ValueError(f"Selected {panel} word grouping is invalid.")
    return mapping.group_rows(state, panel)[index]


def _load_source(project: Any, state: dict, panel: str, path: Any) -> dict:
    loader = sources.load_script if panel == "script" else sources.load_transcript
    loaded = loader(_root(project), path)
    previous = mapping.active_source(state, panel)
    same = bool(previous and all(
        previous["source"].get(key) == loaded["source"].get(key)
        for key in ("path", "segmentation_version")
    ) and (previous["source"]["sha256"] == loaded["source"]["sha256"]
           or matches(sources.project_path(_root(project), loaded["source"]["path"]),
                      previous["source"]["sha256"])))
    if previous and not same and (state["dirty"] or state["links"] or state["discarded"] or state.get("script_discarded")):
        raise ValueError("Save your Line project, then use Reset before choosing a different source.")
    if same:
        # The retained source and grouping layout remain authoritative for the same input.
        state[panel] = previous
    else:
        state[panel] = loaded
        state["groupings"][panel] = mapping.initial_groupings(loaded)
        state["mapping_path"] = None
        state["mapping_name"] = None
        state["dirty"] = True
    state.setdefault("_closed_sources", {})[panel] = None
    state["selected"][panel] = 0
    return state


@action("line.load_script", description="Load spoken script words and initial groupings from a project file.", describe=_describe_line("load_script"))
def load_script(project: Any, state: dict, path: Any) -> dict:
    return _load_source(project, state, "script", path)


@action("line.load_transcript", description="Load timestamped transcript words and initial groupings from a project file.", describe=_describe_line("load_transcript"))
def load_transcript(project: Any, state: dict, path: Any) -> dict:
    return _load_source(project, state, "transcript", path)


@action("line.add_transcripts", description="Add transcript files while retaining the current review.", describe=_describe_line("add_transcripts"))
def add_transcripts(project: Any, state: dict, paths: list[str]) -> dict:
    if not isinstance(paths, list) or not paths:
        raise ValueError("Choose at least one transcript file.")
    # Read and check the whole selection before mutating review state.
    loaded = [sources.load_transcript(_root(project), path) for path in paths]
    items = list(combined.recordings(mapping.active_source(state, "transcript")))
    additions = []
    for source in loaded:
        previous = next((item for item in items if item["source"]["path"] == source["source"]["path"]), None)
        if previous:
            if (any(previous["source"].get(key) != value for key, value in source["source"].items()
                    if key != "sha256")
                    or (previous["source"]["sha256"] != source["source"]["sha256"]
                        and not matches(sources.project_path(_root(project), source["source"]["path"]),
                                        previous["source"]["sha256"]))):
                raise ValueError(f"Transcript source has changed: {source['source']['path']}")
            continue
        offset = sum(len(item["words"]) for item in items)
        additions.extend(combined.shifted(group, offset)
                         for group in mapping.initial_groupings(source))
        items.append(source)
    state["transcript"] = combined.combine(items)
    state["_closed_sources"]["transcript"] = None
    state["groupings"]["transcript"].extend(additions)
    if state["selected"]["transcript"] is None:
        state["selected"]["transcript"] = 0
    if additions:
        state["dirty"] = True
    return state


def _close(state: dict, panel: str) -> dict:
    if state.get(panel) is not None:
        state.setdefault("_closed_sources", {})[panel] = state[panel]
        state[panel] = None
    state["selected"][panel] = None
    return state


@action("line.close_script", description="Close the script panel while retaining its mapping.", describe=_describe_line("close_script"))
def close_script(project: Any, state: dict) -> dict:
    return _close(state, "script")


@action("line.close_transcript", description="Close the transcript panel while retaining its mapping.", describe=_describe_line("close_transcript"))
def close_transcript(project: Any, state: dict) -> dict:
    return _close(state, "transcript")


@action("line.select", description="Select a word grouping by zero-based row index.", meaningful=False)
def select(project: Any, state: dict, panel: str, index: int | None) -> dict:
    panel = _panel(panel)
    source = state.get(panel)
    if index is None:
        state["selected"][panel] = None
        return state
    if not source:
        raise ValueError(f"Load the {panel} first.")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(state["groupings"][panel]):
        raise ValueError(f"Select a {panel} index between 0 and {len(state['groupings'][panel]) - 1}.")
    state["selected"][panel] = index
    return state


@action("line.navigate", description="Move selection by a grouping delta, stopping at either end.", meaningful=False)
def navigate(project: Any, state: dict, panel: str, delta: int) -> dict:
    panel = _panel(panel)
    source = state.get(panel)
    if not source:
        raise ValueError(f"Load the {panel} first.")
    if isinstance(delta, bool) or not isinstance(delta, int):
        raise ValueError("Navigation delta must be an integer.")
    index = state["selected"].get(panel)
    if index is None:
        index = 0 if delta >= 0 else len(state["groupings"][panel]) - 1
    else:
        index = max(0, min(len(state["groupings"][panel]) - 1, index + delta))
    state["selected"][panel] = index
    return state


@action("line.jump", description="Select a word grouping by its one-based row number.", meaningful=False)
def jump(project: Any, state: dict, panel: str, line: int) -> dict:
    if isinstance(line, bool) or not isinstance(line, int):
        raise ValueError("Line number must be an integer.")
    return select(project, state, panel, line - 1)


@action("line.link", description="Link selected word groupings with automatically inferred quality.", describe=_describe_line("link"))
def link(project: Any, state: dict) -> dict:
    script = _selected_row(state, "script")
    transcript = _selected_row(state, "transcript")
    classification = infer_classification(script["text"], transcript["text"])
    if mapping.is_discarded(state, transcript):
        raise ValueError("Restore this transcript grouping before linking it.")
    previous = mapping.linked_group(state, "transcript", transcript)
    if previous and previous["script"] != mapping.word_range(script):
        raise ValueError("This reading is linked to another script grouping. Unlink it first.")
    if previous:
        if previous["classification"] != classification:
            previous["classification"] = classification
            state["dirty"] = True
    else:
        state["links"].append({
            "script": mapping.word_range(script), "transcript": mapping.word_range(transcript),
            "classification": classification,
        })
        state["dirty"] = True
    return state


@action("line.unlink", description="Remove the selected transcript reading’s link.", describe=_describe_line("unlink"))
def unlink(project: Any, state: dict) -> dict:
    transcript = _selected_row(state, "transcript")
    links = [item for item in state["links"] if item["transcript"] != mapping.word_range(transcript)]
    if links != state["links"]:
        state["links"] = links
        state["dirty"] = True
    return state


@action("line.discard", description="Discard a selected script or transcript grouping.", describe=_describe_line("discard"))
def discard(project: Any, state: dict, panel: str = "transcript") -> dict:
    group = mapping.word_range(_selected_row(state, panel))
    discarded = state.setdefault("script_discarded", []) if panel == "script" else state["discarded"]
    if group not in discarded:
        if panel == "transcript":
            state["links"] = [item for item in state["links"] if item["transcript"] != group]
        discarded.append(group)
        state["dirty"] = True
    return state


@action("line.restore", description="Restore the selected discarded grouping as unreviewed.", describe=_describe_line("restore"))
def restore(project: Any, state: dict, panel: str = "transcript") -> dict:
    group = mapping.word_range(_selected_row(state, panel))
    discarded = state.setdefault("script_discarded", []) if panel == "script" else state["discarded"]
    if group in discarded:
        discarded.remove(group)
        state["dirty"] = True
    return state


def grouping_edit_error(state: dict, panel: str, indexes: list[int]) -> str | None:
    panel = _panel(panel)
    groups = state["groupings"][panel]
    if not state.get(panel):
        return f"Load the {panel} first."
    if not indexes or any(type(index) is not int or not 0 <= index < len(groups) for index in indexes):
        return "Select valid word groupings first."
    if any(mapping.linked_group(state, panel, groups[index]) for index in indexes):
        return "Unlink the selected word groupings before splitting or merging."
    if panel == "transcript" and len(indexes) > 1:
        recordings = {combined.recording_for(state[panel], groups[index])[0] for index in indexes}
        if len(recordings) > 1:
            return "Groups from different transcript files cannot be merged."
    return None


@action("line.split", description="Split an unlinked grouping at a word boundary.", describe=_describe_line("split"))
def split(project: Any, state: dict, panel: str, boundary: int) -> dict:
    panel = _panel(panel)
    index = state["selected"].get(panel)
    error = grouping_edit_error(state, panel, [index])
    if error:
        raise ValueError(error)
    group = state["groupings"][panel][index]
    if type(boundary) is not int or not group["word_start"] < boundary <= group["word_end"]:
        raise ValueError("Choose a word boundary inside the selected grouping.")
    first = {"word_start": group["word_start"], "word_end": boundary - 1}
    second = {"word_start": boundary, "word_end": group["word_end"]}
    discarded = state.setdefault("script_discarded", []) if panel == "script" else state["discarded"]
    if group in discarded:
        discarded.remove(group)
        discarded.extend((first, second))
    state["groupings"][panel][index:index + 1] = [first, second]
    state["dirty"] = True
    return state


@action("line.merge", description="Merge adjacent unlinked word groupings.", describe=_describe_line("merge"))
def merge(project: Any, state: dict, panel: str, indexes: list[int]) -> dict:
    panel = _panel(panel)
    if not isinstance(indexes, list) or len(indexes) < 2:
        raise ValueError("Select at least two adjacent word groupings.")
    indexes = sorted(indexes)
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise ValueError("Select adjacent word groupings without gaps.")
    error = grouping_edit_error(state, panel, indexes)
    if error:
        raise ValueError(error)
    groups = state["groupings"][panel]
    originals = groups[indexes[0]:indexes[-1] + 1]
    discard_key = "script_discarded" if panel == "script" else "discarded"
    discarded = [group in state.get(discard_key, []) for group in originals]
    if discarded and any(value != discarded[0] for value in discarded):
        raise ValueError("Restore discarded groupings before merging them with unreviewed groupings.")
    merged = {"word_start": originals[0]["word_start"], "word_end": originals[-1]["word_end"]}
    if discarded and discarded[0]:
        state[discard_key] = [group for group in state[discard_key] if group not in originals]
        state[discard_key].append(merged)
    groups[indexes[0]:indexes[-1] + 1] = [merged]
    state["selected"][panel] = indexes[0]
    state["dirty"] = True
    return state


@action("line.load_mapping", description="Load a Line project after validating source fingerprints.", describe=_describe_line("load_mapping"))
def load_mapping(project: Any, state: dict, path: Any) -> dict:
    if state["dirty"]:
        raise ValueError("Save your changes, or use Reset, before loading another Line project.")
    loaded = mapping.read_mapping(_root(project), path)
    state.clear()
    state.update(loaded)
    return state


@action("line.save", description="Save the Line project as a new immutable version.", describe=_describe_line("save"))
def save(project: Any, state: dict, name: str) -> str:
    return mapping.save_version(_root(project), state, name)


@action("line.reset", description="Explicitly clear the current sources and unsaved Line project.", describe=_describe_line("reset"))
def reset(project: Any, state: dict) -> dict:
    state.clear()
    state.update(mapping.new_state())
    return state
