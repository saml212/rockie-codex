---
name: diligence-deck
description: Produce structured acquisition due-diligence findings from deal inputs and a private data room. Triggers on "run diligence", "due diligence on", "diligence deck", "DD findings", "acquisition analysis", "data room", "ingest data room", "/diligence".
scope: community
contributor_name: Lena Ortiz
contributed_at: "2026-04-09"
---

# diligence-deck — acquisition due-diligence findings for Atlas

## Explicit goal

You are a senior consultant at a top-tier firm (EY-Parthenon / L.E.K. /
Bain DD practice) producing acquisition due-diligence **findings** for an
investment committee (IC). Your output must survive review by a skeptical
senior partner who flags vague claims, unsupported numbers, paraphrased
citations, hedges, and asymmetric depth as slop and sends it back. Every
finding maps to a verbatim quote from a named data-room document or a web
citation. **Nothing is invented. Nothing is paraphrased into a number.**

This skill covers **A1 + A2 + A3 + A5**: `(A5 fetch) -> intake -> ingest ->
reconcile -> research -> structured findings -> deck -> adversarial critic loop
-> (A5 emit)`. A1 produces `findings.json` + `findings.md`; A2 ("Deck
rendering") renders that typed contract into a partner-grade slide deck; A3
("Adversarial critic loop") runs a fresh senior-partner critic until the run
passes twice; **A5 ("Connectors")** wires the running Rockie lab's uploaded
sources in as the data room and ships the deck back out as a downloadable
artifact. One later slice extends it — **do not attempt its work here**:

- **A1b** — swap the built-in research step for a bake-off-selected
  deep-research engine.

## Pipeline

```
[1] INTAKE     — deal inputs (company, sector, ask price, thesis, prior
                 knowledge). Save to deal_inputs.md.
[2] INGEST     — scripts/ingest_dataroom.sh <dataroom> -> manifest.json
[3.5] RECONCILE — scripts/reconcile.py <manifest> -> reconcile.json.
                 Cross-document contradiction scan + missing-but-expected
                 fields. Runs BEFORE synthesis. Its deltas are first-class
                 inputs the findings MUST address.
[3] RESEARCH   — TWO separated sub-steps, never collapsed:
                 [3a] RETRIEVE: read manifest docs + web search; collect
                      verbatim evidence (quote + source) into an evidence
                      pool. The retriever does NOT write findings.
                 [3b] SYNTHESIZE: write each section ONLY from the evidence
                      pool. The synthesizer may not introduce a fact or a
                      number that is not already in the pool with a quote.
[4] FINDINGS   — emit findings.json (typed contract for A2) + findings.md.
```

**Why [3a] and [3b] are separate:** a single agent that retrieves and
writes at once will, under pressure to fill a section, invent a plausible
number. Splitting them means the synthesizer has nothing to invent from —
if the evidence pool lacks a fact, the section says "Not available", not a
guess. See `references/evidence-discipline.md`.

## Findings structure — Section 0 first (pyramid principle)

Findings run **0–8**. Section 0 is the verdict the IC reads first; 1–8 are
the supporting analysis. This is pyramid-first: answer, then evidence.

| # | Section | Core questions |
|---|---------|---------------|
| 0 | **Executive summary & recommendation** | The verdict (proceed / proceed-with-conditions / pass) + 3–5 headline findings + the deal-breakers. The IC reads ONLY this if pressed for time. |
| 1 | Business overview | What does the company actually do? Revenue model, unit economics, moat, key dependencies. |
| 2 | Market & competitive position | TAM/SAM sizing (with methodology), share, growth vector, substitution risk. |
| 3 | Financial quality | Recurring vs one-time, margin bridge, working capital, EBITDA adjustments, capex intensity. |
| 4 | Customer & revenue concentration | Top-1/3/10 revenue %, churn/NRR, contract terms, renewal visibility. |
| 5 | Legal & contractual risk | IP ownership, litigation, key-person & change-of-control clauses, regulatory exposure. |
| 6 | Management & team | Track record, retention risk, org gaps, incentive alignment post-close. |
| 7 | Integration & synergies | Day-1 risks, systems overlap, culture delta, realistic synergy timeline. |
| 8 | Valuation & deal structure | Entry multiple vs comparables, EBITDA/ARR basis, earn-out & rep-and-warranty exposure. |

Section 0 is **derived last** (it summarizes 1–8) but **presented first**.
Its headline findings are pointers to the sections that own the evidence.

## DECLARATIVE ACTION TITLES (the #1 partner-vs-associate marker)

Every section headline and every finding leads with a **declarative
action title** — a complete claim with the number in it, readable in
isolation. This is non-negotiable; vague titles are the single clearest
"associate, not partner" tell.

- Bad (topic label): "Revenue analysis"
- Good (action title): "Revenue is 73% recurring with <5% logo churn"
- Bad: "Customer concentration"
- Good: "Top-3 customers are 41% of ARR; two are month-to-month"
- Bad: "Management overview"
- Good: "CEO has two prior exits; no CFO in seat, a Day-1 gap"

Rule + more examples: `references/declarative-titles.md`.

## findings.json contract (typed interface for A2)

`findings.json` is an array of **9 section objects** (sections 0–8). Schema:

```json
{
  "section_id": 0,
  "section": "Executive summary & recommendation",
  "headline": "DECLARATIVE action title with the number in it",
  "recommendation": "proceed | proceed_with_conditions | pass | null",
  "key_facts": [
    {
      "fact": "declarative claim",
      "cite_file": "financial-summary.txt",
      "cite_quote": "EXACT verbatim string copied from the source",
      "cite_locator": "p.2 | line | section 4.3 | url",
      "confidence": "high | medium | low"
    }
  ],
  "risks": [{ "risk": "...", "rating": "H | M | L" }],
  "open_questions": ["specific question for management"],
  "reconcile_flags": ["cross-doc delta this section must address, if any"]
}
```

Field rules (A3 checks these mechanically — see the critic rubric):

- **`cite_quote` is REQUIRED and VERBATIM.** Copy the exact string from
  the source document — never a paraphrase, never a reconstruction. A
  paraphrased number becomes a wrong number. If you cannot quote it, you
  do not have it.
- **`confidence` enum:**
  - `high` — verbatim figure pulled directly from a data-room document.
  - `medium` — inferred from data room **plus** web (e.g. a ratio you
    computed, a benchmark you applied).
  - `low` — web only; no data-room corroboration.
- **`min_facts: 3, max_facts: 8` per section.** Fewer than 3 = the section
  is underworked (a gap is an open question, not an empty section). More
  than 8 = padding. **Asymmetric section depth is a slop signal** the
  partner reads instantly: do not put 8 facts in section 1 and 1 in
  section 7. (Section 0 carries 3–5 headline facts by design.)
- **`recommendation`** is set only on Section 0; `null` elsewhere.
- **`reconcile_flags`** carries any `reconcile.json` contradiction that
  this section owns. Every contradiction in `reconcile.json` MUST appear
  in exactly one section's `reconcile_flags` and be resolved or raised as
  an open question. A1 fails if a reconcile delta is silently dropped.

A2 consumes this typed contract — do not rename fields without updating it
(`scripts/render_deck.py`).

## Deck rendering (A2)

A2 maps `findings.json` -> a partner-grade slide deck. It does **not**
re-run A1 (intake/ingest/reconcile/research/findings) and does **not** run
the A3 critic loop. The renderer is `scripts/render_deck.py`; it is
stdlib-only except for an optional local `python-pptx` fallback.

**Slide structure (pyramid principle):**

1. **Section 0 FIRST** as the title/exec slide. Its declarative `headline`
   is the slide title; the `recommendation` (proceed / proceed-with-
   conditions / pass) is on the subtitle; the 3–5 headline `key_facts` are
   the exec bullets.
2. **One slide per section 1–8**, in order. The section's declarative
   `headline` is the slide title; each `key_fact` becomes a bullet that
   **preserves its citation** as `[cite_file | "cite_quote"] (confidence)`.
   Any `reconcile_flags` are appended inline as `RECONCILE: …` so a cross-
   doc delta is never lost.
3. **Consolidated Risk register** — all `risks` pooled and sorted H→M→L,
   each tagged `[H|M|L]` with its owning section.
4. **Open questions for management** — all `open_questions` pooled.

Total is 11 slides for a complete 9-section findings set (9 sections + risk
+ open-questions); richer decks land in the **18–24** target band and the
renderer hard-fails above the `powerpoint` skill's **30-slide cap**.

**Evidence discipline in the deck:** no bullet is emitted without its
citation. A fact whose `cite_quote` is empty renders an explicit
`[no citation]` marker (visible to the partner, never silently dropped);
"Not available — open question for management" facts keep that exact string.

**Integration with the `powerpoint` skill (canonical path):**
`render_deck.py --emit payload` produces the `powerpoint` skill request
payload with exactly the current top-level Request Shape:
`prompt`, `slide_count`, `title`, `findings[]`, `attachments[]`,
`template_path`, and `theme`. The per-slide structure rides inside
`findings[]`; `template_path: null` selects the bundled Pebble ML template.
The runtime hands this payload to the `powerpoint` skill, which renders with
`rockie-pptagent-render` and emits the `.pptx` through
`emit_artifact kind=slides`. A2 does not reimplement a deck engine — it
produces that engine's input.

**Local fallback (no runtime tool needed):** `--emit pptx` renders a real
`.pptx` with `python-pptx` if importable (no network, no GPU, no
DeepPresenter); `--emit json` dumps the deck spec when `python-pptx` is
absent. `--emit auto` (default) picks `pptx` if available, else `json`.
These prove the findings->slides mapping end-to-end and yield an
inspectable artifact; the canonical `.pptx` ships via the runtime
`powerpoint` tool.

```
scripts/render_deck.py findings.json --emit payload --out deck_payload.json
scripts/render_deck.py findings.json --emit auto    --out deck.pptx
```

`examples/sample-findings.json` is a complete 9-section fixture (verbatim
citations copied from `examples/sample-dataroom/`) for proving the renderer.

## Connectors (A5): lab sources in, deck out

A5 wires this skill to the **Rockie lab it runs inside**: the lab's uploaded
**sources are the data room** (IN), and the rendered **deck ships back as a
downloadable artifact** (OUT). Nothing else in the pipeline changes — A5 is
pure plumbing at the two ends.

```
[A5 IN]  lab sources --(agent-tools API)--> dataroom/  --> [2] INGEST --> [3.5] RECONCILE
                                                              |
                              [3] RESEARCH -> [4] FINDINGS  <-+
                                                              |
[A2] render_deck --emit payload -> rockie-pptagent-render -> deck.pptx
                                                              |
[A5 OUT] emit_artifact(kind=slides) <-- base64(deck.pptx) <--+   (downloadable)
```

### Data room IN — `scripts/fetch_dataroom.sh`

A tenant's uploaded sources are **not on the runtime's disk**; the diligence
agent reaches them through the authenticated **agent-tools HTTP API** already
available in the runtime. `fetch_dataroom.sh`:

1. Calls `notebook_read` (empty body → defaults `notebook_id` to the runtime's
   `$PLATFORM_LAB_ID`) to **list the lab's sources** `[{id, title}]`.
2. Calls `source_read {source_id}` per source to pull each one's `full_text`,
   and writes one `<slug>.md` per source into a local `dataroom/` folder
   (truncation flagged inline; `source_read` caps `full_text` at 32 000 chars).
3. Hands `dataroom/` straight to `ingest_dataroom.sh` → `manifest.json` →
   `reconcile.py`, exactly as a hand-supplied folder would be.

Auth/identity headers come from the runtime env: `ROCKIELAB_API_BASE`,
`ROCKIELAB_TENANT_TOKEN`, `ROCKIELAB_TENANT_ID`, and
`ROCKIELAB_API_PASSWORD`. `ROCKIELAB_OPERATOR_TENANT_ID` is forwarded when
present.

**No-sources case:** if the lab has zero sources, the script warns, writes an
empty `dataroom/`, and exits 0 — `ingest_dataroom.sh` then emits an explicit
empty manifest. INTAKE must ask the user to upload sources before diligence
can proceed; never invent a data room.

```
scripts/fetch_dataroom.sh dataroom            # uses $PLATFORM_LAB_ID
scripts/fetch_dataroom.sh dataroom <lab_id>   # explicit lab
scripts/ingest_dataroom.sh dataroom manifest.json
```

**Offline proof:** set `DATAROOM_FIXTURE=<dir>` (holding `notebook_read.json`
and `source_read.<source_id>.json` captures) to run the assembly logic without
a live API — the HTTP path and the fixture path share one code path.
`examples/dataroom-fixture/` is a captured fixture of the sample data room.

### Deck OUT — `emit_artifact(kind=slides)`

The deck leaves the runtime through the agent-tools **`emit_artifact`** tool.
For office artifacts the tool requires base64 bytes
(`content_encoding: "base64"`), a `.pptx` filename, and
`{kind, content, title, notebook_id}`. The default `destinations`
`["ui", "chat"]` make the deck downloadable in the lab UI and drop it into the
chat thread. The full OUT sequence:

```
# 1. A2 produces the powerpoint-skill Request Shape (canonical):
scripts/render_deck.py findings.json --emit payload --out request.json

# 2. The runtime renders the real .pptx (powerpoint skill / pptagent):
rockie-pptagent-render --input request.json --output deck.pptx

# 3. A5 builds the EXACT emit_artifact arguments body from that .pptx:
scripts/render_deck.py findings.json --emit emit-args \
    --pptx-in deck.pptx --out emit_args.json     # notebook_id <- $PLATFORM_LAB_ID

# 4. The agent posts emit_args.json["arguments"] to the emit_artifact tool:
#    POST $ROCKIELAB_API_BASE/api/agent-tools/emit_artifact
#         {"arguments": { ... }}   (or call the emit_artifact MCP tool)
```

The exact `emit_artifact` arguments body (step 3 output) is:

```json
{
  "kind": "slides",
  "content": "<base64 of deck.pptx bytes>",
  "content_encoding": "base64",
  "filename": "<deal>-diligence-deck.pptx",
  "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "title": "<deal> — Acquisition DD findings",
  "notebook_id": "<$PLATFORM_LAB_ID>",
  "destinations": ["ui", "chat"],
  "metadata": {"skill": "diligence-deck", "slide_count": <n>}
}
```

The emitted artifact is then listable/streamable at `/api/artifacts/{id}/file`.
Confidentiality (Rule 9) still holds: only the rendered deck leaves —
`manifest.json` / `reconcile.json` / the fetched `dataroom/` stay internal.

## Adversarial critic loop (A3)

A3 is the conceptual heart: every Atlas run must survive a fresh, no-memory
**senior-partner critic** before its deck reaches the IC. This is the diligence
agent's **OWN internal adversarial flow** — it hardens ONE diligence run. It is
**NOT** the meta agent-builder's publish gate (slice **B1**), which decides
whether the *skill itself* is fit to ship. Same twice-clean shape, different
scope; A3 does not build B1. The loop reuses the `paper` skill's gauntlet /
detector-gate pattern (`../paper/references/adversarial-gauntlet.md`,
`../paper/references/detector-gate.md`).

**The critic.** A fresh "senior partner at a big-three firm" reads **only**
`references/partner-critic-rubric.md` + the draft `findings.json` (plus
`reconcile.json`/`manifest.json` to verify citations and reconcile coverage,
and optionally the rendered deck) — **never the research history or the
evidence pool**. A critic who saw how a number was derived rationalizes a weak
citation; the IC only gets the findings and their stated citations, so the
critic judges exactly that. The critic runs the rubric's mechanical checks
first (verbatim citation, quote presence, section completeness, fact-count
band, reconcile coverage, recommendation placement, confidence sanity) then the
judgment checks (declarative titles, hedges, false precision, MECE overlap,
story arc, so-what). Prompt: `prompts/partner-critic.md`.

**The verdict schema** (one round) — the contract `critic_loop.py` validates:

```json
{
  "pass": false,
  "violations": [
    { "check": "verbatim_citation", "severity": "critical",
      "where": "section 4, key_fact[1]",
      "fix": "concrete bounded action — re-quote / re-balance / rewrite" }
  ]
}
```

`pass` is **re-derived, not trusted**: a verdict passes **iff it carries zero
`critical` violations**. `critic_loop.py` recomputes this from the violation
list, so a critic that says `pass:true` while listing a CRITICAL cannot slip
through. MAJOR/MINOR may appear on a passing round as polish; they do not flip
`pass`, but the builder clears them before the next round.

**The loop** (`scripts/critic_loop.py`, mirroring the paper detector-gate):

1. Run the critic on the **current** draft; increment the round counter.
2. **FAIL** (any CRITICAL) → **reset the consecutive-pass counter to 0**, the
   builder applies the cited `fix` actions to `findings.json`, re-run.
3. **PASS** → increment the counter. **One PASS does NOT unlock** — only when
   the counter reaches **2** (`REQUIRED_CONSECUTIVE_PASSES`) does the loop
   converge. The second pass is a fresh read of the (re-touched) draft and
   catches regressions the fixes themselves introduced.
4. **Cap.** At most **`MAX_ROUNDS = 6`** (mirrors the paper gauntlet/detector
   cap). Six rounds without two *consecutive* clean rounds → the loop **stops
   and FAILS LOUDLY** (`converged=False`, reason names the cap). It never runs a
   seventh round and never silently accepts; the run hands off for human review.

**Why pass-twice + reset-on-fail:** one clean pass can be luck or a critic that
anchored on its first read; a second fresh pass on the possibly-re-touched
draft is an independent confirmation. Any failed round — or any edit between
rounds — resets the counter, because the edit could have broken something the
prior pass approved.

### Two execution modes (both supported — load-bearing)

The diligence agent may itself run as a subagent that **cannot spawn nested
subagents**, so the loop must work both ways. The termination rules (CRITICAL
blocks PASS, two consecutive clean rounds, `MAX_ROUNDS` cap) are **identical**
across modes; only the isolation mechanism changes.

- **`subagent` (preferred).** Each round dispatches a **fresh no-memory
  subagent** (`make_subagent_critic`) handed only `prompts/partner-critic.md` +
  `references/partner-critic-rubric.md` + the draft (+ `reconcile.json` /
  `manifest.json`). A new context per round cannot be coached and cannot
  remember what it waved through last round — freshness is process-enforced.
- **`single` (degraded fallback).** When no subagent dispatch is available, run
  each round as a separate fresh **reasoning pass in-process**
  (`make_single_process_critic`) with a **hard context reset** between rounds:
  the pass reads ONLY the draft + prompt + rubric and carries **no memory** of
  any prior verdict. That self-imposed amnesia substitutes for an empty
  context. It is **weaker** than the subagent path — a single context can leak a
  tell it already waved through — so verdicts are labelled **`single-process`**
  and read accordingly; do not claim subagent-grade independence. This is the
  load-bearing path when the agent has no nested-subagent capability.

The live LLM critic lands at **A6**; `critic_loop.py` injects the critic as a
callable so the **loop logic is provable deterministically** with mocked
verdicts. `critic_loop.py --selftest` proves: (a) a FAIL resets the counter,
(b) a single PASS does not unlock, (c) the `MAX_ROUNDS` cap fails loudly, and
(d) a CRITICAL blocks PASS even when `pass:true` is claimed.

## Routing

| Command | Intent | What it does |
|---------|--------|-------------|
| `/diligence intake` | "start diligence on X" | Collect deal inputs; save `deal_inputs.md` |
| `/diligence fetch` | "use my lab's sources", "pull the data room" | A5 IN: `scripts/fetch_dataroom.sh dataroom` assembles the lab's uploaded sources (via `notebook_read`+`source_read`) into `dataroom/` |
| `/diligence ingest` | "ingest the data room" | Run `scripts/ingest_dataroom.sh <folder>`; emit `manifest.json` |
| `/diligence reconcile` | "check for contradictions" | Run `scripts/reconcile.py <manifest>`; emit `reconcile.json` |
| `/diligence research` | "run the research" | [3a] retrieve verbatim evidence; [3b] synthesize section drafts |
| `/diligence findings` | "compile findings" | Assemble sections 0–8 into `findings.md` + `findings.json` |
| `/diligence deck` | "render the deck", "make the slides" | A2: `scripts/render_deck.py findings.json` -> `powerpoint`-skill payload (canonical) or local `.pptx`/spec fallback |
| `/diligence critic` | "run the partner critic", "harden the findings" | A3: `scripts/critic_loop.py` runs the fresh senior-partner critic (`prompts/partner-critic.md`) until two consecutive zero-CRITICAL passes or the `MAX_ROUNDS` cap |
| `/diligence emit` | "send me the deck", "ship the deck" | A5 OUT: `render_deck.py --emit emit-args --pptx-in deck.pptx` then post to `emit_artifact` (kind=slides) |
| `/diligence` | full run | (A5) fetch -> ingest -> reconcile -> research -> findings -> deck -> critic loop (A3) -> emit |

## Rules

1. **Verbatim citations only.** Every key fact carries `cite_file` +
   `cite_quote` (exact string) + `cite_locator`. A bare `[CIM p2]` with no
   quote is slop. A quote that does not appear in the source is a
   hallucinated citation — the worst failure mode; the partner will spot it.
2. **No invented numbers.** If a fact is absent from both data room and
   web, write `"Not available — open question for management"` and add the
   question to `open_questions`. Never estimate.
3. **No hedge-laundering.** Banned: "appears to be", "seems", "relatively
   high quality", "we believe ... could potentially". Convert every hedge
   into either a cited fact or a specific open question. A hedge is an
   un-asked question in disguise.
4. **Reconcile first, synthesize second.** Read `reconcile.json` before
   writing any section. Each contradiction must land in exactly one
   section's `reconcile_flags` and be addressed.
5. **Retrieve before you synthesize.** The synthesizer writes only from the
   evidence pool. It may not introduce a fact the retriever did not capture
   with a quote.
6. **MECE discipline.** Sections do not overlap. Customer-concentration
   risk lives in section 4 only, not scattered across 2 and 5.
7. **Declarative titles everywhere.** Section headlines and findings are
   complete claims with numbers, not topic labels.
8. **Balance depth.** 3–8 facts per section. A thin section is an open
   question, not a short section.
9. **Data room is confidential.** Never emit raw data-room content to
   non-findings artifacts. `manifest.json` / `reconcile.json` are internal
   scaffolding; do not ship them as deliverables.
10. **A1b stub.** A1's research uses built-in web search + direct document
    reading. When A1b lands, replace ONLY step [3a]/[3b]; intake, ingest,
    reconcile, and findings are unchanged.

## File map

- `references/dd-methodology.md` — MECE, pyramid principle, DD anatomy.
- `references/declarative-titles.md` — the action-title rule + worked
  before/after examples.
- `references/evidence-discipline.md` — verbatim-citation contract,
  confidence enum, retrieve-vs-synthesize separation, the partner-grade
  pitfalls (hallucinated citations, vague titles, asymmetric depth, false
  precision, invented numbers, hedge-laundering) and how this skill
  prevents each.
- `references/financial-quality.md` — revenue-quality tests, EBITDA bridge,
  working-capital checklist.
- `references/legal-checklist.md` — contractual-risk categories for M&A.
- `references/partner-critic-rubric.md` — the A3 senior-partner critic's
  scoring rubric (the fixed mechanical bar the A3 loop grades against).
- `prompts/partner-critic.md` — the A3 critic's prompt: a fresh, no-research-
  history senior partner that loads the rubric + draft `findings.json` and
  returns the structured verdict (`pass` + `violations[]`).
- `scripts/fetch_dataroom.sh` — A5 IN connector: assemble the running lab's
  uploaded sources into a local `dataroom/` via the agent-tools API
  (`notebook_read` + `source_read`). `DATAROOM_FIXTURE=<dir>` runs offline.
- `scripts/ingest_dataroom.sh` — normalize PDF/DOCX/TXT/MD into
  `manifest.json`.
- `scripts/reconcile.py` — cross-document contradiction + missing-field
  scan over the manifest -> `reconcile.json`.
- `scripts/render_deck.py` — A2 renderer: `findings.json` -> `powerpoint`-
  skill payload (canonical) or a local `.pptx` / deck-spec JSON fallback.
  `--emit emit-args --pptx-in deck.pptx` (A5 OUT) builds the exact
  `emit_artifact(kind=slides)` arguments body that ships the deck to the user.
- `scripts/critic_loop.py` — A3 orchestrator: the senior-partner critic loop
  (rounds, consecutive-pass count, reset-on-fail, `MAX_ROUNDS` cap, both
  execution modes). `--selftest` proves the loop logic with mocked verdicts.
- `examples/sample-findings.json` — complete 9-section findings fixture
  (verbatim citations from `sample-dataroom/`) for proving the renderer.
- `examples/dataroom-fixture/` — captured `notebook_read` + `source_read`
  agent-tools responses (mirroring `sample-dataroom/`) for proving
  `fetch_dataroom.sh` offline via `DATAROOM_FIXTURE`.
- `examples/sample-dataroom/` — synthetic fixture proving the pipeline.
  Contains deliberate cross-document inconsistencies (ARR and headcount
  differ between the CIM and the financial summary) so `reconcile.py` has a
  real delta to catch. A NESTED doc (`financials/Q-summary.txt`) states ARR
  differently again, proving reconcile reads docs in subfolders (resolved by
  `relpath`), not just root-level files.
