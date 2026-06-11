---
name: self-critic
description: This agent's own internal adversarial critic loop. Hardens every deliverable with a fresh no-memory domain critic until it passes twice consecutively with zero CRITICALs before return.
---

# self-critic — the agent's internal adversarial flow

This skill is wired in by default (the agent-builder is biased to emit
self-adversarial agents). Before this agent returns ANY deliverable for the
goal below, harden it here.

## Goal under guard

> Turn a list of merged pull requests into a clear, user-facing release-notes section grouped by Added / Changed / Fixed, with one plain-language line per change.

## How to run

1. Produce a draft deliverable.
2. Run `critic_loop.py` with the domain critic (`domain-critic.md`) as a fresh,
   no-memory reviewer of the draft.
3. On any CRITICAL: apply the cited fixes, re-run. The clean-pass counter resets
   to zero on any failed round.
4. Converge only on TWO consecutive zero-CRITICAL passes (cap: MAX_ROUNDS=6).
   Never return a deliverable that has not converged.

Prefer the subagent mode (a fresh subagent per round). When the agent cannot
spawn subagents, use the single-process fallback with a hard context reset
between rounds — weaker isolation, same termination rules.

Prove the loop logic any time with `critic_loop.py --selftest`.
