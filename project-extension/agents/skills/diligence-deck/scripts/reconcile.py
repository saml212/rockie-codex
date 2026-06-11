#!/usr/bin/env python3
"""reconcile.py -- cross-document consistency check over a data room.

A1 step [3.5] RECONCILE. Runs AFTER ingest_dataroom.sh and BEFORE synthesis.
A paraphrased number is a wrong number; a number that two data-room
documents disagree on is a finding the partner WILL ask about. This script
surfaces those deltas mechanically so the synthesizer cannot paper over them.

What it does:
  1. Reads the full text of every document listed in the ingest manifest
     (not the 500-char excerpt -- load-bearing numbers live deeper).
  2. Extracts values for a fixed set of canonical metrics (ARR, revenue,
     headcount, customer count, ...) wherever a document states one.
  3. Flags CONTRADICTIONS: a metric with materially different values across
     two or more documents.
  4. Flags MISSING-BUT-EXPECTED metrics: canonical metrics no document
     states at all -> open questions for management / A3 compliance checks.

Output: reconcile.json next to the manifest (or --out PATH), plus a
human-readable summary on stdout. Exit code is ALWAYS 0 on a successful
run -- contradictions are data, not script failures. Use --strict to exit
2 when contradictions are found (handy for CI / the A3 critic).

Dependencies: python3 stdlib only. pdftotext (poppler) used if present for
PDFs; degrades to an excerpt-only note otherwise. docx via zipfile.

Usage:
  reconcile.py <manifest.json> [--out reconcile.json] [--strict]
  reconcile.py <dataroom_dir>  [--out reconcile.json] [--strict]
"""

import sys
import os
import re
import json
import math
import shutil
import zipfile
import subprocess
from datetime import datetime, timezone

# --- Canonical metric catalog -------------------------------------------------
# Each metric: a stable id, a human label, the regexes that capture a value
# in document text, a normalizer to a comparable number, and whether it is
# "expected" in a standard data room (drives missing-but-expected flags).
#
# Keep this catalog small and high-signal. It is not a full financial parser;
# it targets the handful of figures that, when they disagree across the CIM,
# the financials, and the cap table / customer list, blow up a deal.

# Helper normalizers -----------------------------------------------------------

def _to_usd(num_str, unit):
    """Normalize a captured money figure to USD (float)."""
    n = float(num_str.replace(",", ""))
    u = (unit or "").lower()
    if u in ("b", "bn", "billion"):
        return n * 1_000_000_000
    if u in ("m", "mm", "million"):
        return n * 1_000_000
    if u in ("k", "thousand"):
        return n * 1_000
    return n


def _plain_int(num_str, _unit=None):
    return float(num_str.replace(",", ""))


# Each pattern is (regex, value_group, unit_group_or_None). The regex runs
# case-insensitively over the whole document text. value_group / unit_group
# are 1-based capture indices.
#
# Patterns are deliberately CONSERVATIVE: the number must sit immediately
# after the metric label (only whitespace / ":" / "$" / parens between), and
# a "%" right after the value disqualifies the hit (a ratio is not the metric).
# A missed real figure becomes a missing-but-expected open question; a
# FALSE contradiction would mislead the synthesizer. We bias to the former.
#
# `exclude` is a regex tested against the ~40 chars of context BEFORE each
# hit; a match drops the hit (kills "TOP-10 ARR TOTAL", "ARR at risk", etc.).

_MONEY_VAL = r"\$?\s*([\d,]+(?:\.\d+)?)\s*(billion|million|thousand|bn|mm|[bmk])?\b"

