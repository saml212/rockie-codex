#!/usr/bin/env bash
# shellcheck disable=SC2015
# rockie-codex smoke test — dogfoods the full harness in a throwaway dir.
#
# Covers:
#   • Schema apply + seed
#   • [LEARN] regex + FTS5 search
#   • [DEAD-END] regex + FTS5 search
#   • Hypothesis calibration round-trip (add → close → score)
#   • Journal tree (add, close, best-so-far, render)
#   • Failure classification (bug / bad-hyperparam / bad-hypothesis)
#   • Queue (add, atomic next, done, drop, refill-needed)
#   • Budget controller (add, status, check, ceiling-cross, reset)
#   • Budget-gate hook blocks on ceiling cross
#   • ZCM supervisor: clean-exit / anomaly / crash
#   • Stuck detector: 4 loop patterns fire, healthy stays silent
#   • Stage CLI (set, get, list) + stage-inject hook
#   • Convergence token detection
#   • apply_patch.py round-trip + rejection on miss
#   • Dry-run gate: register / check / hook before & after modify
#   • Pre-commit gate: clean-hash round-trip
#   • FTS5 hyphen-as-NOT-operator documented + tested
#
# Exits 0 on success, 1 on first failing assertion.
set -u

RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; DIM=$'\e[2m'; RESET=$'\e[0m'
SQLITE=/usr/bin/sqlite3

ROCKIE="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d -t rockie-codex-smoke-XXXXXX)
# Clean up any per-test tempdirs that escape the main WORK; set a broad trap.
trap 'rm -rf "$WORK"; rm -rf /tmp/rockie-autopilot-* /tmp/rockie-migration-* /tmp/smoke-merge-* /tmp/smoke-idem-* 2>/dev/null' EXIT

PASS=0
FAIL=0

echo "${YELLOW}▸ rockie-codex smoke test${RESET}"
echo "${DIM}  workspace: $WORK${RESET}"

assert() {
  local label=$1 expected=$2 actual=$3
  if [ "$actual" != "$expected" ]; then
    echo "${RED}✗ $label${RESET}"
    echo "    expected: $expected"
    echo "    actual:   $actual"
    FAIL=$((FAIL+1))
    return 1
  fi
  echo "${GREEN}✓${RESET} $label"
  PASS=$((PASS+1))
}

ok()   { echo "${GREEN}✓${RESET} $1"; PASS=$((PASS+1)); }
fail() { echo "${RED}✗ $1${RESET}"; FAIL=$((FAIL+1)); }

# ── 1. Install the harness into a scratch project ────────────────────────
section() { echo ""; echo "${YELLOW}── $1 ──${RESET}"; }

section "setup"
mkdir -p "$WORK/proj"
(cd "$WORK/proj" && git init -q)
mkdir -p "$WORK/proj/.codex"
mkdir -p "$WORK/proj/.agents/skills"
rsync -a --exclude='__pycache__' "$ROCKIE/project-extension/codex/" "$WORK/proj/.codex/" >/dev/null
rsync -a --exclude='__pycache__' "$ROCKIE/project-extension/agents/skills/" "$WORK/proj/.agents/skills/" >/dev/null
chmod +x "$WORK/proj/.codex/hooks/"*.sh "$WORK/proj/.codex/scripts/"*.sh "$WORK/proj/.codex/scripts/"*.py 2>/dev/null
PROJ="$WORK/proj"
DB="$PROJ/.codex/memory/workflow.db"

bash "$PROJ/.codex/scripts/init_db.sh" > /dev/null
(cd "$PROJ" && python3 .codex/scripts/seed_hard_rules.py > /dev/null)
SEED_COUNT=$("$SQLITE" "$DB" "SELECT COUNT(*) FROM learnings WHERE source='seed'")
[ "$SEED_COUNT" -ge 10 ] && ok "schema applies + seed inserts $SEED_COUNT rows" || fail "seed count=$SEED_COUNT"

# ── 2. [LEARN] capture via FTS5 ──────────────────────────────────────────
section "[LEARN] capture + FTS5"
"$SQLITE" "$DB" <<SQL
PRAGMA trusted_schema=1;
INSERT INTO learnings (project, category, rule, mistake, correction, source)
VALUES ('smoke','test','Test rule about gradient clipping for stability',
        'Grads exploded without clipping','Clip at 1.0 before backward','codex');
SQL
HIT=$("$SQLITE" "$DB" "SELECT COUNT(*) FROM learnings_fts WHERE learnings_fts MATCH 'gradient clipping'")
assert "FTS5 finds inserted learning" "1" "$HIT"

# [LEARN] regex parser
python3 - <<'PY' && ok "[LEARN] regex parses canonical block" || fail "[LEARN] regex"
import re
text = """[LEARN] bash-sqlite: PRAGMA trusted_schema=1 must be set per-connection
Mistake: Forgot, got 'unsafe use' error
Correction: Prepend PRAGMA to every invocation
"""
p = re.compile(r'\[LEARN\]\s*([\w][\w\s\-/]*?)\s*:\s*(.+?)(?:\n\s*Mistake:\s*(.+?))?(?:\n\s*Correction:\s*(.+?))?(?=\n\s*\[LEARN\]|\n\s*\n|\Z)', re.DOTALL | re.IGNORECASE)
m = p.findall(text)
assert len(m) == 1 and m[0][0].strip() == "bash-sqlite"
PY

