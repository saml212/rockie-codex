# Due-Diligence Methodology Reference

Context for Atlas (diligence-deck skill). This is prompt-usable methodology,
not a textbook. Keep it close at hand during the research step.

---

## MECE — what it actually means in DD

MECE = Mutually Exclusive, Collectively Exhaustive. In diligence, violations
cost credibility instantly:

- **Overlap violation** (not ME): "customer concentration" appears in both
  the market section and the financial section. Pick one owner.
- **Gap violation** (not CE): you deliver 7 of 8 sections but skip legal
  because "the data room was thin." Thin data room = open question, not
  omission. Write "Not available — open question for management."

Practical test: can you draw a box around each section with zero overlap?
Does every material risk have exactly one section home? If no, fix it.

---

## Pyramid principle in diligence findings

The pyramid principle (Barbara Minto) in practice. This is why findings
run **0–8 with Section 0 (Executive summary & recommendation) first**:
the IC reads the answer (verdict + 3–5 headline findings), then drills into
the supporting sections 1–8. Answer first, evidence second.

1. **Headline first — as a declarative action title.** Each section opens
   with one sentence the partner can read in isolation, with the number in
   it, and know whether to drill down. See `declarative-titles.md`.
   Bad: "Customers are an important aspect of the business."
   Good: "Top-3 customers represent 68% of ARR; two contracts are
   month-to-month with no renewal clause."

2. **Key facts support the headline.** Every bullet below the headline
   is evidence for it, not tangential color.

3. **Risks are separate.** Do not embed risk language in key facts.
   A fact is a fact; a risk is the so-what. Keep them in separate lists.

4. **One open question per fact gap.** If you cannot find the fact,
   the question you would ask management IS the finding.

---

## Acquisition DD deck anatomy

A standard big-three firm DD deck has this spine (not all clients see
all sections; findings.md must cover all so A2 can prune). It maps 1:1 to
findings sections 0–8:

```
Cover + deal snapshot (1 slide)
§0 Executive summary & recommendation — verdict + 3-5 headlines (1 slide)
§1 Business overview (2-3 slides)
§2 Market and competitive position (2-3 slides)
§3 Financial quality (3-5 slides — most scrutinized)
§4 Customer and revenue concentration (2 slides)
§5 Legal and contractual risk (1-2 slides)
§6 Management and team (1-2 slides)
§7 Integration and synergies (1-2 slides)
§8 Valuation and deal structure (1-2 slides)
Open questions / next steps (1 slide)
```

Total: 18-24 slides. A2 maps findings sections to this spine. Note the deck
LEADS with §0 (the verdict) even though §0 is derived last — pyramid-first.

---

## What partners flag as slop (verbatim feedback patterns)

These are real failure modes. Atlas must not produce them.

**Vagueness slop:**
- "The market is large and growing." (Size it or delete it.)
- "Management has significant experience." (Name the exits/tenures.)
- "Revenue is high quality." (Define: % recurring, churn, NPS, CAC.)

**Hedge slop:**
- "It appears that..." / "It seems like..." (You are not hedging — you
  are producing findings. State or question, never hedge.)
- "We believe the market could potentially..." (This is not a law brief.
  State the number or say it is unavailable.)

**Non-MECE slop:**
- Customer concentration risk mentioned in three sections. One section owns it.
- "Financial risk" used as a catch-all for legal exposure, churn, and
  working capital simultaneously.

**Missing-citation slop:**
- A number with no source. Partners assume you invented it.
- "According to industry sources..." without naming the source.

**Symmetry slop:**
- 5 bullets in section 1, 1 bullet in section 7. Either section 7 is
  underworked or section 1 is padded. Balance the effort.

**False precision slop:**
- "TAM of $4.7B" when the number came from one VC-funded market map with
  no methodology cited. Write the source's methodology or flag it.

---

## Research sequencing — retrieve, then synthesize

The research step is two separated sub-steps (see `evidence-discipline.md`
§3). Run [3.5] reconcile FIRST so you know the contradictions before you
write.

**[3a] RETRIEVE (build the evidence pool — no findings yet):**
1. Read `reconcile.json`. Note every contradiction and missing field.
2. For each document in the manifest, read the FULL text and copy verbatim
   quotes that bear on any section: `(quote, filename, locator, candidate
   section)`. Copy the EXACT string — never paraphrase a number.
3. For facts no document supplies, web-search with specific operators
   (site:sec.gov, site:crunchbase.com, company + "annual report"). Capture
   the URL + the verbatim quote. Tag web-only facts `low`.

**[3b] SYNTHESIZE (write sections 1–8, then derive 0):**
4. Write each section USING ONLY the evidence pool. Compute ratios / apply
   benchmarks (tag `medium`), but introduce no fact absent from the pool.
5. Assign each reconcile contradiction to exactly one section's
   `reconcile_flags`; resolve it or raise it as an open question.
6. Where the pool is silent, write "Not available — open question for
   management" and add the specific question. Never hedge, never estimate.
7. Draft each section: declarative Headline, Key facts (verbatim-cited,
   confidence-tagged), Risks (H/M/L), Open questions, reconcile_flags.
8. Derive Section 0 LAST from sections 1–8: the verdict + 3–5 headline
   findings that point to the sections owning the evidence.

Do not write a section until every key fact in it has a `cite_quote`.
Keep each section in the 3–8 fact band; a thin section is an open question,
not a stub.

---

## Risk rating rubric

Used in the Risks list of each section:

| Rating | Definition |
|--------|-----------|
| H | Could materially impair deal value or block close. Needs resolution before LOI or as closing condition. |
| M | Real risk but manageable with contractual protections, escrow, or earn-out structure. |
| L | Noted for completeness. Would not change valuation or structure recommendation. |

---

## Key references (external)

- McKinsey M&A practice notes on financial due diligence quality.
- Rosenbaum & Pearl, "Investment Banking" ch. 6 (LBO/M&A diligence).
- Minto, "The Pyramid Principle" (SCQA structure for findings).
- AICPA "Due Diligence in M&A" guidance (SAS No. 73 equivalent for
  financial quality).

These are methodology anchors, not citation sources for deal facts.
