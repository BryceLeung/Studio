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


def add_titles(clip, take, words, frame, effect_id, id_start=0):
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
        events.append((label, start_frame, end_frame))
    count = 0
    phrase = []
    groups = []
    for event in events:
        if phrase and (len(phrase) == 3 or len(" ".join(e[0] for e in phrase + [event])) > 24):
            groups.append(phrase)
            phrase = []
        phrase.append(event)
    if phrase:
        groups.append(phrase)
    for group in groups:
        font_size = min(FONT_SIZE, FONT_SIZE * LINE_LENGTH // max(len(word) for word, _, _ in group))
        for index, (_, start, end) in enumerate(group):
            next_start = group[index + 1][1] if index + 1 < len(group) else end
            finish = min(take["cut_out"], max(end, next_start)) if index + 1 == len(group) else next_start
            if finish <= start:
                raise ValueError(f"Subtitle words collide on one frame: {take['take_key']}")
            title = ET.SubElement(clip, "title", {"ref": effect_id, "lane": "1", "offset": time(start),
                                                  "name": " ".join(e[0] for e in group),
                                                  "start": "3600s", "duration": time(finish - start)})
            # Basic Title is point text by default. Request paragraph layout and
            # keep explicit word-boundary breaks for Final Cut versions that ignore it.
            for name, key, value in (("Layout Method", "314", "1 (Paragraph)"),
                                     ("Left Margin", "323", "-1500"),
                                     ("Right Margin", "324", "1500")):
                ET.SubElement(title, "param", {"name": name,
                                               "key": f"9999/999166631/999166633/2/{key}", "value": value})
            text = ET.SubElement(title, "text")
            plain, highlight = f"ts{id_start + count}p", f"ts{id_start + count}h"
            line_length = 0
            for n, (label, _, _) in enumerate(group):
                color = HIGHLIGHT_COLOR if n == index else "1 1 1 1"
                if n:
                    separator = "\n" if line_length + 1 + len(label) > LINE_LENGTH else " "
                    text[-1].text += separator
                    line_length = len(label) if separator == "\n" else line_length + 1 + len(label)
                else:
                    line_length = len(label)
                ET.SubElement(text, "text-style", {"ref": highlight if n == index else plain,
                                                    "fontColor": color}).text = label
            for style_id, color in ((plain, "1 1 1 1"), (highlight, HIGHLIGHT_COLOR)):
                definition = ET.SubElement(title, "text-style-def", {"id": style_id})
                ET.SubElement(definition, "text-style", {"font": "Arial Black", "fontSize": str(font_size),
                                                          "fontColor": color, "alignment": "center"})
            count += 1
    return count
