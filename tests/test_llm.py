#!/usr/bin/env python3
"""Tests for the LLM reformatting layer. No server, no model, no network.

    python3 tests/test_llm.py

Covers prompt construction and output cleanup — the parts that must be right
before a model is ever involved, and the parts that stay broken silently if they
are wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.llm import (  # noqa: E402
    DEFAULT_PROFILE,
    PROFILES,
    LlamaReformatter,
    Reformatted,
    _strip_wrapping,
)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(name)


def test_profiles() -> None:
    print("=== profiles ===")
    for want in ("raw", "clean", "slack", "commit", "email"):
        check(f"{want!r} exists", want in PROFILES)
    check("default is 'clean' (AI-cleaned is the default mode)",
          DEFAULT_PROFILE == "clean")
    check("raw has no system prompt", PROFILES["raw"].system_prompt == "")
    check("commit is capped short", PROFILES["commit"].max_tokens <= 64)
    check("email allows more room", PROFILES["email"].max_tokens >= 256)


def test_system_prompt() -> None:
    """The injection guard and fidelity clause must be in every non-raw prompt."""
    print("=== system prompt construction ===")
    for name, profile in PROFILES.items():
        if name == "raw":
            continue
        sys_prompt = profile.build_system()
        check(f"{name}: refuses to obey the transcript",
              "never answer" in sys_prompt.lower() and "data" in sys_prompt.lower())
        check(f"{name}: forbids inventing detail",
              "never invent" in sys_prompt.lower())
        check(f"{name}: suppresses preamble",
              "no preamble" in sys_prompt.lower())

    with_vocab = PROFILES["clean"].build_system(["voicekb"])
    check("vocabulary is injected when supplied", "voicekb" in with_vocab)
    check("vocabulary absent when not supplied",
          "voicekb" not in PROFILES["clean"].build_system())


def test_strip_wrapping() -> None:
    """Small models add preamble and quotes despite being told not to."""
    print("=== output cleanup ===")
    check("strips wrapping double quotes", _strip_wrapping('"hello there"') == "hello there")
    check("strips wrapping single quotes", _strip_wrapping("'hello there'") == "hello there")
    check("keeps quotes that are part of the content",
          _strip_wrapping('He said "hi" to me') == 'He said "hi" to me')
    check("keeps an apostrophe-containing sentence intact",
          _strip_wrapping("it's fine") == "it's fine")
    check("strips 'Here is the rewritten text:'",
          _strip_wrapping("Here is the rewritten text: hello") == "hello")
    check("strips 'Output:'", _strip_wrapping("Output: hello") == "hello")
    check("removes leaked transcript tags",
          _strip_wrapping("<transcript>hello</transcript>") == "hello")
    check("leaves clean text alone", _strip_wrapping("Fix the deploy") == "Fix the deploy")


def test_raw_bypass() -> None:
    print("=== raw profile bypasses the model ===")
    r = LlamaReformatter(base_url="http://127.0.0.1:1")  # nothing listening
    out = r.reformat("hello there", "raw")
    check("raw returns input unchanged", out.text == "hello there")
    check("raw does no work", out.elapsed_seconds == 0.0)
    check("empty input short-circuits", r.reformat("   ", "clean").text == "   ")


def test_unknown_profile() -> None:
    print("=== error handling ===")
    r = LlamaReformatter()
    try:
        r.reformat("hi", "nonsense")
        check("unknown profile raises", False)
    except KeyError:
        check("unknown profile raises KeyError", True)
    check("available() is False with nothing listening",
          not LlamaReformatter(base_url="http://127.0.0.1:1").available())
    check("Reformatted defaults to unchanged",
          Reformatted(text="x", profile="clean", elapsed_seconds=0.0).changed is False)


def main() -> int:
    for fn in (test_profiles, test_system_prompt, test_strip_wrapping,
               test_raw_bypass, test_unknown_profile):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
