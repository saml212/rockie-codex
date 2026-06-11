#!/usr/bin/env python3
"""battleground.py -- a MODEL BATTLEGROUND harness for the diligence agent.

Run the SAME diligence-findings task across multiple LLMs (Stage 1: COMPETE),
then hand ALL of their outputs -- model identities STRIPPED and order
RANDOMIZED -- to a fresh, anonymized senior-partner critic (Stage 2: JUDGE)
that scores each on the partner-critic rubric, ranks them, and picks a winner.
The judge is never told which output came from which model, so it cannot anchor
on a brand or a reputation; it judges only the artifact.

This is a BAKE-OFF, not the A3 critic loop. A3 (`critic_loop.py`) hardens ONE
run until it passes twice; this harness compares N runs from N models and ranks
them once. They share the partner-critic rubric but answer different questions:
A3 asks "is this run partner-grade yet?"; the battleground asks "which model's
run is most partner-grade?".

------------------------------------------------------------------------------
PROVIDER ADAPTERS
------------------------------------------------------------------------------
Each adapter reads its API key from the environment and is SKIPPED if its key
is unset. NO key is ever hardcoded, logged, or written to any output file.

  * anthropic -> POST https://api.anthropic.com/v1/messages
                 header x-api-key: $ANTHROPIC_API_KEY, anthropic-version 2023-06-01
                 model claude-opus-4-8
  * openai    -> POST https://api.openai.com/v1/responses
                 header Authorization: Bearer $OPENAI_API_KEY
                 model gpt-5.5
  * gemini    -> POST .../v1beta/models/gemini-2.5-pro:generateContent
                 header x-goog-api-key: $GEMINI_API_KEY

Each adapter returns PLAIN TEXT and records API errors instead of crashing the
whole battle: one provider's 500 must not kill the other competitors.

------------------------------------------------------------------------------
STAGE 1 -- COMPETE
------------------------------------------------------------------------------
Ingest the data room (run ingest_dataroom.sh + reconcile.py, or read their
existing JSON), then assemble ONE diligence prompt from the skill itself:
  goal (SKILL.md) + methodology + findings.json contract + evidence discipline
  + the data-room document text + the reconcile flags.
Send that SAME prompt to every available model and collect each model's
findings (we ask for findings.json; prose is tolerated and recorded as-is).

------------------------------------------------------------------------------
STAGE 2 -- JUDGE (fresh, anonymized, unbiased)
------------------------------------------------------------------------------
Assemble the partner-critic prompt (prompts/partner-critic.md +
references/partner-critic-rubric.md), present ALL competitor outputs with model
identities STRIPPED and order RANDOMIZED -- labelled A / B / C, with a PRIVATE
label->model map kept only in this process -- and ask ONE judge model (default
anthropic) to SCORE each on the rubric dimensions (faithfulness, citation
quality / verbatim, hallucination, MECE coverage, declarative titles, reconcile
coverage), RANK them, and pick the winner with reasoning. The judge prompt
NEVER names a model.

------------------------------------------------------------------------------
OUTPUT
------------------------------------------------------------------------------
Print a ranking table (winner + per-entry scores, DE-ANONYMIZED for the human)
and write a full results JSON to --out (default /tmp/battleground-results.json).
Keys are NEVER written anywhere.

------------------------------------------------------------------------------
CLI
------------------------------------------------------------------------------
  --models anthropic,openai,gemini   competitors (default: all with a key present)
  --judge  anthropic                 judge provider (default: anthropic)
  --dataroom <path>                  default: examples/sample-dataroom
  --out <path>                       default: /tmp/battleground-results.json
  --dryrun                           assemble EVERYTHING + print the exact
                                     prompts and API request shapes and which
                                     providers are active, but make NO network
                                     calls.

Dependencies: python3 stdlib + urllib only. NO third-party deps.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Paths -- everything is resolved relative to the skill root so the harness
# reads the LIVE skill files (prompt/rubric/references) and never drifts from
# them. SKILL_ROOT is the parent of this scripts/ directory.
# --------------------------------------------------------------------------- #

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(SCRIPTS_DIR)

SKILL_MD = os.path.join(SKILL_ROOT, "SKILL.md")
METHODOLOGY_MD = os.path.join(SKILL_ROOT, "references", "dd-methodology.md")
EVIDENCE_MD = os.path.join(SKILL_ROOT, "references", "evidence-discipline.md")
CRITIC_PROMPT_MD = os.path.join(SKILL_ROOT, "prompts", "partner-critic.md")
CRITIC_RUBRIC_MD = os.path.join(SKILL_ROOT, "references", "partner-critic-rubric.md")
INGEST_SH = os.path.join(SCRIPTS_DIR, "ingest_dataroom.sh")
RECONCILE_PY = os.path.join(SCRIPTS_DIR, "reconcile.py")
DEFAULT_DATAROOM = os.path.join(SKILL_ROOT, "examples", "sample-dataroom")
DEFAULT_OUT = "/tmp/battleground-results.json"

# How many chars of a doc / prompt we show in --dryrun previews.
PREVIEW_CHARS = 1400

HTTP_TIMEOUT = 120  # seconds per model call

# --------------------------------------------------------------------------- #
# Provider adapters
# --------------------------------------------------------------------------- #
# Each provider is a dict describing how to read its key, build its request, and
# parse its text out of the response. `request_shape()` returns the URL/headers/
# body skeleton WITHOUT the key (for --dryrun and for the results JSON) -- the
# real key is injected only at call time, in memory, never stored or printed.


class ProviderError(Exception):
    """An API call failed; recorded per-model, never crashes the battle."""


def _post_json(url: str, headers: dict, body: dict) -> dict:
    """POST a JSON body and return the parsed JSON response. urllib only."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
        raise ProviderError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"network error: {e.reason}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProviderError(f"non-JSON response: {raw[:300]}") from e


