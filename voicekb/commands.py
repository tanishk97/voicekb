"""Spoken commands: say what you want instead of typing it.

Two kinds, both from the original design:

  "commit mode"        -> switch the active formatting profile
  "press enter"        -> send a real HID key event, not the words
  "go up twice"        -> the same key, repeated

THE SAFETY RULE: an utterance is only ever a command if it is *entirely* a
command. Substring matching would be a disaster here -- dictating "press enter
your name in the form" must type that sentence, not send Enter and swallow the
rest. Every pattern below is a full match against the whole utterance, and
anything that is not an exact command falls through to being typed verbatim.

That bias is deliberate. Failing to recognise a command costs one repeat;
misrecognising dictation as a command destroys what you said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

from .hid_keycodes import MODIFIER_NAMES, MOD_NONE, NAMED_KEYS, key_for_char

# Spoken profile names. Kept here rather than imported from llm.PROFILES so a
# missing llama-server cannot stop "raw mode" from working.
PROFILE_NAMES = ("raw", "clean", "slack", "commit", "email", "structured")

_WORD_COUNTS = {
    "once": 1, "twice": 2, "thrice": 3,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# Cap repeats. whisper mishearing a number should not send 500 arrow keys.
MAX_REPEAT = 20


@dataclass(frozen=True)
class ProfileCommand:
    profile: str


@dataclass(frozen=True)
class KeyCommand:
    key: str
    modifiers: int = MOD_NONE
    count: int = 1


# typing.Union rather than `X | Y`: the latter is evaluated at runtime and
# needs Python 3.10+, while the Mac-side dev venv is older than the Pi's 3.13.
Command = Union[ProfileCommand, KeyCommand]


def _normalise(text: str) -> str:
    """Lowercase, drop punctuation whisper adds, collapse spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[.,!?;:]+", " ", text.lower())).strip()


def _count_from(word: str | None) -> int:
    if not word:
        return 1
    word = word.strip()
    if word.isdigit():
        return max(1, min(MAX_REPEAT, int(word)))
    return max(1, min(MAX_REPEAT, _WORD_COUNTS.get(word, 1)))


_RE_PROFILE = re.compile(
    rf"^(?:switch (?:to )?|change (?:to )?|go (?:to )?|use )?"
    rf"({'|'.join(PROFILE_NAMES)})(?: mode| profile)$"
)

_VERBS = ("press", "hit", "send", "tap", "go", "type")
_RE_VERB = re.compile(rf"^(?:{'|'.join(_VERBS)})\s+(?P<rest>.+)$")

# Multi-word key names must be tried before single words, or "page up" parses as
# modifier "page" plus key "up".
_MULTIWORD_KEYS = ("page up", "page down")


def parse(text: str) -> "Command | None":
    """Return a Command if the whole utterance is one, else None."""
    norm = _normalise(text)
    if not norm:
        return None

    m = _RE_PROFILE.fullmatch(norm)
    if m:
        return ProfileCommand(profile=m.group(1))

    m = _RE_VERB.fullmatch(norm)
    if not m:
        return None
    words = m.group("rest").split()

    # Peel off the trailing repeat count: "... twice", "... 3 times".
    #
    # Only when something remains to repeat. "press 5" is the digit 5, not a
    # repeat of nothing -- a count with no key is not a count.
    count = 1
    if words and words[-1] in ("time", "times"):
        words.pop()
    if (len(words) > 1
            and (words[-1].isdigit() or words[-1] in _WORD_COUNTS)):
        count = _count_from(words.pop())
    # Trailing noise words: "press the enter key", "press up arrow".
    if words and words[-1] in ("key", "arrow"):
        words.pop()
    if words and words[0] == "the":
        words.pop(0)
    if not words:
        return None

    # Longest key match first, so "page up" is not read as modifier "page".
    key = None
    if len(words) >= 2 and " ".join(words[-2:]) in _MULTIWORD_KEYS:
        key = "".join(words[-2:])
        words = words[:-2]
    elif words[-1] in NAMED_KEYS:
        key = words.pop()
    elif len(words[-1]) == 1 and key_for_char(words[-1]) is not None:
        # A single printable character: "press z", "press slash". Needed because
        # macOS's Keyboard Setup Assistant asks for the key right of left Shift
        # (z on a US layout) and there was otherwise no way to answer it.
        key = words.pop()
    if key is None:
        # "press the big red button" is dictation, not a key we know.
        return None

    # Whatever precedes the key must be modifiers and nothing else. Any other
    # word means this was ordinary speech that merely began with a verb.
    modifiers = MOD_NONE
    for word in words:
        bit = MODIFIER_NAMES.get(word)
        if bit is None:
            return None
        modifiers |= bit

    return KeyCommand(key=key, modifiers=modifiers, count=count)


def describe(cmd: Command) -> str:
    if isinstance(cmd, ProfileCommand):
        return f"profile -> {cmd.profile}"
    mods = [n for n, b in (("cmd", 0x08), ("ctrl", 0x01), ("alt", 0x04), ("shift", 0x02))
            if cmd.modifiers & b]
    prefix = "+".join(mods) + "+" if mods else ""
    times = f" x{cmd.count}" if cmd.count > 1 else ""
    return f"key -> {prefix}{cmd.key}{times}"