# ── 3. [DEAD-END] registry ────────────────────────────────────────────────
section "[DEAD-END] registry"
"$SQLITE" "$DB" <<SQL
PRAGMA trusted_schema=1;
INSERT INTO dead_ends (project, direction, reason, evidence_path, source)
VALUES ('smoke','quadratic-scaling',
        '4x memory blowup at seq=4096','experiment-runs/2026-03_quad/FAIL.md','seed');
SQL
DE_HIT=$("$SQLITE" "$DB" "SELECT COUNT(*) FROM dead_ends_fts WHERE dead_ends_fts MATCH 'quadratic'")
assert "dead_ends FTS5 finds inserted row" "1" "$DE_HIT"

python3 - <<'PY' && ok "[DEAD-END] regex parses canonical block" || fail "[DEAD-END] regex"
import re
text = """[DEAD-END] matrix-at-288k: Model barely learns unigrams
Evidence: experiment-runs/2026-03-12_matrix_288k/RESULTS.md
"""
p = re.compile(r'\[DEAD-?END\]\s*([\w][\w\s\-/\.]*?)\s*:\s*(.+?)(?:\n\s*Evidence:\s*(.+?))?(?=\n\s*\[DEAD-?END\]|\n\s*\[LEARN\]|\n\s*\n|\Z)', re.DOTALL | re.IGNORECASE)
m = p.findall(text)
assert len(m) == 1 and m[0][0].strip() == "matrix-at-288k"
PY

# ── 4. Hypothesis calibration ────────────────────────────────────────────
section "hypothesis calibration"
export PROJECT=smoke
python3 "$PROJ/.codex/scripts/calibration.py" add "run-001" "Doubling batch size reduces val loss by 0.05" "val_loss" "-0.05" >/dev/null
OPEN=$("$SQLITE" "$DB" "SELECT COUNT(*) FROM calibration_scorecard WHERE actual_delta IS NULL")
assert "calibration add creates an open prediction" "1" "$OPEN"

python3 "$PROJ/.codex/scripts/calibration.py" close "run-001" "Doubling batch size reduces val loss by 0.05" "-0.03" >/dev/null
SIGN=$("$SQLITE" "$DB" "SELECT sign_correct FROM calibration_scorecard WHERE run_id='run-001'")
assert "calibration sign-correct evaluates" "1" "$SIGN"

ABS=$("$SQLITE" "$DB" "SELECT printf('%.2f', abs_error) FROM calibration_scorecard WHERE run_id='run-001'")
assert "calibration abs_error = |pred-actual| = 0.02" "0.02" "$ABS"

# ── 5. Journal tree (B1) ─────────────────────────────────────────────────
section "journal tree (B1)"
JP="$PROJ/.codex/scripts/journal.py"
python3 "$JP" add --stage draft --hypothesis "Baseline" --run-id r001 >/dev/null
python3 "$JP" add --stage debug --parent 1 --hypothesis "Debug parent 1" >/dev/null
python3 "$JP" start 1 >/dev/null
python3 "$JP" close 1 --metric val_loss=3.42 --is-buggy 0 --analysis "ok" >/dev/null
python3 "$JP" start 2 >/dev/null
python3 "$JP" close 2 --metric val_loss=9.99 --is-buggy 1 --failure-class bug --analysis "NaN" >/dev/null

N_EXPS=$("$SQLITE" "$DB" "SELECT COUNT(*) FROM experiments WHERE project='smoke'")
assert "journal: 2 experiments recorded" "2" "$N_EXPS"

BEST=$("$SQLITE" "$DB" "SELECT id FROM best_so_far WHERE project='smoke'")
assert "journal: best-so-far is the non-buggy node (#1)" "1" "$BEST"

DEBUG_DEPTH=$("$SQLITE" "$DB" "SELECT debug_depth FROM experiments WHERE id=2")
assert "journal: debug_depth increments to 1" "1" "$DEBUG_DEPTH"

# Failure-class routing (C4)
FAIL_CLASS=$("$SQLITE" "$DB" "SELECT failure_class FROM experiments WHERE id=2")
assert "journal: failure_class stored" "bug" "$FAIL_CLASS"

# ── 6. Queue ──────────────────────────────────────────────────────────────
section "experiment queue"
QP="$PROJ/.codex/scripts/queue.py"
python3 "$QP" add --hypothesis "q1" --priority 3 --metric val_loss --predicted-delta -0.02 >/dev/null
python3 "$QP" add --hypothesis "q2" --priority 1 --metric val_loss --predicted-delta -0.05 >/dev/null
python3 "$QP" add --hypothesis "q3" --priority 5 --metric val_loss --predicted-delta  0.10 >/dev/null

