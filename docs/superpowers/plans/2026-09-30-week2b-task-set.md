# Week 2B: Task Set — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a benchmark task set with headroom (two new repositories, 15 new tasks, a sealed held-out split), calibrate its difficulty on Flash, and run one dev-split comparison of the two systems.

**Architecture:** Two new stdlib-only Python repositories under `bench/repos/`, each written to be read by an agent, with numbered rules in its README. Tasks use the existing overlay format (`plant/`, `solution/`, `hidden_tests/`) plus a `shortcut/` overlay for tempting tasks, checked by an extended `bench validate`. Held-out tasks are written and checked by subagents the main session never reads. Result rows gain `repo`, `difficulty` and `split` so the report can break results down.

**Tech Stack:** Python 3.12, `uv`, pytest, Docker (sandbox image `issue-to-pr-sandbox:dev`), google-adk 2.8.x (unchanged), model `gemini-3.8-flash`.

**Spec:** `docs/superpowers/specs/2026-09-30-week2b-task-set-design.md` (this plan implements it), parent spec `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` §9.1.

## How to read the tasks

As in Week 2A, this plan fixes what must be exact: names, fields, IDs, commands, validation rules, and the tests that must exist. The implementation and the content of repositories and tasks are the implementer's, test-first where there is code. Content tasks (repositories, bench tasks) have acceptance checks instead of unit tests.

## Global Constraints

- Python 3.12 via `uv` only (`uv run ...`). Never call `pip`. Repositories under `bench/repos/` use the standard library only.
- Work on branch `week2b-task-set`. Never commit to `main` directly.
- Prompts, agents, graph edges and model names are frozen for all of Week 2B: do not edit `app/agents/`, `app/pipeline.py`, `app/baseline.py`, `app/models.py`.
- Cost guards keep their defaults and apply to both systems: `RUN_BUDGET_USD=1.00`, `MAX_TOOL_CALLS_PER_RUN=75`, `RUN_TIMEOUT_S=1500`, `SANDBOX_TTL_S=1800`, 3 test-fix returns, 2 review returns.
- No credentials or network in any sandbox. Model-written code never runs on the host.
- Benchmark integrity: hidden tests are never in the agents' sandbox; never weaken a hidden test or a validation check; run `uv run python -m bench.validate` after touching `bench/repos` or `bench/tasks`.
- **Held-out tasks are sealed.** Only the sealed author and the sealed checker (Task 7) may open anything under `bench/tasks/*-h[0-9][0-9]/`. Every other agent and the main session never open, print, diff or `git show` those paths. Review diffs exclude them with the pathspec `':(exclude,glob)bench/tasks/*-h[0-9][0-9]/**'` (a pattern without `/**` matches only the directory name, not the files inside, and does not exclude them; check with `git diff --name-only <range> -- . '<pathspec>' | grep -c -- '-h[0-9][0-9]/'`, which must print 0).
- pytest never calls a real model or GCP. Only Tasks 6 and 8 do, and only with the owner's approval.
- Any step that spends credits is marked **[OWNER APPROVAL]**.
- Commits: plain messages, owner identity only, no `Co-Authored-By` or other AI attribution. Never bypass commit signing.
- Before each commit: `uv run ruff format <files> && uv run ruff check --fix <files>` (or `uvx ruff` if `uv run ruff` is unavailable), `agents-cli lint` passes (write its output to a file and check the exit status), bare `uv run pytest -q` passes.
- Update `AGENTS.md` in the same commit when a task adds fields, commands or rules.

## Review Focus

