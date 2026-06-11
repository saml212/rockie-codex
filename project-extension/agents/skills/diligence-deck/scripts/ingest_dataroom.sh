#!/usr/bin/env bash
# ingest_dataroom.sh -- normalize a data room folder into manifest.json
#
# Usage: ingest_dataroom.sh <dataroom_path> [output_path]
#
# <dataroom_path>  Folder of deal documents (PDF, DOCX, TXT, MD).
# [output_path]    Where to write manifest.json. Defaults to
#                  <dataroom_path>/manifest.json.
#
# Outputs manifest.json: array of objects with fields:
#   filename (basename, for display), relpath (path relative to the data-room
#   root, used to actually open the file -- nested docs live in subfolders),
#   type, size_bytes, excerpt (first ~500 chars of text),
#   extraction_method, warnings[]
#
# Dependencies: bash, python3 (stdlib only), pdftotext (optional).
# Degrades gracefully when pdftotext is absent.

set -euo pipefail

DATAROOM="${1:-}"
OUTPUT="${2:-}"

# -- Validation ---------------------------------------------------------------

if [[ -z "$DATAROOM" ]]; then
  echo "ERROR: no data room path supplied." >&2
  echo "Usage: $0 <dataroom_path> [output_path]" >&2
  exit 1
fi

if [[ ! -d "$DATAROOM" ]]; then
  echo "ERROR: '$DATAROOM' is not a directory." >&2
  exit 1
fi

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="${DATAROOM%/}/manifest.json"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required but not found in PATH." >&2
  exit 1
fi

SUPPORTED_EXTENSIONS="pdf docx txt md"

# -- Python helpers -----------------------------------------------------------

# extract_text_py: python3 script for text extraction; called with filepath.
# Writes extracted text to stdout. Uses stdlib only (zipfile for docx).
EXTRACT_PY="$(cat <<'ENDPY'
import sys, os, re

filepath = sys.argv[1]
ext = os.path.splitext(filepath)[1].lower().lstrip(".")

if ext in ("txt", "md"):
    with open(filepath, "rb") as f:
        raw = f.read(500).decode("utf-8", errors="replace")
    print(raw[:500], end="")
    print("direct_read", file=sys.stderr)

elif ext == "pdf":
    # Try pdftotext via subprocess; stdlib only (no pdfminer dep)
    import subprocess, shutil
    if shutil.which("pdftotext"):
        result = subprocess.run(
            ["pdftotext", "-l", "2", filepath, "-"],
            capture_output=True, timeout=15
        )
        text = result.stdout.decode("utf-8", errors="replace")[:500]
        print(text, end="")
        print("pdftotext", file=sys.stderr)
    else:
        print("[PDF text unavailable -- install poppler-utils (pdftotext) for extraction]", end="")
        print("placeholder:no_pdftotext", file=sys.stderr)

elif ext == "docx":
    import zipfile
    try:
        with zipfile.ZipFile(filepath) as z:
            with z.open("word/document.xml") as f:
                xml = f.read().decode("utf-8", errors="replace")
        text = re.sub(r"<[^>]+>", " ", xml)
        text = re.sub(r"\s+", " ", text).strip()[:500]
        print(text, end="")
        print("docx_xml_extract", file=sys.stderr)
    except Exception as e:
        print(f"[DOCX extraction error: {e}]", end="")
        print(f"placeholder:docx_error", file=sys.stderr)

else:
    print(f"[Unsupported file type: .{ext}]", end="")
    print(f"unsupported", file=sys.stderr)
ENDPY
)"

# -- Discover files -----------------------------------------------------------

echo "NOTE: scanning up to 5 directory levels deep; deeper files are skipped." >&2
mapfile -t ALL_FILES < <(find "$DATAROOM" -maxdepth 5 -type f | sort)

SUPPORTED_FILES=()
SKIPPED_FILES=()

for f in "${ALL_FILES[@]}"; do
  ext="${f##*.}"
  ext="${ext,,}"
  basename_f="$(basename "$f")"
  # Skip our own scaffolding outputs and hidden files (so re-runs stay clean
  # and reconcile.json/manifest.json never count as "skipped data-room docs").
  if [[ "$basename_f" == manifest.json || "$basename_f" == reconcile.json || "$basename_f" == .* ]]; then
    continue
  fi
  if echo "$SUPPORTED_EXTENSIONS" | grep -qw "$ext"; then
    SUPPORTED_FILES+=("$f")
  else
    SKIPPED_FILES+=("$f")
  fi
done

# -- Empty folder check -------------------------------------------------------

