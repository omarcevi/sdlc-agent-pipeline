# Week 2B design: a task set that can separate the systems

Date: 2026-09-30. Status: approved in conversation, written for review.
Parent spec: `2026-09-29-sdlc-agent-pipeline-design.md` (§9.1 Benchmark). Where this document is more specific, it wins for Week 2B; the parent spec's §19 records the resulting amendments.

## 1. Why

Two Flash comparisons on the five `taskcli` tasks ended 15 of 15 for both systems once the caps were equal. Both systems wrote near-identical first patches, no test-fix or review loop was ever entered, and the reviewer approved 21 of 21 patches. The tasks are too easy to show whether a planner or a reviewer adds anything. Week 2B builds a task set with headroom, where a plan or a second look could plausibly change the outcome.

## 2. Success criteria

1. Two new repositories exist under `bench/repos/`, each about 1,000 to 1,500 lines of Python (stdlib plus pytest), with visible tests that pass at base.
2. Fifteen new tasks exist, so the benchmark has 20 in total, and `bench validate` prints `ok` for every one, including the new checks in §6.
3. On the 10 new dev tasks, the single agent on Flash resolves between 5 and 7 in the pilot (§8), after at most two rounds of changes, all of them logged.
4. The 5 held-out tasks are written and checked without the main session ever reading them (§7).
5. One dev-split comparison of both systems on Flash, three repeats, is run with the owner's approval and reported like the Week 2A runs (§10).

## 3. Repositories

Both follow `taskcli`'s layout: a package directory, `tests/`, a `README.md`, no third-party dependencies, no network, no file writes outside a temp directory in tests.

**`mdlite`: a Markdown-to-HTML library.** Modules for the block parser (headings, paragraphs, lists, code blocks, block quotes), the inline parser (emphasis, code spans, links, escapes), the HTML renderer (with escaping), a small table-of-contents helper and a CLI (`python -m mdlite file.md`). The README states the supported syntax and the edge-case rules (for example how nested emphasis and list continuation behave). Those written rules are what some tasks rely on.

**`stockroom`: an inventory and pricing module.** Modules for the catalog (SKUs, units), stock movements (receive, ship, adjust, with a ledger), pricing (list price, tiered and percentage discounts, tax, `Decimal` rounding rules), orders (reserve, fulfil, cancel), and CSV import and export. The README states the business rules: rounding mode, the order in which discounts and tax apply, what happens on over-shipping. Those written rules are what some tasks rely on.

Each repo is written to be read by an agent: clear module boundaries, docstrings, and enough code between cause and symptom that a task can span modules. Neither repo contains planted bugs at base; bugs are introduced by a task's `plant/` overlay, as today.

## 4. Task set

**IDs:** `md-NNN` and `sr-NNN` for dev tasks, `md-hNN` and `sr-hNN` for held-out tasks. `taskcli` keeps `tc-001` to `tc-005`.

| | Bugs | Features | Refactors | Traps | Total |
|---|---|---|---|---|---|
| Existing, `taskcli`, dev | 2 | 1 | 1 | 1 | 5 |
| New, dev | 4 | 4 | 1 | 1 | 10 |
| New, held out | 2 | 1 | 1 | 1 | 5 |
| **Total** | **8** | **6** | **3** | **3** | **20** |

The new tasks are split about evenly between the two repos. Difficulty for the new tasks: about 2 easy, 7 medium and 6 hard, spread across dev and held out.

**What makes a task hard.** Every medium or hard task uses at least one of these, recorded in its `task.yaml` as `levers` (documentation only; nothing scores on it):

- `multi_file`: the correct fix changes two or three modules.
- `distant_symptom`: the issue describes a symptom in one module; the cause is in another.
- `doc_rule`: the correct behaviour is stated in the repo's README or docstrings, not in the issue.
- `regression_risk`: hidden tests cover neighbouring behaviour that an obvious fix breaks.
- `shortcut`: a narrow change passes the visible tests but not the hidden ones (the tempting tasks below).

**Tempting tasks.** Two dev tasks are marked `tempting: true`. Each carries a `shortcut/` overlay: a plausible narrow patch that makes the visible tests pass and fails the hidden ones. `bench validate` proves that it does (§6). These are the tasks where a reviewer should be able to earn its cost.

