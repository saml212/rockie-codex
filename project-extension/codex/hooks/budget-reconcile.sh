#!/usr/bin/env bash
# UserPromptSubmit hook: continuously reconcile the dollars budget
# against LIVE provider state, so budget-gate enforces against reality.
#
# TTL: skip if last reconcile was less than RECONCILE_TTL_SECS ago.
# Default 120s — cheap enough to fire every couple of prompts but
# doesn't spam the compute API.
#
# Fail-open: any error exits 0 so a broken hook never blocks a session.
set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
bash "$ROOT/scripts/rotate_hook_log.sh" 2>/dev/null
RECONCILE_TTL_SECS=${RECONCILE_TTL_SECS:-120}

STATE_DIR="$ROOT/.state"
mkdir -p "$STATE_DIR"
LAST_FILE="$STATE_DIR/last_reconcile_ts"

# Source .env so the compute credential is visible to the Python script.
if [ -f "$REPO/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$REPO/.env" 2>/dev/null
  set +a
fi
# Skip in non-router modes — no compute state for THIS hook to reconcile.
# (Custom-mode users own their own accounting; they can wire their own
# reconcile in their gpu-custom.md flow if they want.)
case "${ROCKIE_GPU_MODE:-router}" in
  custom|none) exit 0 ;;
esac
# Skip if no compute credential configured — no point querying.
if [ -z "${GPU_API_KEY:-}" ]; then
  exit 0
fi

# TTL check
NOW=$(date +%s)
if [ -f "$LAST_FILE" ]; then
  LAST=$(cat "$LAST_FILE" 2>/dev/null || echo 0)
  DELTA=$((NOW - LAST))
  if [ "$DELTA" -lt "$RECONCILE_TTL_SECS" ]; then
    exit 0   # still fresh
  fi
fi

echo "[$(date -Iseconds)] budget-reconcile: fired" >> "$ROOT/memory/hook.log"

# Cross-provider reconcile: pulls live state from every configured
# provider, sums into budget_usage.dollars. Fail-open — reconcile
# errors must never block a session.
python3 "$ROOT/scripts/gpu.py" reconcile --quiet >/dev/null 2>&1 || true

exit 0
