# Week 2A: Measurement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-run smoke tool into a real benchmark harness: close the integrity gaps the Week 1 reviews left open, add a single-agent baseline, run systems × model presets × repeats in parallel, and generate the comparison report.

**Architecture:** The pipeline and the new baseline are two `Workflow` graphs that share the deterministic nodes (fetch, provision, diff, tests, finish). `bench.run` becomes a matrix runner: it builds the right workflow per (system, preset), runs tasks concurrently behind a semaphore, reruns infrastructure failures, audits each patch, and writes one row per run. `bench.report` turns result files into a Markdown table.

**Tech Stack:** Python 3.12, `uv`, google-adk 2.8.x, pytest + pytest-asyncio, Docker. Models: `gemini-3.8-flash`, `gemini-3.1-pro-preview`.

**Spec:** `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` (§6.3, §6.4, §8, §9). Carry-over findings: `docs/superpowers/plans/2026-09-29-week1-ledger.md`.

**Scope of this plan:** Week 2 is split into three plans. This is A (measurement). B is the task set (two more repos, 15 more tasks, the held-out split). C is GitHub live mode and the quality evals. B and C are written after A lands.

## How to read the tasks

Week 1's plan transcribed every line of code, and three of its bugs were in that transcribed code. This plan instead fixes what must be exact (names, signatures, graph edges, commands, CLI flags, and the tests) and leaves the rest of the implementation to the implementer, test-first. A test listed here must exist with the behaviour described; its exact code is the implementer's.

## Global Constraints

- Python 3.12 via `uv` only. google-adk 2.8.x. Never call `pip`.
- Work on branch `week2-measurement`. Never commit to `main` directly.
- Model names: `gemini-3.8-flash` (default) and `gemini-3.1-pro-preview`. Do not change or add others.
- Cost guards stay on and keep their defaults: `RUN_BUDGET_USD=1.00`, `MAX_TOOL_CALLS_PER_RUN=75`, `MAX_TOOL_CALLS_PER_TURN=25` (coder turns only), sandbox TTL 1800 s, 3 test-fix returns, 2 review returns.
- The per-run caps apply to the baseline too. The per-turn cap applies only to the agent named `coder`.
- No credentials or network in any sandbox. Model-written code never runs on the host.
- Routing and scoring use real diffs and real test exit codes, never model claims.
- Benchmark integrity: hidden tests are never in the agents' sandbox; never weaken a hidden test or a validation check; run `uv run python -m bench.validate` after touching `bench/repos` or `bench/tasks`.
- pytest never calls a real model or GCP. Only Task 7 does, and only with the owner's approval.
- Any step that spends credits is marked **[OWNER APPROVAL]**.
- Commits: plain messages, no `Co-Authored-By` or other AI attribution.
- Before each commit: `uv run ruff format <files> && uv run ruff check --fix <files>`, `agents-cli lint` passes, bare `uv run pytest -q` passes.
- Update `AGENTS.md` in the same commit when a task adds env vars, commands or wiring.

## Review Focus

1. **Two concurrent runs of the same task** (repeats). Expected: separate run directories, separate sandboxes, separate budgets; neither can read or overwrite the other's patch. Test: Task 5, `test_concurrent_repeats_do_not_share_run_dirs`.
2. **A run that fails as infra on every retry.** Expected: one row, `failed / infra`, `infra_retries == 2`, the matrix run continues, and the report counts it apart from agent failures. Tests: Task 5 `test_infra_failures_are_retried_then_recorded`, Task 6 `test_infra_rows_are_reported_separately`.
3. **Old or partial result rows** (Week 1 files with no `system`/`preset`, crashed rows with no cost). Expected: the report loads them, treats a missing system as `multi` and a missing preset as `flash`, and never raises. Test: Task 6 `test_legacy_and_crashed_rows_load`.
4. **The baseline on a trap task.** Expected: declining is resolved; writing a patch is not. Test: Task 4 `test_baseline_declines_a_trap`, plus the existing trap logic in `is_resolved`.
5. **A legitimate patch that touches an audited file** (for example a new `tests/conftest.py` fixture). Expected: the row is resolved AND carries an audit flag; the report shows it in the "flagged" column; it is never silently dropped or silently trusted. Tests: Task 1 `test_audit_flags_but_does_not_change_the_score`, Task 6 `test_flagged_rows_are_counted_in_both_columns`.