**Traps.** The two new traps must look actionable: an issue whose requirements contradict each other or the README, or one that needs a product decision the repo cannot answer. They are resolved only by declining, as today.

**Issue text.** Written like real issues: a symptom, a reproduction, the expected behaviour. Never the fix, never a file name the reporter would not know. Hidden tests check only what the issue and the repo's written rules require.

## 5. Task format changes

`task.yaml` gains two optional fields:

- `tempting: bool` (default `false`). When true, the task directory must contain `shortcut/`.
- `levers: list[str]` from the five names above (default empty).

`TaskSpec` gains both fields. Rows written by `bench.matrix` gain `repo`, `difficulty` and `split`, so the report can break results down without reading task files.

## 6. Validation

`bench validate` keeps its current checks and adds:

- For `tempting: true`: visible tests pass at base + plant + shortcut, and hidden tests fail there.
- `levers` contains only the five known names; `tempting: true` implies `shortcut` is among them.
- Every non-trap task's hidden tests fail at base + plant (already checked) and at least one hidden test file exists.
- Output for a held-out task stays one line: `ok` or the problem, never file contents.

## 7. Held-out tasks: sealed

- A separate subagent (the sealed author) writes the five held-out tasks from a brief that fixes only repo, category, difficulty and levers. It works from the committed repos and may read the dev tasks as examples.
- A second subagent (the sealed checker) reads each held-out task and answers only: is the issue fair, do the hidden tests test what the issue and the repo's rules say, does the trap really need declining. It reports pass or fail per task ID with no quotes from the task.
- The main session never opens, prints or diffs anything under a held-out task directory. Commits that add them are made with `git add` on the directory, checked only with `git status` and `bench validate`. Review diffs given to non-sealed reviewers exclude those directories.
- Held-out tasks are not run in Week 2B. They run once, at the end of the project, after any prompt tuning, at a commit recorded before the run. `bench run` already refuses them without `--confirm-heldout`.

## 8. Pilot on Flash

- After a dev task validates, run it once: `bench run --tasks <id> --system single --preset flash --repeats 1 --out results/pilot`.
- Target: the single agent resolves 5 to 7 of the 10 new dev tasks. The five `taskcli` tasks are not piloted again.
- If more than 7 resolve, harden the easiest resolved tasks (add a lever, move the cause further from the symptom) or replace them. If fewer than 5, soften or replace the hardest. At most two rounds.
- Every change is logged in `docs/results/<date>-2b-pilot-log.md`: task, what the pilot showed, what was changed and why. A task is never changed because of how the multi-agent system does, and the multi-agent system is not run until §10.
- Estimated cost: $3 to $5.

## 9. Report changes

`bench.report` adds two tables in the same shape as "By category": by repo and by difficulty. Nothing else changes.

## 10. The final Week 2B run

- Dev split only (15 tasks), both systems, Flash, three repeats, concurrency 3: 90 runs.
- Estimated $20 to $30; stop and report if the total passes $40. Needs the owner's explicit approval at the time.
- Prompts are untouched throughout Week 2B. Pro or mixed presets only if the Pro reviewer hang has been diagnosed first.
- Reported like the Week 2A runs: generated tables, a hand-written "What happened", every resolved patch read, a run log with costs.

## 11. Build order

1. Code changes (§5, §6, §9) with tests. Small; one task.
2. The two repos, built in parallel by two subagents, each reviewed.
3. Dev tasks per repo, written by one subagent per repo, each task validated and reviewed. Pilot as they land.
4. Held-out tasks: sealed author, then sealed checker.
5. Pilot rounds and the pilot log.
6. Final run (approval), report, README.

## 12. Out of scope

Prompt tuning, GitHub live mode, the human approval step, quality evals (all Plan 2C), public GitHub copies of the repos, the held-out run, and the Pro diagnosis (a separate spike if the owner wants it).

## 13. Risks

- **Tasks too hard for both systems.** The pilot target guards against that for the single agent; the multi-agent system could still fail more. That would be a result, not a defect.
- **Leaked held-out content.** Guarded by §7. If the main session sees a held-out task by accident, that task is replaced by the sealed author, and the replacement is logged.
- **Hidden tests that test more than the issue asks.** The task reviewer and the sealed checker both check for it; `doc_rule` tasks must point to text that exists in the repo at base.
- **The model has seen the answer.** Unlikely: the repos and tasks are new and not published.
