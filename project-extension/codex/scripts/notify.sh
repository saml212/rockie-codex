#!/usr/bin/env bash
# notify.sh — fire-and-forget notification to the user's phone via ntfy.sh.
# Records each send in workflow.db.notifications so we can match user replies
# back to the original and avoid re-notifying about the same thing.
#
# Usage:
#   notify.sh <tier> <title> <body> [correlation_id]
#
# Tiers:
#   1 = critical (Priority: max, wakes phone; use sparingly)
#   2 = review   (Priority: high, normal push)
#   3 = info     (Priority: low, silent / tray only)
#
# Env overrides:
#   NTFY_TOPIC    — defaults to value below
#   NTFY_SERVER   — defaults to https://ntfy.sh
#
# Always exits 0 so a caller relying on notifications never fails because
# of notification problems (the "never idle" invariant).
set +e

NTFY_TOPIC="${NTFY_TOPIC:-}"
NTFY_SERVER="${NTFY_SERVER:-https://ntfy.sh}"

if [ -z "$NTFY_TOPIC" ]; then
  echo "notify.sh: NTFY_TOPIC not set — skipping push. See docs/ntfy-setup.md." >&2
  exit 0
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/memory/workflow.db"

TIER="${1:-3}"
TITLE="${2:-autopilot}"
BODY="${3:-}"
CORR="${4:-}"

# Tier → ntfy priority
case "$TIER" in
  1) PRIORITY="max";    TAGS="rotating_light,robot_face" ;;
  2) PRIORITY="high";   TAGS="eyes,robot_face"            ;;
  3) PRIORITY="low";    TAGS="information_source,robot_face" ;;
  *) PRIORITY="default"; TAGS="robot_face" ;;
esac

[ -z "$BODY" ] && { echo "notify.sh: empty body, skipping" >&2; exit 0; }

# Fire-and-forget via curl. 5s connect timeout, 10s total timeout.
# The ntfy response contains the message id we can correlate later.
RESPONSE=$(curl -sS --max-time 10 \
  -H "Title: $TITLE" \
  -H "Priority: $PRIORITY" \
  -H "Tags: $TAGS" \
  ${CORR:+-H "X-Correlation: $CORR"} \
  -d "$BODY" \
  "$NTFY_SERVER/$NTFY_TOPIC" 2>/dev/null)

NTFY_ID=$(echo "$RESPONSE" | python3 -c 'import sys,json
try: print(json.loads(sys.stdin.read()).get("id",""))
except: print("")' 2>/dev/null)

# Record in DB — parameterized, so TITLE/BODY/NTFY_ID/CORR can't SQL-inject.
# This matters because $NTFY_ID comes from the ntfy server response: a
# compromised or malicious ntfy would otherwise have a DB write vector.
if [ -f "$DB" ]; then
  TIER="$TIER" TITLE="$TITLE" BODY="$BODY" NTFY_TOPIC="$NTFY_TOPIC" \
  NTFY_ID="$NTFY_ID" CORR="$CORR" DB="$DB" python3 <<'PYEOF' 2>/dev/null
import os, sqlite3
conn = sqlite3.connect(os.environ["DB"])
conn.execute("PRAGMA trusted_schema=1")
conn.execute(
    "INSERT INTO notifications (tier, title, body, topic, ntfy_id, correlation) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (
        int(os.environ["TIER"]),
        os.environ["TITLE"],
        os.environ["BODY"],
        os.environ["NTFY_TOPIC"],
        os.environ["NTFY_ID"] or None,
        os.environ["CORR"] or None,
    ),
)
conn.commit(); conn.close()
PYEOF
fi

# Secondary channel: macOS notification. We escape by passing values via
# argv to osascript so AppleScript never sees bare interpolated text —
# osascript's -e buffer would still interpolate, so we use stdin instead.
if command -v osascript >/dev/null 2>&1; then
  TITLE="$TITLE" BODY="$BODY" TIER="$TIER" osascript <<'APPLESCRIPT' 2>/dev/null &
on run
  set theTitle to (system attribute "TITLE")
  set theBody to (system attribute "BODY")
  if length of theBody > 240 then set theBody to (text 1 thru 240 of theBody) & "…"
  set theTier to (system attribute "TIER")
  display notification theBody with title theTitle subtitle ("autopilot · tier " & theTier)
end run
APPLESCRIPT
fi

echo "notify: tier=$TIER title=\"$TITLE\" ntfy_id=$NTFY_ID" >&2
exit 0