class Provider:
    """Base provider adapter. Subclasses set name/key_env/model and implement
    request_shape() + parse_text()."""

    name = "base"
    key_env = ""
    model = ""

    def key(self) -> str | None:
        return os.environ.get(self.key_env)

    def available(self) -> bool:
        return bool(self.key())

    def request_shape(self, prompt: str) -> dict:
        """Return {url, method, headers, body} with the KEY REDACTED. Used both
        to build the real request (key injected separately) and to print the
        shape in --dryrun without ever revealing the key."""
        raise NotImplementedError

    def _inject_key(self, headers: dict) -> dict:
        raise NotImplementedError

    def parse_text(self, resp: dict) -> str:
        raise NotImplementedError

    def call(self, prompt: str) -> str:
        """Live call. Raises ProviderError on any failure."""
        key = self.key()
        if not key:
            raise ProviderError(f"{self.name}: {self.key_env} not set")
        shape = self.request_shape(prompt)
        headers = self._inject_key(dict(shape["headers"]))
        resp = _post_json(shape["url"], headers, shape["body"])
        text = self.parse_text(resp)
        if not text:
            raise ProviderError(f"{self.name}: empty text in response")
        return text


class AnthropicProvider(Provider):
    name = "anthropic"
    key_env = "ANTHROPIC_API_KEY"
    model = "claude-opus-4-8"

    def request_shape(self, prompt: str) -> dict:
        return {
            "url": "https://api.anthropic.com/v1/messages",
            "method": "POST",
            "headers": {
                "x-api-key": "$ANTHROPIC_API_KEY",  # redacted placeholder
                "anthropic-version": "2023-06-01",
            },
            "body": {
                "model": self.model,
                "max_tokens": 32000,
                "messages": [{"role": "user", "content": prompt}],
            },
        }

    def _inject_key(self, headers: dict) -> dict:
        headers["x-api-key"] = self.key()
        return headers

    def parse_text(self, resp: dict) -> str:
        parts = resp.get("content", []) or []
        return "".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"
        ).strip()


class OpenAIProvider(Provider):
    name = "openai"
    key_env = "OPENAI_API_KEY"
    model = "gpt-5.5"

    def request_shape(self, prompt: str) -> dict:
        return {
            "url": "https://api.openai.com/v1/responses",
            "method": "POST",
            "headers": {
                "Authorization": "Bearer $OPENAI_API_KEY",  # redacted placeholder
            },
            "body": {
                "model": self.model,
                "input": prompt,
                "max_output_tokens": 32000,
            },
        }

    def _inject_key(self, headers: dict) -> dict:
        headers["Authorization"] = f"Bearer {self.key()}"
        return headers

    def parse_text(self, resp: dict) -> str:
        # Responses API: prefer the convenience aggregate, else walk output[].
        if isinstance(resp.get("output_text"), str) and resp["output_text"].strip():
            return resp["output_text"].strip()
        chunks = []
        for item in resp.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            for c in item.get("content", []) or []:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    chunks.append(c["text"])
        return "".join(chunks).strip()


