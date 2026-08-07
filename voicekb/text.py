"""Turn whisper output into something a US HID keyboard can actually type.

whisper emits real typography — curly quotes, em dashes, ellipsis characters,
accented letters — none of which exist on a US keyboard. Sent as-is they are
silently dropped, so "it's" arrives as "its" and nobody can explain why. This
module folds them to ASCII equivalents before anything reaches the wire.

It also strips whisper's own annotations: the CLI emits markers like
[BLANK_AUDIO] and (upbeat music) for non-speech, which must never be typed.
"""

from __future__ import annotations

import re
import unicodedata

# Characters with a deliberate ASCII equivalent. Order matters only in that
# every key must be a single character.
_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",  # single quotes
    "“": '"', "”": '"', "„": '"', "‟": '"',  # double quotes
    "–": "-", "—": "-", "―": "-", "−": "-",  # dashes
    "‐": "-", "‑": "-",
    "…": "...",  # ellipsis
    " ": " ", " ": " ", " ": " ", " ": " ",  # exotic spaces
    "•": "-",  # bullet
    "´": "'", "ʼ": "'", "`": "`",
    "×": "x",
    "€": "EUR", "£": "GBP", "¥": "JPY",
}

# whisper's non-speech annotations. It brackets them consistently, which is what
# makes them safe to strip wholesale.
_ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*(?:music|silence|blank|noise|laugh)[^)]*\)",
                         re.IGNORECASE)


def strip_annotations(text: str) -> str:
    """Remove [BLANK_AUDIO]-style markers whisper emits for non-speech."""
    return _ANNOTATION.sub("", text)


def fold_to_ascii(text: str) -> str:
    """Replace typographic characters with ASCII equivalents.

    Two passes: an explicit table for characters with a sensible mapping, then
    Unicode decomposition to strip diacritics (café -> cafe). Anything still
    non-ASCII after that has no keyboard equivalent and is dropped rather than
    mangled.
    """
    out = "".join(_FOLD.get(ch, ch) for ch in text)
    # NFKD splits "é" into "e" + combining acute; dropping the combining marks
    # leaves the base letter.
    decomposed = unicodedata.normalize("NFKD", out)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in stripped if ord(ch) < 128)


def collapse_whitespace(text: str) -> str:
    """Squeeze runs of whitespace, preserving intentional newlines."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def is_non_speech(text: str) -> bool:
    """True if whisper heard a sound rather than words, and nothing should be typed.

    whisper describes non-speech audio in parentheses, and the vocabulary is
    open-ended — a breath blast on the mic came back as "(swooshing)", which no
    keyword list would have anticipated. So rather than trying to enumerate the
    words, the rule is structural: if the entire utterance is one parenthesized
    or bracketed phrase, it is a sound description, not speech.

    This deliberately does not touch parentheses used *within* a sentence, so
    dictating "call foo(bar) now" still works.
    """
    stripped = collapse_whitespace(strip_annotations(text))
    if not stripped:
        return True
    # Entire remaining text wrapped in one pair of brackets.
    return bool(re.fullmatch(r"[(\[][^()\[\]]*[)\]][.!?]?", stripped))


def normalize_for_hid(text: str) -> str:
    """Full pipeline: annotations out, typography folded, whitespace tidied.

    Returns "" for non-speech, so callers can treat empty as "type nothing".
    """
    if is_non_speech(text):
        return ""
    return collapse_whitespace(fold_to_ascii(strip_annotations(text)))
