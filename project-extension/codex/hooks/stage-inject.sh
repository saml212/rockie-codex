#!/usr/bin/env bash
# UserPromptSubmit hook: if a current stage is set via scripts/stage.py,
# scan the project AGENTS.md for a matching `## Stage: <name>` section
# and inject its body on stdout. Agent sees stage-specific context
# only when it's relevant.
set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_FILE="$ROOT/.state/current-stage"
bash "$ROOT/scripts/rotate_hook_log.sh" 2>/dev/null

[ ! -f "$STATE_FILE" ] && exit 0
STAGE=$(tr -d '[:space:]' < "$STATE_FILE")
[ -z "$STAGE" ] && exit 0

# AGENTS.md lives at the project repo root: <repo>/<AGENTS.md>
REPO_ROOT=$(cd "$ROOT/.." && pwd)
CLAUDE_MD="$REPO_ROOT/AGENTS.md"
[ ! -f "$CLAUDE_MD" ] && exit 0

echo "[$(date -Iseconds)] stage-inject: stage=$STAGE" >> "$ROOT/memory/hook.log"

# Extract the `## Stage: <STAGE>` section body.
# Section ends at next `## ` (same level) or EOF.
SECTION=$(awk -v want="$STAGE" '
  BEGIN{in_section=0}
  /^##[[:space:]]+Stage:[[:space:]]+/ {
    # extract stage name after "Stage: "
    gsub(/^##[[:space:]]+Stage:[[:space:]]+/, "", $0)
    sub(/[[:space:]]*$/, "", $0)
    in_section = ($0 == want) ? 1 : 0
    next
  }
  /^##[[:space:]]/ { in_section = 0 }
  { if (in_section) print }
' "$CLAUDE_MD")

if [ -n "$SECTION" ]; then
  echo "[stage-inject] Active stage: $STAGE — context specific to this stage:"
  printf '%s\n' "$SECTION" | sed 's/^/  /'
fi

exit 0