class GeminiProvider(Provider):
    name = "gemini"
    key_env = "GEMINI_API_KEY"
    model = "gemini-2.5-pro"

    def request_shape(self, prompt: str) -> dict:
        return {
            "url": (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent"
            ),
            "method": "POST",
            "headers": {
                "x-goog-api-key": "$GEMINI_API_KEY",  # redacted placeholder
            },
            "body": {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 32000},
            },
        }

    def _inject_key(self, headers: dict) -> dict:
        headers["x-goog-api-key"] = self.key()
        return headers

    def parse_text(self, resp: dict) -> str:
        cands = resp.get("candidates", []) or []
        if not cands:
            return ""
        parts = (cands[0].get("content", {}) or {}).get("parts", []) or []
        return "".join(
            p.get("text", "") for p in parts if isinstance(p, dict)
        ).strip()


PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


def get_provider(name: str) -> Provider:
    cls = PROVIDERS.get(name)
    if cls is None:
        raise SystemExit(
            f"ERROR: unknown provider '{name}'. Known: {', '.join(PROVIDERS)}"
        )
    return cls()


# --------------------------------------------------------------------------- #
# Data-room ingestion
# --------------------------------------------------------------------------- #


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)


def ingest_dataroom(dataroom: str, *, dryrun: bool) -> tuple[dict, dict]:
    """Return (manifest, reconcile). Runs ingest_dataroom.sh + reconcile.py if
    their JSON is absent; otherwise reuses existing JSON. In --dryrun we still
    do this (it is local, makes no network call) so the assembled prompt is
    real -- but we tolerate failure and fall back to whatever JSON exists."""
    manifest_path = os.path.join(dataroom, "manifest.json")
    reconcile_path = os.path.join(dataroom, "reconcile.json")

    def _run(cmd: list[str], label: str) -> None:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            msg = getattr(e, "stderr", "") or str(e)
            print(f"NOTE: {label} did not run cleanly ({msg.strip()[:160]}); "
                  f"using existing JSON if present.", file=sys.stderr)

    if not os.path.isfile(manifest_path):
        _run(["bash", INGEST_SH, dataroom, manifest_path], "ingest_dataroom.sh")
    if not os.path.isfile(reconcile_path):
        _run([sys.executable, RECONCILE_PY, manifest_path, "--out", reconcile_path],
             "reconcile.py")

    manifest = _read_json(manifest_path) if os.path.isfile(manifest_path) else {
        "documents": [], "warnings": ["manifest.json missing"]}
    reconcile = _read_json(reconcile_path) if os.path.isfile(reconcile_path) else {
        "contradictions": [], "unit_ambiguities": [], "missing_expected": [],
        "warnings": ["reconcile.json missing"]}
    return manifest, reconcile


def assemble_dataroom_text(dataroom: str, manifest: dict) -> str:
    """Concatenate the FULL text of every manifest document (not the excerpt --
    load-bearing numbers live deeper). Each doc is fenced with its relpath so
    the model can cite `cite_file` precisely. PDFs/unsupported types degrade to
    the manifest excerpt with a note."""
    blocks = []
    for d in manifest.get("documents", []):
        relpath = d.get("relpath") or d.get("filename", "?")
        fp = os.path.join(dataroom, relpath)
        ext = (d.get("type") or "").lower()
        if ext in ("txt", "md") and os.path.isfile(fp):
            text = _read_text(fp)
            note = ""
        elif os.path.isfile(fp):
            # Non-text (pdf/docx) -- use the excerpt the ingest already pulled.
            text = d.get("excerpt", "") or "[no text extracted]"
            note = f"  (excerpt only; type=.{ext})"
        else:
            text = d.get("excerpt", "") or "[file unavailable]"
            note = "  (file not found at read time; excerpt only)"
        blocks.append(
            f"===== DOCUMENT: {relpath}{note} =====\n{text.strip()}\n"
            f"===== END {relpath} ====="
        )
    if not blocks:
        return "[data room is empty or no documents could be read]"
    return "\n\n".join(blocks)


def format_reconcile_flags(reconcile: dict) -> str:
    """Render reconcile.json deltas as first-class prompt inputs the findings
    MUST address (each lands in exactly one section's reconcile_flags)."""
    lines = []
    for c in reconcile.get("contradictions", []):
        srcs = "; ".join(
            f"{o.get('filename')}={o.get('value'):g}" for o in c.get("observations", [])
            if isinstance(o.get("value"), (int, float))
        )
        lines.append(
            f"- CONTRADICTION [{c.get('metric_label')}]: {c.get('delta_pct')}% delta "
            f"across documents [{srcs}] -- assign to exactly one section's "
            f"reconcile_flags and resolve or raise as an open question."
        )
    for u in reconcile.get("unit_ambiguities", []):
        srcs = "; ".join(
            f"{o.get('filename')}={o.get('value'):g}" for o in u.get("observations", [])
            if isinstance(o.get("value"), (int, float))
        )
        lines.append(
            f"- UNIT-AMBIGUITY [{u.get('metric_label')}]: ~1000x mismatch "
            f"[{srcs}] -- {u.get('note', 'likely a unit mismatch')}; raise as an open question."
        )
    for m in reconcile.get("missing_expected", []):
        lines.append(
            f"- MISSING-BUT-EXPECTED [{m.get('metric_label')}]: not stated in any "
            f"document -- make it an open question for management."
        )
    if not lines:
        return "(no reconcile deltas detected)"
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stage 1 -- COMPETE: the single shared diligence prompt
# --------------------------------------------------------------------------- #


