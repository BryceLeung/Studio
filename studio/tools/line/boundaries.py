"""Propose transcript regroupings without reading human benchmark mappings."""

from copy import deepcopy
from functools import lru_cache
import re

from rapidfuzz import fuzz

from . import mapping, quality


SETTINGS = {
    "window_groups": 3, "max_window_words": 80, "max_readings": 4,
    "beam_per_state": 3, "min_similarity": 75.0, "min_prefix_words": 3,
    "min_gain": 5.0, "boundary_change_cost": 0.25,
    "pause_bonus": 2.0, "sentence_end_bonus": 1.0, "original_boundary_bonus": 0.5,
}


def regroup(transcript: dict, links: list[dict]) -> tuple[list[dict], list[dict]]:
    boundaries = {0, len(transcript["words"]), *(row["word_end"] + 1 for row in transcript["rows"])}
    for link in links:
        start, end = link["transcript"]["word_start"], link["transcript"]["word_end"] + 1
        boundaries = {point for point in boundaries if not start < point < end}
        boundaries.update((start, end))
    points = sorted(boundaries)
    groups = [{"word_start": start, "word_end": end - 1} for start, end in zip(points, points[1:])]
    linked = {mapping.range_key(link["transcript"]) for link in links}
    return groups, [dict(group) for group in groups if mapping.range_key(group) not in linked]


