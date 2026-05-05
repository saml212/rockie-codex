#!/usr/bin/env bash
# clean-finalize.sh — final action of $clean.
#
# Writes the audit sentinel that pre-commit-gate.sh reads, AND emits the
# upstream-contribute nudge to stderr so Codex CLI surfaces it to the
# agent's context every time the audit passes (per EVENT-MAPPING:
# stderr from hooks/scripts flows directly to the agent). The nudge is
# no longer prose-only in SKILL.md — putting it here makes it
# deterministic.
#
# Usage:
#   clean-finalize.sh <staged-hash>
#
# Called from agents/skills/clean/audit.py after a zero-blocker pass.
# Sentinel format must stay compatible with hooks/pre-commit-gate.sh
# (touch-style marker file at .codex/.state/clean-ok-<hash>).
set -u

STAGED_HASH="${1:-}"
if [ -z "$STAGED_HASH" ]; then
  echo "usage: clean-finalize.sh <staged-hash>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$ROOT/.state"
mkdir -p "$STATE_DIR"

# Drop stale sentinels from prior staged sets so /.state doesn't grow
# unbounded. Mirrors what audit.py used to do inline.
for old in "$STATE_DIR"/clean-ok-*; do
  [ -e "$old" ] || continue
  rm -f "$old"
done

SENTINEL="$STATE_DIR/clean-ok-$STAGED_HASH"
TS="$(date -Iseconds 2>/dev/null || date)"
printf 'clean ok %s\n' "$TS" > "$SENTINEL"

# Nudge — printed to stderr so Codex CLI surfaces it as a hook message
# the agent can't silently ignore. One short paragraph; the agent
# decides whether to act on it. Never auto-invokes $upstream-contribute.
cat >&2 <<'NUDGE'
[clean] audit clean. anything in this session worth upstreaming to
saml212/rockie-codex? (skills, hooks, memory schema improvements,
pruning patterns — anything generalizable across research domains).
run $upstream-contribute to scan and propose. user-driven; never
auto-pushes.
NUDGE

# Tell stdout where the sentinel landed; audit.py prints this back to
# the agent in its human-readable summary.
echo "$SENTINEL"
