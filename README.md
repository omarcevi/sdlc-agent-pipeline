# issue-to-pr

A multi-agent pipeline on Google ADK 2.x that turns a GitHub issue into a tested pull request.

Status: under construction. Design: `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md`.

## Results so far

First comparison, 2026-09-30: the multi-agent pipeline (planner, coder, reviewer) against a single agent that has the same tools, sandbox, guardrails, budget and test-fix loop. Five tasks on one demo repository, three repeats each, Gemini 3.8 Flash, scored by hidden tests the agents never see.

| System | Resolved | Cost per run | Cost per resolved issue | Median time |
|---|---|---|---|---|
| Multi-agent | 12 of 15 (80%) | $0.13 | $0.17 | 147 s |
| Single agent | 15 of 15 (100%) | $0.15 | $0.15 | 124 s |

Read this with its caveats:

- **This is not a benchmark yet.** Five small tasks (three easy, two medium), all from the development split, cannot separate the two systems. Both wrote near-identical fixes.
- **The whole gap is one cap.** All three multi-agent failures are the same task, stopped by a limit of 25 tool calls per coder turn. That limit does not apply to the single agent, which used 30 to 38 calls on the same task. In each failed run the fix and its tests were already written.
- **The reviewer never changed an outcome.** It approved all 9 patches it saw on the first round.
- **The Pro model run did not complete.** A reviewer call on Gemini 3.1 Pro did not return in 2 of 2 attempts, which exposed a missing time limit in the harness (since added).

Full report, including every failed run and what was checked by hand: [`docs/results/2026-09-30-week2a-comparison.md`](docs/results/2026-09-30-week2a-comparison.md). How the run went, step by step, with costs: [`docs/results/2026-09-30-week2a-run-log.md`](docs/results/2026-09-30-week2a-run-log.md).

Next: equal caps for both systems, more repositories and harder tasks, and a held-out split.
