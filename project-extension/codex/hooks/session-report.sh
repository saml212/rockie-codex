#!/usr/bin/env bash
# SessionStart hook: emit an onboarding status report INTO THE AGENT'S
# CONTEXT so it sees full project state on its first turn.
#
# Codex's SessionStart hook semantics (verified 2026-04-24):
#   * stderr → shown in the startup banner to the human; NOT injected
#     into the agent's system prompt.
#   * stdout JSON envelope with hookSpecificOutput.additionalContext →
#     prepended to the agent's system prompt for the session.
#
# So the report goes to stdout wrapped in JSON. The human still sees
# the "session-report: fired" log line for debuggability.
set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"

# Stamp session start time for the wallclock budget counter.
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("session_id",""))
except: print("")' 2>/dev/null)
if [ -n "$SESSION_ID" ]; then
  mkdir -p "$ROOT/.state"
  date +%s > "$ROOT/.state/session_start_$SESSION_ID"
fi
bash "$ROOT/scripts/rotate_hook_log.sh" 2>/dev/null
echo "[$(date -Iseconds)] session-report: fired" >> "$ROOT/memory/hook.log"

# Load .env so session_report.py sees GPU_API_KEY etc.
if [ -f "$REPO/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$REPO/.env" 2>/dev/null
  set +a
fi

# Generate the report and wrap in the JSON envelope Codex expects.
REPORT_TXT=$(python3 "$ROOT/scripts/session_report.py" 2>/dev/null)
if [ -z "$REPORT_TXT" ]; then
  # Fail-open: don't break the session on a report generation error.
  echo "[session-report] session_report.py returned empty (non-fatal)" >&2
  exit 0
fi

# hookSpecificOutput.additionalContext is how SessionStart hooks inject
# text into the agent's system prompt in Codex ≥ 2.0. Use
# python's json module to guarantee valid escaping even if the report
# contains quotes, newlines, backslashes, or non-ASCII.
REPORT_TXT="$REPORT_TXT" python3 <<'PY'
import json, os
ctx = os.environ.get("REPORT_TXT", "")
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": ctx,
    }
}))
PY

exit 0
