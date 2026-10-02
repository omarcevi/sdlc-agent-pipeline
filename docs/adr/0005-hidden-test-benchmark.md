# ADR 5: Hidden-test benchmark with a sealed held-out split and trap tasks

Date: 2026-10-02. Status: accepted.
Sources: [parent spec](../superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md) §3, §9 and §19 (2026-09-30, Week 2A Task 1; 2026-10-01, Week 2B; 2026-10-01, the held-out decision); [task-set design](../superpowers/specs/2026-09-30-week2b-task-set-design.md) §4, §6 and §7; [`AGENTS.md`](../../AGENTS.md) hard rule 5; [Week 2B comparison](../results/2026-10-01-2b-comparison.md).

## Context

The project needs a number for what the pipeline is worth that a model cannot game and a reader can check. Official SWE-bench scoring and large open-source repositories are out of scope (§3). The easy ways to a flattering number are well known: score with the tests the agent can see, so a patch that special-cases them passes; tune prompts on the tasks that are then reported; and count only issues that should be fixed, so an agent that patches everything never pays for it.

## Decision

A benchmark written for this project, scored with tests the agents never see.

- **Tasks.** 20 tasks on three small stdlib and pytest repositories written for it (`taskcli`, `mdlite`, `stockroom`): 8 bug fixes, 6 features, 3 refactors and 3 traps, each tagged easy, medium or hard; 15 in the dev split, 5 held out. A task is a `task.yaml` (issue text, category, difficulty, split), a `plant/` overlay that introduces the bug when one is needed, a `solution/` overlay (none for traps), and `hidden_tests/`, which never enter the run's sandbox.
- **Scoring.** The patch is applied to a clean base-plus-plant copy in a fresh sandbox, existing test files are restored, and the visible tests run with any pytest config in the repository ignored. Only if they pass do the hidden tests run, in a second fresh sandbox. Resolved means both pass. `bench/audit.py` flags patches that touch pytest's machinery (`conftest.py`, `sitecustomize.py`, `atexit` and similar); flagged rows are reported apart, and the flag never changes `resolved`.
- **Traps** are ambiguous or impossible issues that look actionable, for example requirements that contradict the README, or a product decision the repository cannot answer. A trap is resolved only when the run declines.
- **Tempting tasks.** Two dev tasks ship a `shortcut/` overlay: a narrow patch that passes the visible tests and fails the hidden ones. They are where a reviewer should earn its cost.
- **`bench validate`** proves every task before a scored run: visible tests pass at base plus plant, hidden tests fail there and pass with the solution, and a shortcut passes the visible tests and fails the hidden ones.
- **The seal.** The held-out tasks were written by one subagent and checked by another. The main session never opens, prints or diffs them, review diffs exclude them with a fixed pathspec, `bench.run` refuses them without `--confirm-heldout` and always on the cloud backend, and prompts are never tuned on them.
- **Infrastructure is not the agent's fault.** An infra failure is rerun up to twice and never counted against the agent; every run lands in one of six buckets (resolved, unresolved, agent, budget, infra, crashed).

## Consequences

- On the dev split the benchmark does not separate the two systems. Under equal caps the multi-agent pipeline resolved 34 of 45 runs and the single agent 36 of 45 (15 tasks, 3 repeats). Both failed the same three multi-file features at the $1.00 cap, and the multi-agent pipeline's two extra failures were provider stalls. Repeats rerun the same tasks; they do not enlarge the sample.
- The tempting tasks did not tempt: all 12 runs fixed both at the root. The reviewer was never shown a wrong patch, so `review_catch_rate` (§9.2) is not computed (it would be 0 of 0); reviewer probes were added to measure the reviewer directly ([ADR 2](0002-deterministic-routing.md)).
- The seal costs care and was broken twice while the set was built: a progress note named one task's plant location, and a pathspec without `/**` showed two tasks' file names in a diff stat. All three affected tasks were replaced, avoiding the revealed modules.
- **The held-out run is dropped for now** (owner decision, 2026-10-01). This is a demo repository, and prompts were never tuned on benchmark results: they were frozen through Weeks 2A and 2B, and the Week 2C change only delimits the issue text. So no held-out number is published. The five tasks stay sealed and unrun; they are published with the repository as they are, and the README says they were written sealed and never run.
- The tasks are small, synthetic and written by the same project, so the numbers describe these tasks, not real-world issues. Scoring has one known gap: patch code that attacks the test process during the hidden run could fake a pass. The audit flags the known patterns; in Week 2B it flagged no patch, and a sample of each task's patches was read in full.