def build_competitor_prompt(dataroom: str, manifest: dict, reconcile: dict) -> str:
    """Assemble ONE diligence prompt from the skill. The SAME string goes to
    every competing model. It carries the goal, methodology, the findings.json
    contract, evidence discipline, the full data-room text, and the reconcile
    flags -- everything a model needs to produce findings.json with no extra
    tool access."""
    goal = _read_text(SKILL_MD)
    methodology = _read_text(METHODOLOGY_MD)
    evidence = _read_text(EVIDENCE_MD)
    dr_text = assemble_dataroom_text(dataroom, manifest)
    flags = format_reconcile_flags(reconcile)
    doc_names = ", ".join(
        d.get("relpath") or d.get("filename", "?") for d in manifest.get("documents", [])
    ) or "(none)"

    return f"""\
You are a senior consultant at a top-tier DD firm producing acquisition
due-diligence FINDINGS for an investment committee. Produce a complete,
partner-grade `findings.json` for the data room below. Your output will be
graded by a skeptical senior partner against a fixed rubric (verbatim
citations, no hallucinated quotes, MECE coverage, declarative titles,
reconcile coverage). Nothing is invented; nothing is paraphrased into a number.

############################################################
# SECTION A -- THE SKILL (goal, pipeline, findings.json contract, rules)
############################################################
{goal}

############################################################
# SECTION B -- DD METHODOLOGY (MECE, pyramid principle, slop patterns)
############################################################
{methodology}

############################################################
# SECTION C -- EVIDENCE DISCIPLINE (verbatim-citation contract, confidence)
############################################################
{evidence}

############################################################
# SECTION D -- DATA ROOM ({len(manifest.get('documents', []))} documents: {doc_names})
# These are the ONLY source documents. Every `cite_quote` MUST be an EXACT
# verbatim substring of one of these documents, and `cite_file` MUST be its
# relpath. A quote that does not appear here is a hallucinated citation -- the
# worst failure mode.
############################################################
{dr_text}

############################################################
# SECTION E -- RECONCILE FLAGS (cross-document deltas you MUST address)
# Each of the following MUST appear in exactly one section's `reconcile_flags`
# and be resolved or raised as an open question. A dropped delta fails review.
############################################################
{flags}

############################################################
# SECTION F -- YOUR TASK
############################################################
Produce `findings.json`: a JSON ARRAY of exactly 9 section objects (section_id
0 through 8), each matching the contract in SECTION A:
  section_id, section, headline (declarative, with the number in it),
  recommendation (set ONLY on section 0; null elsewhere),
  key_facts[] (each: fact, cite_file, cite_quote [EXACT verbatim], cite_locator,
  confidence in {{high, medium, low}}),
  risks[] (each: risk, rating in {{H, M, L}}),
  open_questions[], reconcile_flags[].

Constraints: 3-8 key_facts per section (section 0: 3-5). Declarative titles
everywhere. No hedges. No invented numbers -- absent facts become
"Not available -- open question for management" with a specific open_question.
Every reconcile flag from SECTION E lands in exactly one section.

OUTPUT: Respond with ONLY the `findings.json` array -- a single valid JSON
array, no prose, no markdown fences. If you must add commentary, put the JSON
first so it can be parsed.
"""


# --------------------------------------------------------------------------- #
# Stage 2 -- JUDGE: anonymized, randomized critic prompt
# --------------------------------------------------------------------------- #


def anonymize_entries(entries: list[dict], rng: random.Random) -> tuple[list[dict], dict]:
    """Strip model identities and RANDOMIZE order. Returns (anon_entries,
    private_map) where anon_entries are labelled A/B/C... in shuffled order and
    private_map maps label -> real model name. The judge sees ONLY the labels;
    the map stays in this process for de-anonymizing the final table."""
    shuffled = list(entries)
    rng.shuffle(shuffled)
    labels = [chr(ord("A") + i) for i in range(len(shuffled))]
    anon = []
    private_map = {}
    for label, e in zip(labels, shuffled):
        private_map[label] = e["model"]
        anon.append({"label": label, "output": e["output"], "ok": e["ok"]})
    return anon, private_map


