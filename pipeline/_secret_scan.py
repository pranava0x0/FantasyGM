"""Pre-commit guard: refuse to commit anything that looks like an ESPN cookie.

Run manually: `python -m pipeline._secret_scan`
Or wire as a git pre-commit hook (see scripts/install_hooks.sh).

Exits 0 if no patterns matched; 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys

# Patterns that should NEVER appear in committed code.
# Each pattern is (label, compiled-regex).
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ESPN espn_s2 cookie value", re.compile(r"espn_s2\s*=\s*[A-Za-z0-9%]{30,}")),
    ("ESPN SWID cookie value",    re.compile(r"SWID\s*=\s*\{[0-9A-Fa-f-]{30,}\}")),
    ("Generic curly-brace UUID",  re.compile(r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}")),
]

# Files that intentionally describe the patterns (docs). Stripped from results.
ALLOWLIST_PATHS = {"security.md", ".env.example", "pipeline/_secret_scan.py", "README.md"}


def staged_diff() -> str:
    out = subprocess.run(
        ["git", "diff", "--cached", "--unified=0"],
        capture_output=True, text=True, check=False,
    )
    return out.stdout


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=False,
    )
    return [p for p in out.stdout.splitlines() if p.strip()]


def main() -> int:
    diff = staged_diff()
    if not diff:
        return 0

    hits: list[str] = []
    # Walk file-by-file so we can apply ALLOWLIST_PATHS.
    current_file: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/"):]
            continue
        if line.startswith("---") or not line.startswith("+"):
            continue
        if current_file in ALLOWLIST_PATHS:
            continue
        added = line[1:]
        for label, pat in PATTERNS:
            if pat.search(added):
                hits.append(f"{current_file}: {label}: {added.strip()[:80]}")

    if hits:
        print("Refusing to commit — possible secret leak:", file=sys.stderr)
        for h in hits:
            print(f"  - {h}", file=sys.stderr)
        print(
            "\nIf these are false positives, add the file to ALLOWLIST_PATHS "
            "in pipeline/_secret_scan.py and re-commit.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
