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


# Standalone hesitation sounds. Unambiguous -- these are never content.
_HESITATIONS = r"(?:um+|uh+|erm+|ehm+|hmm+|mm+|ah+|er)"

# Parenthetical verbal tics. Deliberately conservative: "like", "actually",
# "basically" and "right" all have legitimate uses and are NOT removed, because
# deleting real words is far worse than leaving a stray "like" in.
_TICS = r"(?:you know|i mean|sort of|kind of)"

# Trailing check-in tags. Speech habits, not content.
_TRAILING_TAGS = r"(?:you know what,?\s*right|if that makes sense|does that make sense|you know|right)"

_RE_HESITATION = re.compile(rf"\b{_HESITATIONS}\b[,]?\s*", re.IGNORECASE)
# Swallow the commas that bracket a mid-sentence tic, so "We should, you know,
# ship it" does not leave "We should, ship it".
_RE_TIC = re.compile(rf"\s*,?\s*\b{_TICS}\b\s*,?\s*", re.IGNORECASE)
_RE_LEADING_TIC = re.compile(rf"^\s*\b{_TICS}\b\s*,?\s*", re.IGNORECASE)
# Capture the sentence-final punctuation the tag carried, so it can be put back
# only if removing the tag left the sentence without any.
_RE_TRAILING_TAG = re.compile(
    rf"[,\s]*\b{_TRAILING_TAGS}\b[\s,]*([.?!]*)\s*$", re.IGNORECASE
)


def strip_fillers(text: str) -> str:
    """Remove hesitation sounds and verbal tics deterministically.

    This exists because a 1.5B model could not be trusted with it. Asked to
    remove filler, Qwen2.5-1.5B also silently dropped content -- "The build is
    red and I will look at it after lunch" came back as "I will look at the
    build after lunch", losing the only fact in the sentence. The content-overlap
    guard scores that 0.75, well above any usable floor, so it cannot be caught
    structurally either.

    Filler removal is a bounded, well-specified problem. A regex does it in
    microseconds, cannot hallucinate, and cannot delete a word that was not on
    the list. The LLM is better spent on profiles that genuinely transform text.

    Conservative by design: ambiguous words like "like" and "actually" are left
    alone, because leaving filler in is a much smaller failure than deleting
    meaning.
    """
    if not text.strip():
        return text

    # Drop the trailing tag, then restore its punctuation only if what remains
    # does not already end a sentence. Otherwise "...broke again. You know what,
    # right?" yields a stray "...broke again.?".
    match = _RE_TRAILING_TAG.search(text)
    if match:
        tail_punct = match.group(1)
        out = text[: match.start()].rstrip(" ,")
        if tail_punct and not out.endswith((".", "?", "!")):
            out += tail_punct[0]
    else:
        out = text

    out = _RE_LEADING_TIC.sub("", out)
    out = _RE_TIC.sub(" ", out)
    out = _RE_HESITATION.sub("", out)
    out = re.sub(r"\s+([,.?!])", r"\1", out)
    out = re.sub(r"^[\s,]+", "", out)
    out = collapse_whitespace(out)
    # Restore a capital if stripping a leading filler exposed a lowercase start.
    #
    # Gated on the source containing any uppercase at all, because whisper
    # sometimes returns entirely lowercase text. Capitalising only the first word
    # of an all-lowercase transcript would look like a typo rather than a fix.
    if out and out[:1].islower() and any(c.isupper() for c in text):
        out = out[0].upper() + out[1:]
    return out


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
