# Declarative Action Titles

The single clearest "partner, not associate" marker in a DD deck. Every
section headline and every finding must be a **declarative action title**:
a complete claim, with the number in it, that a partner can read in
isolation and act on. Topic labels ("Revenue analysis") are the associate
tell — they describe what the slide is *about* instead of stating what is
*true*.

This is enforced in `findings.json` via the `headline` field and the
`fact` field of each key fact, and it is the first thing the A3 critic
checks (see `partner-critic-rubric.md`).

---

## The test

Read the title with the body covered. If you cannot tell whether to drill
down or move on, it is a topic label, not an action title. Rewrite it.

A good action title:
1. Is a full sentence (subject + verb + claim).
2. Contains the load-bearing number.
3. States the so-what, not the topic.
4. Survives in isolation (works on a one-line IC summary).

---

## Before / after

| Topic label (bad) | Action title (good) |
|---|---|
| Revenue analysis | Revenue is 73% recurring with <5% logo churn |
| Customer concentration | Top-3 customers are 41% of ARR; two are month-to-month |
| Market overview | $4.1B SAM growing 18%/yr; incumbent holds 60% share |
| Management team | CEO has two prior exits; no CFO in seat — a Day-1 gap |
| Margin profile | 76% gross margin but 9% EBITDA on heavy S&M (35% of rev) |
| Legal review | Top customer can terminate on change of control (30-day window) |
| Integration | Two ERPs to consolidate; realistic synergy capture is 18 months |
| Valuation | Ask of 5x ARR is 1.4x the median of 6 SaaS comparables |
| EBITDA quality | Adj. EBITDA leans on a $320K founder-comp addback (44% of the bridge) |
| Working capital | DSO of 41 days is in range; deferred revenue inflates reported WC |

---

## Section-0 headlines are verdict-grade

Section 0 (executive summary) headlines carry the recommendation logic:

- "Proceed with conditions: durable recurring revenue, but renewal cliff
  in 4 contracts (26% of ARR) must be closed pre-LOI."
- "Pass: revenue quality is sound, but unassigned founder IP and an
  uncapped litigation exposure make the rep surface uninsurable at this price."

Each Section-0 headline finding points to the section (1–8) that owns the
underlying evidence. Section 0 asserts; sections 1–8 prove.

---

## Common failure: the number-less title

"Strong recurring revenue" is still a label — it has an adjective where the
number should be. Replace the adjective with the figure: "87% of revenue is
recurring subscription." If you do not have the number, the title becomes
an open question ("Recurring-revenue split not disclosed — request from
management"), never a vague adjective.