METRICS = [
    {
        "id": "arr",
        "label": "ARR (annual recurring revenue)",
        "expected": True,
        "norm": _to_usd,
        "tolerance_pct": 2.0,   # values within 2% are "the same"
        # "5x ARR ($35M)" is a VALUATION, not ARR -> "x ARR" / "multiple"
        # context is excluded so the parenthetical price is never read as ARR.
        "exclude": r"(top[- ]?\d+|total|at risk|combined|%|less than|below|over|above|x ARR|multiple|valuation)",
        "patterns": [
            # "ARR (as of ...): 7,400"  /  "subscription ARR: 7,100"
            (r"\bARR\b\s*(?:\([^)]*\))?\s*:\s*" + _MONEY_VAL, 1, 2),
            # Gap is BOUNDED + non-greedy so "annual recurring revenue stands
            # at $9.0M" anchors $9.0M, not a stray trailing digit. An unbounded
            # greedy gap consumes "...$9." and captured "0" -> a silent 0.
            (r"\bannual recurring revenue\b[^\n:]{0,30}?:?\s*" + _MONEY_VAL, 1, 2),
            (r"subscription ARR\b\s*:?\s*" + _MONEY_VAL, 1, 2),
        ],
    },
    {
        "id": "total_revenue",
        "label": "Total revenue",
        "expected": True,
        "norm": _to_usd,
        "tolerance_pct": 2.0,
        "exclude": r"(recurring|subscription|services|cost of|%)",
        "patterns": [
            (r"total revenue\b\s*:?\s*" + _MONEY_VAL, 1, 2),
        ],
    },
    {
        "id": "headcount",
        "label": "Headcount / employees",
        "expected": True,
        "norm": _plain_int,
        "tolerance_pct": 0.0,   # headcount must match exactly
        "exclude": r"(#|rank|customer)",
        "patterns": [
            (r"\bemployees?\b\s*:?\s*\**\s*([\d,]{2,})\b", 1, None),
            (r"\bheadcount\b\s*:?\s*\**\s*([\d,]{2,})\b", 1, None),
            # Number-BEFORE-label: keep it on ONE line ([ \t], not \s) so a
            # value on the previous line (e.g. "8,200\nHeadcount") can't bind.
            (r"\b([\d,]{2,})[ \t]+(?:full[- ]time employees|FTEs?)\b", 1, None),
        ],
    },
    {
        "id": "customer_count",
        "label": "Customer count",
        "expected": True,
        "norm": _plain_int,
        "tolerance_pct": 0.0,
        # "12.4% of ARR", "Customer #1", "top-10 customer" are NOT a count.
        "exclude": r"(#|rank|top[- ]?\d+|%|\.\d)",
        "patterns": [
            (r"\bcustomers\b\s*(?:\([^)]*\))?\s*:\s*([\d,]{2,})\b", 1, None),
            # Number-BEFORE-label: same-line only ([ \t]) so "8,200\nCustomers"
            # (a Total Revenue value on the line above) is not read as a count.
            (r"\b([\d,]{2,})[ \t]+customers\b", 1, None),
        ],
    },
    {
        "id": "cap_table_shares",
        "label": "Fully-diluted shares / cap table",
        "expected": True,   # expected in a real data room; absence is an open question
        "norm": _plain_int,
        "tolerance_pct": 0.0,
        "exclude": r"",
        "patterns": [
            # Bounded non-greedy gap: an unbounded greedy gap mis-aligns the
            # value group on "fully-diluted shares (as converted): 12,000,000".
            (r"fully[- ]diluted shares\b[^\n:]{0,30}?:?\s*([\d,]+)\b", 1, None),
            (r"total shares outstanding\b[^\n:]{0,30}?:?\s*([\d,]+)\b", 1, None),
        ],
    },
]


# Whether a captured figure is plausibly in $thousands when no unit is given.
# The fixtures state financials "in USD thousands"; we detect that header so
# a bare "7,400" reconciles against a CIM "$7.1M".

def _thousands_context(text):
    return bool(re.search(r"in USD thousands|\$K\b|\(\$K\)|figures in.*thousand", text, re.I))


# --- Text extraction ----------------------------------------------------------

def extract_text(filepath):
    """Best-effort full-text extraction. stdlib + optional pdftotext."""
    ext = os.path.splitext(filepath)[1].lower().lstrip(".")
    try:
        if ext in ("txt", "md"):
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read(), "direct_read"
        if ext == "pdf":
            if shutil.which("pdftotext"):
                r = subprocess.run(
                    ["pdftotext", filepath, "-"],
                    capture_output=True, timeout=30,
                )
                return r.stdout.decode("utf-8", errors="replace"), "pdftotext"
            return "", "pdf_no_pdftotext"
        if ext == "docx":
            with zipfile.ZipFile(filepath) as z:
                with z.open("word/document.xml") as f:
                    xml = f.read().decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", " ", xml)
            return re.sub(r"\s+", " ", text), "docx_xml_extract"
    except Exception as e:  # noqa: BLE001 -- never let one bad file kill the run
        return "", f"error:{e}"
    return "", "unsupported"


# --- Metric extraction --------------------------------------------------------

