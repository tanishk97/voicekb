"""ASCII and named keys to USB HID keyboard usage codes.

Pure lookup logic with no Bluetooth involved, so it is testable on any machine.
Codes come from the USB HID Usage Tables, Keyboard/Keypad page (0x07).

A boot-protocol keyboard report is 8 bytes:

    [modifiers, reserved, key1, key2, key3, key4, key5, key6]

We only ever press one key at a time plus modifiers, so key2..key6 stay zero.
"""

from __future__ import annotations

from typing import NamedTuple

# Modifier bitmask, byte 0 of the report.
MOD_NONE = 0x00
MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08  # Command on macOS
MOD_RCTRL = 0x10
MOD_RSHIFT = 0x20
MOD_RALT = 0x40
MOD_RGUI = 0x80

MODIFIER_NAMES = {
    "ctrl": MOD_LCTRL,
    "control": MOD_LCTRL,
    "shift": MOD_LSHIFT,
    "alt": MOD_LALT,
    "option": MOD_LALT,
    "cmd": MOD_LGUI,
    "command": MOD_LGUI,
    "gui": MOD_LGUI,
    "super": MOD_LGUI,
    "win": MOD_LGUI,
}


class Key(NamedTuple):
    usage: int
    modifiers: int = MOD_NONE


# Unshifted printable characters.
_BASE: dict[str, int] = {}
for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _BASE[ch] = 0x04 + i
for i, ch in enumerate("123456789"):
    _BASE[ch] = 0x1E + i
_BASE["0"] = 0x27
_BASE.update({
    "\n": 0x28,  # Enter
    "\x1b": 0x29,  # Escape
    "\b": 0x2A,  # Backspace
    "\t": 0x2B,
    " ": 0x2C,
    "-": 0x2D,
    "=": 0x2E,
    "[": 0x2F,
    "]": 0x30,
    "\\": 0x31,
    ";": 0x33,
    "'": 0x34,
    "`": 0x35,
    ",": 0x36,
    ".": 0x37,
    "/": 0x38,
})

# Characters produced by holding shift. The value is the *unshifted* character
# whose usage code we reuse.
_SHIFTED: dict[str, str] = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
    "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\",
    ":": ";", '"': "'", "~": "`", "<": ",", ">": ".", "?": "/",
}

# Non-printing keys addressable by name, for spoken commands like "press enter".
NAMED_KEYS: dict[str, int] = {
    "enter": 0x28, "return": 0x28,
    "escape": 0x29, "esc": 0x29,
    "backspace": 0x2A,
    "tab": 0x2B,
    "space": 0x2C,
    "capslock": 0x39,
    "f1": 0x3A, "f2": 0x3B, "f3": 0x3C, "f4": 0x3D, "f5": 0x3E, "f6": 0x3F,
    "f7": 0x40, "f8": 0x41, "f9": 0x42, "f10": 0x43, "f11": 0x44, "f12": 0x45,
    "home": 0x4A,
    "pageup": 0x4B,
    "delete": 0x4C, "del": 0x4C, "forwarddelete": 0x4C,
    "end": 0x4D,
    "pagedown": 0x4E,
    "right": 0x4F,
    "left": 0x50,
    "down": 0x51,
    "up": 0x52,
}


def key_for_char(ch: str) -> Key | None:
    """HID key for a single printable character, or None if unmappable."""
    if ch in _BASE:
        return Key(_BASE[ch], MOD_NONE)
    if ch in _SHIFTED:
        return Key(_BASE[_SHIFTED[ch]], MOD_LSHIFT)
    if ch.isupper() and ch.lower() in _BASE:
        return Key(_BASE[ch.lower()], MOD_LSHIFT)
    if ch == "\r":
        return Key(0x28, MOD_NONE)
    return None


def key_for_name(name: str, modifiers: int = MOD_NONE) -> Key | None:
    """HID key for a named key like "enter" or "up"."""
    usage = NAMED_KEYS.get(name.strip().lower().replace(" ", "").replace("_", ""))
    if usage is None:
        return None
    return Key(usage, modifiers)


def build_report(key: Key | None) -> bytes:
    """8-byte boot-protocol keyboard report. None yields the all-keys-up report."""
    if key is None:
        return bytes(8)
    return bytes([key.modifiers, 0x00, key.usage, 0, 0, 0, 0, 0])


RELEASE_REPORT = bytes(8)


def unmappable(text: str) -> list[str]:
    """Characters in `text` that cannot be typed on a US keyboard layout.

    Worth checking before sending: whisper happily emits smart quotes, em dashes,
    and accented characters that a US HID keyboard has no code for.
    """
    return sorted({ch for ch in text if key_for_char(ch) is None})
