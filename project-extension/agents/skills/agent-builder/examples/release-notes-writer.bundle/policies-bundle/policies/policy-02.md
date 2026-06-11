# Policy 2

**Statement:** deny:internal-only

This is a declared policy for the agent. Where it is mechanically enforceable
(e.g. `deny:<substring>`), `.claude/hooks/policy_gate.py` blocks violating tool
calls. Otherwise the agent enforces it by judgment. Breaking this policy is a
run failure even if the task otherwise completes.