def propose(script: dict, recording: dict, settings: dict | None = None) -> tuple[dict, dict]:
    """Return diagnostics and a separate preview; leave the original untouched.

    Search at word gaps with fixed script groups. A path may skip words or emit
    repeated readings. Exact established links act as anchors. The text score caps
    each reading's word weight at its script length, so extra words cannot earn
    extra credit. All words in the window remain in the denominator. Timing and
    punctuation rank paths that clear the minimum text gain. This bounded beam
    search reports retained alternatives, not a global confidence estimate.
    """
    settings = dict(SETTINGS, **(settings or {}))
    if not 0 < settings["min_gain"] or not 0 < settings["min_similarity"] <= 100:
        raise ValueError("Boundary gain must be positive and similarity must be in (0, 100].")
    for key in ("window_groups", "max_window_words", "max_readings", "beam_per_state", "min_prefix_words"):
        if type(settings[key]) is not int or settings[key] < 1:
            raise ValueError(f"{key} must be a positive integer.")
    for key in ("boundary_change_cost", "pause_bonus", "sentence_end_bonus", "original_boundary_bonus"):
        if settings[key] < 0:
            raise ValueError(f"{key} must be nonnegative.")
    transcript = recording["transcript"]
    groups = recording["groupings"]
    script_groups = mapping.initial_groupings(script)
    script_indexes = {mapping.range_key(group): index for index, group in enumerate(script_groups)}
    script_texts = [quality.normalize(mapping.range_text(script, group)) for group in script_groups]
    script_lengths = [len(text.split()) for text in script_texts]
    original_cuts = {row["word_end"] + 1 for row in transcript["rows"]}
    links = recording["links"]

    @lru_cache(None)
    def text(start, end):
        return quality.normalize(mapping.range_text(transcript, {"word_start": start, "word_end": end - 1}))

    @lru_cache(None)
    def score(index, start, end):
        return fuzz.ratio(script_texts[index], text(start, end)) if text(start, end) else 0.0

    @lru_cache(None)
    def weight(start, end):
        return len(text(start, end).split())

    def credit(reading):
        index, start, end = reading
        return min(weight(start, end), script_lengths[index]) * score(index, start, end)

    def path_for(items):
        return tuple((script_indexes[mapping.range_key(link["script"])],
                      link["transcript"]["word_start"], link["transcript"]["word_end"] + 1) for link in items)

    def describe(path):
        return [{"script": dict(script_groups[index]), "transcript": {"word_start": start, "word_end": end - 1},
                 "classification": quality.classify(mapping.range_text(script, script_groups[index]),
                                                    mapping.range_text(transcript, {"word_start": start, "word_end": end - 1})),
                 "quality_score": round(score(index, start, end), 6)} for index, start, end in path]

    def cuts(path, start, end):
        return {point for _, left, right in path for point in (left, right) if start < point < end}

    @lru_cache(None)
    def boundary_support(path):
        points = {point for _, start, end in path for point in (start, end)}
        pause = 0
        for point in points:
            if 0 < point < len(transcript["words"]):
                left, right = transcript["words"][point - 1], transcript["words"][point]
                if left.get("end_ms") is not None and right.get("start_ms") is not None:
                    pause += max(0, min(2000, right["start_ms"] - left["end_ms"]))
        return len(points & original_cuts), pause

    @lru_cache(None)
    def boundary_bonus(path):
        value = 0.0
        for point in {point for _, start, end in path for point in (start, end)}:
            if 0 < point < len(transcript["words"]):
                left, right = transcript["words"][point - 1], transcript["words"][point]
                pause = (max(0, right["start_ms"] - left["end_ms"])
                         if right.get("start_ms") is not None and left.get("end_ms") is not None else 0)
                value += settings["pause_bonus"] * min(1.0, pause / 1000)
                if re.search(r'[.!?…][\"\u201d\u2019\')\]]*$', left["text"]):
                    value += settings["sentence_end_bonus"]
                if point in original_cuts:
                    value += settings["original_boundary_bonus"]
        return value

    def search(start, end, current, allowed):
        denominator = weight(start, end)
        if not denominator:
            return None
        before = path_for(current)
        anchors = {reading for reading in before if text(reading[1], reading[2]) == script_texts[reading[0]]}
        if len(anchors) == len(before) and before and all(
                any(left <= group["word_start"] <= group["word_end"] < right for _, left, right in before)
                for group in groups if start <= group["word_start"] and group["word_end"] < end):
            return None
        before_credit = sum(credit(reading) for reading in before)
        before_cuts = cuts(before, start, end)
        # Per-position states retain several paths to measure competing cuts.
        states = [{(None, 0): [(0.0, ())]}] + [{} for _ in range(end - start)]

        def retain(position, key, value):
            bucket = states[position - start].setdefault(key, [])
            if any(item[1] == value[1] for item in bucket):
                return
            bucket.append(value)
            bucket.sort(key=lambda item: (item[0] / denominator + boundary_bonus(item[1]),
                                          boundary_support(item[1])), reverse=True)
            del bucket[settings["beam_per_state"]:]

        @lru_cache(None)
        def candidates(position):
            result = []
            for index in allowed:
                limit = min(end, position + max(3, int(script_lengths[index] * 1.4) + 4))
                for stop in range(position + 1, limit + 1):
                    words = weight(position, stop)
                    if words < min(settings["min_prefix_words"], script_lengths[index]):
                        continue
                    value = score(index, position, stop)
                    prefix = (words >= settings["min_prefix_words"]
                              and script_texts[index].startswith(text(position, stop) + " ")
                              and (stop in original_cuts or (stop < end and
                                   script_texts[index].startswith(text(stop, min(end, stop + 2)) + " "))))
                    if value >= settings["min_similarity"] or prefix:
                        reading = (index, position, stop)
                        if all(stop <= left or position >= right or reading == anchor
                               for anchor in anchors for _, left, right in [anchor]):
                            result.append((reading, credit(reading)))
            return result

        for position in range(start, end):
            available = candidates(position)
            for (last, count), paths in list(states[position - start].items()):
                for value, path in paths:
                    # Skip remains possible, but an anchored reading cannot be dropped.
                    if not any(left <= position < right for _, left, right in anchors):
                        retain(position + 1, (last, count), (value, path))
                    if count >= settings["max_readings"]:
                        continue
                    for reading, amount in available:
                        index, _, stop = reading
                        if last is not None and not 0 <= index - last <= 3:
                            continue
                        retain(stop, (index, count + 1), (value + amount, path + (reading,)))
        alternatives = {before: before_credit}
        for paths in states[-1].values():
            for value, path in paths:
                if anchors <= set(path):
                    alternatives[path] = value

        def supported_trims(path):
            # A strong adjacent repair must not carry a weak crop along with
            # it. Test newly discarded edges of an existing reading locally.
            covered = {word for _, left, right in path for word in range(left, right)}
            for old in before:
                index, left, right = old
                replacements = [reading for reading in path if reading[0] == index
                                and left <= reading[1] < reading[2] <= right]
                dropped = set(range(left, right)) - covered
                if replacements and dropped:
                    local_gain = ((sum(credit(reading) for reading in replacements) - credit(old))
                                  / max(1, weight(left, right)))
                    if local_gain < settings["min_gain"]:
                        return False
            return True

        ranked = []
        for path, value in alternatives.items():
            if not supported_trims(path):
                continue
            changed_cuts = len(cuts(path, start, end) ^ before_cuts)
            total = (value / denominator + boundary_bonus(path) - boundary_bonus(before)
                     - changed_cuts * settings["boundary_change_cost"])
            ranked.append((total, boundary_support(path), path, value / denominator, changed_cuts))
        ranked.sort(reverse=True)
        eligible = [item for item in ranked if item[2] == before or
                    item[3] - before_credit / denominator >= settings["min_gain"]]
        best = eligible[0]
        gain = best[0] - before_credit / denominator
        if best[2] == before or gain < settings["min_gain"]:
            return None
        next_score = eligible[1][0] if len(eligible) > 1 else before_credit / denominator
        return {"window": {"word_start": start, "word_end": end - 1},
                "before": describe(before), "after": describe(best[2]),
                "before_score": round(before_credit / denominator, 6),
                "after_score": round(best[3], 6), "adjusted_after_score": round(best[0], 6),
                "gain": round(gain, 6), "runner_up_score": round(next_score, 6),
                "text_gain": round(best[3] - before_credit / denominator, 6),
                "runner_up_margin": round(best[0] - next_score, 6),
                "changed_boundaries": best[4], "original_boundary_support": best[1][0],
                "boundary_evidence_gain": round(boundary_bonus(best[2]) - boundary_bonus(before), 6),
                "pause_support_ms": best[1][1], "selected": False}

    candidates = []
    for first in range(len(groups)):
        for count in range(1, settings["window_groups"] + 1):
            if first + count > len(groups):
                break
            start, end = groups[first]["word_start"], groups[first + count - 1]["word_end"] + 1
            if end - start > settings["max_window_words"]:
                break
            current = [link for link in links if start <= link["transcript"]["word_start"]
                       and link["transcript"]["word_end"] < end]
            seeds = [script_indexes[mapping.range_key(link["script"])] for link in current]
            if not seeds:
                adjacent = [link for link in links if link["transcript"]["word_end"] < start]
                following = [link for link in links if link["transcript"]["word_start"] >= end]
                seeds = [script_indexes[mapping.range_key(link["script"])] for link in (adjacent[-1:] + following[:1])]
            allowed = sorted({index for seed in seeds for index in range(max(0, seed - 1), min(len(script_groups), seed + 2))})
            if allowed:
                proposal = search(start, end, current, allowed)
                if proposal:
                    candidates.append(proposal)
    # Choose non-overlapping proposals by total added credit, rather than by
    # whichever overlapping window happened to be visited first.
    ordered = sorted(candidates, key=lambda item: (item["window"]["word_end"], item["window"]["word_start"]))
    choices = [(0.0, [])]
    for index, proposal in enumerate(ordered):
        start = proposal["window"]["word_start"]
        prior = next((i + 1 for i in range(index - 1, -1, -1) if ordered[i]["window"]["word_end"] < start), 0)
        amount = proposal["gain"] * weight(start, proposal["window"]["word_end"] + 1)
        take = (choices[prior][0] + amount, choices[prior][1] + [index])
        choices.append(take if take[0] > choices[-1][0] else choices[-1])
    selected = [ordered[index] for index in choices[-1][1]]
    preview = deepcopy(recording)
    for proposal in selected:
        proposal["selected"] = True
        start, end = mapping.range_key(proposal["window"])
        preview["links"] = [link for link in preview["links"]
                            if not start <= link["transcript"]["word_start"] <= link["transcript"]["word_end"] <= end]
        preview["links"].extend({key: deepcopy(link[key]) for key in ("script", "transcript", "classification")}
                                for link in proposal["after"])
    preview["links"].sort(key=lambda link: mapping.range_key(link["transcript"]))
    preview["groupings"], preview["discarded"] = regroup(transcript, preview["links"])
    original_scores = {mapping.range_key(item["transcript"]): item for item in recording.get("match_scores", [])}
    original_links = {mapping.range_key(link["transcript"]): mapping.range_key(link["script"])
                      for link in recording["links"]}
    preview["match_scores"] = []
    for link in preview["links"]:
        key = mapping.range_key(link["transcript"])
        index = script_indexes[mapping.range_key(link["script"])]
        unchanged = original_links.get(key) == mapping.range_key(link["script"])
        item = deepcopy(original_scores[key] if unchanged and key in original_scores
                        else {"transcript": dict(link["transcript"])})
        item.update(quality_score=round(score(index, key[0], key[1] + 1), 6))
        if not unchanged:
            item.update(boundary_proposal=True)
        preview["match_scores"].append(item)
    return {"settings": settings, "score": "script-capped normalized-word-weighted RapidFuzz; unmatched words score zero",
            "candidates": ordered, "selected_count": len(selected)}, preview
