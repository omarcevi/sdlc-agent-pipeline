# Week 2A comparison run: log

A plain record of the first multi-agent vs single-agent run: what was run, in what order, what it cost, and what went wrong. Written as it happened on 2026-09-30. Times are UTC. The results themselves are in the comparison report next to this file.

## Approval

The owner approved the paid run in session on 2026-09-30 ("task 7 is a go i approve just document what you do"). Estimate given beforehand: about $12 (30 Flash runs at about $0.16, 10 Pro runs at about $0.70). Cost guards were left at their defaults: $1.00 and 75 tool calls per run.

## Before spending anything

| Time | Step | Result |
|---|---|---|
| 12:35 | Review of the scoring fix (hidden tests in a second, fresh sandbox) | Approved. Three patches that fixed nothing but scored "resolved" on the old scorer now score unresolved |
| 12:40 | Review of the matrix runner | Approved with one fix: the CLI validated tasks before loading `.env`. Fixed and tested |
| 12:50 | Merged state at `b52a36b` | `uv run pytest -q`: 341 passed. `bench.validate`: ok for all 5 tasks. `agents-cli lint`: clean |
| 12:53 | `make sandbox-image` | Built (cached), image `issue-to-pr-sandbox:dev` |
| 12:53 | Credentials and project | Application default credentials valid; project `cloud-agents-project` |
| 13:15 | Whole-branch code review of everything the run depends on | No critical defect. Seven findings that would change a number or how it reads (below). Verdict: go, after fixes |

### What the review found, and what was done before the run

The review was moved ahead of the paid run on purpose: a bug found afterwards would have meant paying twice.

| Finding | Decision |
|---|---|
| A model that returns an empty answer was recorded as a runner crash with $0 cost when it happened to the planner, the reviewer or the single agent, but was tolerated in a coder turn | Fixed before the run: it is now an agent failure and keeps its cost |
| The single agent's "declined" flag was optional, the planner's was required, so a forgetful single agent was treated as "done" | Fixed before the run: the flag is required |
| One failed `docker run` during scoring threw away a paid run | Fixed before the run: scoring retries twice |
| A crashed run reported $0 spent | Fixed before the run: a crash keeps its numbers |
| The report could overstate the result (one caveat for all rows, `0%` for a configuration that never ran, wrong patches in no column, one "budget" number for three different caps) | Fixed before the report is generated |
| The per-turn cap of 25 tool calls applies to the multi-agent coder only, and the 75-call run cap is shared by three agents there. In Week 1 the coder used exactly 25 calls in three of six turns | Not changed: cost guards are the owner's to change. Disclosed in the report, with budget failures broken down by cap |
| The single agent and the Pro models had never met a real model | A cents-level smoke run first, into `results/smoke/` so it cannot be mixed into the report |
| No spending ceiling across the whole matrix | Stop rule: the four invocations run one after another, and the next one does not start if the running total is above $15 |

The fixes were reviewed again by a separate reviewer. The first pass was a no-go: a model reply with no content at all (cut off at the token limit, or blocked by a safety filter) was still recorded as a crash. After a second fix the reviewer probed 86 cases and returned a go.

| Time | Step | Result |
|---|---|---|
| 13:31 | Merged state at `5d86208` (the commit the run uses) | `uv run pytest -q`: 379 passed. `bench.validate`: ok for all 5 tasks. `agents-cli lint`: clean |

## Settings in force

- Code: branch `week2-measurement` at `5d86208`.
- Tasks: `tc-001` to `tc-005` of the `taskcli` demo repo, all in the dev split: two bugs (one easy, one medium), one easy feature, one medium refactor, one trap (an issue that should be declined).
- Systems: `multi` (planner, coder, reviewer) and `single` (one agent with the same tools, sandbox, guardrails, budget and test-fix loop).
- Models: `flash` = `gemini-3.8-flash` for every role; `pro` = `gemini-3.1-pro-preview` for every role.
- Caps per run: $1.00, 75 tool calls; 25 tool calls per coder turn (multi-agent coder only); 3 returns to the coder after failing tests, 2 after review.
- Sandbox: local Docker, image `issue-to-pr-sandbox:dev`, no network.
- Prompts were not tuned between Week 1 and this run, except one sentence added to the single agent's prompt ("Otherwise set declined to false.") when its flag became required.
- Cost is an estimate from token counts at list prices. Cached tokens are billed at the full rate, so it overstates.

