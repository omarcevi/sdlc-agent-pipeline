# ADR 1: Graph workflow over autonomous multi-agent chat

Date: 2026-10-02. Status: accepted.
Sources: [parent spec](../superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md) §5, §6.2 and §19 (2026-09-29; 2026-09-30, Week 2A Tasks 2 to 5); [`app/pipeline.py`](../../app/pipeline.py), [`app/baseline.py`](../../app/baseline.py); [first comparison](../results/2026-09-30-week2a-comparison.md), [Week 2B comparison](../results/2026-10-01-2b-comparison.md).

## Context

Turning an issue into a pull request has three judgment steps (plan, code, review) and several steps that must be exact: fetch the issue, start a sandbox, take the diff, run the tests, write the patch or open the pull request.

A common way to build this is a group of agents that talk to each other, where a model decides who acts next, when to hand work back and when the work is done. That leaves the properties this project needs to the model. A coder-reviewer loop ends when a model says so. Tests run when an agent decides to run them. A route cannot be unit-tested without a real model, and the path a run takes lives in model text, which makes runs hard to replay. The benchmark also compares the pipeline with a single agent, so the two systems must differ in their roles only, not in hidden control flow.

## Decision

The pipeline is a graph `Workflow` from Google's Agent Development Kit (ADK) 2.x, with fixed edges, built in `app/pipeline.py`.

- **Function nodes for exact steps, `LlmAgent` nodes for judgment.** `fetch_issue`, `provision_sandbox`, `collect_diff`, `run_tests`, `deliver_patch` and `report_failure` (in live mode also `human_gate`, `route_approval` and `open_pr`) are Python functions. The planner, coder and reviewer are `LlmAgent` nodes with fixed tools; the planner and the reviewer can only read.
- **Routers after the planner and the reviewer.** An ADK LLM node cannot emit a route, so `route_plan` and `route_review` read the agent's validated answer (`Plan`, `Review`) and pick the edge ([ADR 2](0002-deterministic-routing.md)).
- **Loop bounds in code.** A failing test run goes back to the coder at most 3 times, a review asking for changes at most 2 times. Both counters live in session state for the whole run; when one runs out, the run ends in `report_failure`.
- **Retries on the exact steps.** Function nodes that touch the sandbox or GitHub retry an infrastructure error, 3 attempts with backoff.
- **The baseline is the same graph without two roles.** `app/baseline.py` keeps fetch, provision, diff, tests, delivery and the test-fix loop, and puts one `solo` agent with the coder's tools where the planner, coder and reviewer were.

## Consequences

- Routing is tested without a model. Unit tests drive the graph with a scripted fake model (`tests/fakes.py`): failing tests end the run after three returns, a declined plan skips the coder, an empty diff goes back to the coder. `tests/unit/test_graph_edges.py` pins the edges of both graphs.
- Every run takes a path the graph allows, and the driver writes each event to `runs/<run-id>/events.jsonl`, so a recorded run can be replayed step by step on a graph exported from this code ([ADR 7](0007-replay-first-public-demo.md)).
- Live mode pauses at one known point: `human_gate` yields ADK's `RequestInput`, and the driver resumes the same session with the approver's decision.
- The agents cannot change the order of work. They share only session state (plan, diff, test report, review), and the coder cannot ask the planner a question. The planner decides once whether to decline, while the single agent can decline on any of its turns; the multi-agent coder can get up to six turns (1 + 3 + 2), the single agent four. The first comparison lists both differences.
- The graph makes the cost of the extra roles visible, and on this benchmark they have not paid for it. In Week 2B the multi-agent pipeline resolved 34 of 45 runs and the single agent 36 of 45, at $0.57 against $0.47 per resolved issue and a median 319 s against 171 s.
- The graph API was new in ADK 2.x. The design was checked against the ADK 2.8.0 source and API probes before building (§19, 2026-09-29), and its limits shaped other decisions: edges cannot route exceptions, so a cap aborts the run ([ADR 6](0006-runner-wide-budget.md)); and agents return structured answers through ADK's `set_model_response` tool, because a native output schema together with tools looped on gemini-3.8-flash.
