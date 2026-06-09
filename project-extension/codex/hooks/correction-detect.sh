#!/usr/bin/env bash
# UserPromptSubmit hook: advisory detection of correction language.
# Emits a prompt-context nudge on stdout so Codex knows the user is correcting it and should
# consider emitting a [LEARN] block. Also increments sessions.corrections_count.
# Does NOT block the prompt — fail-open.
set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/memory/workflow.db"
bash "$ROOT/scripts/rotate_hook_log.sh" 2>/dev/null
echo "[$(date -Iseconds)] correction-detect: fired" >> "$ROOT/memory/hook.log"

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)

[ -z "$PROMPT" ] && exit 0

# High-confidence correction patterns — targeted to avoid false positives
# on normal developer speech ("I should probably..." is not a correction).
if echo "$PROMPT" | grep -qE -i \
  "that'?s (wrong|not right|incorrect|not what (i|we))|you (forgot|shouldn'?t have|should have|need to)|wrong (file|function|variable|approach|directory|repo)|(undo|revert|rollback|roll back) that|i (said|told you|already told)|no,? (don'?t|not|it should|you should)|stop doing that"; then

  # Plain stdout on UserPromptSubmit becomes additional context in Codex.
  echo "[correction-detect] User appears to be correcting you. If this is a durable rule, emit a [LEARN] block in your response so it is saved to the learnings DB."

  # Increment counter via python3 + sqlite3 bindings (parameterized, so
  # session_id/project are never shell- or SQL-interpolated).
  if [ -n "$SESSION_ID" ] && [ -f "$DB" ]; then
    PROJECT=$(basename "$(dirname "$ROOT")")
    SESSION_ID="$SESSION_ID" PROJECT="$PROJECT" DB="$DB" python3 <<'PYEOF' 2>/dev/null
import os, sqlite3
db = os.environ["DB"]; sid = os.environ["SESSION_ID"]; proj = os.environ["PROJECT"]
conn = sqlite3.connect(db)
conn.execute("PRAGMA trusted_schema=1")
conn.execute(
    "INSERT OR IGNORE INTO sessions (id, project) VALUES (?, ?)",
    (sid, proj),
)
conn.execute(
    "UPDATE sessions SET corrections_count = corrections_count + 1 WHERE id = ?",
    (sid,),
)
conn.commit(); conn.close()
PYEOF
  fi
fi

exit 0
