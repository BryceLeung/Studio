"""Build editable, word-highlighted Final Cut titles inside take compounds."""
from fractions import Fraction
import math
import xml.etree.ElementTree as ET


TITLE_UID = ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"
HIGHLIGHT_COLOR = "1 0.827451 0.301961 1"  # #ffd34d in the approved style mockup.
LINE_LENGTH = 15
FONT_SIZE = 140


def frame_at(value, frame):
    return math.floor(value / frame + Fraction(1, 2)) * frame


def time(value):
    value = Fraction(value)
    return f"{value.numerator}/{value.denominator}s" if value.denominator != 1 else f"{value.numerator}s"


def add_titles(clip, take, words, frame, effect_id, id_start=0, portrait=False):
    """Anchor source-time titles to the multicam clip, matching FCP's exported XML."""
    start_index, end_index = take["transcript"]["word_start"], take["transcript"]["word_end"]
    selected = words[start_index:end_index + 1]
    if not selected:
        raise ValueError(f"Empty transcript range for {take['take_key']}")
    events = []
    for word in selected:
        label = word["text"].strip()
        if not label or word["start_ms"] is None or word["end_ms"] is None:
            raise ValueError(f"Missing subtitle text/timestamp in {take['take_key']}")
        start = take["origins"]["lav"] + Fraction(str(word["start_ms"])) / 1000
        end = take["origins"]["lav"] + Fraction(str(word["end_ms"])) / 1000
        if (not start < end or end <= take["cut_in"] or start >= take["cut_out"]
                or take["cut_in"] - start > frame or end - take["cut_out"] > frame):
            raise ValueError(f"Subtitle outside take trim: {take['take_key']} {label}")
        # Adjacent sentence cuts are frame-snapped; a boundary word can spill a few ms.
        start_frame = max(take["cut_in"], frame_at(start, frame))
        end_frame = min(take["cut_out"], frame_at(end, frame))
        events.append(([label], start_frame, end_frame))
    if any(left[1] > right[1] for left, right in zip(events, events[1:])):
        raise ValueError(f"Subtitle words out of source order: {take['take_key']}")
    # Resolve frame collisions before phrase wrapping, so a word can share its
    # predecessor even when that pair originally straddled a phrase boundary.
    # Handle zero-duration words first, so a short second word joins its
    # predecessor instead of making the predecessor adopt its empty interval.
    for merge_same_start in (False, True):
        index = 0
        while index < len(events):
            labels, start, end = events[index]
            same_start = index + 1 < len(events) and events[index + 1][1] == start
            if end > start and not (merge_same_start and same_start):
                index += 1
                continue
            if index:
                previous, previous_start, previous_end = events[index - 1]
                events[index - 1] = (previous + labels, previous_start, max(previous_end, end))
                del events[index]
                index -= 1
            elif len(events) > 1:
                following, next_start, next_end = events[1]
                events[1] = (labels + following, next_start, next_end)
                del events[0]
            else:
                start = max(take["cut_in"], min(start, take["cut_out"] - frame))
                if start + frame > take["cut_out"]:
                    raise ValueError(f"No subtitle frame fits take: {take['take_key']}")
                events[0] = (labels, start, start + frame)
                index += 1
    count = 0
    phrase = []
    groups = []
    for event in events:
        labels = [word for e in phrase + [event] for word in e[0]]
        if phrase and (len(labels) > 3 or len(" ".join(labels)) > 24):
            groups.append(phrase)
            phrase = []
        phrase.append(event)
    if phrase:
        groups.append(phrase)
    for group in groups:
        display = [(word, index) for index, (labels, _, _) in enumerate(group) for word in labels]
        font_size = min(FONT_SIZE, FONT_SIZE * LINE_LENGTH // max(len(word) for word, _ in display))
        if portrait:
            font_size //= 2
        for index, (_, start, end) in enumerate(group):
            next_start = group[index + 1][1] if index + 1 < len(group) else end
            finish = min(take["cut_out"], max(end, next_start)) if index + 1 == len(group) else next_start
            if finish <= start:
                raise ValueError(f"Subtitle words collide on one frame: {take['take_key']}")
            title = ET.SubElement(clip, "title", {"ref": effect_id, "lane": "1", "offset": time(start),
                                                  "name": " ".join(word for word, _ in display),
                                                  "start": "3600s", "duration": time(finish - start)})
            # Basic Title is point text by default. Request paragraph layout and
            # keep explicit word-boundary breaks for Final Cut versions that ignore it.
            for name, key, value in (("Layout Method", "314", "1 (Paragraph)"),
                                     ("Left Margin", "323", "-450" if portrait else "-1500"),
                                     ("Right Margin", "324", "450" if portrait else "1500")):
                ET.SubElement(title, "param", {"name": name,
                                               "key": f"9999/999166631/999166633/2/{key}", "value": value})
            text = ET.SubElement(title, "text")
            plain, highlight = f"ts{id_start + count}p", f"ts{id_start + count}h"
            line_length = 0
            for n, (label, owner) in enumerate(display):
                color = HIGHLIGHT_COLOR if owner == index else "1 1 1 1"
                if n:
                    separator = "\n" if line_length + 1 + len(label) > LINE_LENGTH else " "
                    text[-1].text += separator
                    line_length = len(label) if separator == "\n" else line_length + 1 + len(label)
                else:
                    line_length = len(label)
                ET.SubElement(text, "text-style", {"ref": highlight if owner == index else plain,
                                                    "fontColor": color}).text = label
            for style_id, color in ((plain, "1 1 1 1"), (highlight, HIGHLIGHT_COLOR)):
                definition = ET.SubElement(title, "text-style-def", {"id": style_id})
                ET.SubElement(definition, "text-style", {"font": "Arial Black", "fontSize": str(font_size),
                                                           "fontColor": color, "alignment": "center"})
            if portrait:
                ET.SubElement(title, "adjust-transform", {"position": "0 -28"})
            count += 1
    return count
