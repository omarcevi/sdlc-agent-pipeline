# issue-to-pr

[![CI](https://github.com/omarcevi/sdlc-agent-pipeline/actions/workflows/ci.yaml/badge.svg)](https://github.com/omarcevi/sdlc-agent-pipeline/actions/workflows/ci.yaml)

A multi-agent pipeline on Google ADK 2.x that turns a GitHub issue into a tested pull request.

**Watch it work:** [replays of seven real runs](https://omarcevi.dev/sdlc-agent-pipeline/), step by step: the pipeline graph, every tool call, the diff, the tests, the reviewer's verdict and the cost. A static site, no backend.

Status: under construction. Design: `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md`.

## Results so far

### 15 tasks on three repositories (2026-10-01)

The multi-agent pipeline (planner, coder, reviewer) against a single agent with the same tools, sandbox, guardrails, test-fix loop and caps. Fifteen development tasks on three demo repositories (bugs, features, a refactor and traps that should be declined), three repeats each, Gemini 3.8 Flash, scored by hidden tests the agents never see.

| System | Resolved | Cost per run | Cost per resolved | Median time |
|---|---|---|---|---|
| Multi-agent | 34 of 45 | $0.43 | $0.57 | 319 s |
| Single agent | 36 of 45 | $0.38 | $0.47 | 171 s |

Read this with its caveats:

- **The same three tasks failed for both systems, for the same reason.** The three multi-file features ran out of the $1.00 per-run budget in every run, nine times each, with the change mostly written. The planner did not make them cheaper: the coder's context still grew past a million input tokens.
- **The rest of the gap is two stalled model calls.** Two multi-agent runs on easy tasks waited on a model reply that never came, and the wall-clock cap ended them. Without those two runs, the multi-agent pipeline resolves 34 of 43 (79%) against the single agent's 36 of 45 (80%). The harness now gives up on a stalled call after 8 minutes and retries it once, instead of waiting for the run's cap.
- **The planner and the reviewer changed no outcome.** No test-fix or review loop was entered in 90 runs; the reviewer approved all 28 patches it saw. On two tasks built to tempt a narrow fix, both systems fixed the root cause in all 12 runs.
- **The extra agents cost more.** On the resolved tasks that need a patch, the multi-agent pipeline used about 42 tool calls per run against 23, and $0.35 against $0.26.
- **Fifteen tasks is a small sample.** One task is 6.7 points of resolve rate. Five more tasks were written as a sealed held-out split: written by one agent, checked by another, and never opened by the main session. They have never been run; the owner dropped the held-out run for now (design spec §19), and they are published as they are.

Report, including every failed run and what was checked by hand: [comparison](docs/results/2026-10-01-2b-comparison.md). How the run went, with costs: [run log](docs/results/2026-10-01-2b-run-log.md). How the tasks were checked before it: [pilot log](docs/results/2026-09-30-2b-pilot-log.md).

### 5 tasks on one repository (2026-09-30)

Two runs on 2026-09-30 compare the multi-agent pipeline (planner, coder, reviewer) with a single agent that has the same tools, sandbox, guardrails and test-fix loop. Five tasks on one demo repository, three repeats each, Gemini 3.8 Flash, scored by hidden tests the agents never see.

| Run | System | Resolved | Cost per run | Median time |
|---|---|---|---|---|
| Equal caps | Multi-agent | 15 of 15 | $0.16 | 249 s |
| Equal caps | Single agent | 15 of 15 | $0.14 | 151 s |
| First run (uneven caps) | Multi-agent | 12 of 15 | $0.13 | 147 s |
| First run (uneven caps) | Single agent | 15 of 15 | $0.15 | 124 s |

Read this with its caveats:

- **This is not a benchmark yet.** Five small tasks (three easy, two medium), all from the development split. Both systems are at the ceiling and wrote near-identical fixes.
- **The first run's gap was one cap.** A limit of 25 tool calls per coder turn applied to the multi-agent coder only, and stopped the same task three times with the fix already written. With one set of limits per issue for both systems ($1.00, 75 tool calls, 25 minutes), the gap is gone.
- **On these tasks the extra agents cost more and changed nothing.** The reviewer approved every patch it saw on the first round (21 of 21 across both runs). Under equal caps the multi-agent pipeline cost about 13% more and took about 1.8 times as long on the tasks that need a patch.
- **The Pro model run did not complete.** A reviewer call on Gemini 3.1 Pro did not return in 2 of 2 attempts, which exposed a missing time limit in the harness (since added).

Reports, including every failed run and what was checked by hand: [equal caps](docs/results/2026-09-30-equal-caps-comparison.md), [first run](docs/results/2026-09-30-week2a-comparison.md). How the runs went, step by step, with costs: [run log](docs/results/2026-09-30-week2a-run-log.md).

Since then: live mode on real GitHub issues with a human approval step, a cloud sandbox backend on Agent Runtime with a $500 budget hard stop, and the replay site above.