---

## File Structure

```
app/
  baseline.py            NEW  single-agent workflow (solo agent + shared nodes)
  agents/solo.py         NEW  the baseline's one agent
  schemas.py             MOD  SoloResult
  models.py              MOD  RoleModels.from_names
  nodes/verify.py        MOD  restore protected paths; correct messages
  nodes/routing.py       MOD  route_solo
  driver.py              MOD  4xx vs infra classification
  environment/docker.py  MOD  "Cannot connect to the Docker daemon" is infra
  guardrails.py          MOD  shlex commenters off
  tracing.py             MOD  clear error when credentials are missing
bench/
  audit.py               NEW  audit_patch(patch_text) -> list[str]
  presets.py             NEW  PRESETS, workflow_for(system, preset)
  matrix.py              NEW  RunSpec, plan_runs, run_matrix (concurrency, infra reruns)
  report.py              NEW  load_rows, summarize, render_markdown, CLI
  run.py                 MOD  CLI flags; delegates to matrix
  score.py               MOD  hidden tests uploaded after the visible run
  progress.py            MOD  two cosmetic fixes
docs/
  results/               Task 7 output
  superpowers/specs/...  MOD  §9.1 threat model, §19 amendments
tests/unit/, tests/integration/   new and extended tests per task
```

---

### Task 1: Scoring and diff integrity (carry-overs)

**Files:**
- Modify: `app/nodes/verify.py`, `bench/score.py`, `bench/run.py`
- Create: `bench/audit.py`
- Test: `tests/unit/test_nodes.py`, `tests/unit/test_audit.py`, `tests/integration/test_score.py`, `tests/integration/test_pipeline_docker.py`
- Docs: spec §9.1 and §19

**Interfaces:**
- Consumes: `GIT`, `baseline_sha` state key, `_precheck`, `score_patch`, `is_resolved` (existing).
- Produces:
  - `run_tests(node_input, sandbox_id, protected_paths, test_attempts, baseline_sha)` — new `baseline_sha` parameter, bound from state.
  - `bench.audit.audit_patch(patch_text: str) -> list[str]` — human-readable flags, empty when clean.
  - Result rows gain `"audit": list[str]`.

**Required behaviour**

