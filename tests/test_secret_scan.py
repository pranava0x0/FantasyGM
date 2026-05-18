"""Tests for the pre-commit secret scanner.

Critical: this scanner is the last line of defense before cookies hit
git history. Every pattern it claims to catch must be exercised here.
"""

from __future__ import annotations

import re

import pytest

from pipeline._secret_scan import PATTERNS


# Catalog of known-bad strings the scanner MUST flag. Format:
# (label, sample). Synthetic — never paste a real cookie here.
LEAK_SAMPLES: list[tuple[str, str]] = [
    ("uuid in braces (SWID shape)",
     "{ABCDEF12-3456-7890-ABCD-EF1234567890}"),
    ("env line: ESPN_SWID=...",
     "ESPN_SWID={ABCDEF12-3456-7890-ABCD-EF1234567890}"),
    ("env line: ESPN_S2=...",
     "ESPN_S2=" + "Z" * 100),
    ("bare cookie pair: SWID=",
     "SWID={ABCDEF12-3456-7890-ABCD-EF1234567890}"),
    ("bare cookie pair: espn_s2=",
     "espn_s2=" + "Q" * 60),
    ("URL-encoded blob (espn_s2 mid-value)",
     "AaAaAaAa%2BBbBbBbBb%2FCcCcCcCc%2BDdDdDdDdEeEeEeEeFfFfFfFfGgGgGgGg"),
]

# Strings that LOOK suspicious but should NOT trigger the scanner —
# either common base64-ish IDs or doc placeholders. Helps us catch
# over-zealous regex changes.
SAFE_SAMPLES: list[tuple[str, str]] = [
    ("git commit hash", "a3a66d6"),
    ("short base64 token (no % escapes)", "AbCdEf123456789"),
    ("just curly braces, no UUID", "{not a uuid}"),
    ("docs placeholder", "ESPN_SWID="),
]


def _any_pattern_matches(text: str) -> bool:
    return any(p.search(text) for _, p in PATTERNS)


@pytest.mark.parametrize("label,sample", LEAK_SAMPLES, ids=[s[0] for s in LEAK_SAMPLES])
def test_known_leak_shapes_are_caught(label: str, sample: str) -> None:
    assert _any_pattern_matches(sample), f"scanner failed to catch: {label}"


@pytest.mark.parametrize("label,sample", SAFE_SAMPLES, ids=[s[0] for s in SAFE_SAMPLES])
def test_safe_strings_do_not_trigger(label: str, sample: str) -> None:
    assert not _any_pattern_matches(sample), f"scanner false-positive on: {label}"


def test_patterns_are_compiled_regexes() -> None:
    for label, p in PATTERNS:
        assert isinstance(p, re.Pattern), f"{label!r} is not a compiled regex"
