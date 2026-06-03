---
name: gpu-custom
description: Runtime skill for users in ROCKIE_GPU_MODE=custom — invoked when the user (or agent) needs to do anything GPU-related (provision, connect, check status, check cost, terminate) in a project where Rockie's GPU router is bypassed in favor of the user's own setup. Reads `.codex/gpu-custom.md` (populated by /gpu-custom-setup) for the user's documented flow and follows it. Replaces the deidentified `rockie-gpu` surface and /gpu-spend in custom mode — those route to gpu.py which exits gracefully when ROCKIE_GPU_MODE is not 'router'. If `.codex/gpu-custom.md` doesn't exist, redirect to /gpu-custom-setup first.
---

# /gpu-custom — runtime GPU operations in custom mode

When `ROCKIE_GPU_MODE=custom`, the agent doesn't drive GPU
provisioning through rockie's router. Instead, it follows the
user's own flow as documented in `.codex/gpu-custom.md` (populated
once by `/gpu-custom-setup`).

This skill is the agent's gateway to that flow.

## Pre-flight checks

```bash
# Are we in custom mode?
case "${ROCKIE_GPU_MODE:-router}" in
  custom) ;;  # ok, proceed
  router)
    echo "[gpu-custom] ROCKIE_GPU_MODE=router — use rockie-gpu / /gpu-spend instead"
    exit 0 ;;
  *) echo "[gpu-custom] ROCKIE_GPU_MODE=${ROCKIE_GPU_MODE} — not custom; nothing to do"; exit 0 ;;
esac

# Is the setup file present?
test -s .codex/gpu-custom.md || {
  echo "[gpu-custom] .codex/gpu-custom.md missing or empty"
  echo "  → run /gpu-custom-setup first to onboard your custom GPU flow"
  exit 0
}
```

If both pass, read `.codex/gpu-custom.md` and route based on the
user's intent.

## Routing user intent → setup file section

| User intent | Read this section of gpu-custom.md |
|---|---|
| "spin up a GPU" / "provision" / "start training" | §2 Provision (then §3 Connect) |
| "connect to my pod" / "ssh in" | §3 Connect |
| "what's running" / "list pods" / "status" | §4 Monitor → Status |
| "what's it costing" / "spend" / "burn rate" | §4 Monitor → Cost |
| "tear down" / "terminate" / "stop the pod" / "we're done" | §5 Terminate (verbatim — don't improvise) |
| "preemption" / "got kicked off" | §6 Optional → Preemption (if present) |
| "checkpoint" / "save state" | §6 Optional → Persistent storage (if present) |

## The hard rule about §5 Terminate

The terminate command captured during onboarding was reviewed by the
user for correctness. **Run it verbatim**. Do NOT:

- Substitute "similar" commands you think might work
- Add `--dry-run` or `--no-op` flags the user didn't include
- Skip the command because you're worried about side effects (the
  user wrote it; they own it)
- Run a different terminate command for "their other pod" you noticed
  — only act on the pod the user explicitly told you to terminate

If the user's command needs an instance ID / job ID / pod ID
substituted in, ask the user what to substitute. Don't guess.

## When the file is incomplete

If the user asks for an operation that isn't covered in
`.codex/gpu-custom.md` (e.g. they ask about cost but §4 doesn't have
a cost subsection), respond honestly:

> Your `.codex/gpu-custom.md` doesn't document a cost-tracking flow.
> Want me to add it now? (You'd paste the command or URL you check.)

Don't make up a flow. Don't fall back to rockie's router-mode
commands like `gpu.py cost` — those will exit with a "custom mode
bypassed" message anyway.

## Budget integration (optional)

If the user has a way to surface live $/hr (URL, CLI), and it's
documented in §4 Monitor → Cost, you can periodically query it and
write to rockie's `budget.py` so the dollars ceiling stays honest:

```bash
# In a wrapper the user manually invokes (NOT auto-fired by hooks
# in custom mode — we don't presume to know their billing cadence):
python3 .codex/scripts/budget.py add dollars <amount>
```

But don't auto-wire this without explicit user opt-in — custom mode
exists because the user wants to drive their own tooling.

## Composition with other harness pieces

- **autopilot loop** still runs in custom mode. The training
  launcher inside autopilot.conf points at the user's own command
  (e.g. `LAUNCHER_CMD="ssh worker bash /home/user/train.sh"`); the
  ZCM supervisor still detects exit/anomaly/crash; the budget hook
  still gates on token counts.
- **`/post-run-review`** still works (independent of GPU layer).
- **`/propose-harness-change`** still works.
- **The dollars budget dimension** is what's affected. In custom
  mode it's only as good as the user's manual reporting (or the
  optional budget.py wrapper above).