def extract_metric_values(filename, text):
    """Return list of observations: {metric_id, raw, value, snippet}."""
    obs = []
    thousands = _thousands_context(text)
    for metric in METRICS:
        exclude_re = metric.get("exclude") or None
        for pat, vg, ug in metric["patterns"]:
            for m in re.finditer(pat, text, re.I):
                # A value immediately followed by "%" is a ratio, not the metric.
                tail = text[m.end():m.end() + 1]
                if tail == "%":
                    continue
                # Context guard: drop hits whose preceding text ON THE SAME
                # LINE matches the metric's exclude regex (kills "TOP-10 ARR
                # TOTAL", "12.4% of ARR", "Customer #1"). Line-local so an
                # unrelated prior line cannot poison the match.
                line_start = text.rfind("\n", 0, m.start()) + 1
                ctx = text[line_start:m.start()]
                if exclude_re and re.search(exclude_re, ctx, re.I):
                    continue
                num = m.group(vg)
                unit = m.group(ug) if ug else None
                # If money metric, no explicit unit, but doc is in thousands,
                # treat the bare figure as $K so it compares to "$7.1M".
                if metric["norm"] is _to_usd and not unit and thousands:
                    unit = "k"
                try:
                    value = metric["norm"](num, unit)
                except (ValueError, TypeError):
                    continue
                # A normalized 0 is never a real ARR / headcount / share count;
                # it means the regex mis-captured (e.g. the trailing "0" of
                # "$9.0M"). Drop it as unparseable rather than treat 0 as a
                # figure that fires a spurious ~99900% delta against a real one.
                if value == 0:
                    continue
                snippet = re.sub(r"\s+", " ", m.group(0).strip())[:140]
                obs.append({
                    "metric_id": metric["id"],
                    "metric_label": metric["label"],
                    "raw": snippet,
                    "value": value,
                    "filename": filename,
                })
    return obs


def _dedupe_observations(obs):
    """Collapse identical (metric, value, filename) hits from overlapping patterns."""
    seen = set()
    out = []
    for o in obs:
        key = (o["metric_id"], round(o["value"], 4), o["filename"])
        if key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


# --- Reconciliation -----------------------------------------------------------

def _is_unit_ambiguity(values):
    """True if the ONLY spread across values is a ~1000x unit jump.

    "$7.0M" vs "7000" is the same figure stated in $M vs $K (or units vs
    thousands), not a contradiction. We treat the cluster as unit-ambiguous
    when every distinct value, divided by the smallest, is within 5% of a
    power of 1000 (i.e. 1, 1000, 1e6 ...). Anything that doesn't collapse to a
    clean 1000^k ratio is a genuine disagreement and stays a contradiction.
    """
    lo = min(values)
    if lo <= 0:
        return False
    for v in values:
        ratio = v / lo
        # nearest power of 1000
        k = round(math.log(ratio, 1000)) if ratio > 0 else 0
        target = 1000 ** k
        if target <= 0 or abs(ratio - target) / target > 0.05:
            return False
    # must actually contain a jump (not all identical) AND k must reach >=1000x
    return max(values) / lo >= 999.0


def reconcile(observations):
    """Group observations by metric; flag contradictions, unit-ambiguities,
    and missing-but-expected."""
    by_metric = {}
    for o in observations:
        by_metric.setdefault(o["metric_id"], []).append(o)

    contradictions = []
    unit_ambiguities = []
    for metric in METRICS:
        mid = metric["id"]
        hits = _dedupe_observations(by_metric.get(mid, []))
        if not hits:
            continue
        # A contradiction requires DIFFERENT FILES to disagree. A single
        # document restating its own number two ways (e.g. "subscription ARR
        # 7,100" vs "ARR incl. future contracts 7,400") is the doc explaining
        # itself, not a cross-document delta -- the synthesizer handles that in
        # the relevant section, the reconcile step does not raise it.
        per_file_value = {}
        for h in hits:
            per_file_value.setdefault(h["filename"], set()).add(round(h["value"], 4))
        cross_file_values = sorted({v for vs in per_file_value.values() for v in vs})
        if len(per_file_value) < 2 or len(cross_file_values) < 2:
            continue
        # Only raise if the SPREAD across files exceeds tolerance.
        values = cross_file_values
        lo, hi = min(values), max(values)
        base = lo if lo else 1.0
        delta_pct = ((hi - lo) / base) * 100.0
        if delta_pct <= metric["tolerance_pct"]:
            continue
        observations_out = [
            {"filename": h["filename"], "value": h["value"], "raw": h["raw"]}
            for h in hits
        ]
        # A pure ~1000x spread is a unit ambiguity ($7.0M vs 7000), not a
        # contradiction -- emit it as an open question / warning instead.
        if _is_unit_ambiguity(values):
            unit_ambiguities.append({
                "metric_id": mid,
                "metric_label": metric["label"],
                "low": lo,
                "high": hi,
                "note": "values differ by ~1000x; likely a unit mismatch "
                        "($M vs $K / units vs thousands), not a contradiction",
                "observations": observations_out,
            })
            continue
        contradictions.append({
            "metric_id": mid,
            "metric_label": metric["label"],
            "low": lo,
            "high": hi,
            "delta_pct": round(delta_pct, 2),
            "tolerance_pct": metric["tolerance_pct"],
            "observations": observations_out,
        })

    present_ids = {o["metric_id"] for o in observations}
    missing_expected = [
        {"metric_id": m["id"], "metric_label": m["label"]}
        for m in METRICS
        if m["expected"] and m["id"] not in present_ids
    ]
    return contradictions, unit_ambiguities, missing_expected


