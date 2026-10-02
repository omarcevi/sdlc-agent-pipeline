# Week 3A: cloud sandbox parity

Date: 2026-10-02. Question: does the pipeline behave the same when its code runs in Agent Runtime sandboxes instead of local Docker containers? Same model (Gemini 3.8 Flash), same prompts (version `2c`), same caps ($1.00, 75 tool calls), same three dev tasks, one run each, started at the same time.

## Model-free check: the reviewer probes

`bench.probes validate` applies each of the 12 reviewer probes in a sandbox and checks it against the visible and hidden tests.

| Backend | Probes `ok` |
|---|---|
| Docker | 12 of 12 |
| Agent Runtime | 12 of 12 |

## The pipeline on three tasks

| Task | Docker | Agent Runtime | Same outcome |
|---|---|---|---|
| `tc-003` | resolved · $0.157 · 104 s · 36 calls | resolved · $0.117 · 194 s · 30 calls | yes |
| `md-001` | stopped at the tool-call cap · $0.727 · 285 s | stopped at the tool-call cap · $0.789 · 374 s | yes |
| `sr-002` | resolved · $0.312 · 203 s · 35 calls | resolved · $0.287 · 205 s · 32 calls | yes |
| Total | 2 of 3 · $1.20 | 2 of 3 · $1.19 | |

- Every task ended in the same outcome bucket on both backends. No cloud run ended in an infrastructure failure, none was rerun for one, and none crashed.
- **Wall time:** the cloud runs took 0 to 90 s longer per task (+87%, +31% and +1%). Each command is an HTTPS round trip to the sandbox instead of a local `docker exec`. Model time dominates the rest.
- **Cost:** the same within noise; the sandboxes add cents, billed separately and not counted here.
- **`md-001`:** both runs used their 76th tool call before a patch was written (the cap is checked on the call that exceeds it). The most any resolved multi-agent run of Week 2B needed was 73 calls, so at 75 this task is a coin flip on either backend. Raising the cap is an owner decision; the analysis is in the run log.
- After both runs: `sweep --all --dry-run` lists no sandbox.

Reports from `bench.report`: [Docker](2026-10-02-3a-parity-docker.md), [Agent Runtime](2026-10-02-3a-parity-cloud.md). Results: `results/3a-parity/`.

## Process note

The first attempt, started a few minutes earlier, was stopped: both runs began in the same second, got the same run stamp, and wrote into the same run directories. `bench.run` now claims each stamp exclusively (`705a066`), and the two runs above have different stamps. The stopped attempt cost about $0.50 to $1 and its results are not used.
