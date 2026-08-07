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


def normalize_for_hid(text: str) -> str:
    """Full pipeline: annotations out, typography folded, whitespace tidied."""
    return collapse_whitespace(fold_to_ascii(strip_annotations(text)))
