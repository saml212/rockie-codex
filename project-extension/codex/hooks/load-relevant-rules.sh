#!/usr/bin/env bash
# UserPromptSubmit hook: query the learnings DB for rules relevant to the
# current prompt and inject the top N as prompt context so Codex sees them.
#
# Relevance: FTS5 BM25 match against rule+mistake+correction, weighted by
# times_applied. Fall back to recency if no FTS matches.
set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/memory/workflow.db"
SQLITE=/usr/bin/sqlite3
bash "$ROOT/scripts/rotate_hook_log.sh" 2>/dev/null
echo "[$(date -Iseconds)] load-relevant-rules: fired" >> "$ROOT/memory/hook.log"

[ ! -f "$DB" ] && exit 0

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null)
[ -z "$PROMPT" ] && exit 0

# Extract top 5 tokens by length (cheap keyword filter) to build an FTS query.
# This avoids turning every common word into a match.
QUERY=$(echo "$PROMPT" | python3 -c '
import sys, re
text = sys.stdin.read().lower()
tokens = [w for w in re.findall(r"[a-z][a-z0-9_-]{3,}", text) if w not in {
  "this","that","with","from","have","what","your","just","like","were",
  "they","them","then","when","where","which","would","could","should",
  "will","been","does","some","more","than","other","these","those","into",
}]
# keep unique, prefer longer
seen = set(); out = []
for w in sorted(tokens, key=len, reverse=True):
    if w in seen: continue
    seen.add(w); out.append(w)
    if len(out) >= 6: break
print(" OR ".join(out))
' 2>/dev/null)

[ -z "$QUERY" ] && exit 0

# Safe escape for FTS5: keep alphanumerics, spaces, underscores. Strip hyphens
# by converting to space so `matrix-arch` becomes `matrix arch` (both tokens
# still searched, BM25 weights give the right match). The python extractor
# upstream keeps hyphens in raw tokens but FTS5 query parser treats bare `-`
# as NOT-operator, which would invert the search.
QUERY_SAFE=$(echo "$QUERY" | sed "s/[^a-zA-Z0-9_ ]/ /g" | tr -s ' ')

# BM25 gate: only inject rules if best match is strong (score < STRONG_THRESHOLD).
# BM25 is negative; more negative = better match. -4 is a good empirical cutoff
# calibrated against this repo's rules corpus (see handoff doc). Adjust if the
# corpus shape changes materially.
STRONG_THRESHOLD=-4.0

# First: check the best match score. If it's not strong enough, skip entirely
# (no noise injection). Otherwise return top 5 matches.
# BM25 is an FTS5 aux function — can't use with aggregates. Use ORDER BY LIMIT 1.
BEST=$("$SQLITE" "$DB" <<SQL 2>/dev/null
SELECT bm25(learnings_fts, 1.0, 2.0, 1.0, 1.0) as score
FROM learnings_fts WHERE learnings_fts MATCH '$QUERY_SAFE'
ORDER BY score LIMIT 1;
SQL
)

if [ -z "$BEST" ]; then
  exit 0
fi

# Compare floats via awk (bash can't compare floats natively)
IS_STRONG=$(awk -v b="$BEST" -v t="$STRONG_THRESHOLD" 'BEGIN{print (b < t) ? 1 : 0}')
if [ "$IS_STRONG" != "1" ]; then
  exit 0
fi

RESULTS=$("$SQLITE" "$DB" <<SQL 2>/dev/null
.mode list
SELECT '[' || l.category || '] ' || l.rule
FROM learnings_fts fts
JOIN learnings l ON l.id = fts.rowid
WHERE learnings_fts MATCH '$QUERY_SAFE'
ORDER BY bm25(learnings_fts, 1.0, 2.0, 1.0, 1.0) + (l.times_applied * -0.1)
LIMIT 5;
SQL
)

if [ -n "$RESULTS" ]; then
  echo "[load-relevant-rules] Prior learnings that may apply to this prompt:"
  printf '%s\n' "$RESULTS" | sed 's/^/  /'
fi

exit 0
