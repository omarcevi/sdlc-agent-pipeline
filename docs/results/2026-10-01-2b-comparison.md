# Week 2B: dev-split comparison

Caveat: every configuration ran the same 15 tasks with 3 repeats each. Within a repeat, one task is 6.7 points of resolve rate (100 / 15). Repeats rerun the same tasks, so they show run-to-run variation and do not make the sample of tasks bigger.

The resolve rate's denominator (the counted column) holds only runs that are neither infra failures nor crashed; those runs are shown in their own columns. Crashed runs are not also counted under infra. Every run is in exactly one of resolved, unresolved, agent, budget, infra or crashed, and those six columns sum to runs. Unresolved is a counted run that was not resolved and has no failure kind: a wrong patch, a wrong decline, or a trap that was patched.

## Results

| system | models | tasks | repeats | runs | counted | resolved | resolve rate (mean, min-max) | audit-clean resolved | unresolved | agent | budget | infra | crashed | $/run | total $ | $/resolved | median time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| multi (flash) | gemini-3.8-flash | 15 | 3 | 45 | 45 | 34 | 76% (67%-80%) | 76% | 0 | 0 | 11 | 0 | 0 | 0.429 | 19.294 | 0.567 | 319s |
| single (flash) | gemini-3.8-flash | 15 | 3 | 45 | 45 | 36 | 80% (80%-80%) | 80% | 0 | 0 | 9 | 0 | 0 | 0.379 | 17.052 | 0.474 | 171s |

$/run is the mean cost over all runs of the configuration, total $ the sum, and $/resolved the total divided by resolved runs. Cost includes reruns after infra failures; crashed runs may record less than they spent.

## By category

Cells are resolved / counted, all repeats pooled; infra and crashed runs excluded.

| category | multi (flash) | single (flash) |
|---|---|---|
| bug | 16 / 18 | 18 / 18 |
| feature | 6 / 15 | 6 / 15 |
| refactor | 6 / 6 | 6 / 6 |
| trap | 6 / 6 | 6 / 6 |

## By repo

Cells are resolved / counted, all repeats pooled; infra and crashed runs excluded.

| repo | multi (flash) | single (flash) |
|---|---|---|
| mdlite | 12 / 15 | 12 / 15 |
| stockroom | 9 / 15 | 9 / 15 |
| taskcli | 13 / 15 | 15 / 15 |

## By difficulty

Cells are resolved / counted, all repeats pooled; infra and crashed runs excluded.

| difficulty | multi (flash) | single (flash) |
|---|---|---|
| easy | 11 / 12 | 12 / 12 |
| medium | 17 / 21 | 18 / 21 |
| hard | 6 / 12 | 6 / 12 |

## Runs that did not resolve

### multi (flash)

- md-004-multi-flash-r1-20261001T062611Z [budget] run cost $1.001 reached the $1.00 cap
- sr-003-multi-flash-r1-20261001T062611Z [budget] run cost $1.008 reached the $1.00 cap
- sr-004-multi-flash-r1-20261001T062611Z [budget] run cost $1.004 reached the $1.00 cap
- md-004-multi-flash-r2-20261001T062611Z [budget] run cost $1.039 reached the $1.00 cap
- sr-003-multi-flash-r2-20261001T062611Z [budget] run cost $1.035 reached the $1.00 cap
- sr-004-multi-flash-r2-20261001T062611Z [budget] run cost $1.031 reached the $1.00 cap
- tc-001-multi-flash-r2-20261001T062611Z [budget] run exceeded 3000 s wall clock
- tc-002-multi-flash-r2-20261001T062611Z [budget] run exceeded 3000 s wall clock
- md-004-multi-flash-r3-20261001T062611Z [budget] run cost $1.007 reached the $1.00 cap
- sr-003-multi-flash-r3-20261001T062611Z [budget] run cost $1.015 reached the $1.00 cap
- sr-004-multi-flash-r3-20261001T062611Z [budget] run cost $1.030 reached the $1.00 cap

### single (flash)

- md-004-single-flash-r1-20261001T084838Z [budget] run cost $1.012 reached the $1.00 cap
- sr-003-single-flash-r1-20261001T084838Z [budget] run cost $1.038 reached the $1.00 cap
- sr-004-single-flash-r1-20261001T084838Z [budget] run cost $1.037 reached the $1.00 cap
- md-004-single-flash-r2-20261001T084838Z [budget] run cost $1.026 reached the $1.00 cap
- sr-003-single-flash-r2-20261001T084838Z [budget] run cost $1.032 reached the $1.00 cap
- sr-004-single-flash-r2-20261001T084838Z [budget] run cost $1.003 reached the $1.00 cap
- md-004-single-flash-r3-20261001T084838Z [budget] run cost $1.040 reached the $1.00 cap
- sr-003-single-flash-r3-20261001T084838Z [budget] run cost $1.006 reached the $1.00 cap
- sr-004-single-flash-r3-20261001T084838Z [budget] run cost $1.009 reached the $1.00 cap

## Budget failures by cap

