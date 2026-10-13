"""Adapt independently indexed recordings to Line's shared review actions.

Only the in-memory view uses concatenated word indexes. Saved recordings retain
their original source objects and local word ranges.
"""

from copy import deepcopy
import hashlib
import json


def recordings(source):
    if not source:
        return []
    return source.get("recordings", [source])


def spans(source):
    offset = 0
    for recording in recordings(source):
        end = offset + len(recording["words"])
        yield recording, offset, end
        offset = end


def shifted(group, offset):
    return {"word_start": group["word_start"] + offset, "word_end": group["word_end"] + offset}


def combine(items):
    if len(items) == 1:
        return deepcopy(items[0])
    words, rows = [], []
    for recording in items:
        offset = len(words)
        first_line = len(rows) + 1
        words.extend(dict(word, index=offset + i) for i, word in enumerate(recording["words"]))
        rows.extend(dict(row, **shifted(row, offset), line=first_line + i)
                    for i, row in enumerate(recording["rows"]))
    fingerprint = hashlib.sha256(json.dumps([item["source"] for item in items], sort_keys=True).encode()).hexdigest()
    return {"source": {"path": f"{len(items)} transcript files", "sha256": fingerprint},
            "words": words, "rows": rows, "recordings": deepcopy(items)}


def recording_for(source, group):
    return next(((i, recording, start, end) for i, (recording, start, end) in enumerate(spans(source))
                 if start <= group["word_start"] <= group["word_end"] < end), None)


def pack(state):
    from .mapping import active_source
    result = []
    source = active_source(state, "transcript")
    for recording, start, end in spans(source):
        groups = [shifted(g, -start) for g in state["groupings"]["transcript"]
                  if start <= g["word_start"] <= g["word_end"] < end]
        links = [dict(link, transcript=shifted(link["transcript"], -start)) for link in state["links"]
                 if start <= link["transcript"]["word_start"] <= link["transcript"]["word_end"] < end]
        discarded = [shifted(g, -start) for g in state["discarded"]
                     if start <= g["word_start"] <= g["word_end"] < end]
        result.append({"transcript": deepcopy(recording), "groupings": groups,
                       "links": deepcopy(links), "discarded": discarded})
    if sum(len(r["groupings"]) for r in result) != len(state["groupings"]["transcript"]):
        raise ValueError("A transcript group cannot cross file boundaries.")
    if (sum(len(r["links"]) for r in result) != len(state["links"])
            or sum(len(r["discarded"]) for r in result) != len(state["discarded"])):
        raise ValueError("Transcript links and discards must remain inside a single file.")
    return result


def unpack(document):
    from .mapping import new_state
    state = new_state()
    state["script"] = deepcopy(document["script"])
    state["groupings"]["script"] = deepcopy(document["script_groupings"])
    state["script_discarded"] = deepcopy(document.get("script_discarded", []))
    state["transcript"] = combine([r["transcript"] for r in document["recordings"]])
    offset = 0
    for recording in document["recordings"]:
        state["groupings"]["transcript"].extend(shifted(g, offset) for g in recording["groupings"])
        state["links"].extend(dict(deepcopy(link), transcript=shifted(link["transcript"], offset))
                              for link in recording["links"])
        state["discarded"].extend(shifted(g, offset) for g in recording["discarded"])
        offset += len(recording["transcript"]["words"])
    state["selected"] = {"script": 0, "transcript": 0}
    return state


def validate(root, document, verify_inputs=True):
    from .mapping import SCHEMA, SCHEMA_VERSION, validate_document
    document = deepcopy(document)
    items = document.get("recordings")
    if not isinstance(items, list) or not items:
        raise ValueError("A multi-transcript review must contain recordings.")
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each recording must contain a transcript and mappings.")
        validated = validate_document(root, {
            "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "mapping_name": document.get("mapping_name"), "version": document.get("version"),
            "sources": {"script": document.get("script"), "transcript": item.get("transcript")},
            "groupings": {"script": document.get("script_groupings"), "transcript": item.get("groupings")},
            "links": item.get("links"), "discarded": item.get("discarded"),
            "script_discarded": document.get("script_discarded", []),
        }, verify_inputs=verify_inputs)
        path = validated["sources"]["transcript"]["source"]["path"]
        if path in seen:
            raise ValueError("A transcript file can appear only once in a review.")
        seen.add(path)
        item["links"] = validated["links"]
    return document
