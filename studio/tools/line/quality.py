"""Shared full-sentence quality scores for Line and Find Takes."""

from importlib.metadata import version
import re
import unicodedata

from num2words import num2words
import rapidfuzz
from rapidfuzz import fuzz


PARTIAL_THRESHOLD = 75.0
NUMBER = re.compile(r"(?<!\w)(?P<number>-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)(?P<ordinal>st|nd|rd|th)?(?!\w)")


def _spoken_number(match: re.Match) -> str:
    number = match["number"].replace(",", "")
    try:
        if match["ordinal"] and "." not in number:
            return num2words(int(number), lang="en", to="ordinal")
        whole, point, fraction = number.partition(".")
        unsigned = whole.lstrip("-")
        # Preserve explicitly written leading/decimal zeroes in the reading.
        words = (" ".join(num2words(int(digit), lang="en") for digit in unsigned)
                 if len(unsigned) > 1 and unsigned.startswith("0")
                 else num2words(int(unsigned), lang="en"))
        if whole.startswith("-"):
            words = "minus " + words
        if point:
            words += " point " + " ".join(num2words(int(digit), lang="en") for digit in fraction)
        return words
    except (TypeError, ValueError, OverflowError):
        return match[0]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = NUMBER.sub(_spoken_number, text)
    text = "".join(" " if unicodedata.category(char) == "Pd" else char for char in text)
    return " ".join("".join(char for char in text
                            if char.isalnum() or char.isspace() or unicodedata.category(char).startswith("S")).split())


def similarity(script_text: str, transcript_text: str) -> float:
    script, transcript = normalize(script_text), normalize(transcript_text)
    # Empty/punctuation-only content must not receive a perfect similarity.
    return fuzz.ratio(script, transcript) if script and transcript else 0.0


def classify(script_text: str, transcript_text: str, *, partial_threshold: float = PARTIAL_THRESHOLD) -> str:
    if not 0 < partial_threshold <= 100:
        raise ValueError("The Partial threshold must satisfy 0 < Partial <= 100.")
    script, transcript = normalize(script_text), normalize(transcript_text)
    if not script or not transcript:
        return "flub"
    if script == transcript:
        return "exact"
    return "partial" if fuzz.ratio(script, transcript) >= partial_threshold else "flub"


def provenance() -> dict:
    return {"method": "rapidfuzz.fuzz.ratio", "rapidfuzz_version": rapidfuzz.__version__,
            "normalization": "NFKC, casefold, English number forms, hyphens as spaces, remove punctuation, collapse whitespace; preserve symbols",
            "num2words_version": version("num2words"), "number_language": "en",
            "exact_rule": "normalized_text_equality", "partial_threshold": PARTIAL_THRESHOLD,
            "score_scale": [0, 100]}