def build_judge_prompt(anon_entries: list[dict]) -> str:
    """Assemble the judge prompt from the skill's partner-critic prompt + rubric,
    then append all competitor outputs as anonymized A/B/C blocks. The judge is
    NEVER told which output is which model -- the prompt contains no model name."""
    critic_prompt = _read_text(CRITIC_PROMPT_MD)
    rubric = _read_text(CRITIC_RUBRIC_MD)

    blocks = []
    for e in anon_entries:
        if e["ok"]:
            body = e["output"]
        else:
            body = (f"[this competitor produced no usable output -- "
                    f"error recorded: {e['output'][:300]}]")
        blocks.append(
            f"================ CANDIDATE {e['label']} ================\n"
            f"{body}\n"
            f"================ END CANDIDATE {e['label']} ================"
        )
    candidates_block = "\n\n".join(blocks)
    label_list = ", ".join(e["label"] for e in anon_entries)

    return f"""\
You are a fresh, no-memory senior partner judging a BAKE-OFF. Several
anonymous associates ({label_list}) each independently produced a draft
`findings.json` for the SAME acquisition data room. You do NOT know who wrote
which; they are labelled only {label_list}. Judge ONLY the artifacts. Do not
guess at authorship and do not let any stylistic tell sway you toward a
"brand" -- there are no brands here, only candidates.

You grade against the SAME fixed rubric you always use. Below is your standing
critic prompt and the rubric, unchanged; apply it to score and RANK the
candidates rather than to pass/fail a single one.

############################################################
# YOUR STANDING CRITIC PROMPT
############################################################
{critic_prompt}

############################################################
# YOUR FIXED RUBRIC
############################################################
{rubric}

############################################################
# THE CANDIDATES (identities stripped, order randomized)
############################################################
{candidates_block}

############################################################
# YOUR JUDGING TASK
############################################################
Score EACH candidate ({label_list}) on these six rubric dimensions, 0-10 each
(10 = partner-grade, 0 = unusable):
  - faithfulness        : facts trace to the data room; no invented numbers.
  - citation_quality    : cite_quote present and VERBATIM (exact substring of a
                          source document); cite_file/locator precise.
  - hallucination       : INVERTED -- 10 = zero hallucinated quotes/numbers,
                          0 = rampant. A single hallucinated citation is fatal.
  - mece_coverage       : 9 sections present, non-overlapping, none thin.
  - declarative_titles  : every headline/fact is a claim with a number, not a
                          topic label.
  - reconcile_coverage  : every cross-doc delta landed in exactly one section's
                          reconcile_flags and was resolved or raised.

Then RANK the candidates best-to-worst and pick the WINNER, with reasoning that
cites the specific rubric dimensions and concrete evidence (e.g. "Candidate B
has a hallucinated cite_quote in section 4 that is not in customer-list.txt").

Return STRICT JSON ONLY, no prose outside it, matching exactly:

{{
  "scores": {{
    "A": {{"faithfulness": 0-10, "citation_quality": 0-10, "hallucination": 0-10,
          "mece_coverage": 0-10, "declarative_titles": 0-10,
          "reconcile_coverage": 0-10, "total": <sum>, "notes": "..."}},
    "...": {{ ... one object per candidate label ... }}
  }},
  "ranking": ["<label best>", "...", "<label worst>"],
  "winner": "<label>",
  "reasoning": "why the winner won and the loser lost, citing rubric dimensions"
}}

Score every candidate label shown above ({label_list}). Output ONLY the JSON.
"""


# --------------------------------------------------------------------------- #
# Output parsing helpers
# --------------------------------------------------------------------------- #


def try_extract_json(text: str):
    """Best-effort: pull the first JSON value (object or array) out of model
    text that may be wrapped in prose or markdown fences. Returns the parsed
    value or None."""
    if not text:
        return None
    t = text.strip()
    # Strip a leading ```json / ``` fence if present.
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Scan for the first balanced {...} or [...] span.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = t.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


# --------------------------------------------------------------------------- #
# Stage runners
# --------------------------------------------------------------------------- #


