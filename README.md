# issue-to-pr

A multi-agent pipeline on Google ADK 2.x that turns a GitHub issue into a tested pull request.

Status: under construction. Design: `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md`.

## Results so far

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

Next: more repositories and harder tasks that leave headroom, and a held-out split.
