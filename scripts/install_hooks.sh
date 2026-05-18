#!/usr/bin/env bash
# Install the project's git hooks.
#
# Sets up .git/hooks/pre-commit to run the FantasyGM secret scanner and
# pytest before every commit, so a forgetful `git commit` can't slip
# cookies, member IDs, or a broken pipeline past us.
#
# Idempotent — re-running just overwrites the hook.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-commit"

cat > "$HOOK_PATH" <<'HOOK'
#!/usr/bin/env bash
# FantasyGM pre-commit hook.
# - Runs pipeline/_secret_scan.py against the staged diff (blocks cookie leaks).
# - Runs the pytest suite (blocks failing test commits).
#
# Bypass at your own risk: `git commit --no-verify`. CLAUDE.md says never.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

if ! "$PY" -m pipeline._secret_scan; then
  echo "" >&2
  echo "pre-commit: secret scanner blocked the commit. Fix the issue, restage, and retry." >&2
  exit 1
fi

# Tests are fast (~0.3s). If they're slow in the future, gate behind an env flag.
if ! "$PY" -m pytest -q --no-header > /tmp/fgm-pretest.log 2>&1; then
  echo "" >&2
  echo "pre-commit: pytest failed. Last 20 lines:" >&2
  tail -20 /tmp/fgm-pretest.log >&2
  exit 1
fi

exit 0
HOOK
chmod +x "$HOOK_PATH"
echo "Installed: $HOOK_PATH"