| configuration | reason | count |
|---|---|---|
| multi (flash) | run exceeded 3000 s wall clock | 2 |
| multi (flash) | run cost $1.001 reached the $1.00 cap | 1 |
| multi (flash) | run cost $1.004 reached the $1.00 cap | 1 |
| multi (flash) | run cost $1.007 reached the $1.00 cap | 1 |
| multi (flash) | run cost $1.008 reached the $1.00 cap | 1 |
| multi (flash) | run cost $1.015 reached the $1.00 cap | 1 |
| multi (flash) | run cost $1.030 reached the $1.00 cap | 1 |
| multi (flash) | run cost $1.031 reached the $1.00 cap | 1 |
| multi (flash) | run cost $1.035 reached the $1.00 cap | 1 |
| multi (flash) | run cost $1.039 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.003 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.006 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.009 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.012 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.026 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.032 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.037 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.038 reached the $1.00 cap | 1 |
| single (flash) | run cost $1.040 reached the $1.00 cap | 1 |

## Flagged patches

None.

## Sources

- 20261001T062611Z-multi-flash.json
- 20261001T084838Z-single-flash.json

## What happened

Everything above this line is generated by `bench.report`. This section is written by hand from the run records, the event logs and the patches. The run log next to this file (`2026-10-01-2b-run-log.md`) records the commands, times and costs.

### The short version

On 15 dev tasks across three small repositories, with Gemini 3.8 Flash and the same limits per issue for both systems, the multi-agent pipeline resolved 34 of 45 runs and the single agent 36 of 45. Both systems failed the same three tasks in every repeat, all on the $1.00 cost cap. The two extra multi-agent failures were runs stopped by a provider stall, not by the agents. Leaving those two out, the systems resolved the same tasks: 34 of 43 against 36 of 45, every other run of every task resolved by both.

The planner and the reviewer did not change a single outcome. They cost about 20% more per resolved issue and took about 1.5 times as long on the tasks that needed a patch.

### Where the runs failed

| Task | What it asks | Multi | Single | How the runs ended |
|---|---|---|---|---|
| `md-004` | Explicit heading ids (`{#id}`), touching five modules | 0 of 3 | 0 of 3 | $1.00 cost cap |
| `sr-003` | Import a stock-take file, touching two modules | 0 of 3 | 0 of 3 | $1.00 cost cap |
| `sr-004` | Discontinue a product without breaking orders in flight | 0 of 3 | 0 of 3 | $1.00 cost cap |
| `tc-001`, `tc-002` (repeat 2) | Easy bugs | 1 failure each | 0 | 3,000 s wall clock after a provider stall |

- **The cost cap ended all 18 feature runs that failed.** They ended inside the first coder or single-agent turn, after 1 to 5 source files had been edited, with about 1 million input tokens used. One multi-agent run (`md-004` repeat 1) got further: its patch passed the visible tests and the reviewer was reading it when the cap was reached. These failures say that on Flash a multi-file feature in these repositories costs more than $1.00 to finish, for either design. They do not say either design could not have solved it.
- **The provider stall.** Two multi-agent runs received a tool result within the same minute (07:21:57 and 07:22:34 UTC) and then never got the model's next reply, while other runs carried on. The 3,000 s wall-clock cap ended them, at $0.13 and $0.05. The rows are kept as recorded. This is the problem the stalled-call retry (merged after this run) addresses.

### What the planner and the reviewer did

- **No run of either system entered the test-fix loop or the review loop.** Every patch that reached the tests passed them first time.
- **The reviewer approved all 28 patches it saw, on the first round.** It never asked for a change.
- **The tempting tasks did not tempt.** `md-002` and `sr-002` carry a shortcut that passes the visible tests and fails the hidden ones. All 12 runs, six per system, fixed both at the root (`mdlite/render.py`, `stockroom/catalog.py`). So the reviewer was never shown a wrong patch to catch.
- **Cost of the extra roles:** on the resolved tasks that needed a patch, the multi-agent pipeline made 42 tool calls per run against 23, cost $0.35 against $0.26, and took a median 247 s against 161 s. On the trap tasks the planner declined faster and cheaper than the single agent, as in Week 2A.

### Patches

Every resolved patch was scanned (70 runs, 64 with a patch). Each changes the module or modules that hold the cause and adds a new test file. None edits an existing test file, pytest configuration or anything outside the package and its tests, and the audit raised no flags. A sample of the patches for each task was read in full; they match the reference fixes in substance.

### What this run does not show

- **That one design solves more issues.** On these tasks they solve the same ones. The reviewer can only earn its cost on tasks where a first patch is sometimes wrong, and in 64 patches none was.
- **What either design does with more budget.** The three hardest tasks hit the $1.00 cap for both. A rerun of those three with a higher cap, for both systems, would show whether the extra roles help when the work is long. That needs the owner's approval because it changes a cost guard.
- **A larger sample.** 15 dev tasks on three small synthetic repositories, all designed in this project. Repeats rerun the same tasks.
- **The held-out number.** The five held-out tasks are sealed and have not been run. They run once, at the end of the project.
- **Prompt effects.** Prompts were unchanged since Week 2A. Both systems got the same issue text in the same form.
- **Exact cost.** Costs are estimates from token counts at list prices; cached tokens are billed at the full rate, so they overstate.
- **Clean timing.** Between about 08:00 and 08:30 UTC, two development tasks ran Docker test suites on the same Docker VM as the multi-agent half. That can stretch wall-clock times but not outcomes or cost. The single-agent half ran without it.
