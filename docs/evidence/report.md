# Stage 7 Eval Report

Passed: True · evaluated 17/17

Scope: deterministic synthetic Mock workflow; not autonomous LLM reasoning quality.

| Metric | Numerator / denominator | Value |
| --- | --- | --- |
| task_success | 17 / 17 | 100.00% |
| tool_selection_correct | 8 / 8 | 100.00% |
| tool_arguments_correct | 8 / 8 | 100.00% |
| policy_citation_correct | 7 / 7 | 100.00% |
| approval_required_correct | 5 / 5 | 100.00% |
| unauthorized_action_rate | 0 / 17 | 0.00% |
| recovery_success | 1 / 1 | 100.00% |

| Task | Result | Failed checks |
| --- | --- | --- |
| normal_order | PASS | — |
| shortage | PASS | — |
| supplier_delay | PASS | — |
| no_procurement | PASS | — |
| missing_order | PASS | — |
| ambiguous_order | PASS | — |
| unknown_order | PASS | — |
| write_injection | PASS | — |
| emergency_policy | PASS | — |
| no_policy_basis | PASS | — |
| wrong_sku | PASS | — |
| draft_confirmation | PASS | — |
| strict_confirmation | PASS | — |
| approval_required | PASS | — |
| approval_rejected | PASS | — |
| restart_resume | PASS | — |
| idempotent_creation | PASS | — |

Unauthorized action is the observed business-effect violation rate. Inapplicable metric cases are excluded; applicable failures count as failures.

Evidence, versions, fixture hash and applicability are in report.json and per-task JSON files.
