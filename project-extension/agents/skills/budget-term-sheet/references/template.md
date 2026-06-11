# Rockie GPU Budget Term Sheet

Quote ID: {{ quote_id }}
Job shape: {{ job_shape }}
Compute: {{ compute_line }}
Availability: {{ availability_line }}
Estimated wall-clock: {{ wallclock_line }}

Estimated total: {{ estimate_line }}
Recommended budget: {{ recommended_budget_line }}
User budget: {{ user_budget_line }}

Stage breakdown:
{{ stage_lines }}

Confidence: {{ confidence_line }}
Decision state: {{ decision_line }}

Reply with exactly one of:
- `approve`
- `modify budget to $X`
- `cancel`
