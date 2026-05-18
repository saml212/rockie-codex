#!/usr/bin/env bash
# Poll the ntfy topic for user replies. Writes any new user messages to stdout
# as one JSON object per line: {"id":..., "time":..., "message":..., "title":...}
#
# Filters out autopilot's own messages (tagged with robot_face).
#
# Cursor persisted at .codex/memory/ntfy_last_seen — only returns messages
# newer than the last successful poll. First call returns empty (primes cursor).
set +e

NTFY_TOPIC="${NTFY_TOPIC:-}"
NTFY_SERVER="${NTFY_SERVER:-https://ntfy.sh}"

if [ -z "$NTFY_TOPIC" ]; then
  exit 0
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CURSOR_FILE="$ROOT/memory/ntfy_last_seen"

# Read cursor — Unix timestamp of last seen message (default: now, so first
# call returns nothing and primes the cursor)
if [ -f "$CURSOR_FILE" ]; then
  SINCE=$(cat "$CURSOR_FILE")
else
  SINCE=$(date +%s)
  echo "$SINCE" > "$CURSOR_FILE"
  exit 0
fi

# Fetch messages since cursor (poll mode: returns immediately, no streaming)
RESPONSE=$(curl -sS --max-time 8 \
  "$NTFY_SERVER/$NTFY_TOPIC/json?poll=1&since=$SINCE" 2>/dev/null)

# Parse via python -c with response piped as stdin.
# Cursor file passed as argv. Can't use heredoc here — heredoc hijacks stdin
# even when a pipe is also present, so the response body never reaches python.
echo "$RESPONSE" | python3 -c '
import sys, json
cursor_file = sys.argv[1]
latest = 0
user_msgs = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except Exception:
        continue
    if m.get("event") != "message":
        continue
    tags = m.get("tags", []) or []
    ts = m.get("time", 0)
    if ts > latest:
        latest = ts
    # Autopilot-sent messages advance the cursor but are not emitted
    if "robot_face" in tags:
        continue
    user_msgs.append(m)

if cursor_file and latest > 0:
    with open(cursor_file, "w") as f:
        f.write(str(latest + 1))

for m in user_msgs:
    print(json.dumps({
        "id": m.get("id"),
        "time": m.get("time"),
        "message": m.get("message", ""),
        "title": m.get("title", ""),
    }))
' "$CURSOR_FILE"

exit 0