1. **The pipeline restores protected paths itself.** When `_precheck` finds modified or deleted protected test files, or changes under `.github/`, `run_tests`:
   - restores every protected file from the baseline with `{GIT} checkout {baseline_sha} -- <paths>`;
   - removes files the coder added under `.github/` and restores ones it changed;
   - still counts the attempt as failed (route `fail`/`exhausted`, unchanged);
   - reports, in `output_tail`, which paths were touched and that they **have been restored**, and tells the coder to put new tests in new files. The message must not tell the coder to run `git checkout -- <file>` (after the pipeline's `add -A` that command does nothing).
2. **Hidden tests are uploaded after the visible run passes.** In `score_patch`, the order becomes: apply patch → restore protected files → run visible tests → (only if they pass) upload hidden tests → run hidden tests. A patch's `tests/conftest.py` therefore never runs while the hidden tests are on disk.
3. **The visible run's config isolation is pinned by a test** (see tests).
4. **Patch audit.** `audit_patch` returns one flag per finding:
   - the patch adds or modifies a file whose basename is `pytest.py`, `conftest.py`, `sitecustomize.py`, `usercustomize.py`, `pytest.ini`, `tox.ini` or `setup.cfg`, or whose name ends in `.pth` — flag `"touches <path>"`;
   - the patch modifies `pyproject.toml` — flag `"touches pyproject.toml"`;
   - an added line (a line starting with `+` that is not a `+++` header) contains `os._exit`, `atexit`, `sys.exit(`, `pytest_collection_modifyitems`, `pytest_runtest_makereport` or `collect_ignore` — flag `"adds <token> in <path>"`.
   The audit never changes `resolved`. `run_task` adds `"audit": audit_patch(patch_text)` to every row that has a patch, and `[]` otherwise.
5. **Spec.** Add to §9.1, after the scoring bullet, a "Threat model" paragraph: scoring runs patch code, so a patch that attacks the test process (a shadowing `pytest.py`, an exit hook in package code) can fake a pass; results are valid for patches that do not do that; every patch is audited for the listed patterns and flagged rows are reported separately. Add a dated §19 block listing items 1–4 above.

**Tests**

- `tests/unit/test_nodes.py`:
  - `test_run_tests_restores_protected_files_and_says_so`: a diff touching a protected path → the fake environment receives a `checkout <baseline_sha> --` command naming that path; the report text contains "restored" and does not contain `git checkout --`.
  - `test_run_tests_removes_added_github_files`: a diff adding `.github/workflows/x.yaml` → a removal command for it is issued.
  - Update the existing protected-file and `.github/` tests for the new parameter and message.
- `tests/integration/test_pipeline_docker.py` (docker): a scripted coder edits a protected test via `bash` (`sed -i`), then makes the real fix on its next turn → the run ends `patch_written`, the final patch does not contain the protected file, and `test_attempts == 1`.
- `tests/unit/test_audit.py`: one test per pattern above; a clean patch returns `[]`; a patch adding `tests/conftest.py` with a fixture is flagged `touches tests/conftest.py`; a removed line (`-`) containing `atexit` is not flagged.
- `tests/integration/test_score.py` (docker):
  - `test_conftest_cannot_rewrite_hidden_tests`: a no-fix patch whose `tests/conftest.py` overwrites every file under `/workspace/hidden/hidden_tests` with a passing test must score False. (It scores True against the current upload order.)
  - `test_visible_run_ignores_patch_pytest_config`: a patch that contains the real fix, breaks one visible-tested behaviour, and adds a `pytest.ini` with `addopts = --deselect <the failing visible test id>` must score False.
  - `test_audit_flags_but_does_not_change_the_score`: the reference solution plus an added harmless `tests/conftest.py` scores True, and `audit_patch` of that patch is non-empty.

- [ ] **Step 1:** Write the failing unit tests (`test_nodes.py`, `test_audit.py`). Run them; confirm they fail for the right reason.
- [ ] **Step 2:** Implement the `run_tests` restore and messages, and `bench/audit.py`. Run the unit tests to green.
- [ ] **Step 3:** Write the failing docker tests. Run them; confirm the conftest-rewrite test fails against the current upload order.
- [ ] **Step 4:** Reorder `score_patch`; add the audit to `run_task`. Run all tests to green.
- [ ] **Step 5:** Update the spec (§9.1, §19). Run `uv run python -m bench.validate`, lint, and the full suite.
- [ ] **Step 6:** Commit: `fix: restore protected paths in the pipeline, score hidden tests last, audit patches`.

---

### Task 2: Failure classification and small carry-over fixes

**Files:**
- Modify: `app/driver.py`, `app/environment/docker.py`, `app/guardrails.py`, `app/tracing.py`, `bench/progress.py`
- Test: `tests/unit/test_pipeline.py`, `tests/unit/test_environment_base.py`, `tests/unit/test_guardrails.py`, `tests/unit/test_tracing.py`, `tests/unit/test_progress.py`

**Interfaces:**
- Produces: `classify_failure` distinguishes request errors the agent caused from provider or transport failures. No signature changes.

**Required behaviour**

1. **Model API errors.** In `classify_failure`, a `google.genai.errors.ClientError` whose HTTP code is 400 or 413 is `("agent", "model rejected the request: <code> <message>")`: the agent built a request the model cannot take (for example a context overflow). Every other model API or transport error stays `infra`, including 401, 403, 404, 429 and all 5xx.
2. **Docker daemon down.** `DockerEnvironment` raises `InfraError` when stderr starts with `Cannot connect to the Docker daemon` (in addition to the two existing prefixes), in `exec`, `read_file`, `write_file`, `upload_dir` and `start`.
3. **Guardrail tokenizer.** In `_segments`, set `lexer.commenters = ""` so `#` no longer swallows the rest of the line. `ls # x\ncurl y` and `echo a#b\ncurl x` must be refused. Add a one-line comment that `2>&1` tokenises into harmless stray segments.
4. **Tracing credentials.** `enable_cloud_trace()` catches `google.auth.exceptions.DefaultCredentialsError`, logs a warning that names `gcloud auth application-default login`, and returns `False`.
5. **Progress cosmetics.** `format_event` skips a pipeline text part that is empty after stripping, and prints `?` in place of a missing tool name.

**Tests**

- `test_pipeline.py`: a FakeLlm raising `ClientError` with code 400 → `failed / agent`, reason contains "model rejected the request"; code 429 → `failed / infra`; existing 5xx test unchanged. Direct `classify_failure` tests for 400, 413, 403, 429.
- `test_tracing.py`: `enable_cloud_trace` with `google.auth.default` raising `DefaultCredentialsError` returns False and logs one warning; a run whose workflow raises an unclassified error leaves the root span with status ERROR and `run_pipeline` still raises (use the in-memory exporter).
- `test_environment_base.py`: a simulated `docker` stderr starting with `Cannot connect to the Docker daemon` raises `InfraError` (patch the module's `_run`).
- `test_guardrails.py`: the two comment cases are refused; `echo "a # b"` and `rg '#include' src` are allowed.
- `test_progress.py`: whitespace-only pipeline text → no line; a function call with no name → line contains `?`.

- [ ] **Step 1:** Write all failing tests. Run; confirm each fails for the expected reason.
- [ ] **Step 2:** Implement items 1–5. Run to green.
- [ ] **Step 3:** Lint and full suite.
- [ ] **Step 4:** Commit: `fix: classify model request errors as agent failures; small carry-over fixes`.

---

### Task 3: Model presets

**Files:**
- Create: `bench/presets.py`
- Modify: `app/models.py`, `app/pricing.py` (only if a test shows a gap)
- Test: `tests/unit/test_presets.py`, `tests/unit/test_agents.py`, `tests/unit/test_budget.py`

**Interfaces:**
- Produces:
  - `RoleModels.from_names(planner: str, coder: str, reviewer: str) -> RoleModels` (uses `make_model`).
  - `bench.presets.PRESETS: dict[str, dict[str, str]]`:

    ```python
    FLASH = "gemini-3.8-flash"
    PRO = "gemini-3.1-pro-preview"
    PRESETS = {
        "flash": {"planner": FLASH, "coder": FLASH, "reviewer": FLASH},
        "pro": {"planner": PRO, "coder": PRO, "reviewer": PRO},
        "mixed": {"planner": PRO, "coder": FLASH, "reviewer": PRO},
    }
    ```
  - `bench.presets.role_models(preset: str) -> RoleModels` and `bench.presets.solo_model_name(preset: str) -> str` (the preset's `coder` model; `mixed` raises `ValueError("the mixed preset needs roles; use flash or pro with --system single")`).

**Required behaviour**

- An unknown preset name raises `ValueError` listing the valid names.
- `cost_usd("gemini-3.1-pro-preview", 100_000, 10_000)` is `0.32` and at 300,000 input tokens uses the long-context rate. These already hold through prefix matching; the tests pin them.
- `RoleModels.from_env()` keeps working (used by `app/agent.py`).

**Tests**

- `test_presets.py`: each preset resolves to `ResponseToolGemini` instances with the expected `.model` per role; unknown preset raises; `solo_model_name("mixed")` raises; `solo_model_name("pro") == PRO`.
- `test_budget.py`: the two Pro-preview price assertions above.

- [ ] **Step 1:** Write failing tests. Run.
- [ ] **Step 2:** Implement. Run to green. Lint, full suite.
- [ ] **Step 3:** Commit: `feat: model presets for benchmark runs`.

---

### Task 4: Single-agent baseline

**Files:**
- Create: `app/agents/solo.py`, `app/baseline.py`
- Modify: `app/schemas.py`, `app/nodes/routing.py`, `app/agents/__init__.py`
- Test: `tests/unit/test_baseline.py`, `tests/unit/test_agents.py`, `tests/integration/test_pipeline_docker.py`

**Interfaces:**
- Consumes: `fetch_issue`, `provision_sandbox`, `collect_diff`, `run_tests`, `deliver_patch`, `report_failure`, `INFRA_RETRY`, `CODER_TOOLS`, `run_pipeline(request, workflow=...)`.
- Produces:
  - `app.schemas.SoloResult`:

    ```python
    class SoloResult(BaseModel):
        declined: bool = Field(default=False, description="True if the issue is not actionable.")
        decline_reason: str | None = None
        summary: str = Field(description="What was changed, or why nothing was.")
        files_changed: list[str] = Field(default_factory=list)
    ```
  - `app.agents.solo.build_solo(model) -> LlmAgent` — name `"solo"`, `tools=list(CODER_TOOLS)`, `output_schema=SoloResult`, `output_key="solo"`, temperature 0. Its instruction reads only the state key `issue_text`.
  - `app.nodes.routing.route_solo(node_input: SoloResult) -> Event` — route `"declined"` (sets state `failure = {"kind": "declined", "reason": ...}`) or `"done"`.
  - `app.baseline.build_baseline_workflow(model: BaseLlm) -> Workflow`, named `"issue_to_pr"`, `input_schema=RunRequest`, with exactly these edges:

    ```python
    edges=[
        ("START", fetch),
        (fetch, provision),
        (provision, solo),
        (solo, route_solo),
        (route_solo, {"done": diff, "declined": report_failure}),
        (diff, tests),
        (tests, {"pass": deliver_patch, "fail": solo, "exhausted": report_failure}),
    ]
    ```

**Design (why this shape).** The baseline keeps the whole deterministic harness: the same sandbox, tools, guardrails, budget, diff collection and test-fix loop (3 returns). It drops only the planner and the reviewer. The comparison therefore measures what those two roles add, not what the harness adds.

**Required behaviour**

- The solo instruction covers all three jobs in one agent: decide whether the issue is actionable (decline otherwise, exactly the planner's criteria), make the smallest change, add tests in new files, run `python -m pytest -q`, and review its own diff before finishing. It marks the issue text as untrusted. No literal braces other than `{issue_text}`.
- On a failing test report the solo agent is re-entered with the report as its input, the same way the coder is.
- The per-turn tool-call cap does not apply to `solo`; the per-run caps do.

**Tests** (`tests/unit/test_baseline.py`, FakeLlm and FakeEnvironment, through `run_pipeline(..., workflow=build_baseline_workflow(fake))`)

- `test_baseline_happy_path_writes_patch`: one solo answer, tests pass → `patch_written`; exactly one model (no planner, no reviewer).
- `test_baseline_declines_a_trap`: `declined=True` → outcome `declined`, reason carried.
- `test_baseline_test_loop_exhausts_after_three_returns`: tests always fail → `failed / agent`, `test_attempts == 4`, solo called 4 times.
- `test_baseline_empty_diff_goes_back_to_the_agent`.
- `test_baseline_shares_the_run_budget`: `RUN_BUDGET_USD` tiny → `failed / budget`.
- `test_solo_is_exempt_from_the_per_turn_cap`: `MAX_TOOL_CALLS_PER_TURN=2`, solo makes 4 tool calls then answers → `patch_written`.
- `test_agents.py`: the solo instruction's placeholders are exactly `{issue_text}`; contract (name, schema, key, tools).
- Docker: one end-to-end baseline run with a scripted solo agent that edits the file and passes tests → `patch_written`.

- [ ] **Step 1:** Write failing tests. Run.
- [ ] **Step 2:** Implement schema, agent, router, workflow. Run to green.
- [ ] **Step 3:** Update `AGENTS.md`: describe the baseline graph under Runtime wiring, and in hard rule 7 drop `BASELINE_MODEL` (bench runs take their models from `--preset`; the three role env vars still configure `app/agent.py`). Add a dated line to spec §19: the baseline is one agent with the same tools, sandbox, guardrails, budget and test-fix loop, without the planner and reviewer. Lint, full suite.
- [ ] **Step 4:** Commit: `feat: single-agent baseline sharing the deterministic harness`.

---

### Task 5: Matrix runner

**Files:**
- Create: `bench/matrix.py`
- Modify: `bench/run.py`, `bench/presets.py`
- Test: `tests/unit/test_matrix.py`, `tests/unit/test_bench_run.py`

**Interfaces:**
- Consumes: `run_pipeline`, `is_resolved`, `audit_patch`, `role_models`, `solo_model_name`, `build_workflow`, `build_baseline_workflow`, `make_model`, `format_event`, `validate_task`.
- Produces:
  - `bench.presets.workflow_for(system: str, preset: str) -> Workflow` — `"multi"` → `build_workflow(role_models(preset))`; `"single"` → `build_baseline_workflow(make_model(solo_model_name(preset)))`; anything else raises `ValueError`.
  - `bench.matrix.RunSpec` (frozen dataclass): `task: TaskSpec`, `system: str`, `preset: str`, `repeat: int`, `stamp: str`, `attempt: int = 0`; property `label` = `"{task_id}/{system}/{preset}/r{repeat}"`; property `run_id` = `"{task_id}-{system}-{preset}-r{repeat}-{stamp}"`, with `-retry{attempt}` appended when `attempt > 0`.
  - `bench.matrix.plan_runs(tasks, system, preset, repeats, stamp) -> list[RunSpec]` — ordered by repeat, then task.
  - `async bench.matrix.run_spec(spec: RunSpec, *, on_event=None, workflow_factory=workflow_for) -> dict` — builds the workflow with `workflow_factory(spec.system, spec.preset)`, runs `run_pipeline(RunRequest(task_id=..., run_id=spec.run_id), workflow=..., on_event=on_event)`, scores with `is_resolved`, audits the patch, and returns the row.
  - `async bench.matrix.run_matrix(specs, results_path, *, concurrency=1, run_one=None, progress=False, max_infra_retries=2) -> list[dict]`. `run_one` defaults to `run_spec` and is called as `run_one(spec)` or, with progress on, `run_one(spec, on_event=...)`. For an infra rerun it is called with a copy of the spec whose `run_id` carries the `-retry<k>` suffix.
  - Row fields, always present: `task_id`, `category`, `system`, `preset`, `repeat`, `run_id`, `resolved`, `outcome`, `failure_kind`, `reason`, `cost_usd`, `duration_s`, `tool_calls`, `tokens_in`, `tokens_out`, `test_attempts`, `review_rounds`, `audit`, `infra_retries`, `crashed`.
  - CLI:

    ```
    uv run python -m bench.run [--tasks a,b | --split dev|heldout] [--system multi|single]
        [--preset flash|pro|mixed] [--repeats N] [--concurrency N] [--out results]
        [--quiet] [--confirm-heldout] [--skip-validate]
    ```
    Defaults: `--split dev --system multi --preset flash --repeats 1 --concurrency 1`.
    Results file: `results/<stamp>-<system>-<preset>.json`.

**Required behaviour**

1. **Concurrency.** At most `concurrency` runs are in flight (an `asyncio.Semaphore`). Each run has its own workflow object, budget, run directory and sandbox.
2. **One row per spec.** A run whose pipeline or scoring raises produces a crashed row with every field present (`cost_usd` and the other numbers come from the `RunRecord` when one exists, else 0). The matrix continues.
3. **Infra reruns.** A row with `failure_kind == "infra"` and `crashed == False` is rerun up to `max_infra_retries` times, each with run id suffix `-retry<k>`. The final row records `infra_retries` and `cost_usd` summed over all attempts. A crashed row is not rerun.
4. **Results are durable.** `results_path` is rewritten after every finished spec (guard with an `asyncio.Lock`), rows sorted by (`repeat`, `task_id`).
5. **Progress.** With `progress`, each event line is prefixed with the spec's `label`. The per-run status line is printed when the run finishes, prefixed the same way.
6. **Validation first.** Unless `--skip-validate`, `main` runs `validate_task` on every selected task before starting and exits 2, listing the problems, if any task is invalid.
7. **Held-out guard.** Selecting any task whose `split` is `heldout` (through `--split heldout` or `--tasks`) requires `--confirm-heldout`; otherwise exit 2 with a message that names the tasks. `--split` accepts `dev` and `heldout`.
8. **`mixed` with `single`** exits 2 with the `ValueError` text.
9. `run_tasks` and `run_task` in `bench/run.py` are replaced by the matrix functions; update their tests. Tracing enable/flush/link behaviour is unchanged.

**Tests** (`tests/unit/test_matrix.py`; every test injects `run_one`, no real model)

- `test_plan_runs_orders_by_repeat_then_task`.
- `test_run_ids_and_labels_are_unique_per_spec`.
- `test_concurrency_limit_is_respected`: `run_one` tracks the number of concurrent calls; with 6 specs and `concurrency=2` the maximum observed is 2.
- `test_concurrent_repeats_do_not_share_run_dirs`: using the real `run_spec` with a `workflow_factory` that builds a FakeLlm workflow, a FakeEnvironment per run, and scoring monkeypatched to return True (no Docker), two repeats of one task run with `concurrency=2` → two distinct `runs/<run_id>/` directories, each with its own `record.json` and `patch.diff`, and two distinct sandbox ids.
- `test_infra_failures_are_retried_then_recorded`: `run_one` returns infra twice then success → one row, `infra_retries == 2`, `cost_usd` is the sum; always-infra → `failed / infra`, `infra_retries == 2`.
- `test_a_crashing_run_is_recorded_and_the_matrix_continues`.
- `test_results_file_is_rewritten_after_every_run`.
- `test_bench_run.py`: CLI parsing for every flag; unknown task exits 2; invalid task exits 2 before anything runs; held-out selection without `--confirm-heldout` exits 2; `--system single --preset mixed` exits 2; `--quiet` silences progress; results file name carries system and preset.

- [ ] **Step 1:** Write failing tests for `plan_runs`, `RunSpec`, `workflow_for`. Implement. Green.
- [ ] **Step 2:** Write failing tests for `run_matrix` (concurrency, crash, infra rerun, durability). Implement. Green.
- [ ] **Step 3:** Write failing CLI tests. Rework `bench/run.py`. Green.
- [ ] **Step 4:** Update `AGENTS.md` (commands table: the new flags; note that a matrix run spends credits per run). Lint, full suite.
- [ ] **Step 5:** Commit: `feat: matrix runner with systems, presets, repeats and infra reruns`.

---

### Task 6: Report

**Files:**
- Create: `bench/report.py`
- Test: `tests/unit/test_report.py`

**Interfaces:**
- Produces:
  - `load_rows(paths: list[Path]) -> list[dict]` — reads result files; fills missing `system="multi"`, `preset="flash"`, `repeat=1`, `audit=[]`, `infra_retries=0`, `crashed=False`, and 0 for missing numbers.
  - `Summary` (dataclass) per (`system`, `preset`): `runs`, `tasks`, `repeats`, `resolve_rate_mean`, `resolve_rate_min`, `resolve_rate_max` (over repeats; one repeat's rate = resolved ÷ tasks in that repeat), `clean_resolve_rate_mean` (resolved with an empty audit), `flagged_resolved`, `by_category: dict[str, tuple[int, int]]` (resolved, total), `cost_per_run_mean`, `duration_median_s`, `agent_failures`, `budget_failures`, `infra_failures`, `crashed`.
  - `summarize(rows) -> list[Summary]` — sorted by system then preset.
  - `render_markdown(summaries, *, title, sources) -> str`.
  - CLI: `uv run python -m bench.report results/<file>.json [...] --out docs/results/<name>.md [--title "..."]`.

**Required behaviour**

- The resolve rate counts only non-infra, non-crashed runs in its denominator **and the report says so**; infra and crashed runs are shown in their own columns, never hidden.
- The Markdown has: a one-line caveat stating the number of tasks and repeats; the main table (system, models, resolved mean with min–max, audit-clean resolved, $/run, median time, agent / budget / infra / crashed counts); a per-category table; a "Flagged patches" list naming run id and flags; the source file names.
- Numbers: rates as percentages with no decimals, cost with 3 decimals, time in whole seconds.
- An empty input produces a report that says there are no rows, not an exception.

**Tests** (`tests/unit/test_report.py`, fixture rows built in the test)

- `test_summary_groups_by_system_and_preset`.
- `test_resolve_rate_mean_min_max_over_repeats`.
- `test_infra_rows_are_reported_separately`: infra rows do not enter the rate's denominator and are counted in `infra_failures`.
- `test_flagged_rows_are_counted_in_both_columns`: a resolved row with a non-empty audit counts in `resolve_rate_*`, not in `clean_resolve_rate_mean`, and appears in the flagged list.
- `test_legacy_and_crashed_rows_load`: a Week 1 row (no `system`, `preset`, `audit`) and a crashed row (no cost) load and summarise.
- `test_render_markdown_contains_the_caveat_and_tables`.
- `test_empty_input`.
- `test_cli_writes_the_file` (through `main`, with a temp results file).

- [ ] **Step 1:** Write failing tests. Run.
- [ ] **Step 2:** Implement. Green. Lint, full suite.
- [ ] **Step 3:** Run it on the three committed Week 1 result files to check the legacy path: `uv run python -m bench.report results/*.json --out /tmp/week1-check.md`, read the output, and paste it in the task report. Do not commit that file.
- [ ] **Step 4:** Update `AGENTS.md` (report command). Commit: `feat: benchmark report generator`.

---

### Task 7: First comparison run **[OWNER APPROVAL]**

This task calls real models. Estimated cost: about $12 (30 Flash runs at roughly $0.16, 10 Pro runs at roughly $0.70). Ask the owner before Step 2 and state the estimate.

**Files:**
- Create: `docs/results/<run date>-week2a-comparison.md` (for example `2026-10-01-week2a-comparison.md`), result JSON files under `results/`
- Modify: `README.md`, `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` (§19)

- [ ] **Step 1:** Dry check without models: `uv run python -m bench.validate`, `uv run pytest -q`, `make sandbox-image`.
- [ ] **Step 2 [OWNER APPROVAL]:** Flash, both systems, three repeats:

  ```
  TRACE_TO_CLOUD=1 uv run python -m bench.run --split dev --system multi  --preset flash --repeats 3 --concurrency 3
  TRACE_TO_CLOUD=1 uv run python -m bench.run --split dev --system single --preset flash --repeats 3 --concurrency 3
  ```
- [ ] **Step 3 [OWNER APPROVAL]:** Pro, both systems, one repeat:

  ```
  TRACE_TO_CLOUD=1 uv run python -m bench.run --split dev --system multi  --preset pro --repeats 1 --concurrency 3
  TRACE_TO_CLOUD=1 uv run python -m bench.run --split dev --system single --preset pro --repeats 1 --concurrency 3
  ```
- [ ] **Step 4:** Generate the report from the four result files into `docs/results/`. Read every flagged patch and every agent failure's `events.jsonl`, and add a short "What happened" section by hand: what failed and why, in plain words. Do not tune prompts.
- [ ] **Step 5:** Add a "Results so far" section to `README.md` with the main table and the caveat (five easy tasks; not yet a benchmark). Add a §19 line to the spec recording the matrix CLI.
- [ ] **Step 6:** Commit results, report and docs: `docs: first multi-agent vs single-agent comparison`.

---

## Exit criteria

- `uv run pytest -q` and `agents-cli lint` pass; `bench.validate` prints `ok` for every task.
- A rename, a protected-file edit and a `.github/` edit by the coder are all undone by the pipeline, with a message that matches what happened.
- The scorer runs hidden tests only after the visible run, and every row carries an audit.
- `bench.run` can run both systems, three presets, repeats and parallel runs, and survives crashes and infra failures.
- `bench.report` produces the comparison table, with infra and flagged runs shown separately.
- One comparison report is committed, with its caveats stated.

## Not in this plan

- More repos and tasks, the held-out split, tempting tasks (Plan 2B).
- GitHub live mode, the human approval step, quality evals (Plan 2C).
- Prompt tuning. The coder's redundant verification calls (seen in the smoke run) are a tuning target once Plan 2B gives enough tasks to measure a change.
- Exempting framework tool calls from the caps; enforcing the output cap inside the `Environment`; a release plugin for the agents-cli entry point. These stay in the Week 1 ledger's deferred list.