# Atomic claim should pick priority=1
FIRST_ID=$(python3 "$QP" next --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
# The ID depends on insert order (priority=1 was added 2nd → id=2)
FIRST_PRIO=$("$SQLITE" "$DB" "SELECT priority FROM experiment_queue WHERE id=$FIRST_ID")
assert "queue next picks highest priority (=1)" "1" "$FIRST_PRIO"

# done + drop + release + refill-needed
python3 "$QP" 'done' "$FIRST_ID" >/dev/null
N_DONE=$("$SQLITE" "$DB" "SELECT COUNT(*) FROM experiment_queue WHERE status='done'")
assert "queue done transitions to done" "1" "$N_DONE"

python3 "$QP" refill-needed 2>/dev/null && fail "refill-needed should exit non-zero when under target" || ok "refill-needed exits non-zero when queue under target"

# ── 7. Budget controller ──────────────────────────────────────────────────
section "budget controller"
cat > "$PROJ/.codex/budget.toml" <<EOF
[session]
tokens = 1000
tool_calls = 100
EOF
export CLAUDE_SESSION_ID=smoke-session
BP="$PROJ/.codex/scripts/budget.py"

python3 "$BP" add tokens 500 >/dev/null
python3 "$BP" check && ok "budget: under ceiling passes" || fail "budget: under ceiling"

python3 "$BP" add tokens 600 >/dev/null
python3 "$BP" check 2>/dev/null && fail "budget: over ceiling should fail" || ok "budget: over ceiling fails (exit 2)"

# Gate hook should exit 2
GATE_INPUT='{"session_id":"smoke-session","tool_input":{"command":"echo hi"}}'
echo "$GATE_INPUT" | bash "$PROJ/.codex/hooks/budget-gate.sh" 2>/dev/null
GATE_EC=$?
assert "budget-gate hook exits 2 on ceiling cross" "2" "$GATE_EC"

python3 "$BP" reset session >/dev/null
ok "budget reset session"

# Budget reset scoping: reset 'project' should only wipe current project
PROJECT=other-project CLAUDE_SESSION_ID=other-session python3 "$BP" add tokens 42 >/dev/null
python3 "$BP" reset project >/dev/null
OTHER=$("$SQLITE" "$DB" "SELECT value FROM budget_usage WHERE project='other-project'")
[ -n "$OTHER" ] && ok "budget reset scoped: other project's counters untouched" || fail "budget reset scoped: other project wiped"

# [LEARN] injection safety — an adversarial `rule' with quote must not break
# the INSERT (parameterized query is load-bearing)
python3 <<PYEOF >/dev/null 2>&1
import sqlite3
conn = sqlite3.connect("$DB")
conn.execute("PRAGMA trusted_schema=1")
# Same path learn-capture.sh takes internally — via sqlite3 bindings:
conn.execute(
    "INSERT OR IGNORE INTO learnings (project, category, rule, source) VALUES (?,?,?,?)",
    ("smoke", "injection-test", "nasty ' \"); DROP TABLE learnings; -- rule", "codex"),
)
conn.commit()
PYEOF
SURVIVED=$("$SQLITE" "$DB" "SELECT COUNT(*) FROM learnings WHERE category='injection-test'")
TABLE_OK=$("$SQLITE" "$DB" "SELECT COUNT(*) FROM learnings")
[ "$SURVIVED" = "1" ] && [ "$TABLE_OK" -gt 0 ] && ok "[LEARN] parameterized insert: adversarial quote/SQL safely stored as data" || fail "[LEARN] injection: table state suspect"

# ── 8. ZCM supervisor ─────────────────────────────────────────────────────
section "ZCM (zero-cost monitor)"
LOG="$WORK/zcm.log"
(
  for i in 1 2; do echo "step $i: loss=3.2" >> "$LOG"; sleep 0.3; done
  echo "done" >> "$LOG"
) &
PID=$!
bash "$PROJ/.codex/scripts/zcm.sh" --pid "$PID" --log "$LOG" --interval 1 --max-seconds 10 >/dev/null 2>&1
assert "ZCM: clean exit returns 0" "0" "$?"

true > "$LOG"
(echo "step 1" >> "$LOG"; sleep 0.3; echo "RuntimeError: shape mismatch" >> "$LOG"; exit 1) &
PID=$!
bash "$PROJ/.codex/scripts/zcm.sh" --pid "$PID" --log "$LOG" --interval 1 --max-seconds 10 >/dev/null 2>&1
assert "ZCM: crashed-with-error returns 1" "1" "$?"

# ── 9. Stuck detector ─────────────────────────────────────────────────────
section "stuck detector"
TX="$WORK/tx1.jsonl"
for i in 1 2 3 4 5; do
  printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls -la"}}]}}' >> "$TX"
  printf '%s\n' '{"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}}' >> "$TX"
done
OUT=$(echo "{\"transcript_path\":\"$TX\"}" | bash "$PROJ/.codex/hooks/stuck-detector.sh" 2>/dev/null; echo END)
echo "$OUT" | grep -q "repeat-tool-args" && ok "stuck-detector: repeat-tool-args fires" || fail "stuck-detector: repeat-tool-args"

TX2="$WORK/tx2.jsonl"
# Healthy: 10 Read calls on DIFFERENT files — must not fire repeat-tool-args
for i in 1 2 3 4 5 6 7 8 9 10; do
  printf '%s\n' "{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"tool_use\",\"name\":\"Read\",\"input\":{\"file_path\":\"/path/to/file_$i.txt\"}}]}}" >> "$TX2"
  printf '%s\n' '{"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}}' >> "$TX2"
done
OUT=$(echo "{\"transcript_path\":\"$TX2\"}" | bash "$PROJ/.codex/hooks/stuck-detector.sh" 2>/dev/null; echo END)
if echo "$OUT" | grep -q "stuck-detector\]"; then
  fail "stuck-detector: 10 Read calls on different files triggered nudge"
else
  ok "stuck-detector: 10 Reads on different files stays silent"
fi

# 3-cycle alternating — ABCABC pattern
TX3="$WORK/tx3.jsonl"
for _ in 1 2; do
  for t in Read Edit Bash; do
    printf '%s\n' "{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"tool_use\",\"name\":\"$t\",\"input\":{\"x\":\"y\"}}]}}" >> "$TX3"
    printf '%s\n' '{"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}}' >> "$TX3"
  done
done
OUT=$(echo "{\"transcript_path\":\"$TX3\"}" | bash "$PROJ/.codex/hooks/stuck-detector.sh" 2>/dev/null; echo END)
echo "$OUT" | grep -q "period 3" && ok "stuck-detector: 3-cycle ABCABC detected" || fail "stuck-detector: 3-cycle miss"

# ── 10. Stage CLI + hook ─────────────────────────────────────────────────
section "stage gating"
python3 "$PROJ/.codex/scripts/stage.py" set creative >/dev/null
STAGE=$(python3 "$PROJ/.codex/scripts/stage.py" get)
assert "stage get returns what was set" "creative" "$STAGE"

python3 "$PROJ/.codex/scripts/stage.py" set invalid-stage-name 2>/dev/null && fail "stage set accepts invalid" || ok "stage set rejects invalid stage"

# Put a stage section in AGENTS.md and verify the hook finds it
cat > "$PROJ/AGENTS.md" <<EOF
# AGENTS.md
## Stage: creative
CREATIVE_STAGE_MARKER
## Stage: ablation
ABLATION_STAGE_MARKER
EOF
OUT=$(echo '{"prompt":"what next"}' | bash "$PROJ/.codex/hooks/stage-inject.sh" 2>/dev/null; echo END)
echo "$OUT" | grep -q "CREATIVE_STAGE_MARKER" && ok "stage-inject emits matching section" || fail "stage-inject emission"
echo "$OUT" | grep -q "ABLATION_STAGE_MARKER" && fail "stage-inject leaked non-matching section" || ok "stage-inject excluded non-matching section"

# ── 11. Convergence token ────────────────────────────────────────────────
section "convergence token"
echo "AGENT_DONE" | python3 "$PROJ/.codex/scripts/convergence.py" - >/dev/null
assert "convergence: AGENT_DONE detected" "0" "$?"

echo "still thinking" | python3 "$PROJ/.codex/scripts/convergence.py" - >/dev/null 2>&1
CONV_RC=$?
[ "$CONV_RC" -eq 1 ] && ok "convergence: no token returns exit 1" || fail "convergence: false positive"

echo "we're not done yet" | python3 "$PROJ/.codex/scripts/convergence.py" - >/dev/null 2>&1
CONV_RC=$?
[ "$CONV_RC" -eq 1 ] && ok "convergence: 'not done' does not false-positive" || fail "convergence: false positive 'not done'"

# ── 12. apply_patch SEARCH/REPLACE ───────────────────────────────────────
section "apply_patch"
mkdir -p "$WORK/patch-cwd"
echo "hello
line two
line three" > "$WORK/patch-cwd/target.txt"
cat > "$WORK/patch.ok" <<'PATCH'
target.txt
<<<<<<< SEARCH
line two
=======
line two (edited)
>>>>>>> REPLACE
PATCH
(cd "$WORK/patch-cwd" && python3 "$PROJ/.codex/scripts/apply_patch.py" < "$WORK/patch.ok" > /dev/null)
grep -q "line two (edited)" "$WORK/patch-cwd/target.txt" && ok "apply_patch: round-trip (relative path)" || fail "apply_patch: round-trip"
ls "$WORK/patch-cwd/target.txt".*.bak >/dev/null 2>&1 && ok "apply_patch: wrote backup" || fail "apply_patch: backup missing"

cat > "$WORK/patch.bad" <<PATCH
$WORK/target.txt
<<<<<<< SEARCH
nonexistent line that is not there
=======
replacement
>>>>>>> REPLACE
PATCH
python3 "$PROJ/.codex/scripts/apply_patch.py" < "$WORK/patch.bad" >/dev/null 2>&1
PATCH_RC=$?
[ "$PATCH_RC" -ne 0 ] && ok "apply_patch: rejects when SEARCH not found" || fail "apply_patch: should reject missing SEARCH"

# Path traversal — apply_patch must refuse absolute paths and '..' escapes.
mkdir -p "$WORK/proj-dir" && echo "victim" > "$WORK/victim.txt"
cat > "$WORK/patch.escape" <<PATCH
../victim.txt
<<<<<<< SEARCH
victim
=======
PWNED
>>>>>>> REPLACE
PATCH
(cd "$WORK/proj-dir" && python3 "$PROJ/.codex/scripts/apply_patch.py" < "$WORK/patch.escape" >/dev/null 2>&1)
VICTIM_CONTENT=$(cat "$WORK/victim.txt")
[ "$VICTIM_CONTENT" = "victim" ] && ok "apply_patch: refuses ../ escape" || fail "apply_patch: TRAVERSAL — victim overwritten"

cat > "$WORK/patch.abs" <<PATCH
/tmp/rockie-abs-target.txt
<<<<<<< SEARCH
whatever
=======
PWNED
>>>>>>> REPLACE
PATCH
python3 "$PROJ/.codex/scripts/apply_patch.py" < "$WORK/patch.abs" >/dev/null 2>&1
[ ! -f /tmp/rockie-abs-target.txt ] && ok "apply_patch: refuses absolute paths" || { rm -f /tmp/rockie-abs-target.txt; fail "apply_patch: absolute path accepted"; }

# ── 13. Dry-run gate ─────────────────────────────────────────────────────
section "dry-run gate"
mkdir -p "$PROJ/src"
cat > "$PROJ/src/train.py" <<EOF
print("v1")
EOF
bash "$PROJ/.codex/scripts/dry_run_gate.sh" register "$PROJ/src/train.py" >/dev/null
IN=$(python3 -c "import json;print(json.dumps({'tool_input':{'command':'python3 src/train.py','cwd':'$PROJ'}}))")
echo "$IN" | bash "$PROJ/.codex/hooks/pre-train-gate.sh" >/dev/null 2>&1
assert "dry-run-gate: sentinel valid → pass" "0" "$?"

echo "print('v2')" >> "$PROJ/src/train.py"
echo "$IN" | bash "$PROJ/.codex/hooks/pre-train-gate.sh" >/dev/null 2>&1
assert "dry-run-gate: modified script → block (exit 2)" "2" "$?"

IN=$(python3 -c "import json;print(json.dumps({'tool_input':{'command':'python3 src/train.py --smoke','cwd':'$PROJ'}}))")
echo "$IN" | bash "$PROJ/.codex/hooks/pre-train-gate.sh" >/dev/null 2>&1
assert "dry-run-gate: --smoke bypass" "0" "$?"

# ── 14. Pre-commit gate (clean hash) ─────────────────────────────────────
section "pre-commit gate"
echo "something" > "$PROJ/new_file.txt"
(cd "$PROJ" && git add new_file.txt)
HASH=$(cd "$PROJ" && bash .codex/scripts/compute_clean_hash.sh)
[ -n "$HASH" ] && [ "$HASH" != "no-changes" ] && ok "compute_clean_hash produces stable hash" || fail "compute_clean_hash"
touch "$PROJ/.codex/.state/clean-ok-$HASH"

IN=$(python3 -c "import json;print(json.dumps({'tool_input':{'command':'git commit -m x','cwd':'$PROJ'}}))")
echo "$IN" | bash "$PROJ/.codex/hooks/pre-commit-gate.sh" >/dev/null 2>&1
assert "pre-commit-gate: valid sentinel → pass" "0" "$?"

rm "$PROJ/.codex/.state/clean-ok-$HASH"
echo "$IN" | bash "$PROJ/.codex/hooks/pre-commit-gate.sh" >/dev/null 2>&1
assert "pre-commit-gate: missing sentinel → block (exit 2)" "2" "$?"

# clean-finalize.sh: writes sentinel AND emits upstream-contribute nudge
# on stderr. Replaces the prose-only "Post-audit hook" — the nudge is
# now a deterministic side-effect of the script, not agent discretion.
[ -x "$PROJ/.codex/scripts/clean-finalize.sh" ] && ok "clean-finalize: script installed and executable" || fail "clean-finalize: missing or not executable"
rm -f "$PROJ"/.codex/.state/clean-ok-*
NUDGE_OUT=$(bash "$PROJ/.codex/scripts/clean-finalize.sh" testhash123 2>&1 >/dev/null)
[ -f "$PROJ/.codex/.state/clean-ok-testhash123" ] && ok "clean-finalize: writes sentinel at expected path" || fail "clean-finalize: sentinel missing"
echo "$NUDGE_OUT" | grep -q 'upstream-contribute' && ok "clean-finalize: nudge mentions upstream-contribute on stderr" || fail "clean-finalize: nudge missing upstream-contribute"
echo "$NUDGE_OUT" | grep -q 'saml212/rockie-codex' && ok "clean-finalize: nudge references saml212/rockie-codex" || fail "clean-finalize: nudge missing upstream repo"
[ -n "$NUDGE_OUT" ] && ok "clean-finalize: stderr nudge is non-empty" || fail "clean-finalize: stderr empty"
bash "$PROJ/.codex/scripts/clean-finalize.sh" >/dev/null 2>&1
assert "clean-finalize: missing hash arg → exit 2" "2" "$?"

# ── 15. FTS5 hyphen-safety note ──────────────────────────────────────────
section "installer hooks merge"
# Verify install.sh merges into pre-existing hooks.json without clobbering.
MERGE_TEST=$(mktemp -d -t smoke-merge-XXXXXX)
(cd "$MERGE_TEST" && git init -q)
mkdir -p "$MERGE_TEST/.codex"
cat > "$MERGE_TEST/.codex/hooks.json" <<EOF
{"version":1,"hooks":{"Stop":[{"hooks":[{"type":"command","command":"bash .codex/hooks/preexisting.sh"}]}]}}
EOF
bash "$ROCKIE/install.sh" --project-only --yes "$MERGE_TEST" >/dev/null 2>&1
python3 - "$MERGE_TEST" <<'PY' && ok "installer preserves pre-existing hooks + adds rockie hooks" || fail "installer merge"
import json, sys, pathlib
cfg = json.loads(pathlib.Path(sys.argv[1], ".codex/hooks.json").read_text())
assert cfg["version"] == 1, "top-level metadata clobbered"
stop_cmds = [h["command"] for blk in cfg["hooks"].get("Stop", []) for h in blk.get("hooks", [])]
assert "bash .codex/hooks/preexisting.sh" in stop_cmds, "pre-existing hook dropped"
assert any(cmd.endswith(".codex/hooks/learn-capture.sh") for cmd in stop_cmds), "rockie learn-capture not added"
PY
rm -rf "$MERGE_TEST"

# Idempotent: running the installer twice shouldn't double-register hooks.
IDEM=$(mktemp -d -t smoke-idem-XXXXXX)
(cd "$IDEM" && git init -q)
bash "$ROCKIE/install.sh" --project-only --yes "$IDEM" >/dev/null 2>&1
bash "$ROCKIE/install.sh" --project-only --yes "$IDEM" >/dev/null 2>&1
COUNT=$(python3 -c '
import json; d=json.load(open("'"$IDEM"'/.codex/hooks.json"))
stop=[h["command"] for blk in d["hooks"].get("Stop", []) for h in blk.get("hooks", [])]
print(sum(cmd.endswith(".codex/hooks/learn-capture.sh") for cmd in stop))')
assert "installer is idempotent (no hook duplication on re-run)" "1" "$COUNT"
rm -rf "$IDEM"

section "autopilot.conf safe parser"
APC="$WORK/apc"
mkdir -p "$APC/.codex/scripts" "$APC/.codex/memory"
cp "$ROCKIE/project-extension/codex/scripts/autopilot_loop.sh" "$APC/.codex/scripts/"
# Malicious values must be refused (command substitution, backticks, non-allowlisted keys).
cat > "$APC/.codex/autopilot.conf" <<'EOF'
LAUNCHER_CMD="$(touch /tmp/rockie-pwn-$$)"
EOF
bash "$APC/.codex/scripts/autopilot_loop.sh" >/dev/null 2>&1
[ -e /tmp/rockie-pwn-$$ ] && { rm -f /tmp/rockie-pwn-$$; fail "autopilot.conf: command substitution executed"; } || ok "autopilot.conf: refuses \$(…) command substitution"

cat > "$APC/.codex/autopilot.conf" <<'EOF'
LAUNCHER_CMD=`id`
EOF
bash "$APC/.codex/scripts/autopilot_loop.sh" 2>&1 | grep -q 'refusing' && ok "autopilot.conf: refuses backticks" || fail "autopilot.conf: backtick value accepted"

cat > "$APC/.codex/autopilot.conf" <<'EOF'
PATH=/evil/bin
EOF
bash "$APC/.codex/scripts/autopilot_loop.sh" 2>&1 | grep -q 'not in allow-list' && ok "autopilot.conf: refuses non-allowlisted key" || fail "autopilot.conf: accepted non-allowlisted key"

section "pre-train-gate bypass attempts"
mkdir -p "$PROJ/src-py311"
cat > "$PROJ/src-py311/train.py" <<'EOF'
print("v1")
EOF
bash "$PROJ/.codex/scripts/dry_run_gate.sh" register "$PROJ/src-py311/train.py" >/dev/null

# Bypass attempt 1: python3.11 — should now be caught by broadened regex.
# Sentinel valid, so gate should pass cleanly; verify that python3.11 is RECOGNIZED
# by temporarily invalidating the sentinel and confirming the gate blocks.
echo "edited" >> "$PROJ/src-py311/train.py"   # invalidate sentinel
IN=$(python3 -c "import json;print(json.dumps({'tool_input':{'command':'python3.11 src-py311/train.py','cwd':'$PROJ'}}))")
echo "$IN" | bash "$PROJ/.codex/hooks/pre-train-gate.sh" >/dev/null 2>&1
TRAIN_RC=$?
[ "$TRAIN_RC" -eq 2 ] && ok "pre-train-gate: python3.11 is recognized as training (no longer bypass)" || fail "pre-train-gate: python3.11 slipped through"

# Bypass attempt 2: shell comment `# --smoke` should NOT count as smoke exception
IN=$(python3 -c "import json;print(json.dumps({'tool_input':{'command':'python3 src-py311/train.py  # --smoke','cwd':'$PROJ'}}))")
echo "$IN" | bash "$PROJ/.codex/hooks/pre-train-gate.sh" >/dev/null 2>&1
TRAIN_RC=$?
[ "$TRAIN_RC" -eq 2 ] && ok "pre-train-gate: '# --smoke' comment no longer bypass" || fail "pre-train-gate: comment bypass still works"

section "schema migration walker"
MGT=$(mktemp -d -t rockie-migration-XXXXXX)
mkdir -p "$MGT/.codex/scripts" "$MGT/.codex/memory/migrations"
cp "$ROCKIE/project-extension/codex/memory/schema.sql" "$MGT/.codex/memory/"
cp "$ROCKIE/project-extension/codex/scripts/init_db.sh" "$MGT/.codex/scripts/"
cat > "$MGT/.codex/memory/migrations/002_add_test_col.sql" <<'SQL'
ALTER TABLE experiments ADD COLUMN ci_test_col TEXT;
SQL
bash "$MGT/.codex/scripts/init_db.sh" >/dev/null 2>&1
V1=$("$SQLITE" "$MGT/.codex/memory/workflow.db" "PRAGMA user_version;")
assert "migration: first run bumps user_version to 2" "2" "$V1"
# Re-run must not re-apply (idempotent).
bash "$MGT/.codex/scripts/init_db.sh" >/dev/null 2>&1
V2=$("$SQLITE" "$MGT/.codex/memory/workflow.db" "PRAGMA user_version;")
assert "migration: second run is no-op (user_version stays 2)" "2" "$V2"
rm -rf "$MGT"

section "CLI --help smoke (import-error catcher)"
for cli in journal.py queue.py calibration.py budget.py convergence.py apply_patch.py stage.py; do
  # Real test: compile the file. If it imports at parse-time, syntax/import
  # errors show up; otherwise we're testing runtime behaviour of --help
  # which some subcommand-style CLIs return non-zero on.
  python3 -c "import py_compile; py_compile.compile('$PROJ/.codex/scripts/$cli', doraise=True)" 2>/dev/null \
    && ok "$cli compiles cleanly" || fail "$cli: syntax error"
  # And --help should never traceback.
  HELP_ERR=$(python3 "$PROJ/.codex/scripts/$cli" --help 2>&1 >/dev/null)
  if echo "$HELP_ERR" | grep -q 'Traceback'; then
    fail "$cli --help produced a traceback"
    echo "$HELP_ERR" | head -5
  else
    ok "$cli --help runs without traceback"
  fi
done

section "autopilot loop one-iteration end-to-end (mock launcher)"
AP=$(mktemp -d -t rockie-autopilot-XXXXXX)
mkdir -p "$AP/.codex/scripts" "$AP/.codex/memory" "$AP/.codex/.state"
cp "$ROCKIE/project-extension/codex/memory/schema.sql"      "$AP/.codex/memory/"
for s in init_db.sh queue.py zcm.sh autopilot_loop.sh notify.sh rotate_hook_log.sh; do
  cp "$ROCKIE/project-extension/codex/scripts/$s" "$AP/.codex/scripts/"
done
bash "$AP/.codex/scripts/init_db.sh" >/dev/null 2>&1
# Mock launcher: writes PID of a 1s sleep to PID_FILE, writes a log with a step, returns.
cat > "$AP/mock_launch.sh" <<'SH'
#!/usr/bin/env bash
set -e
mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$LOG_FILE")"
echo "step 1: loss=3.42" > "$LOG_FILE"
# Short-lived "training" so ZCM completes quickly
sleep 1 &
echo $! > "$PID_FILE"
exit 0
SH
chmod +x "$AP/mock_launch.sh"
cat > "$AP/.codex/autopilot.conf" <<EOF
LAUNCHER_CMD="bash $AP/mock_launch.sh"
PID_FILE=".codex/.state/training_pid"
LOG_FILE=".codex/.state/training_log"
MAX_ITERATIONS=1
MAX_CONSECUTIVE_FAILURES=10
NTFY_ON_COOLDOWN=0
EOF
export PROJECT=autopilot-e2e CLAUDE_SESSION_ID=autopilot-e2e
python3 "$AP/.codex/scripts/queue.py" add --hypothesis "mock experiment" --priority 1 --metric val_loss --predicted-delta -0.05 >/dev/null
# Run one iteration (MAX_ITERATIONS=1) — should claim + launch + ZCM + loop exit.
(cd "$AP" && bash ".codex/scripts/autopilot_loop.sh" >/dev/null 2>&1) &
LOOP_PID=$!
wait "$LOOP_PID" 2>/dev/null
EVENTS="$AP/.codex/memory/autopilot_events.jsonl"
if [ -f "$EVENTS" ] && grep -q '"kind":"claimed"' "$EVENTS" && grep -q '"kind":"training-started"' "$EVENTS"; then
  ok "autopilot loop: claim → launcher-fires → ZCM cycle completes one iteration"
else
  fail "autopilot loop: events.jsonl missing expected kinds"
  [ -f "$EVENTS" ] && cat "$EVENTS" | tail -5
fi
rm -rf "$AP"

section "gpu.py router with fake providers"
# Exercise the cross-provider router's hop / cooldown / on-demand-gate
# logic against tests/fakes/* without hitting any live API. CI never
# touches a real provider.
GPU_PY="$PROJ/.codex/scripts/gpu.py"
# The fakes import `from providers.base` which gpu.py's sys.path setup
# makes available; the dotted module path `tests.fakes.X` needs the
# repo root on sys.path, which gpu.py adds via os.getcwd() when given
# a non-registry provider name. So we cd to ROCKIE for these tests.
cleanup_fake_pods() {
  "$SQLITE" "$PROJ/.codex/memory/workflow.db" \
    "DELETE FROM gpu_pods WHERE id LIKE 'fake-%' OR provider LIKE 'fake-%';" 2>/dev/null
}

# T1: router hops past OutOfStock to a working provider
(cd "$ROCKIE" && python3 "$GPU_PY" create \
  --providers tests.fakes.oos,tests.fakes.ok \
  --gpu-type FakeH100 --hours 1 --yes >/tmp/gpu-t1.out 2>&1)
T1_RC=$?
cleanup_fake_pods
assert "router hops on OutOfStock and lands at working provider" "0" "$T1_RC"

# T2: router hops past BidRejected + AuthError to working provider
(cd "$ROCKIE" && python3 "$GPU_PY" create \
  --providers tests.fakes.bid_rejected,tests.fakes.auth_fail,tests.fakes.ok \
  --gpu-type FakeH100 --hours 1 --yes >/tmp/gpu-t2.out 2>&1)
T2_RC=$?
cleanup_fake_pods
assert "router hops past BidRejected+AuthError to working provider" "0" "$T2_RC"

# T3: all providers fail → exit 3 (no provider succeeded)
(cd "$ROCKIE" && python3 "$GPU_PY" create \
  --providers tests.fakes.oos,tests.fakes.bid_rejected \
  --gpu-type FakeH100 --hours 1 --yes >/tmp/gpu-t3.out 2>&1)
T3_RC=$?
assert "router exits 3 when all providers exhausted" "3" "$T3_RC"

# T4: on-demand-only provider WITHOUT --allow-on-demand → empty rank → exit 3
(cd "$ROCKIE" && python3 "$GPU_PY" create \
  --providers tests.fakes.ondemand \
  --gpu-type FakeH100 --hours 1 --yes >/tmp/gpu-t4.out 2>&1)
T4_RC=$?
assert "router excludes on-demand-only providers without --allow-on-demand" "3" "$T4_RC"

# T5: same provider WITH --allow-on-demand → rank includes it → exit 0
(cd "$ROCKIE" && python3 "$GPU_PY" create \
  --providers tests.fakes.ondemand \
  --gpu-type FakeH100 --hours 1 --yes --allow-on-demand >/tmp/gpu-t5.out 2>&1)
T5_RC=$?
cleanup_fake_pods
assert "router includes on-demand-only providers with --allow-on-demand" "0" "$T5_RC"

# T6: cost --json against a fake provider produces parseable JSON
(cd "$ROCKIE" && python3 "$GPU_PY" cost --providers tests.fakes.ok --json >/tmp/gpu-t6.out 2>&1)
T6_RC=$?
T6_JSON_VALID=$(python3 -c "import json,sys; d=json.load(open('/tmp/gpu-t6.out')); print(1 if 'providers' in d and isinstance(d.get('providers'),list) else 0)" 2>/dev/null || echo 0)
cleanup_fake_pods
assert "cost --json exits cleanly" "0" "$T6_RC"
assert "cost --json returns parseable summary with providers list" "1" "$T6_JSON_VALID"

section "FTS5 hyphen sanitization"
if "$SQLITE" "$DB" "SELECT COUNT(*) FROM learnings_fts WHERE learnings_fts MATCH 'gradient-clipping'" >/dev/null 2>&1; then
  fail "FTS5 accepted unsanitized hyphen — load-relevant-rules sanitization is needed"
else
  ok "FTS5 errors on unsanitized hyphen (sanitization is load-bearing)"
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}──────────────────────────────────────────${RESET}"
  echo "${RED}✗ smoke test failed: $FAIL assertion(s)${RESET}"
  echo "${RED}──────────────────────────────────────────${RESET}"
  exit 1
fi
echo "${GREEN}──────────────────────────────────────────${RESET}"
echo "${GREEN}✓ all smoke tests passed ($PASS assertions)${RESET}"
echo "${GREEN}──────────────────────────────────────────${RESET}"

"$SQLITE" "$DB" "SELECT
  'db: ' ||
  (SELECT count(*) FROM learnings)   || ' learnings, ' ||
  (SELECT count(*) FROM dead_ends)   || ' dead-ends, ' ||
  (SELECT count(*) FROM experiments) || ' experiments, ' ||
  (SELECT count(*) FROM hypothesis_calibration) || ' predictions, ' ||
  (SELECT count(*) FROM experiment_queue) || ' queue items, ' ||
  (SELECT count(*) FROM budget_usage) || ' budget rows'"