def run_compete(competitors: list[Provider], prompt: str, *, dryrun: bool) -> list[dict]:
    """Send the SAME prompt to each competitor. Returns one entry per model:
    {model, ok, output, parsed_ok, request_shape}. On --dryrun no call is made;
    each entry records the request shape and a placeholder output."""
    entries = []
    for p in competitors:
        shape = p.request_shape(prompt)
        if dryrun:
            entries.append({
                "model": p.name,
                "model_id": p.model,
                "ok": False,
                "output": "[DRYRUN -- no network call made]",
                "parsed_ok": False,
                "request_shape": shape,
            })
            continue
        try:
            text = p.call(prompt)
            parsed = try_extract_json(text)
            entries.append({
                "model": p.name,
                "model_id": p.model,
                "ok": True,
                "output": text,
                "parsed_ok": parsed is not None,
                "request_shape": shape,
            })
        except ProviderError as e:
            print(f"WARNING: competitor '{p.name}' failed: {e}", file=sys.stderr)
            entries.append({
                "model": p.name,
                "model_id": p.model,
                "ok": False,
                "output": f"ERROR: {e}",
                "parsed_ok": False,
                "request_shape": shape,
            })
    return entries


def run_judge(judge: Provider, judge_prompt: str, *, dryrun: bool) -> dict:
    """Run the single judge model on the anonymized prompt. Returns
    {ok, raw, parsed, request_shape}."""
    shape = judge.request_shape(judge_prompt)
    if dryrun:
        return {"ok": False, "raw": "[DRYRUN -- no network call made]",
                "parsed": None, "request_shape": shape}
    try:
        text = judge.call(judge_prompt)
        return {"ok": True, "raw": text, "parsed": try_extract_json(text),
                "request_shape": shape}
    except ProviderError as e:
        print(f"WARNING: judge '{judge.name}' failed: {e}", file=sys.stderr)
        return {"ok": False, "raw": f"ERROR: {e}", "parsed": None,
                "request_shape": shape}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

_DIMS = ["faithfulness", "citation_quality", "hallucination", "mece_coverage",
         "declarative_titles", "reconcile_coverage"]


def print_ranking_table(judge_result: dict, private_map: dict) -> dict:
    """De-anonymize the judge verdict and print a ranking table. Returns the
    de-anonymized verdict dict for the results JSON."""
    parsed = judge_result.get("parsed")
    if not isinstance(parsed, dict) or "scores" not in parsed:
        print("\nJudge returned no parseable verdict; raw output preserved in results JSON.")
        return {"deanonymized": False, "note": "judge verdict unparseable"}

    scores = parsed.get("scores", {})
    ranking = parsed.get("ranking", [])
    winner_label = parsed.get("winner", "")

    # De-anonymize.
    deanon_scores = {}
    for label, sc in scores.items():
        model = private_map.get(label, f"?{label}")
        deanon_scores[model] = dict(sc, _label=label)
    deanon_ranking = [private_map.get(l, f"?{l}") for l in ranking]
    winner_model = private_map.get(winner_label, f"?{winner_label}")

    print("\n" + "=" * 78)
    print("BATTLEGROUND RANKING (de-anonymized)")
    print("=" * 78)
    header = f"{'model':<12}{'lbl':<5}" + "".join(f"{d[:6]:>8}" for d in _DIMS) + f"{'TOTAL':>8}"
    print(header)
    print("-" * len(header))
    order = deanon_ranking or list(deanon_scores)
    for model in order:
        sc = deanon_scores.get(model, {})
        row = f"{model:<12}{sc.get('_label', '?'):<5}"
        row += "".join(f"{_fmt_num(sc.get(d)):>8}" for d in _DIMS)
        row += f"{_fmt_num(sc.get('total')):>8}"
        print(row)
    print("-" * len(header))
    print(f"WINNER: {winner_model}  (judge label {winner_label})")
    reasoning = parsed.get("reasoning", "")
    if reasoning:
        print(f"\nReasoning: {reasoning}")

    return {
        "deanonymized": True,
        "winner_model": winner_model,
        "winner_label": winner_label,
        "ranking_models": deanon_ranking,
        "scores_by_model": deanon_scores,
        "reasoning": reasoning,
    }


def _fmt_num(v):
    if isinstance(v, (int, float)):
        return f"{v:g}"
    return "-"


# --------------------------------------------------------------------------- #
# Dryrun reporting
# --------------------------------------------------------------------------- #


def _truncate(s: str, n: int = PREVIEW_CHARS) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n... [truncated {len(s) - n} chars]"