1. **A task marked `tempting: true` with no `shortcut/` directory, or a shortcut that does not actually tempt** (it fails the visible tests, or it passes the hidden ones). Expected: `bench validate` reports a one-line problem for that task and carries on with the others; it never crashes. Tests: Task 1 `test_tempting_task_without_shortcut_is_a_problem`, `test_shortcut_that_fails_visible_tests_is_a_problem`, `test_shortcut_that_passes_hidden_tests_is_a_problem`.
2. **A broken held-out task.** Expected: `bench validate` prints only the task ID and a fixed problem string, never test names, test output or file contents, so the main session can run it without breaking the seal. Test: Task 1 `test_validate_output_for_a_heldout_task_is_one_generic_line`.
3. **Result files from Week 2A** (no `repo`, `difficulty` or `split` on the rows). Expected: the report loads them and puts those rows under `unknown` in the new tables, without raising. Test: Task 1 `test_rows_without_repo_or_difficulty_load_as_unknown`.
4. **A repository whose visible tests pass on the host but not in the sandbox** (no network, read-only root file system, uid 1000, writes outside `/tmp` and `/workspace`, dependence on time zone, locale or wall clock). Expected: every repository's visible tests pass inside the sandbox image, twice in a row. Test: Tasks 2 and 3, `tests/integration/test_bench_repos_docker.py::test_repo_visible_tests_pass_in_the_sandbox[<repo>]`.
5. **A `levers` entry that is misspelled or false** (for example `multi_file` on a task whose reference solution changes one module). Expected: `bench validate` reports it. Tests: Task 1 `test_unknown_lever_is_a_problem`, `test_multi_file_lever_needs_two_changed_modules`.

---

## File Structure

```
app/
  task_store.py              MOD  TaskSpec.tempting, TaskSpec.levers, LEVERS; materialize(with_shortcut=...)
bench/
  validate.py                MOD  shortcut, lever and held-out output rules
  matrix.py                  MOD  rows carry repo, difficulty, split
  report.py                  MOD  "By repo" and "By difficulty" tables; defaults for old rows
  repos/mdlite/              NEW  Markdown-to-HTML library (Task 2)
  repos/stockroom/           NEW  inventory and pricing module (Task 3)
  tasks/md-001..md-005/      NEW  dev tasks, mdlite (Task 4)
  tasks/sr-001..sr-005/      NEW  dev tasks, stockroom (Task 5)
  tasks/md-h01, md-h02, sr-h01, sr-h02, sr-h03/   NEW  held-out tasks, sealed (Task 7)
tests/
  unit/test_task_store.py    MOD
  unit/test_validate.py      NEW  validator rules on synthetic tasks
  unit/test_bench_tasks.py   MOD  the real task set's shape
  unit/test_matrix.py        MOD
  unit/test_report.py        MOD
  integration/test_bench_repos_docker.py   NEW  repo visible tests inside the sandbox image
docs/results/
  <date>-2b-pilot-log.md     NEW  every pilot run and every task change (Task 6)
  <date>-2b-comparison.md    NEW  the final dev-split report (Task 8)
```

## The task table

Fixed by this plan. Authors choose the content; they do not change IDs, repos, categories, difficulty, split, `tempting` or `levers`.

| ID | Repo | Category | Difficulty | Split | Tempting | Levers |
|---|---|---|---|---|---|---|
| md-001 | mdlite | bug | medium | dev | no | distant_symptom |
| md-002 | mdlite | bug | hard | dev | yes | shortcut, regression_risk |
| md-003 | mdlite | feature | medium | dev | no | doc_rule |
| md-004 | mdlite | feature | hard | dev | no | multi_file |
| md-005 | mdlite | trap | medium | dev | no | (none) |
| sr-001 | stockroom | bug | easy | dev | no | (none) |
| sr-002 | stockroom | bug | hard | dev | yes | shortcut, doc_rule |
| sr-003 | stockroom | feature | medium | dev | no | multi_file |
| sr-004 | stockroom | feature | hard | dev | no | multi_file, regression_risk |
| sr-005 | stockroom | refactor | medium | dev | no | regression_risk |
| md-h01 | mdlite | bug | hard | heldout | no | distant_symptom, regression_risk |
| md-h02 | mdlite | refactor | medium | heldout | no | regression_risk |
| sr-h01 | stockroom | bug | medium | heldout | no | doc_rule |
| sr-h02 | stockroom | feature | hard | heldout | no | multi_file |
| sr-h03 | stockroom | trap | easy | heldout | no | (none) |