# --- Manifest / dataroom resolution ------------------------------------------

def resolve_inputs(arg):
    """Return (dataroom_path, [docs], manifest_path) from a manifest or dir path.

    Each doc is (display_name, relpath): relpath is the path RELATIVE to the
    data-room root and is what we actually open -- nested docs live in
    subfolders, so resolving by basename alone silently misses them. Older
    manifests without a relpath field fall back to the basename.
    """
    if os.path.isdir(arg):
        manifest_path = os.path.join(arg, "manifest.json")
        if not os.path.isfile(manifest_path):
            sys.exit(f"ERROR: no manifest.json in '{arg}'. Run ingest_dataroom.sh first.")
        arg = manifest_path
    if not os.path.isfile(arg):
        sys.exit(f"ERROR: '{arg}' is not a manifest file or data room directory.")
    with open(arg, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    dataroom = manifest.get("dataroom_path") or os.path.dirname(arg)
    docs = []
    for d in manifest.get("documents", []):
        name = d["filename"]
        relpath = d.get("relpath") or name
        docs.append((name, relpath))
    return dataroom, docs, arg


# --- Main --------------------------------------------------------------------

def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    opts = [a for a in argv[1:] if a.startswith("--")]
    if not args:
        sys.exit(__doc__)

    strict = "--strict" in opts
    out_path = None
    for o in opts:
        if o.startswith("--out="):
            out_path = o.split("=", 1)[1]
    if "--out" in opts:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out_path = argv[i + 1]

    dataroom, docs, manifest_path = resolve_inputs(args[0])
    if out_path is None:
        out_path = os.path.join(dataroom, "reconcile.json")

    observations = []
    extraction_notes = []
    for fn, relpath in docs:
        # Resolve by relpath (relative to the data-room root) so nested docs in
        # subfolders are actually opened; fn (basename) is for display only.
        fp = os.path.join(dataroom, relpath)
        if not os.path.isfile(fp):
            extraction_notes.append({"filename": fn, "relpath": relpath,
                                     "note": "file_missing_at_reconcile"})
            continue
        text, method = extract_text(fp)
        if not text:
            extraction_notes.append({"filename": fn, "relpath": relpath, "note": method})
            continue
        observations.extend(extract_metric_values(fn, text))

    observations = _dedupe_observations(observations)
    contradictions, unit_ambiguities, missing_expected = reconcile(observations)

    report = {
        "manifest": manifest_path,
        "dataroom_path": dataroom,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents_scanned": len(docs),
        "metrics_catalog": [m["id"] for m in METRICS],
        "contradiction_count": len(contradictions),
        "contradictions": contradictions,
        "unit_ambiguity_count": len(unit_ambiguities),
        "unit_ambiguities": unit_ambiguities,
        "missing_expected": missing_expected,
        "extraction_notes": extraction_notes,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Human-readable summary --------------------------------------------------
    print(f"reconcile.json -> {out_path}")
    print(f"  documents scanned : {len(docs)}")
    print(f"  contradictions    : {len(contradictions)}")
    for c in contradictions:
        srcs = ", ".join(
            f"{o['filename']}={o['value']:g}" for o in c["observations"]
        )
        print(f"    ! {c['metric_label']}: {c['delta_pct']:g}% delta  [{srcs}]")
    print(f"  unit-ambiguities  : {len(unit_ambiguities)}")
    for u in unit_ambiguities:
        srcs = ", ".join(
            f"{o['filename']}={o['value']:g}" for o in u["observations"]
        )
        print(f"    ~ {u['metric_label']}: ~1000x unit mismatch (open question)  [{srcs}]")
    print(f"  missing-but-expected : {len(missing_expected)}")
    for mexp in missing_expected:
        print(f"    ? {mexp['metric_label']} -- not found in any document")

    if strict and contradictions:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
