#!/usr/bin/env bash
# fetch_dataroom.sh -- A5 connector: assemble a Rockie lab's uploaded SOURCES
# into a local data-room folder that ingest_dataroom.sh + reconcile.py consume.
#
# WHY an API fetch (not a filesystem read): a tenant's uploaded sources live in
# SurrealDB + on the platform-context server's disk (UPLOADS_FOLDER =
# {DATA_FOLDER}/uploads/<tenant_id>; see platform-context/open_notebook/config.py
# and api/routers/sources.py:save_uploaded_file). The diligence agent runs in a
# SEPARATE tenant runtime container (its own Fly machine) that does NOT mount
# that disk. So the only way the in-runtime agent reaches its lab's sources is
# the authenticated agent-tools HTTP API the runtime already has credentials for:
#   GET-equivalent  POST /api/agent-tools/notebook_read  {arguments:{}}  -> sources[]
#                   POST /api/agent-tools/source_read     {arguments:{source_id}} -> full_text
# (platform-context/api/agent_tools/tools.py:_notebook_read / _source_read;
#  routed through api/routers/agent_tools.py:invoke_tool; the mcp-rockie bridge
#  in platform-runtime/overlay/multitenant/mcp-rockie/server.js makes the same
#  calls. notebook_read defaults notebook_id to PLATFORM_LAB_ID, so the agent
#  grounds in its OWN lab with an empty body.)
#
# Auth + identity headers (already in the runtime env -- see
# platform-runtime/overlay/multitenant/entrypoint.sh and mcp-rockie/server.js
# authHeaders()):
#   ROCKIELAB_API_BASE     base URL (default https://api.rockielab.com)
#   ROCKIELAB_TENANT_TOKEN  -> X-Tenant-Token   (auth; DEV_TOKEN alias accepted)
#   ROCKIELAB_TENANT_ID     -> X-Tenant-Id       (tenant scoping)
#   ROCKIELAB_OPERATOR_TENANT_ID -> X-Operator-Tenant-Id (operator override)
#   ROCKIELAB_API_PASSWORD  -> Authorization: Bearer  (mirrors OPEN_NOTEBOOK_PASSWORD)
#   PLATFORM_LAB_ID / LAB_ID  the lab whose sources are the data room
#
# Each source's extracted full_text is written as a .md file into <out_dir>,
# one file per source, named from its title. That folder is then a normal
# data room: ingest_dataroom.sh <out_dir> -> manifest.json -> reconcile.py.
#
# Usage:
#   fetch_dataroom.sh [out_dir] [lab_id]
#     out_dir  destination folder (default: ./dataroom). Created if absent.
#     lab_id   lab/notebook id (default: $PLATFORM_LAB_ID, else $LAB_ID).
#
# OFFLINE PROOF MODE (no live API): set DATAROOM_FIXTURE=<dir> to a folder
# holding pre-captured tool JSON responses:
#   <fixture>/notebook_read.json           the notebook_read result
#   <fixture>/source_read.<source_id>.json each source_read result
# The script then reads those instead of calling the API, so the assembly
# logic (sources -> local .md files -> ingest-ready folder) is provable
# without a runtime. The HTTP path and the fixture path share one code path
# below (only the fetch function differs).
#
# Dependencies: bash, python3 (stdlib), curl (live mode only).

set -euo pipefail

OUT_DIR="${1:-./dataroom}"
LAB_ID="${2:-${PLATFORM_LAB_ID:-${LAB_ID:-}}}"

API_BASE="${ROCKIELAB_API_BASE:-https://api.rockielab.com}"
TENANT_TOKEN="${ROCKIELAB_TENANT_TOKEN:-${ROCKIELAB_TENANT_DEV_TOKEN:-}}"
TENANT_ID="${ROCKIELAB_TENANT_ID:-}"
OPERATOR_TENANT_ID="${ROCKIELAB_OPERATOR_TENANT_ID:-}"
API_PASSWORD="${ROCKIELAB_API_PASSWORD:-${OPEN_NOTEBOOK_PASSWORD:-}}"
FIXTURE="${DATAROOM_FIXTURE:-}"

log() { printf '[fetch_dataroom] %s\n' "$*" >&2; }

path_has_parent_component() {
  local path="${1//\\//}" part
  local -a parts
  IFS='/' read -r -a parts <<< "$path"
  for part in "${parts[@]}"; do
    [[ "$part" == ".." ]] && return 0
  done
  return 1
}