## Smoke runs

Kept in `results/smoke/` and not part of the report.

| Time | Command | Result | Cost |
|---|---|---|---|
| 13:34–13:36 | `--tasks tc-005,tc-002 --system single --preset flash --concurrency 2` | 2 of 2 resolved. The trap was declined in 24 s; the bug fix passed its tests first time in 114 s | $0.12 |
| 13:36 | `--tasks tc-005 --system multi --preset pro` | Trap declined in 7 s. The Pro model name resolves | $0.005 |
| 13:36 | `--tasks tc-005 --system single --preset pro` | Trap declined in 7 s | $0.007 |

Smoke total: $0.14. No sandbox was left running after any of them. This was the first time the single agent and the Pro preset met a real model; both worked.

## The four invocations

Each was run as `TRACE_TO_CLOUD=1 uv run python -m bench.run --split dev --system <system> --preset <preset> --repeats <n> --concurrency 3`, one after another, with output kept in `runs/logs/` (not committed). The running total includes the smoke runs.

| # | Time | System, preset, repeats | Resolved | Cost | Running total | Results file |
|---|---|---|---|---|---|---|
| 1 | 13:37–13:59 | multi, flash, 3 | 12 of 15 | $2.01 | $2.15 | `results/20260930T133710Z-multi-flash.json` |
| 2 | 13:59–14:13 | single, flash, 3 | 15 of 15 | $2.27 | $4.41 | `results/20260930T135946Z-single-flash.json` |
| 3 | 14:14–14:43 | multi, pro, 1 (concurrency 3) | aborted | about $0.55 | about $4.96 | `results/aborted/20260930T141410Z-multi-pro.json` (4 rows, not reported) |
| 3b | 14:44–15:08 | multi, pro, 1 (concurrency 1) | aborted | about $0.20 | about $5.16 | none (no run finished) |
| 4 | not run | single, pro, 1 | | | | |

### Notes taken while it ran

- **Invocation 1.** All three `tc-001` runs failed on the same cap: `coder exceeded 25 tool calls in one turn`. In each, the coder had already edited `taskcli/dates.py`, written a new test file and run the tests; the 26th call was a last `git diff`, `git status` or `pytest`. Before its first edit it made 20 to 22 calls; 13 to 17 were shell commands, and 11 to 15 of those tried inputs on `datetime.fromisoformat`, mostly one input per call. No patch is saved for a run that ends on a cap, so these cannot be scored after the fact. Nothing else failed: no review or test-fix loop was entered in any run, no infra failures, no reruns, no crashed rows, no audit flags.
- **Invocation 2.** The single agent resolved `tc-001` in all three repeats, using 31, 38 and 30 tool calls in the run. Each of those is above 25, so the multi-agent coder's per-turn cap would have stopped it too. It has the same habit of probing `datetime.fromisoformat` call by call. No failures of any kind, no reruns, no audit flags.
- **Invocation 3, first attempt (multi, pro, concurrency 3): aborted.** Two separate problems.
  - *Rate limit.* Vertex answered `429 RESOURCE_EXHAUSTED` when three Pro runs started at once. `tc-001`, `tc-002` and `tc-003` each failed all three attempts within a minute, because a rerun after an infra failure starts immediately and landed in the same rate-limit window. `tc-005` was declined on its second attempt.
  - *A hung model call.* `tc-004` got through the planner and the coder on its third attempt, then a reviewer model call never returned. The process sat at 0% CPU for 23 minutes with the connection still open; there is no request timeout on the model client.
  - At 14:43 the process was interrupted with Ctrl-C (SIGINT). It shut down cleanly: no sandbox left behind, and the four finished rows were already on disk. Spend: $0.09 in recorded rows plus about $0.46 for the hung attempt (estimated from its event log). The partial results file was moved to `results/aborted/` and is not part of the report.
  - Decision: rerun both Pro invocations with `--concurrency 1` on the same commit, under a small watchdog script that interrupts the run if its log is quiet for 10 minutes. No code was changed between the Flash and Pro runs. The two defects (no request timeout, no pause before an infra rerun) are recorded to fix before merge.