Totals with `tc-001` to `tc-005`: 8 bugs, 6 features, 3 refactors, 3 traps; 15 dev, 5 held out; new tasks 2 easy, 7 medium, 6 hard.

---

### Task 1: Harness support for tempting tasks, levers and breakdowns

**Files:**
- Modify: `app/task_store.py`, `bench/validate.py`, `bench/matrix.py`, `bench/report.py`, `AGENTS.md`
- Create: `tests/unit/test_validate.py`
- Modify tests: `tests/unit/test_task_store.py`, `tests/unit/test_matrix.py`, `tests/unit/test_report.py`

**Interfaces:**
- Produces:
  - `app.task_store.LEVERS: frozenset[str]` = `{"multi_file", "distant_symptom", "doc_rule", "regression_risk", "shortcut"}`.
  - `TaskSpec.tempting: bool = False`, `TaskSpec.levers: list[str] = []` (plain strings; the validator, not the model, rejects unknown names, so one bad task never stops `list_tasks()`).
  - `materialize(task, dest, *, with_solution=False, with_hidden_tests=False, with_shortcut=False) -> Path`. `with_shortcut` overlays `shortcut/` after `plant/`. `with_solution` and `with_shortcut` together raise `ValueError`.
  - `bench.validate.validate_task(task) -> list[str]` keeps its signature; problems are fixed strings (below), never test output.
  - Rows from `bench.matrix` gain `repo`, `difficulty`, `split` (from the `TaskSpec`).
  - `bench.report.Summary` gains `by_repo` and `by_difficulty` with the same shape as `by_category`. `load_rows` fills a missing `repo`, `difficulty` or `split` with `"unknown"`.

**Required behaviour**

