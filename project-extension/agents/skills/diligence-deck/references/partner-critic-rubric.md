# Senior-Partner Critic Rubric (for A3)

**Authored in A1 for A3 to consume. A1 does NOT run this loop.** This is the
target the findings are built to pass, written down so the eventual A3
adversarial critic has a fixed, mechanical bar rather than vibes. It adapts
the paper-skill adversarial gauntlet to acquisition DD.

The A3 critic runs as a **fresh, no-memory subagent** playing a skeptical
senior partner at a big-three firm. It reviews the draft deck (A2 output,
which renders A1's `findings.json`). Atlas iterates until the critic returns
**zero CRITICAL findings twice in a row** (pass-twice gate). Any iteration
resets the clean-pass counter to zero.

---

## Severity levels

- **CRITICAL** — blocks publish. A hallucinated citation, an invented
  number, a dropped reconcile contradiction, or a missing MECE section.
  Any single CRITICAL fails the round.
- **MAJOR** — must fix but does not by itself block if resolved before the
  next round: a vague (non-declarative) title, asymmetric section depth, a
  hedge, false precision.
- **MINOR** — polish: wording, ordering, formatting.

---

## Mechanical checks (the critic can run these against `findings.json`)

These are checkable without judgment — A3 should automate them first:

1. **Verbatim citation check.** For every `key_fact` with `cite_file` != web
   /none, the `cite_quote` string MUST appear in that document's text in the
   manifest. Any miss = CRITICAL (hallucinated citation).
2. **Quote presence.** Every `key_fact` has a non-empty `cite_quote`. Empty
   = CRITICAL.
3. **Section completeness.** Exactly 9 sections, ids 0–8, none missing. A
   missing section = CRITICAL (CE violation).
4. **Fact-count band.** Each section has `min_facts` (3) ≤ facts ≤
   `max_facts` (8), Section 0 carrying 3–5 headline facts. Out of band =
   MAJOR (asymmetric depth / padding / underwork).
5. **Reconcile coverage.** Every contradiction in `reconcile.json` appears
   in exactly one section's `reconcile_flags`. A dropped delta = CRITICAL.
   Every `missing_expected` field maps to an `open_questions` entry = MAJOR
   if absent.
6. **Recommendation placement.** `recommendation` is set on Section 0 and
   `null` on 1–8. Otherwise MAJOR.
7. **Confidence sanity.** No `high` confidence on a fact whose `cite_file`
   is `web`/`none`. Violation = MAJOR.

---

## Judgment checks (the critic reads and rules)

8. **Declarative titles.** Every `headline` and `fact` is a complete claim
   with a number, not a topic label. Topic labels = MAJOR each.
9. **No hedges.** Scan for "appears", "seems", "relatively", "we believe",
   "could potentially", "high quality" (un-quantified). Each = MAJOR.
10. **No false precision.** A specific market/TAM figure without
    methodology in the quote or a `low`/open-question fallback = MAJOR.
11. **MECE non-overlap.** A risk owned by two sections = MAJOR. (Customer
    concentration belongs in §4 only.)
12. **Story arc.** Section 0's recommendation follows from sections 1–8;
    the headline findings point to the sections that own the evidence. A
    Section-0 claim with no supporting section = CRITICAL.
13. **So-what test.** Each section earns its place — it changes the
    valuation, the structure, or the go/no-go. A section of true-but-inert
    facts = MAJOR.

---

## Output contract the critic returns

```json
{
  "round": 1,
  "verdict": "fail | pass",
  "criticals": [{ "section_id": 4, "check": "verbatim_citation", "detail": "..." }],
  "majors": [...],
  "minors": [...],
  "clean_passes_in_a_row": 0
}
```

`verdict: pass` requires zero criticals. Two consecutive `pass` verdicts
(`clean_passes_in_a_row >= 2`) unlock publish. Any non-pass round, or any
edit between rounds, resets `clean_passes_in_a_row` to 0.

---

## Why pass-twice

One clean pass can be luck or a critic that anchored on the first read.
A second fresh-context pass on the (possibly re-touched) draft catches
regressions introduced by the fixes themselves. This mirrors the
paper-skill gauntlet's twice-clean gate.