- **Invocation 3b (multi, pro, one run at a time): hung again, same place.** `tc-001`: the planner and the coder finished, the tests passed, the reviewer read two files, and then its model call did not return for 21 minutes. That makes 2 of 2 multi-agent Pro runs that reached the reviewer. The watchdog sent its interrupt at 14:57, but it had no effect: a process started as a background job in a non-interactive shell ignores that signal (a mistake in the wrapper script, not in the project). The process was stopped with SIGTERM at 15:08 and the sandbox removed by hand. The earlier move of the partial results file had also silently failed; it was moved again, properly.
- **Decision at 15:10: no more Pro runs today.** A hang that repeats at the same stage is not going to be fixed by trying again. Multi-agent on Pro is reported as "did not complete", and the single agent on Pro was not run because it would have had nothing to be compared with. The owner asked why there was no failsafe for a stuck run; there was none for time, so a wall-clock cap per run (`RUN_TIMEOUT_S`, 25 minutes by default) and a pause before an infra rerun were built and reviewed the same afternoon (see the spec, §19).
- While invocation 1 ran, the owner noticed the Input/Output tab in Cloud Trace is empty. Cause: only the trace exporter is on; that tab is filled from log events, which would need Cloud Logging. Prompt and response content is on the `call_llm` span's Attributes tab instead. Left as is for this run (owner's decision).

## Totals

| Item | Cost |
|---|---|
| Smoke runs | $0.14 |
| Multi-agent, Flash, 15 runs | $2.01 |
| Single agent, Flash, 15 runs | $2.27 |
| Pro attempts (recorded rows plus two hung runs, estimated from their event logs) | about $0.75 |
| **Total** | **about $5.16** |

The estimate given before the run was about $12, and the stop rule was $15. The two hung model calls may be billed for output that was never received; that cannot be seen from here and would add at most about $0.80 each. The billing report for the project is the final word.

All costs are estimates from token counts at list prices.

## What was checked by hand afterwards

- Every resolved patch (21: 9 multi-agent, 12 single-agent) was read. All are small source fixes plus a new test file; none touches pytest internals or configuration.
- The event logs of the three failed Flash runs were read to see what the coder was doing when the cap ended the run.
- The per-agent tool-call counts and the reviewer's verdict were extracted for every multi-agent run.
- No sandbox container was left running at the end (`docker ps`).

## Where the raw material is

- `results/*.json`: one row per run (committed).
- `runs/<run_id>/`: `events.jsonl`, `record.json` and `patch.diff` for every run (not committed; local only).
- `runs/logs/`: the terminal output of each invocation (not committed).
- Cloud Trace, project `cloud-agents-project`, root span `issue_to_pr.run`: one trace per run.

## Rerun under equal caps (same day)

After reading the first report, the owner decided to remove the per-turn cap so that both systems run under the same limits per issue ($1.00, 75 tool calls, 25 minutes), and to run the Flash comparison once more. Pro was left out: the reviewer hang is not explained, and each hung run would now use its full 25 minutes.

| Time | Step | Result |
|---|---|---|
| 16:04–16:08 | Week 2A merged to `main`; cap removed on branch `equal-caps` (commit `fda8d71`) | 20 lines removed from the budget plugin. `uv run pytest -q`: 394 passed. `bench.validate`: ok for all 5 tasks. Lint clean |
| 16:13–16:36 | `--split dev --system multi --preset flash --repeats 3 --concurrency 3` | 15 of 15 resolved, $2.42. `results/20260930T161316Z-multi-flash.json` |
| 16:40–16:53 | `--split dev --system single --preset flash --repeats 3 --concurrency 3` | 15 of 15 resolved, $2.15. `results/20260930T164020Z-single-flash.json` |

Nothing went wrong in either invocation: no infra failures, no reruns, no crashed rows, no audit flags, no sandbox left running. All 24 patches were read by hand. Report: `2026-09-30-equal-caps-comparison.md`.

Rerun cost: $4.57. Total for the day: about $10.92 ($10.26 in recorded rows, plus about $0.66 estimated for the two hung Pro runs), against the estimate of about $12 for the first run alone. The hung Pro calls may be billed for output that was never received; the project's billing report is the final word.

