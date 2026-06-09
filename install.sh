#!/usr/bin/env bash
set -euo pipefail

PROJECT_ONLY=0
ASSUME_YES=0
TARGET_PROJECT="${PWD}"

while [ $# -gt 0 ]; do
  case "$1" in
    --project-only) PROJECT_ONLY=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --help|-h)
      cat <<'EOF'
Usage:
  ./install.sh [--project-only] [--yes] [<target-project>]

Installs:
  - project hooks/scripts/db into <target>/.codex/
  - project skills into <target>/.agents/skills/
  - user-global hooks/scripts/teams/skills into ~/.codex/ (unless --project-only)
EOF
      exit 0
      ;;
    *)
      TARGET_PROJECT="$1"
      shift
      ;;
  esac
done

if [ ! -t 0 ]; then
  ASSUME_YES=1
fi

ROOT="$(cd "$(dirname "$0")" && pwd -P)"

if [ ! -d "$TARGET_PROJECT" ]; then
  echo "error: target project dir '$TARGET_PROJECT' does not exist" >&2
  exit 2
fi
TARGET_PROJECT="$(cd "$TARGET_PROJECT" && pwd -P)"

if [ "$TARGET_PROJECT" = "$ROOT" ]; then
  echo "error: refusing to install into the rockie-codex repo itself" >&2
  exit 2
fi

command -v /usr/bin/sqlite3 >/dev/null 2>&1 || {
  echo "error: /usr/bin/sqlite3 is required" >&2
  exit 2
}
command -v python3 >/dev/null 2>&1 || {
  echo "error: python3 is required" >&2
  exit 2
}
/usr/bin/sqlite3 ":memory:" "CREATE VIRTUAL TABLE t USING fts5(x)" >/dev/null 2>&1 || {
  echo "error: /usr/bin/sqlite3 lacks FTS5 support" >&2
  exit 2
}

if [ -d "$TARGET_PROJECT/.codex" ] && [ "$ASSUME_YES" = "0" ]; then
  printf "[?] %s/.codex already exists. Merge non-destructively? [Y/n] " "$TARGET_PROJECT"
  read -r ans
  ans=${ans:-Y}
  case "$ans" in
    [Yy]*) ;;
    *) echo "aborted."; exit 1 ;;
  esac
fi

merge_hooks_json() {
  local source_json="$1"
  local target_json="$2"
  python3 - "$source_json" "$target_json" <<'PY'
import json
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
new_cfg = json.loads(src.read_text())
if dst.exists():
    current = json.loads(dst.read_text())
else:
    current = {}

current.setdefault("hooks", {})
for event, groups in new_cfg.get("hooks", {}).items():
    current["hooks"].setdefault(event, [])
    existing = {
        hook.get("command")
        for group in current["hooks"][event]
        for hook in group.get("hooks", [])
    }
    for group in groups:
        merged = {"hooks": []}
        if "matcher" in group:
            merged["matcher"] = group["matcher"]
        for hook in group.get("hooks", []):
            command = hook.get("command")
            if command in existing:
                continue
            merged["hooks"].append(hook)
            existing.add(command)
        if merged["hooks"]:
            current["hooks"][event].append(merged)

dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(json.dumps(current, indent=2) + "\n")
print(f"[+] merged hooks into {dst}")
PY
}

merge_gitignore_block() {
  local template_path="$1"
  local destination="$2"
  python3 - "$template_path" "$destination" <<'PY'
import pathlib
import re
import sys

template = pathlib.Path(sys.argv[1]).read_text()
dst = pathlib.Path(sys.argv[2])
if not dst.exists():
    dst.write_text(template)
    print(f"[+] wrote {dst}")
    raise SystemExit(0)

text = dst.read_text()
begin = "# BEGIN rockie-codex\n"
end = "# END rockie-codex\n"
if begin not in template or end not in template:
    raise SystemExit("gitignore template missing markers")
block = template[template.index(begin):template.index(end) + len(end)]
pattern = re.compile(r"(?ms)^# BEGIN rockie-codex\n.*?^# END rockie-codex\n?")
if pattern.search(text):
    text = pattern.sub(block, text, count=1)
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n" + block
dst.write_text(text)
print(f"[+] merged {dst}")
PY
}

echo "┌── rockie-codex installer ─────────────────────────────"
echo "│  repo project runtime → $TARGET_PROJECT/.codex/"
echo "│  repo project skills  → $TARGET_PROJECT/.agents/skills/"
if [ "$PROJECT_ONLY" = "1" ]; then
  echo "│  user-global runtime  → (skipped: --project-only)"