def print_dryrun(active, skipped, competitor_prompt, anon_entries, private_map,
                 judge, judge_prompt, competitors):
    line = "=" * 78
    print(line)
    print("BATTLEGROUND --dryrun : assemble everything, MAKE NO NETWORK CALLS")
    print(line)

    print("\n--- PROVIDERS ---")
    print("ACTIVE (key present, would be called):")
    if active:
        for p in active:
            print(f"  + {p.name:<10} model={p.model:<20} key_env={p.key_env} [SET]")
    else:
        print("  (none -- no API keys in env, as expected for the build run)")
    print("SKIPPED (key absent):")
    if skipped:
        for p in skipped:
            print(f"  - {p.name:<10} model={p.model:<20} key_env={p.key_env} [UNSET]")
    else:
        print("  (none)")

    print("\n--- REQUEST SHAPES (keys REDACTED -- never printed, never stored) ---")
    for p in competitors:
        shape = p.request_shape("<PROMPT>")
        print(f"\n[{p.name}] {shape['method']} {shape['url']}")
        print(f"  headers: {json.dumps(shape['headers'])}")
        body_preview = dict(shape["body"])
        # Don't dump the whole prompt into the body preview.
        if "messages" in body_preview:
            body_preview["messages"] = "[<PROMPT> as user message]"
        if "input" in body_preview:
            body_preview["input"] = "<PROMPT>"
        if "contents" in body_preview:
            body_preview["contents"] = "[{parts:[{text:<PROMPT>}]}]"
        print(f"  body:    {json.dumps(body_preview)}")

    print("\n--- STAGE 1 COMPETITOR PROMPT (the SAME string sent to every model) ---")
    print(f"(total length: {len(competitor_prompt)} chars; preview below)\n")
    print(_truncate(competitor_prompt))

    print("\n--- STAGE 2 JUDGE PROMPT (anonymized A/B/C, order randomized) ---")
    print(f"judge provider: {judge.name} (model {judge.model})")
    print("anonymized candidate labels in this run: "
          + ", ".join(e["label"] for e in anon_entries))
    print("PRIVATE label->model map (kept in-process; NOT in the judge prompt): "
          + json.dumps(private_map))
    print("VERIFY the judge prompt names no model: "
          + ("FAIL -- a model name leaked!" if _judge_prompt_leaks(judge_prompt, competitors)
             else "OK (no provider name appears in the judge prompt)"))
    print(f"\n(total length: {len(judge_prompt)} chars; preview below)\n")
    print(_truncate(judge_prompt))

    print("\n" + line)
    print("NO-NETWORK PROOF: --dryrun took the dryrun branch in run_compete and")
    print("run_judge; urllib.request.urlopen was never reached. Zero sockets opened.")
    print(line)


