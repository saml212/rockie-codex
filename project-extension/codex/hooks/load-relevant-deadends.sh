#!/usr/bin/env bash
# UserPromptSubmit hook: query the dead-end registry for directions
# relevant to the current prompt and inject the top N as prompt context so
# Codex sees them.
#
# Only fires on prompts that look brainstorm-y ("idea", "try",
# "explore", "what if", etc.) to avoid noise on every turn. The
# gate is cheap — one grep — and prevents dead-ends flooding
# unrelated debugging prompts.
set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/memory/workflow.db"
SQLITE=/usr/bin/sqlite3
bash "$ROOT/scripts/rotate_hook_log.sh" 2>/dev/null

[ ! -f "$DB" ] && exit 0

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null)
[ -z "$PROMPT" ] && exit 0

# Only fire on brainstorm/explore-shaped prompts
if ! echo "$PROMPT" | grep -qiE '(brainstorm|explore|what if|ideas?|propos|try|consider|could we|should we|approach|direction|next experiment|new hypothesis)'; then
  exit 0
fi

echo "[$(date -Iseconds)] load-relevant-deadends: fired" >> "$ROOT/memory/hook.log"

# Build a simple FTS query from the prompt's longer tokens.
QUERY=$(echo "$PROMPT" | python3 -c '
import sys, re
text = sys.stdin.read().lower()
tokens = [w for w in re.findall(r"[a-z][a-z0-9_-]{3,}", text) if w not in {
  "this","that","with","from","have","what","your","just","like","were",
  "they","them","then","when","where","which","would","could","should",
  "will","been","does","some","more","than","other","these","those","into",
  "brainstorm","explore","ideas","idea","propose","consider","approach",
  "direction","experiment","hypothesis",
}]
seen = set(); out = []
for w in sorted(tokens, key=len, reverse=True):
    if w in seen: continue
    seen.add(w); out.append(w)
    if len(out) >= 6: break
print(" OR ".join(out))
' 2>/dev/null)

[ -z "$QUERY" ] && exit 0
QUERY_SAFE=$(echo "$QUERY" | sed "s/[^a-zA-Z0-9_ ]/ /g" | tr -s ' ')

# Strong-match gate — same BM25 threshold as learnings loader.
STRONG_THRESHOLD=-4.0

BEST=$("$SQLITE" "$DB" <<SQL 2>/dev/null
SELECT bm25(dead_ends_fts, 2.0, 1.0, 1.0) as score
FROM dead_ends_fts WHERE dead_ends_fts MATCH '$QUERY_SAFE'
ORDER BY score LIMIT 1;
SQL
)

[ -z "$BEST" ] && exit 0

IS_STRONG=$(awk -v b="$BEST" -v t="$STRONG_THRESHOLD" 'BEGIN{print (b < t) ? 1 : 0}')
if [ "$IS_STRONG" != "1" ]; then
  exit 0
fi

RESULTS=$("$SQLITE" "$DB" <<SQL 2>/dev/null
.mode list
SELECT '[' || d.direction || '] ' || d.reason ||
       coalesce(' (evidence: ' || d.evidence_path || ')', '')
FROM dead_ends_fts fts
JOIN dead_ends d ON d.id = fts.rowid
WHERE dead_ends_fts MATCH '$QUERY_SAFE'
ORDER BY bm25(dead_ends_fts, 2.0, 1.0, 1.0)
LIMIT 5;
SQL
)

if [ -n "$RESULTS" ]; then
  echo "[load-relevant-deadends] This project has killed directions that overlap your prompt. Check before re-proposing:"
  printf '%s\n' "$RESULTS" | sed 's/^/  /'
fi

exit 0