else
  echo "│  user-global runtime  → $HOME/.codex/"
fi
echo "└───────────────────────────────────────────────────────"

mkdir -p "$TARGET_PROJECT/.codex" "$TARGET_PROJECT/.agents/skills"
rsync -a \
  --exclude='__pycache__' \
  --exclude='hooks.json' \
  "$ROOT/project-extension/codex/" \
  "$TARGET_PROJECT/.codex/"
rsync -a \
  --exclude='__pycache__' \
  "$ROOT/project-extension/agents/skills/" \
  "$TARGET_PROJECT/.agents/skills/"

chmod +x "$TARGET_PROJECT/.codex/hooks/"*.sh 2>/dev/null || true
chmod +x "$TARGET_PROJECT/.codex/scripts/"*.sh 2>/dev/null || true
merge_hooks_json "$ROOT/project-extension/codex/hooks.json" "$TARGET_PROJECT/.codex/hooks.json"

PROJECT_ID_FILE="$TARGET_PROJECT/.codex/project_id"
if [ ! -f "$PROJECT_ID_FILE" ]; then
  if command -v uuidgen >/dev/null 2>&1; then
    PROJECT_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
  else
    PROJECT_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  fi
  printf 'id=%s\nname=%s\n' "$PROJECT_ID" "$(basename "$TARGET_PROJECT")" > "$PROJECT_ID_FILE"
  echo "[+] stamped project_id at $PROJECT_ID_FILE"
fi

if [ ! -f "$TARGET_PROJECT/.codex/memory/workflow.db" ]; then
  bash "$TARGET_PROJECT/.codex/scripts/init_db.sh"
  (
    cd "$TARGET_PROJECT"
    python3 .codex/scripts/seed_hard_rules.py
  )
  echo "[+] initialized workflow.db"
else
  /usr/bin/sqlite3 "$TARGET_PROJECT/.codex/memory/workflow.db" < "$TARGET_PROJECT/.codex/memory/schema.sql"
  echo "[.] existing workflow.db updated with create-if-not-exists schema"
fi

python3 "$TARGET_PROJECT/.agents/skills/mode/runtime/mode.py" seed >/dev/null 2>&1 || true
merge_gitignore_block "$ROOT/install-assets/gitignore.rockie-codex" "$TARGET_PROJECT/.gitignore"

if [ "$PROJECT_ONLY" = "0" ]; then
  mkdir -p "$HOME/.codex/hooks" "$HOME/.codex/scripts" "$HOME/.codex/skills" "$HOME/.codex/teams"
  rsync -a --exclude='__pycache__' "$ROOT/user-extension/codex/hooks/" "$HOME/.codex/hooks/"
  rsync -a --exclude='__pycache__' "$ROOT/user-extension/codex/scripts/" "$HOME/.codex/scripts/"
  rsync -a --exclude='__pycache__' "$ROOT/user-extension/codex/teams/" "$HOME/.codex/teams/"
  rsync -a --exclude='__pycache__' "$ROOT/user-extension/codex/skills/" "$HOME/.codex/skills/"
  chmod +x "$HOME/.codex/hooks/"*.sh 2>/dev/null || true
  merge_hooks_json "$ROOT/user-extension/codex/hooks.json" "$HOME/.codex/hooks.json"
  echo "[+] installed user-global hooks, scripts, teams, and dashboard skill"
fi

if [ ! -f "$TARGET_PROJECT/AGENTS.md" ]; then
  cat <<EOF

Next step: add an AGENTS.md at the repo root.

  cp "$ROOT/agents-md/AGENTS.md.template" "$TARGET_PROJECT/AGENTS.md"

or, for an ML research repo:

  cp "$ROOT/agents-md/ml-research.md" "$TARGET_PROJECT/AGENTS.md"
EOF
fi

if [ ! -d "$TARGET_PROJECT/.rockie/taste" ]; then
  cat <<'EOF'

Recommended first-session move:
  Ask Codex to use the `onboard` skill (for example: "use $onboard")

That captures your taste corpus into `.rockie/taste/` for future sessions.
EOF
fi

if [ "$PROJECT_ONLY" = "0" ] && [ ! -d "$HOME/.codex/teams/orchestrator/node_modules" ]; then
  cat <<'EOF'

Optional: install dashboard dependencies for the `deploy-team-dashboard` skill:
  cd "$HOME/.codex/teams/orchestrator" && npm install
EOF
fi

echo
echo "✓ rockie-codex install complete."
