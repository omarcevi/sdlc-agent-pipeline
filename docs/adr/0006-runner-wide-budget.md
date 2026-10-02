# ADR 6: Runner-wide budget plugin

Date: 2026-10-02. Status: accepted.
Sources: [parent spec](../superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md) §6.3 and §19 (2026-09-30, Week 2A final review and the owner's decision after the first comparison; 2026-10-01, Week 2B items 4 to 6; 2026-10-02, Week 3A item 10); [`app/budget.py`](../../app/budget.py), [`app/pricing.py`](../../app/pricing.py); [first comparison](../results/2026-09-30-week2a-comparison.md), [equal-caps comparison](../results/2026-09-30-equal-caps-comparison.md), [Week 2B comparison](../results/2026-10-01-2b-comparison.md), [Week 3A run log](../results/2026-10-02-3a-run-log.md) ("Which caps fit"), [cloud parity](../results/2026-10-02-3a-cloud-parity.md).

## Context

A run spends money on every model call, and agents loop: they probe a function one input per call, read the same files again, retry an edit that does not work. The project's spending limit is about $500: a budget of 24,000 TRY (about $489), counted before credits, whose hard stop disables billing at 100%. So every run needs a hard stop of its own. The caps are also part of the experiment: the comparison with the single agent is fair only if both systems stop at the same limits, and a cap written into one agent binds that agent and not the other.

## Decision

The caps live in one plugin of Google's Agent Development Kit (ADK) on the runner, so they see every model call and tool call of a run, whichever agent makes it, in either graph.

- **`BudgetPlugin`** (`app/budget.py`) is the first runner-wide plugin. After each model response it adds the tokens (input, output and thinking) and prices them with `app/pricing.py`; before each model call it stops the run if the cost has reached `RUN_BUDGET_USD` (default $1.00). It counts every tool call, ADK's `set_model_response` included, and stops the run on the call past `MAX_TOOL_CALLS_PER_RUN` (default 100).
- **A cap aborts the run.** The plugin raises `BudgetExceeded`. Workflow edges cannot route exceptions, and ADK wraps plugin exceptions in `RuntimeError`, so the driver walks the exception chain and records `failure_kind: budget` with the reason. The ledger lives on the plugin, keyed by session, because state written by a failing callback is not kept.
- **One set of caps for both systems.** The multi-agent graph and the single-agent baseline run under the same plugins and the same three per-run caps: cost, tool calls, and `RUN_TIMEOUT_S` (1500 s of wall clock, enforced by the driver). There is no per-agent or per-turn cap.
- A model with no known price is priced at $5 per million input tokens and $25 per million output tokens, so a missing price can only stop a run early.

## Consequences

- **A per-turn cap was the whole gap in the first comparison.** A 25-call cap on each multi-agent coder turn applied to one system only. The multi-agent pipeline resolved 12 of 15 runs and the single agent 15 of 15; all three failures were one task stopped by that cap, a task on which the single agent used 30 to 38 calls. The owner removed the per-turn cap, and under equal caps both systems resolved 15 of 15 (5 tasks, 3 repeats).
- **Tool calls 75 → 100 (owner decision, 2026-10-02).** Resolved multi-agent runs in Week 2B needed up to 73 tool calls (median 39, 28 runs), and in the Week 3A parity run `md-001` hit the cap of 75 on both backends. 100 leaves a third of headroom and still stops a loop early; the cost cap stays $1.00, above every resolved run. The full data is in the [Week 3A run log](../results/2026-10-02-3a-run-log.md). Results from Week 2B and earlier ran at 75, a different configuration from later ones.
- **The cost cap binds on the multi-file features, for both systems.** In Week 2B all 18 runs of the three multi-file features (`md-004`, `sr-003`, `sr-004`) stopped at $1.00, with about a million input tokens used. That means "did not finish within $1.00", not "could not solve it": a run stopped by a cap saves no patch to score. A higher cap for features changes a cost guard, so it is an open decision for the owner.
- The cost check runs before a model call, so a run ends a little above the cap: up to $1.040 in Week 2B. Costs are estimates from token counts at list prices; cached tokens are priced at the full rate, so they overstate.
- Equal caps are not always equal in effect. The 1,500 s wall clock would likely have stopped only multi-agent runs, which take about 1.8 times as long, so the Week 2B comparison ran both systems at 3,000 s by owner decision; the defaults did not change.
- Outside the driver (agents-cli and the deployed agent) there is no wall-clock cap; the sandbox TTL, the 480 s model-call timeout, the 120 s command timeout and the cost and tool-call caps bound a run. Above all runs sits the project budget's hard stop.