guard_replace_target() {
  if [[ -z "${OUT_DIR:-}" || -z "${PWD_REAL:-}" || "$OUT_DIR" == "/" || "$OUT_DIR" == "$PWD_REAL" || "$OUT_DIR" != "$PWD_REAL"/* ]]; then
    log "ERROR: unsafe output directory '$OUT_DIR'"
    exit 1
  fi
}

replace_out_dir() {
  guard_replace_target
  rm -rf -- "$OUT_DIR"
  mv -- "$STAGE_DIR" "$OUT_DIR"
  STAGE_DIR=""
}

cleanup_stage() {
  [[ -n "${STAGE_DIR:-}" && -d "$STAGE_DIR" ]] && rm -rf -- "$STAGE_DIR"
  return 0
}

# -- call_tool NAME ARGS_JSON -> tool result JSON on stdout --------------------
# Live mode: POST /api/agent-tools/<name> with the runtime's auth headers,
# unwrapping the FastAPI {arguments:{...}} request envelope. Fixture mode:
# read the captured response from $FIXTURE. Both emit the tool's result JSON.
call_tool() {
  local name="$1" args="$2"
  if [[ -n "$FIXTURE" ]]; then
    local fpath
    case "$name" in
      notebook_read) fpath="$FIXTURE/notebook_read.json" ;;
      source_read)
        # args is {"source_id":"..."} -- derive the fixture filename from it
        local sid
        sid="$(printf '%s' "$args" | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_id"])')"
        fpath="$FIXTURE/source_read.${sid}.json"
        ;;
      *) log "ERROR: unknown tool '$name' in fixture mode"; return 1 ;;
    esac
    if [[ ! -f "$fpath" ]]; then
      log "ERROR: fixture file missing: $fpath"; return 1
    fi
    cat "$fpath"
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    log "ERROR: curl is required for live API mode."; return 1
  fi
  local url="${API_BASE%/}/api/agent-tools/${name}"
  local -a hdr=(-H "Content-Type: application/json")
  [[ -n "$API_PASSWORD" ]] && hdr+=(-H "Authorization: Bearer ${API_PASSWORD}")
  [[ -n "$TENANT_TOKEN" ]] && hdr+=(-H "X-Tenant-Token: ${TENANT_TOKEN}")
  [[ -n "$TENANT_ID" ]]    && hdr+=(-H "X-Tenant-Id: ${TENANT_ID}")
  [[ -n "$OPERATOR_TENANT_ID" ]] && hdr+=(-H "X-Operator-Tenant-Id: ${OPERATOR_TENANT_ID}")
  local body
  body="$(python3 -c 'import json,sys; print(json.dumps({"arguments": json.loads(sys.argv[1])}))' "$args")"
  local resp http
  resp="$(curl -fsS -w '\n%{http_code}' -X POST "$url" "${hdr[@]}" -d "$body" 2>/dev/null)" || {
    log "ERROR: POST $url failed (network or non-2xx)."; return 1
  }
  http="${resp##*$'\n'}"
  body="${resp%$'\n'*}"
  if [[ "$http" != 2* ]]; then
    log "ERROR: $name -> HTTP $http"; return 1
  fi
  printf '%s' "$body"
}

# -- validate -----------------------------------------------------------------
if [[ -z "$LAB_ID" && -z "$FIXTURE" ]]; then
  log "ERROR: no lab id. Pass it as arg 2 or set PLATFORM_LAB_ID/LAB_ID."
  log "Usage: $0 [out_dir] [lab_id]"
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  log "ERROR: python3 is required but not found in PATH."; exit 1
fi

case "$OUT_DIR" in
  ""|"/"|".")
    log "ERROR: unsafe output directory '$OUT_DIR'"; exit 1 ;;
esac
if path_has_parent_component "$OUT_DIR"; then
  log "ERROR: output directory cannot contain '..': $OUT_DIR"; exit 1
fi

OUT_PARENT_INPUT="$(dirname "$OUT_DIR")"
OUT_BASE="$(basename "$OUT_DIR")"
case "$OUT_BASE" in
  ""|"."|"..")
    log "ERROR: unsafe output directory '$OUT_DIR'"; exit 1 ;;
esac
PWD_REAL="$(python3 -c 'import os; print(os.path.realpath(os.getcwd()))')"
OUT_PARENT="$(python3 -c 'import os,sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' "$OUT_PARENT_INPUT")"
if [[ "$OUT_PARENT" != "$PWD_REAL" && "$OUT_PARENT" != "$PWD_REAL"/* ]]; then
  log "ERROR: output directory must stay under the current work directory: $OUT_DIR"; exit 1
fi
OUT_DIR="${OUT_PARENT%/}/${OUT_BASE}"
guard_replace_target
mkdir -p "$OUT_PARENT"
STAGE_DIR="$(mktemp -d "${OUT_PARENT%/}/.${OUT_BASE}.tmp.XXXXXX")"
trap cleanup_stage EXIT

# -- 1) list the lab's sources -------------------------------------------------
log "listing sources for lab '${LAB_ID:-<fixture>}' via notebook_read"
NB_ARGS='{}'
[[ -n "$LAB_ID" ]] && NB_ARGS="$(python3 -c 'import json,sys; print(json.dumps({"notebook_id": sys.argv[1]}))' "$LAB_ID")"
NB_JSON="$(call_tool notebook_read "$NB_ARGS")"

# Extract [{id,title}] from the notebook_read result.
SOURCES_TSV="$(printf '%s' "$NB_JSON" | python3 -c '
import json, sys
nb = json.load(sys.stdin)
for s in nb.get("sources", []):
    sid = (s.get("id") or "").strip()
    title = (s.get("title") or "").strip()
    if sid:
        print(sid + "\t" + title)
')"

# -- no-sources case ----------------------------------------------------------
if [[ -z "$SOURCES_TSV" ]]; then
  log "WARNING: lab has NO uploaded sources -- the data room is empty."
  log "Nothing to fetch. ingest_dataroom.sh on '$OUT_DIR' will report 0 docs."
  log "INTAKE should ask the user to upload sources before diligence proceeds."
  # Publish an actually empty OUT_DIR, replacing any prior-run files so stale
  # evidence cannot masquerade as the current lab's data room.
  replace_out_dir
  echo "FETCHED=0 LAB=${LAB_ID:-<fixture>} OUT=$OUT_DIR"
  exit 0
fi

# -- 2) fetch each source's full_text + write one .md per source --------------
COUNT=0
SKIPPED=0
while IFS=$'\t' read -r sid title; do
  [[ -z "$sid" ]] && continue
  SR_ARGS="$(python3 -c 'import json,sys; print(json.dumps({"source_id": sys.argv[1]}))' "$sid")"
  if ! SR_JSON="$(call_tool source_read "$SR_ARGS")"; then
    log "WARN: source_read failed for $sid; skipping"
    SKIPPED=$((SKIPPED+1))
    continue
  fi
  # Slugify the title into a safe basename; fall back to the source id.
  # Write the source's full_text as <slug>.md into OUT_DIR. truncated text
  # (source_read caps full_text at 32000 chars) is flagged inline so a
  # downstream reader knows the doc is partial, never silently clipped.
  # The source JSON is passed as argv[1] (NOT stdin): the heredoc below
  # already owns python's stdin, so a pipe would be swallowed by it.
  python3 - "$SR_JSON" "$STAGE_DIR" "$sid" "$title" <<'PYEOF'
import json, re, sys, os

sr = json.loads(sys.argv[1])
out_dir, sid, title = sys.argv[2], sys.argv[3], sys.argv[4]

text = sr.get("full_text") or ""
truncated = bool(sr.get("truncated"))
display_title = (sr.get("title") or title or sid).strip()

slug = re.sub(r"[^A-Za-z0-9._-]+", "-", display_title).strip("-").lower()
if not slug:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", sid).strip("-").lower() or "source"
path = os.path.join(out_dir, slug + ".md")
# Avoid clobbering when two sources slugify to the same name.
n = 1
base = path
while os.path.exists(path):
    path = base[:-3] + ("-%d.md" % n)
    n += 1

header = "# %s\n\n<!-- source_id: %s -->\n" % (display_title, sid)
if truncated:
    header += ("<!-- NOTE: full_text truncated by source_read at 32000 chars; "
               "doc is partial -->\n")
header += "\n"
with open(path, "w", encoding="utf-8") as fh:
    fh.write(header + text)
print(os.path.basename(path))
PYEOF
  COUNT=$((COUNT+1))
  log "  -> ${sid}  (${title:-untitled})"
done <<< "$SOURCES_TSV"

replace_out_dir
log "assembled $COUNT source file(s) into '$OUT_DIR' (skipped $SKIPPED)"
echo "FETCHED=$COUNT SKIPPED=$SKIPPED LAB=${LAB_ID:-<fixture>} OUT=$OUT_DIR"
