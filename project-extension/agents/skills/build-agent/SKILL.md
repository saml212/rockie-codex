---
name: build-agent
description: Lab composer command scaffold for `/build-agent <name>`. In Agent Builder A1 this only creates a lab-scoped draft agent record and opens that lab's agent detail route for editing. It does not build, run, evaluate, observe, or deploy agents yet.
---

# build-agent - lab-scoped draft agent bootstrap

Use this skill when the user enters `/build-agent <name>` in a lab chat
composer and intends to start defining a new agent inside the current lab.

## A1 behavior

The A1 command is intentionally small:

1. Read the agent name from the command text.
2. Create a draft agent record scoped to the current lab.
3. Open the lab-scoped agent detail route:

```text
/labs/<lab_id>/agents/<agent_id>
```

The created record is a placeholder for future builder steps. Treat its config
as an editable draft, not as a runnable or published agent.

## Required scope

- The command must run from an active lab context.
- The created agent must carry the current lab id.
- Lists and detail views must stay under that lab.
- A user without access to the lab must not be able to see or create records for
  that lab.

## Non-goals in A1

- No actual agent build pipeline.
- No run, evaluation, gauntlet, battleground, or publish workflow.
- No observability integration.
- No deployment orchestration.
- No global agents surface; agent records are reached from the owning lab.

## Operator response

After the draft is created, keep the response minimal: name the draft and open
the lab detail route. Do not imply the agent has been built, tested, deployed, or
connected to runtime infrastructure.
