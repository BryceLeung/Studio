"""Line! actions shared by the desktop interface, command line, and agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from studio.actions import action

from . import mapping, sources


def _describe_line(operation: str):
    """Keep Line's audit wording beside the actions that own its state."""
    def describe(inputs: dict, result: Any) -> str:
        state = inputs.get("state", {})
        selected = state.get("selected", {})

        def sentence(panel: str) -> str:
            index = selected.get(panel)
            return "unselected" if index is None else str(index + 1)

        if operation in ("load_script", "load_transcript"):
            panel = operation.removeprefix("load_")
            source = result[panel]
            return f"Loaded {panel}: {Path(source['source']['path']).name} ({len(source['rows'])} sentences)"
        if operation in ("close_script", "close_transcript"):
            return f"Closed {operation.removeprefix('close_')} panel"
        if operation == "link":
            transcript = _selected_row(result, "transcript")
            classification = next(link["classification"] for link in result["links"] if link["transcript_id"] == transcript["id"])
            return f"Linked script sentence {sentence('script')} to transcript sentence {sentence('transcript')} as {classification}"
        if operation in ("unlink", "discard", "restore"):
            verb = {"unlink": "Unlinked", "discard": "Discarded", "restore": "Restored"}[operation]
            return f"{verb} transcript sentence {sentence('transcript')}"
        if operation == "load_mapping":
            return f"Loaded mapping: {Path(inputs['path']).name} (links: {len(result['links'])}, discarded: {len(result['discarded'])})"
        if operation == "save":
            return f"Saved: {result}"
        if operation == "reset":
            return "Reset sources and mapping"
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
        raise ValueError(f"Select a {panel} sentence first.")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(source["rows"]):
        raise ValueError(f"Selected {panel} sentence is invalid.")
    return source["rows"][index]


def _load_source(project: Any, state: dict, panel: str, path: Any) -> dict:
    loader = sources.load_script if panel == "script" else sources.load_transcript
    loaded = loader(_root(project), path)
    previous = mapping.active_source(state, panel)
    same = bool(previous and all(
        previous["source"].get(key) == loaded["source"].get(key)
        for key in ("path", "sha256", "segmentation_version")
    ))
    if previous and not same and (state["dirty"] or state["links"] or state["discarded"]):
        raise ValueError("Save your mapping, then use Reset before choosing a different source.")
    if same:
        # Saved sentence ordering remains authoritative for the same input.
        state[panel] = previous
    else:
        state[panel] = loaded
        state["mapping_path"] = None
        state["mapping_name"] = None
        state["dirty"] = True
    state.setdefault("_closed_sources", {})[panel] = None
    state["selected"][panel] = 0
    return state


@action("line.load_script", description="Load spoken script sentences from a project file.", describe=_describe_line("load_script"))
def load_script(project: Any, state: dict, path: Any) -> dict:
    return _load_source(project, state, "script", path)


@action("line.load_transcript", description="Load timestamped transcript sentences from a project file.", describe=_describe_line("load_transcript"))
def load_transcript(project: Any, state: dict, path: Any) -> dict:
    return _load_source(project, state, "transcript", path)


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


@action("line.select", description="Select a sentence by zero-based row index.", meaningful=False)
def select(project: Any, state: dict, panel: str, index: int | None) -> dict:
    panel = _panel(panel)
    source = state.get(panel)
    if index is None:
        state["selected"][panel] = None
        return state
    if not source:
        raise ValueError(f"Load the {panel} first.")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(source["rows"]):
        raise ValueError(f"Select a {panel} index between 0 and {len(source['rows']) - 1}.")
    state["selected"][panel] = index
    return state


@action("line.navigate", description="Move selection by a row delta, stopping at either end.", meaningful=False)
def navigate(project: Any, state: dict, panel: str, delta: int) -> dict:
    panel = _panel(panel)
    source = state.get(panel)
    if not source:
        raise ValueError(f"Load the {panel} first.")
    if isinstance(delta, bool) or not isinstance(delta, int):
        raise ValueError("Navigation delta must be an integer.")
    index = state["selected"].get(panel)
    if index is None:
        index = 0 if delta >= 0 else len(source["rows"]) - 1
    else:
        index = max(0, min(len(source["rows"]) - 1, index + delta))
    state["selected"][panel] = index
    return state


@action("line.jump", description="Select a sentence by its one-based line number.", meaningful=False)
def jump(project: Any, state: dict, panel: str, line: int) -> dict:
    if isinstance(line, bool) or not isinstance(line, int):
        raise ValueError("Line number must be an integer.")
    return select(project, state, panel, line - 1)


def infer_classification(script_text: str, transcript_text: str) -> str:
    if transcript_text == script_text:
        return "exact"
    return "flub" if len(transcript_text) < len(script_text) else "partial"


@action("line.link", description="Link selected sentences with automatically inferred quality.", describe=_describe_line("link"))
def link(project: Any, state: dict) -> dict:
    script = _selected_row(state, "script")
    transcript = _selected_row(state, "transcript")
    classification = infer_classification(script["text"], transcript["text"])
    if transcript["id"] in state["discarded"]:
        raise ValueError("Restore this transcript sentence before linking it.")
    previous = next((item for item in state["links"] if item["transcript_id"] == transcript["id"]), None)
    if previous and previous["script_id"] != script["id"]:
        raise ValueError("This reading is linked to another script sentence. Unlink it first.")
    if previous:
        if previous["classification"] != classification:
            previous["classification"] = classification
            state["dirty"] = True
    else:
        state["links"].append({
            "script_id": script["id"], "transcript_id": transcript["id"],
            "classification": classification,
        })
        state["dirty"] = True
    return state


@action("line.unlink", description="Remove the selected transcript reading’s link.", describe=_describe_line("unlink"))
def unlink(project: Any, state: dict) -> dict:
    transcript = _selected_row(state, "transcript")
    links = [item for item in state["links"] if item["transcript_id"] != transcript["id"]]
    if links != state["links"]:
        state["links"] = links
        state["dirty"] = True
    return state


@action("line.discard", description="Discard the selected transcript sentence and remove its links.", describe=_describe_line("discard"))
def discard(project: Any, state: dict) -> dict:
    transcript = _selected_row(state, "transcript")
    if transcript["id"] not in state["discarded"]:
        state["links"] = [item for item in state["links"] if item["transcript_id"] != transcript["id"]]
        state["discarded"].append(transcript["id"])
        state["dirty"] = True
    return state


@action("line.restore", description="Restore the selected discarded sentence as unreviewed.", describe=_describe_line("restore"))
def restore(project: Any, state: dict) -> dict:
    transcript = _selected_row(state, "transcript")
    if transcript["id"] in state["discarded"]:
        state["discarded"].remove(transcript["id"])
        state["dirty"] = True
    return state


@action("line.load_mapping", description="Load a mapping after validating source fingerprints.", describe=_describe_line("load_mapping"))
def load_mapping(project: Any, state: dict, path: Any) -> dict:
    if state["dirty"]:
        raise ValueError("Save your changes, or use Reset, before loading another mapping.")
    loaded = mapping.read_mapping(_root(project), path)
    state.clear()
    state.update(loaded)
    return state


@action("line.save", description="Save the mapping as a new immutable version.", describe=_describe_line("save"))
def save(project: Any, state: dict, name: str) -> str:
    return mapping.save_version(_root(project), state, name)


@action("line.reset", description="Explicitly clear the current sources and unsaved mapping.", describe=_describe_line("reset"))
def reset(project: Any, state: dict) -> dict:
    state.clear()
    state.update(mapping.new_state())
    return state
