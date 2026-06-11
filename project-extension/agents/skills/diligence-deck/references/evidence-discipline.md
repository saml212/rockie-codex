# Evidence Discipline

The load-bearing reference. Partner-grade DD is not better prose — it is
evidence that survives an adversarial read. This file defines the citation
contract, the confidence model, the retrieve-vs-synthesize split, and the
specific slop pathologies this skill is built to prevent.

---

## 1. Verbatim citation contract

Every key fact in `findings.json` carries four citation fields:

```
cite_file    — the source document filename (from the manifest) or "web"
cite_quote   — the EXACT string copied from the source. Verbatim. No edits.
cite_locator — p.N, line, "Section 4.3", or the URL
confidence   — high | medium | low (see §2)
```

**`cite_quote` is the discipline.** It is copied character-for-character
from the source, not reconstructed from memory and not paraphrased.

- Bad (paraphrase): `"fact": "Revenue is about 8 million"` with no quote.
  → A paraphrased number is a wrong number waiting to happen.
- Bad (bare cite): `"cite": "[CIM p2]"` with no quote.
  → The partner cannot verify it without re-reading the doc; assume invented.
- Good: `"cite_quote": "Total revenue: $8.2M"`, `cite_file: "cim-blurb.md"`,
  `cite_locator: "Revenue Summary"`, `confidence: "high"`.

Rule of thumb: **if you cannot quote it, you do not have it.** Convert it to
an open question instead of stating it.

A quote that does not appear in the cited source is a **hallucinated
citation** — the single worst failure mode in DD. It is worse than a missing
fact, because it actively misleads the IC and destroys the firm's
credibility. The A3 critic greps quotes back against the manifest.

---

## 2. Confidence model

Every fact is tagged so the reader knows how much weight it bears:

| confidence | Means | Example |
|---|---|---|
| `high` | Verbatim figure pulled straight from a data-room document. | "$8.2M total revenue" quoted from the financial summary. |
| `medium` | Inferred from data room + web — a computed ratio, an applied benchmark, a cross-referenced figure. | "Entry multiple is 1.4x the comparable median" (ask from CIM + comps from web). |
| `low` | Web only; no data-room corroboration. | "Sector grew ~18%/yr per a single analyst note." |

Distribution is itself a signal: a findings set that is all `low` means the
data room was never really used; a set with no `high` on the financials
means the most-scrutinized section is ungrounded. The critic looks at the
mix, not just individual facts.

---

## 3. Retrieve, THEN synthesize (never collapse them)

The research step has two sub-steps that must stay separate:

**[3a] RETRIEVE.** Read the manifest documents and run web searches. Pull
verbatim quotes into an evidence pool: `(quote, file, locator, candidate
section)`. The retriever does not write findings and does not editorialize —
it harvests evidence.

**[3b] SYNTHESIZE.** Write each section using ONLY the evidence pool. The
synthesizer may compute ratios and apply benchmarks (tagged `medium`), but
**may not introduce a fact or a number that is not already in the pool with
a quote.** If the pool lacks the fact, the section says "Not available —
open question," never a guess.

Why this matters: a single agent doing both at once, under pressure to fill
a section, will fabricate a plausible number to avoid an empty bullet.
Separating the steps removes the raw material for fabrication — the
synthesizer has nothing to invent *from*.

---

## 4. "Not available" discipline

When a fact is absent from both the data room and the web:

```
"fact": "Net revenue retention not disclosed",
"cite_file": "none",
"cite_quote": "Not available — open question for management",
"confidence": "low"
```

and add a specific question to `open_questions`: "Provide NRR by cohort
vintage for FY2023–FY2025." A gap is a finding (the question you would ask),
not an omission and not an invitation to hedge.

---

## 5. The partner-grade pitfalls — and how this skill blocks each

| Pitfall | What it looks like | How this skill prevents it |
|---|---|---|
| **Hallucinated citation** | A quote/number that isn't in the cited source. | `cite_quote` is verbatim and grep-checkable against the manifest; the A3 critic verifies quotes back to source. |
| **Vague / topic titles** | "Revenue analysis", "Strong market." | Declarative-action-title rule (`declarative-titles.md`); the `headline` field must be a claim with a number. |
| **Asymmetric depth** | 8 facts in §1, 1 in §7. | `min_facts: 3, max_facts: 8` per section; thin sections become open questions, not stubs. |
| **False precision** | "$4.7B TAM" from one un-methodologied VC map. | Market figures require methodology in `cite_quote` or drop to `low`/open question. |
| **Invented numbers** | A plausible figure with no source. | Retrieve/synthesize split (§3) + "no invented numbers" rule; synthesizer can't introduce un-pooled facts. |
| **Hedge-laundering** | "appears to be relatively high quality." | Hedges are banned; each converts to a cited fact or a specific open question (`open_questions`). |
| **Silent contradictions** | CIM and financials disagree; report picks one quietly. | `reconcile.py` surfaces deltas; every delta must land in a section's `reconcile_flags` and be addressed. |

---

## 6. Cross-document reconcile (step [3.5])

Before synthesis, `scripts/reconcile.py` scans the data room for the same
metric stated differently across documents (ARR, total revenue, headcount,
customer count, cap-table shares) and for expected-but-missing fields. Its
`reconcile.json` output is a first-class input:

- Every contradiction MUST be assigned to exactly one section's
  `reconcile_flags` and resolved (reconcile the figures, or raise an open
  question with the delta named).
- Every missing-but-expected field becomes an open question.
- A1 fails review if a reconcile delta is dropped silently — a partner who
  later finds the discrepancy themselves assumes the whole deck is sloppy.

The reconcile step is deliberately conservative: it biases toward missing a
borderline match over raising a false contradiction, because a false
contradiction wastes the synthesizer's effort and a real one it misses is
caught by the synthesizer reading the same documents. When in doubt, it
stays silent; the section author is the backstop.