if [[ ${#ALL_FILES[@]} -eq 0 ]]; then
  echo "WARNING: data room '$DATAROOM' contains no files." >&2
  python3 -c "
import json, sys
from datetime import datetime, timezone
manifest = {
  'dataroom_path': sys.argv[1],
  'generated_at': datetime.now(timezone.utc).isoformat(),
  'document_count': 0,
  'skipped_count': 0,
  'documents': [],
  'skipped': [],
  'warnings': ['Data room is empty']
}
with open(sys.argv[2], 'w') as out:
    json.dump(manifest, out, indent=2)
print('manifest.json written (empty data room)')
" "$DATAROOM" "$OUTPUT"
  exit 0
fi

# -- Build manifest -----------------------------------------------------------

echo "Ingesting ${#SUPPORTED_FILES[@]} file(s) from '$DATAROOM'..." >&2

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

DOCS_FILE="$TMPDIR_WORK/docs.json"
echo "[]" > "$DOCS_FILE"

DATAROOM_ROOT="${DATAROOM%/}"

for filepath in "${SUPPORTED_FILES[@]}"; do
  filename="$(basename "$filepath")"
  # relpath: the doc's path RELATIVE to the data-room root. Reconcile opens
  # files by relpath so nested docs (financials/Q-summary.txt) are readable;
  # filename alone is ambiguous and unresolvable for non-root docs.
  relpath="${filepath#"$DATAROOM_ROOT"/}"
  ext="${filename##*.}"
  ext="${ext,,}"
  size_bytes="$(wc -c < "$filepath" | tr -d ' ')"

  echo "  -> $relpath ($size_bytes bytes)" >&2

  # Run python extractor; stderr = method, stdout = excerpt
  method_file="$TMPDIR_WORK/method.txt"
  excerpt_file="$TMPDIR_WORK/excerpt.txt"

  python3 -c "$EXTRACT_PY" "$filepath" \
    > "$excerpt_file" \
    2> "$method_file" \
    || true

  method="$(cat "$method_file" 2>/dev/null || echo "error")"
  excerpt="$(cat "$excerpt_file" 2>/dev/null || echo "")"

  # Build warnings from method
  warnings_json="[]"
  if [[ "$method" == placeholder:* ]]; then
    reason="${method#placeholder:}"
    warnings_json="$(python3 -c "import json,sys; print(json.dumps([sys.argv[1]]))" "extraction unavailable: $reason")"
    method="placeholder"
  elif [[ "$method" == "unsupported" ]]; then
    warnings_json="$(python3 -c "import json,sys; print(json.dumps([sys.argv[1]]))" "unsupported file type: .$ext")"
  fi

  # Append to docs array
  python3 - "$DOCS_FILE" "$filename" "$ext" "$size_bytes" "$method" "$warnings_json" "$excerpt" "$relpath" <<'PYEOF'
import sys, json

docs_file  = sys.argv[1]
filename   = sys.argv[2]
ext        = sys.argv[3]
size_bytes = int(sys.argv[4])
method     = sys.argv[5]
warnings   = json.loads(sys.argv[6])
excerpt    = sys.argv[7]
relpath    = sys.argv[8]

with open(docs_file) as f:
    docs = json.load(f)

docs.append({
    "filename":          filename,
    "relpath":           relpath,
    "type":              ext,
    "size_bytes":        size_bytes,
    "extraction_method": method,
    "excerpt":           excerpt,
    "warnings":          warnings,
})

with open(docs_file, "w") as f:
    json.dump(docs, f, indent=2)
PYEOF

done

# -- Assemble final manifest --------------------------------------------------

SKIPPED_JSON="$(python3 -c "
import json, sys
items = sys.argv[1:]
print(json.dumps(items))
" "${SKIPPED_FILES[@]+"${SKIPPED_FILES[@]}"}")"

python3 - "$DATAROOM" "$DOCS_FILE" "$SKIPPED_JSON" "$OUTPUT" <<'PYEOF'
import sys, json
from datetime import datetime, timezone

dataroom    = sys.argv[1]
docs_file   = sys.argv[2]
skipped     = json.loads(sys.argv[3])
output_path = sys.argv[4]

with open(docs_file) as f:
    docs = json.load(f)

global_warnings = []
if not docs:
    global_warnings.append("No supported files found; check extensions (pdf, docx, txt, md)")
if skipped:
    global_warnings.append(f"{len(skipped)} file(s) skipped (unsupported type)")

# Basename collisions across folders: two docs share a filename but live in
# different folders. Files are resolved by relpath so this is not fatal, but a
# collision means display-by-basename is ambiguous -- surface it.
from collections import Counter
name_counts = Counter(d["filename"] for d in docs)
dupes = sorted(n for n, c in name_counts.items() if c > 1)
for n in dupes:
    paths = ", ".join(d["relpath"] for d in docs if d["filename"] == n)
    global_warnings.append(
        f"basename collision: '{n}' appears in multiple folders ({paths}); "
        f"resolved by relpath, but displays are ambiguous"
    )

manifest = {
    "dataroom_path":  dataroom,
    "generated_at":   datetime.now(timezone.utc).isoformat(),
    "document_count": len(docs),
    "skipped_count":  len(skipped),
    "documents":      docs,
    "skipped":        [{"path": p, "reason": "unsupported_type"} for p in skipped],
    "warnings":       global_warnings,
}

with open(output_path, "w") as f:
    json.dump(manifest, f, indent=2)

print(f"manifest.json -> {output_path}")
print(f"  documents : {len(docs)}")
print(f"  skipped   : {len(skipped)}")
for w in global_warnings:
    print(f"  WARNING   : {w}")
PYEOF

echo "Done." >&2