1. New validator problems, each a fixed string:
   - `"tempting task has no shortcut/"`
   - `"shortcut/ given but tempting is false"`
   - `"shortcut fails the visible tests"` (base + plant + shortcut)
   - `"shortcut passes the hidden tests"` (base + plant + shortcut)
   - `"unknown lever: <name>"` (the name is the task author's metadata, not task content)
   - `"tempting tasks must list the shortcut lever"`
   - `"multi_file needs a solution that changes two or more modules"`: count `.py` files under `solution/` that are not test files (use `test_files` logic); fewer than two is a problem. (This check goes one step beyond the design's §6, to keep the lever honest; it is the only lever that can be checked mechanically.)
   - `"non-trap tasks need at least one hidden test file"`
2. `main()` output stays one line per task: `<id>: ok` or `<id>: <problems joined by "; ">`. No pytest output is ever printed (`bench/_pytest.py::run_pytest` already captures it; keep it that way).
3. Report: two new tables after "By category", titled "By repo" and "By difficulty", with the same header sentence and cell format (`resolved / counted`, all repeats pooled, infra and crashed runs excluded). Difficulty rows in the order easy, medium, hard, unknown.
4. `AGENTS.md`: document the two `task.yaml` fields and the `shortcut/` overlay in the Runtime wiring section (one bullet), and add to the Benchmark integrity hard rule: "Held-out task directories (`bench/tasks/*-h[0-9][0-9]/`) are sealed: never open, print or diff them; exclude them from review diffs."

**Tests** (synthetic tasks built with `tests/fakes.py::make_bench_task` or a sibling helper that adds `shortcut/`; no real repos)

- `test_task_store.py`: `test_tempting_and_levers_default_to_false_and_empty`; `test_materialize_with_shortcut_overlays_after_plant`; `test_materialize_with_solution_and_shortcut_is_an_error`.
- `test_validate.py`: `test_tempting_task_without_shortcut_is_a_problem`; `test_shortcut_without_tempting_is_a_problem`; `test_shortcut_that_fails_visible_tests_is_a_problem`; `test_shortcut_that_passes_hidden_tests_is_a_problem`; `test_a_real_shortcut_is_ok` (visible pass, hidden fail → no problem); `test_unknown_lever_is_a_problem`; `test_tempting_needs_the_shortcut_lever`; `test_multi_file_lever_needs_two_changed_modules` (one module → problem, two → ok); `test_non_trap_without_hidden_test_files_is_a_problem`; `test_validate_output_for_a_heldout_task_is_one_generic_line` (a held-out synthetic task whose hidden tests do not fail at base+plant; capture `main()` output; exactly one line for that task, it names only the ID and fixed strings, and no hidden test name or source text appears in it).
- `test_matrix.py`: `test_rows_carry_repo_difficulty_and_split` (including crashed rows).
- `test_report.py`: `test_by_repo_and_by_difficulty_tables`; `test_rows_without_repo_or_difficulty_load_as_unknown` (Week 2A-shaped rows).

- [ ] **Step 1:** Write the task-store and validator tests; run them and capture the failures (RED).
- [ ] **Step 2:** Implement the task-store and validator changes; run to green.
- [ ] **Step 3:** Write the matrix and report tests (RED); implement; green.
- [ ] **Step 4:** Update `AGENTS.md`. `uv run python -m bench.validate` still prints `ok` for `tc-001` to `tc-005`. Lint, full suite.
- [ ] **Step 5:** Commit: `feat: tempting tasks, levers and per-repo and per-difficulty results`.

---

### Task 2: Repository `mdlite`

**Files:**
- Create: `bench/repos/mdlite/` (package `mdlite/`, `tests/`, `README.md`)
- Create: `tests/integration/test_bench_repos_docker.py` (Task 3 adds its repo to the same parametrised test)

**What it is:** a Markdown-to-HTML library. Public API: `mdlite.to_html(text: str) -> str`, `mdlite.toc(text: str) -> list[tuple[int, str, str]]` (level, text, slug), and `python -m mdlite <file>` printing HTML.

**Required**

1. Modules, each with one job and docstrings: `nodes.py` (AST dataclasses), `blocks.py` (block parser: ATX headings, paragraphs, unordered and ordered lists with one level of nesting, fenced code blocks, block quotes, thematic breaks), `inline.py` (emphasis, strong, code spans, links with optional title, autolinks, backslash escapes, hard line breaks), `render.py` (AST to HTML), `escape.py` (HTML escaping and URL checks), `slug.py` (heading slugs and duplicate handling), `toc.py`, `cli.py`, `__init__.py`, `__main__.py`. The inline parser is used by both the renderer and the table of contents, so a change in one place has effects in another.
2. Size: 900 to 1,400 lines in the package, 300 to 700 lines of tests; at least 40 visible test functions; the whole suite runs in under 10 seconds.
3. `README.md` has a "Rules" section with at least 10 numbered rules (`R1` to `Rn`), each stated precisely enough to test, for example how slugs are built and deduplicated, which characters are escaped where, which URL schemes are rendered as links, how list items continue across lines, and what code spans do to emphasis. Every rule is covered by at least one visible test that names the rule in its docstring.
4. Correct at base: no known bugs, no TODOs, no dead code. Deterministic: no clock, randomness, locale, time zone, network or environment variables. Tests write only under `tmp_path`.
5. `tests/integration/test_bench_repos_docker.py::test_repo_visible_tests_pass_in_the_sandbox[mdlite]` (docker-marked): copy the repo into a fresh sandbox (through `DockerEnvironment`, as the scorer does), run `python -m pytest -q tests` twice, both exit 0.

**Acceptance checks** (the reviewer runs them): line counts; test count; `uv run pytest bench/repos/mdlite/tests -q` passes on the host; the docker test passes; every README rule has a test; `agents-cli lint` passes with the new repo (if the linters flag stylistic issues in `bench/repos`, fix the code, do not exclude the directory).

- [ ] **Step 1:** Write the README rules first, then the visible tests for them.
- [ ] **Step 2:** Write the package until the tests pass.
- [ ] **Step 3:** Write the docker test; run it.
- [ ] **Step 4:** Line and test counts; lint; full suite.
- [ ] **Step 5:** Commit: `feat(bench): mdlite demo repository`.

---

### Task 3: Repository `stockroom`

**Files:**
- Create: `bench/repos/stockroom/` (package `stockroom/`, `tests/`, `README.md`)
- Modify: `tests/integration/test_bench_repos_docker.py` (add `stockroom` to the parameters)

**What it is:** an inventory and pricing module. Public API centred on `stockroom.Store` (catalog, inventory, pricing, orders) plus `stockroom.csvio.import_products(path)` and `export_stock(store, path)`.

**Required**

1. Modules, each with one job and docstrings: `money.py` (`Decimal` helpers and the one rounding function), `catalog.py` (SKU, product, unit), `ledger.py` (append-only stock movements: receive, ship, adjust, with reasons), `inventory.py` (on hand, reserved, available, derived from the ledger), `pricing.py` (list price, tiered quantity discounts, percentage discounts with caps, tax), `orders.py` (create, reserve, fulfil, cancel), `csvio.py`, `errors.py`, `store.py` (the facade), `__init__.py`. Money flows through `money.py` everywhere, so a rounding decision in one place shows up in several.
2. Size: 900 to 1,400 lines in the package, 300 to 700 lines of tests; at least 40 visible test functions; under 10 seconds.
3. `README.md` "Rules" section with at least 10 numbered rules, for example: the rounding mode and when rounding happens, the order in which discounts and tax apply, how tiers are chosen, the maximum combined discount, what over-shipping and over-reserving do, what cancelling a partly fulfilled order does, the CSV column set and how bad rows are reported. Every rule covered by a visible test naming it.
4. Same correctness and determinism rules as Task 2. Money is `Decimal` only; floats never touch prices.
5. The docker test passes for `stockroom`.

**Acceptance checks:** as Task 2.

- [ ] **Step 1:** README rules, then visible tests.
- [ ] **Step 2:** Package until green.
- [ ] **Step 3:** Docker test parameter; run it.
- [ ] **Step 4:** Counts; lint; full suite.
- [ ] **Step 5:** Commit: `feat(bench): stockroom demo repository`.

Tasks 2 and 3 are independent and may run in parallel; they touch different directories, except the one parametrised docker test file, which Task 3 edits after Task 2 lands (or the controller merges the two parameter lists).

---

### Task 4: Dev tasks for `mdlite` (md-001 to md-005)

**Files:**
- Create: `bench/tasks/md-001/` to `bench/tasks/md-005/` (`task.yaml`, and as the category needs: `plant/`, `solution/`, `hidden_tests/`, `shortcut/`)
- Modify: `tests/unit/test_bench_tasks.py` (the expected task list)

**Consumes:** Task 1's fields and validator; Task 2's repository (read-only: tasks never change `bench/repos/`).

**Required for every task** (IDs and metadata from the task table)

1. `task.yaml`: `repo`, `title`, `body`, `category`, `difficulty`, `split: dev`, `tempting` when true, `levers`.
2. The issue reads like a real one: symptom, reproduction, expected behaviour. It never names the fix, the faulty function, or a file the reporter would not know. It may point to a README rule by number only for `doc_rule` tasks when a real user would; otherwise the rule must be discoverable in the repo.
3. `plant/` (bugs): the smallest realistic change a developer could have made, on a correct base, that leaves every visible test passing.
4. `solution/`: the reference fix, written as a maintainer would.
5. `hidden_tests/`: self-contained (no repo conftest), public API only, at least three test functions, and they check only what the issue and the README rules require: a different correct fix must pass them.
6. Levers must be true: `distant_symptom` means the issue describes behaviour in one module and the fix is in another; `doc_rule` means the correct behaviour is stated in the README or a docstring at base and not in the issue; `multi_file` means the reference solution changes two or more package modules; `regression_risk` means at least one hidden test covers neighbouring behaviour that the most obvious fix breaks; `shortcut` means `shortcut/` holds a plausible narrow patch that passes the visible tests and fails the hidden ones.
7. `md-005` (trap): an issue that looks actionable but contradicts a README rule or itself, or needs a product decision the repo cannot answer. No `solution/` or `hidden_tests/`. Declining is the only correct outcome, and the issue must not say so.
8. Every task: `uv run python -m bench.validate` prints `ok`.

**Acceptance checks** (the reviewer judges these by reading, and runs the validator): items 2 to 7 for each task; for the tempting task, the reviewer also confirms the shortcut is something a hurried developer would really write.

- [ ] **Step 1:** Write `md-001` to `md-005` one at a time; validate each as it is written.
- [ ] **Step 2:** Update `tests/unit/test_bench_tasks.py` so the expected IDs include the new ones; full suite.
- [ ] **Step 3:** Commit: `feat(bench): mdlite dev tasks md-001 to md-005`.

---

### Task 5: Dev tasks for `stockroom` (sr-001 to sr-005)

**Files:** `bench/tasks/sr-001/` to `bench/tasks/sr-005/`; `tests/unit/test_bench_tasks.py`.

**Consumes:** Task 1, Task 3.

**Required:** the same nine items as Task 4, applied to the `sr-` rows of the task table. `sr-005` (refactor): the issue asks for a behaviour-preserving restructuring with a concrete, testable outcome (for example a new public function or class that other modules must use); hidden tests pin both the new interface and the unchanged behaviour. `sr-001` (easy) has no lever and is meant to be solvable by both systems.

- [ ] **Step 1:** Write `sr-001` to `sr-005`; validate each.
- [ ] **Step 2:** Update `tests/unit/test_bench_tasks.py`; full suite.
- [ ] **Step 3:** Commit: `feat(bench): stockroom dev tasks sr-001 to sr-005`.

Tasks 4 and 5 may run in parallel after Tasks 1 to 3 land; they touch different task directories and both edit the same expected-ID list, which the controller merges.

---

### Task 6: Pilot on Flash **[OWNER APPROVAL]**

Controller task (it calls a real model). Estimated $3 to $5.

**Files:**
- Create: `docs/results/<date>-2b-pilot-log.md`, `results/pilot/*.json`

- [ ] **Step 1:** Dry check: `uv run python -m bench.validate`, `uv run pytest -q`, `make sandbox-image`.
- [ ] **Step 2 [OWNER APPROVAL]:** Round 1:

  ```
  TRACE_TO_CLOUD=1 uv run python -m bench.run --tasks md-001,md-002,md-003,md-004,md-005,sr-001,sr-002,sr-003,sr-004,sr-005 --system single --preset flash --repeats 1 --concurrency 3 --out results/pilot
  ```
- [ ] **Step 3:** Log every row in the pilot log: resolved or not, failure kind, cost, and one line on why (from the event log and the patch). Read every patch.
- [ ] **Step 4:** If 5 to 7 resolved, stop. Otherwise pick changes by the rules in the design (§8): harden or replace the easiest resolved tasks if more than 7 resolved, soften or replace the hardest if fewer than 5. Hand each change to the task's author as a fix brief; each changed task is re-validated and re-reviewed. Log every change and its reason. Never look at, or run, the multi-agent system.
- [ ] **Step 5 [OWNER APPROVAL]:** Round 2, only the changed tasks, same command with `--tasks` limited to them. Log the result. No round 3: whatever round 2 gives is reported as is.
- [ ] **Step 6:** Commit: `docs: 2B pilot log`.

---

### Task 7: Held-out tasks (sealed)

The main session never reads this task's output. It dispatches two subagents and merges their commits by name only.

**Files:** `bench/tasks/md-h01/`, `md-h02/`, `sr-h01/`, `sr-h02/`, `sr-h03/`; `tests/unit/test_bench_tasks.py` (final shape).

**Sealed author** (one subagent, in its own worktree): writes the five held-out rows of the task table under the same nine rules as Task 4 (with `split: heldout`), may read `bench/repos/`, the dev tasks and this plan, and must not reuse a dev task's plant, feature or trap idea. Validates each task. Commits with the message `feat(bench): held-out tasks`. Its reply contains only: the commit SHA, the five `bench.validate` lines, and generic notes that quote nothing from the tasks.

**Sealed checker** (a different subagent): reads the five tasks and answers per ID, pass or fail, with a reason from this fixed list: `issue unfair or reveals the fix`, `hidden tests exceed the issue and rules`, `lever not true`, `trap does not need declining`, `duplicates a dev task`, `other (no quote)`. On a fail, the controller forwards the ID and reason, and nothing else, to the sealed author for a fix round; the checker checks again.

**Final task-set test** (the sealed author writes it; it names IDs and counts only): `tests/unit/test_bench_tasks.py::test_task_set_shape`: 20 tasks; 15 dev and 5 held out; categories 8, 6, 3, 3; exactly 2 tempting tasks, both dev; the held-out split has at least one task of each category; every task validates (the existing parametrised test).

- [ ] **Step 1:** Dispatch the sealed author.
- [ ] **Step 2:** Dispatch the sealed checker; fix rounds until all five pass.
- [ ] **Step 3:** Controller: cherry-pick the author's commits quietly (`git cherry-pick <sha> >/dev/null`); run `uv run python -m bench.validate` (one line per task) and the full suite (`--tb=no`). Check only with counts (for example `git diff --name-only <base> HEAD -- bench/tasks | grep -c -- '-h[0-9][0-9]/'`) and with `bench.validate`, never with a listing of names.

---

### Task 8: Dev-split comparison **[OWNER APPROVAL]**

Controller task. Estimated $20 to $30; stop and report if the running total passes $40.

**Files:**
- Create: `docs/results/<date>-2b-comparison.md`, results under `results/`
- Modify: `README.md` ("Results so far"), `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` (§19), `AGENTS.md` (Status line)

- [ ] **Step 1:** Dry check as in Task 6. Whole-branch review first (with the held-out exclusion pathspec).
- [ ] **Step 2 [OWNER APPROVAL]:** One invocation at a time, each started directly (not as a background job inside a script), output to `runs/logs/`:

  ```
  TRACE_TO_CLOUD=1 uv run python -m bench.run --split dev --system multi  --preset flash --repeats 3 --concurrency 3
  TRACE_TO_CLOUD=1 uv run python -m bench.run --split dev --system single --preset flash --repeats 3 --concurrency 3
  ```
- [ ] **Step 3:** Report from the two result files: `uv run python -m bench.report results/<multi>.json results/<single>.json --out docs/results/<date>-2b-comparison.md --title "Week 2B: dev-split comparison"`. Read every resolved patch and every failure's event log; add a hand-written "What happened" (results, what the planner and reviewer changed if anything, the tempting tasks, cost and time, what the run does not show). Run log with commands, times and costs.
- [ ] **Step 4:** README "Results so far" gains this run with its caveats; spec §19 records the 2B amendments (task set, fields, validator rules, sealed held-out); `AGENTS.md` Status line.
- [ ] **Step 5:** Commit: `docs: week 2B dev-split comparison`.

---

## Exit criteria

- `uv run pytest -q` and `agents-cli lint` pass; `bench.validate` prints `ok` for all 20 tasks.
- Both new repositories pass their visible tests inside the sandbox image.
- The pilot log shows the single agent resolved 5 to 7 of the 10 new dev tasks, or explains why round 2 did not get there.
- The five held-out tasks passed the sealed check, and the main session never opened them.
- One dev-split comparison is committed with its caveats.

## Not in this plan

Prompt tuning; the held-out run; Pro or mixed presets (unless the Pro reviewer hang is diagnosed first, as a separate spike); GitHub live mode, the human approval step and quality evals (Plan 2C); public GitHub copies of the repositories.
