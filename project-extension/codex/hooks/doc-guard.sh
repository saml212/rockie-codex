#!/usr/bin/env bash
# PreToolUse(Write|Edit) hook: nudge when editing/creating .md files.
# - New .md → list existing docs, suggest consolidation
# - Always exits 0 (soft nudge via stderr; hard block happens at commit)
set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
bash "$ROOT/scripts/rotate_hook_log.sh" 2>/dev/null

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_name",""))' 2>/dev/null)
FILE_PATH=$(echo "$INPUT" | python3 -c '
import sys,json
d=json.load(sys.stdin)
i=d.get("tool_input",{})
print(i.get("file_path") or i.get("filePath") or "")
' 2>/dev/null)

[ -z "$FILE_PATH" ] && exit 0

# Only .md files
case "$FILE_PATH" in
  *.md|*.MD|*.markdown) ;;
  *) exit 0 ;;
esac

REPO=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
[ -z "$REPO" ] && exit 0
REL=${FILE_PATH#"$REPO"/}

echo "[$(date -Iseconds)] doc-guard: $TOOL on $REL" >> "$ROOT/memory/hook.log"

if [ ! -f "$FILE_PATH" ]; then
  # New .md file — nudge
  EXISTING=$(find "$REPO" -maxdepth 1 -type f -name '*.md' -exec basename {} \; 2>/dev/null | head -12 | tr '\n' ' ')
  {
    echo "[doc-guard] About to CREATE new .md: $REL"
    echo "[doc-guard] Repo rule: consolidate into existing docs first. New files are slop unless truly novel."
    echo "[doc-guard] Existing top-level: $EXISTING"
    echo "[doc-guard] If you proceed, /clean will flag this as a blocker at commit time. Use CLEAN_BYPASS=1 for the commit if intended."
  } >&2
fi

exit 0
