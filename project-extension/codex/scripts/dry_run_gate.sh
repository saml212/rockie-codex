#!/usr/bin/env bash
# dry-run-gate.sh — record a successful dry-run for a given training script.
# Used BY the user/agent before launching a long training job; a matching
# sentinel lets the pre-train-gate hook (hooks/pre-train-gate.sh) allow the
# real run through.
#
# The pattern: before hitting GPU, you must prove you can do 2 forward-
# backward passes on a toy shape. Ported from arXiv 2604.05854 (Deep
# Researcher Agent §Safety): 18% of experiments caught pre-training.
#
# Usage:
#   dry_run_gate.sh register <training-script-path>  # computes hash, writes sentinel
#   dry_run_gate.sh check    <training-script-path>  # exit 0 if valid sentinel, else 2
#   dry_run_gate.sh list                             # show valid sentinels
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/.state/dry-run-ok"
mkdir -p "$STATE"

# Portable sha256 wrapper: prefer sha256sum (Linux default), then shasum
# (macOS default), then python hashlib.
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    python3 -c 'import sys,hashlib;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
  fi
}
sha256_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  else
    python3 -c 'import sys,hashlib;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  fi
}

hash_script() {
  local p=$1
  [ ! -f "$p" ] && { echo "no such script: $p" >&2; return 2; }
  local sig=""
  sig+=$(sha256 "$p")
  local dir; dir=$(dirname "$p")
  for f in requirements.txt pyproject.toml environment.yml; do
    [ -f "$dir/$f" ] && sig+="$(sha256 "$dir/$f")"
  done
  printf '%s' "$sig" | sha256_stdin
}

cmd="${1:-}"
case "$cmd" in
  register)
    SCRIPT="${2:-}"; [ -z "$SCRIPT" ] && { echo "usage: $0 register <script>" >&2; exit 2; }
    H=$(hash_script "$SCRIPT") || exit 2
    touch "$STATE/$H"
    echo "[dry-run-gate] registered sentinel for $SCRIPT (hash=${H:0:12})"
    ;;
  check)
    SCRIPT="${2:-}"; [ -z "$SCRIPT" ] && { echo "usage: $0 check <script>" >&2; exit 2; }
    H=$(hash_script "$SCRIPT") || exit 2
    if [ -f "$STATE/$H" ]; then
      exit 0
    fi
    echo "[dry-run-gate] BLOCKED: no valid dry-run sentinel for $SCRIPT" >&2
    echo "  expected: $STATE/$H" >&2
    echo "  run your smoke test (forward+backward+grad check), then:" >&2
    echo "    bash .codex/scripts/dry_run_gate.sh register $SCRIPT" >&2
    exit 2
    ;;
  list)
    find "$STATE" -maxdepth 1 -mindepth 1 -printf '%TY-%Tm-%Td %TH:%TM %f\n' 2>/dev/null
    ;;
  *)
    echo "usage: $0 {register|check|list} [script]" >&2
    exit 2
    ;;
esac
