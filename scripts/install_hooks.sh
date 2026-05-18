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
# Hooks live in the common .git/hooks (shared across worktrees). Resolve via
# --git-common-dir so this works from a linked worktree too.
COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
case "$COMMON_GIT_DIR" in /*) ;; *) COMMON_GIT_DIR="$REPO_ROOT/$COMMON_GIT_DIR" ;; esac
HOOK_PATH="$COMMON_GIT_DIR/hooks/pre-commit"

cat > "$HOOK_PATH" <<'HOOK'
#!/usr/bin/env bash
# FantasyGM pre-commit hook.
# - Runs pipeline/_secret_scan.py against the staged diff (blocks cookie leaks).
# - Runs the pytest suite (blocks failing test commits).
#
# Bypass at your own risk: `git commit --no-verify`. CLAUDE.md says never.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
# In a worktree, $REPO_ROOT is the worktree (no .venv there). Also probe the
# main checkout via --git-common-dir so the venv-Python is found from any
# worktree.
COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
case "$COMMON_GIT_DIR" in /*) ;; *) COMMON_GIT_DIR="$REPO_ROOT/$COMMON_GIT_DIR" ;; esac
MAIN_REPO_ROOT="$(dirname "$COMMON_GIT_DIR")"

PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$MAIN_REPO_ROOT/.venv/bin/python"
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