def _judge_prompt_leaks(judge_prompt: str, competitors) -> bool:
    """Defense-in-depth check: assert no provider name appears in the judge
    prompt, so anonymization cannot silently regress."""
    lowered = judge_prompt.lower()
    for p in competitors:
        if p.name.lower() in lowered:
            return True
        # also check model ids
        if p.model.lower() in lowered:
            return True
    return False


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def select_competitors(models_arg: str | None) -> tuple[list, list]:
    """Return (active, skipped) provider instances. Default = all providers;
    a provider is ACTIVE iff its key env var is set, else SKIPPED. An explicit
    --models list restricts the candidate set but the key-presence rule still
    decides active vs skipped."""
    if models_arg:
        names = [m.strip() for m in models_arg.split(",") if m.strip()]
    else:
        names = list(PROVIDERS)
    active, skipped = [], []
    for name in names:
        p = get_provider(name)
        (active if p.available() else skipped).append(p)
    return active, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="MODEL BATTLEGROUND: same diligence task across models, "
                    "anonymized senior-partner critic ranks them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--models", default=None,
                    help="comma list of competitors (default: all providers "
                         "with a key present): anthropic,openai,gemini")
    ap.add_argument("--judge", default="anthropic",
                    help="judge provider (default: anthropic)")
    ap.add_argument("--dataroom", default=DEFAULT_DATAROOM,
                    help=f"data room path (default: {DEFAULT_DATAROOM})")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"results JSON path (default: {DEFAULT_OUT})")
    ap.add_argument("--dryrun", action="store_true",
                    help="assemble prompts + print request shapes; NO network calls")
    ap.add_argument("--exclude-judge-entry", action="store_true",
                    help="when the judge provider is also competing, drop its own "
                         "output from the judged pool (reduces self-affinity bias). "
                         "Default off: judge all entries, blind.")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed for the anonymization shuffle (reproducible runs)")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)

    # Provider selection -----------------------------------------------------
    active, skipped = select_competitors(args.models)
    # For --dryrun we want to SHOW request shapes for every candidate provider,
    # active or not. For a live run we only call active ones.
    candidate_providers = active + skipped
    competitors_to_run = candidate_providers if args.dryrun else active
    judge = get_provider(args.judge)

    # Judge self-affinity: when the judge provider is also competing, a model may
    # favor its own output even when blind. Make the risk visible, and optionally
    # drop the judge's own entry from the judged pool.
    if any(p.name == judge.name for p in competitors_to_run):
        print(f"WARNING: judge '{judge.name}' is also a competitor; self-affinity "
              f"bias is possible even with blind judging. Use --exclude-judge-entry "
              f"to drop the judge's own output from the judged pool.", file=sys.stderr)
        if args.exclude_judge_entry:
            _drop = lambda lst: [p for p in lst if p.name != judge.name]
            active = _drop(active)
            skipped = _drop(skipped)
            candidate_providers = _drop(candidate_providers)
            competitors_to_run = _drop(competitors_to_run)
            print(f"NOTE: --exclude-judge-entry set; dropping '{judge.name}' from the "
                  f"competing pool.", file=sys.stderr)

    if not args.dryrun and not active:
        print("ERROR: no competitor has an API key set. Set ANTHROPIC_API_KEY / "
              "OPENAI_API_KEY / GEMINI_API_KEY, or use --dryrun.", file=sys.stderr)
        return 2
    if not args.dryrun and not judge.available():
        print(f"ERROR: judge '{judge.name}' has no key ({judge.key_env}). "
              f"Set it or pick another --judge.", file=sys.stderr)
        return 2

    # Ingest -----------------------------------------------------------------
    manifest, reconcile = ingest_dataroom(args.dataroom, dryrun=args.dryrun)

    # Stage 1 prompt ---------------------------------------------------------
    competitor_prompt = build_competitor_prompt(args.dataroom, manifest, reconcile)

    # Stage 1 run ------------------------------------------------------------
    entries = run_compete(competitors_to_run, competitor_prompt, dryrun=args.dryrun)

    # Stage 2 anonymize + judge prompt --------------------------------------
    # In --dryrun the outputs are placeholders, but anonymization/randomization
    # still runs so we can prove the A/B/C labelling and the private map.
    anon_entries, private_map = anonymize_entries(entries, rng)
    judge_prompt = build_judge_prompt(anon_entries)

    if args.dryrun:
        print_dryrun(active, skipped, competitor_prompt, anon_entries, private_map,
                     judge, judge_prompt, candidate_providers)
        # Still write a results skeleton so the orchestrator can inspect shapes.
        results = {
            "mode": "dryrun",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataroom": args.dataroom,
            "active_providers": [p.name for p in active],
            "skipped_providers": [p.name for p in skipped],
            "judge": judge.name,
            "competitor_prompt_chars": len(competitor_prompt),
            "judge_prompt_chars": len(judge_prompt),
            "anonymization": {e["label"]: "<model hidden from judge>" for e in anon_entries},
            "private_label_map": private_map,
            "no_network_call": True,
        }
        _write_results(args.out, results)
        return 0

    # Stage 2 run ------------------------------------------------------------
    # Enforce the blind-judging guarantee at runtime, not just in --dryrun: if a
    # competitor identity leaked into the judge prompt, refuse to call the judge.
    if _judge_prompt_leaks(judge_prompt, competitors_to_run):
        raise SystemExit("judge prompt leaked a competitor identity")
    judge_result = run_judge(judge, judge_prompt, dryrun=False)

    # Report -----------------------------------------------------------------
    ranking = print_ranking_table(judge_result, private_map)

    # Full results JSON (NEVER contains keys) --------------------------------
    results = {
        "mode": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataroom": args.dataroom,
        "judge": judge.name,
        "competitors": [
            {"model": e["model"], "model_id": e["model_id"], "ok": e["ok"],
             "parsed_ok": e["parsed_ok"], "output": e["output"]}
            for e in entries
        ],
        "anonymization_map": private_map,  # label -> model, for audit
        "judge_raw": judge_result.get("raw"),
        "judge_parsed": judge_result.get("parsed"),
        "ranking": ranking,
    }
    _write_results(args.out, results)
    print(f"\nFull results -> {args.out}")
    return 0


def _write_results(path: str, results: dict) -> None:
    """Write results JSON. Defense-in-depth: scrub any value that looks like it
    could be an API key before writing, so a key can never land on disk."""
    safe = _scrub_keys(results)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2)


_KEY_PREFIXES = ("sk-", "sk-ant-", "AIza", "AQ.")


def _scrub_keys(obj):
    """Recursively redact strings that match a known API-key shape. Belt-and-
    suspenders: the adapters never put keys in the results, but this guarantees
    it even if a future change tries to."""
    if isinstance(obj, dict):
        return {k: _scrub_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_keys(v) for v in obj]
    if isinstance(obj, str):
        for pfx in _KEY_PREFIXES:
            if obj.startswith(pfx) and len(obj) > 20:
                return "[REDACTED_POSSIBLE_KEY]"
    return obj


if __name__ == "__main__":
    raise SystemExit(main())
