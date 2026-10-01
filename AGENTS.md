# AGENTS.md

Instructions for any coding agent (Claude Code, Gemini CLI, Codex, etc.) working in this repository.

## Project

A multi-agent system built on Google ADK 2.x that turns a GitHub issue into a tested pull request. A graph `Workflow` connects Planner, Coder and Reviewer LLM agents with deterministic function nodes. Code runs in hermetic sandboxes (local Docker or Agent Runtime Sandboxes). The system is measured with a hidden-test benchmark against a single-agent baseline.

- **Design spec (source of truth):** `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md`
- **Status:** Core loop and measurement harness implemented (bench mode only, local Docker sandbox): the multi-agent pipeline, the single-agent baseline, the matrix runner and the report. First comparison: `docs/results/2026-09-30-week2a-comparison.md`. Live GitHub mode, the Agent Runtime sandbox backend and the web UI are not built yet.

Read the spec before making architectural changes. If the code and the spec disagree, stop and ask. Do not quietly pick one.

## Hard rules

1. **No AI attribution.** Never add Claude, Claude Code, or any AI tool as author, co-author or contributor. That means no `Co-Authored-By:` trailers, no "Generated with ..." lines in commits, PRs, docs, or a CONTRIBUTORS file. Commits use the repo owner's git identity only.
2. **The sandbox gets no credentials and no network.** The GitHub token is read only by the orchestrator-side nodes `fetch_issue`, `open_pr` and `report_failure`. Never pass it into prompts, session state, traces, or a sandbox.
3. **Tools go through `Environment`.** Agent tools must never call `subprocess`, `open()` or the host filesystem directly.
4. **Never trust model claims for routing.** Routing uses real `git diff` output and real test exit codes from function nodes.
5. **Benchmark integrity.**
   - Never tune prompts against `split: heldout` tasks, and never open or print their contents during development.
   - Never edit `hidden_tests/` to make a run pass.
   - Run `bench validate` after changing anything under `bench/repos/`.
   - Held-out task directories (`bench/tasks/*-h[0-9][0-9]/`) are sealed: never open, print or diff them, not even as file names in a diff stat. Exclude them from review diffs with the pathspec `':(exclude,glob)bench/tasks/*-h[0-9][0-9]/**'`; a pattern without `/**` does not exclude the files inside.
   - Keep git output quiet around them: `git commit -q`, `git cherry-pick ... >/dev/null`, `git merge --no-stat`. Run pytest with `--tb=no` when held-out tasks are collected. Never review a held-out change in a GitHub PR's file view.
6. **Keep the cost guards.** The budget plugin, sandbox TTLs and loop bounds stay on. Changing their defaults needs the owner's approval.
7. **Don't change model names unless asked.** Models are configured through env vars (`PLANNER_MODEL`, `CODER_MODEL`, `REVIEWER_MODEL`). Bench runs take their models from `--preset`; the three role variables configure `app/agent.py`.
8. **Never deploy, create cloud resources, or push to GitHub without explicit approval** from the owner, given in the current session.

## Runtime wiring

- `app/pipeline.py` builds the graph; `app/agent.py` exposes it to agents-cli as `root_agent` / `app`; `app/driver.py` (`run_pipeline`) is the entry point for bench runs and the only place that releases the sandbox.
- `app/baseline.py` builds the single-agent baseline graph (`build_baseline_workflow`): fetch, provision, one `solo` agent (same tools, sandbox, guardrails and budget as the coder), `route_solo`, diff, tests, deliver, with the same 3-return test-fix loop. It drops only the planner and the reviewer. Both systems run under the same per-run caps (cost, tool calls, wall clock).
- Runner-wide plugins, in order: `BudgetPlugin` (cost and tool-call caps), ADK's `ReflectAndRetryModelPlugin(max_retries=2)` (retries malformed function calls), `GuardrailPlugin` (shell and protected-path checks), then the BigQuery analytics plugin when it is enabled. The driver inserts an internal tracker, used for failure classification, right after the budget plugin. The order matters: a plugin that returns a value stops the ones after it.
- The driver bounds the whole event loop with `RUN_TIMEOUT_S` (`asyncio.timeout`); it cancels the in-flight work cooperatively (not a hard kill: a task that swallows cancellation could still block); only the cap's own expiry becomes a `budget` failure, an outer cancellation still propagates. `bench.matrix` waits `INFRA_RETRY_PAUSE_S * k` (30 s default) before infra rerun k.
- Task format (`app/task_store.py`): `task.yaml` also takes `tempting: bool` (default false) and `levers: [...]` (from `LEVERS`: `multi_file`, `distant_symptom`, `doc_rule`, `regression_risk`, `shortcut`). A tempting task ships a `shortcut/` overlay (applied after `plant/`, never together with `solution/`) that must pass the visible tests and fail the hidden ones; `bench.validate` checks this and prints only fixed problem strings. Result rows and the report also carry repo and difficulty breakdowns.
- Scoring (`bench/score.py`) runs inside a sandbox; model-written code never runs on the host.
- Tracing (`app/tracing.py`) is opt-in. With `TRACE_TO_CLOUD=1`, `bench.run` exports to Cloud Trace: the driver's root span `issue_to_pr.run` (task, run ID, outcome, cost, tool calls) with ADK's workflow, agent, model-call and tool-call spans under it.
- The pipeline's git directory is `/workspace/.pipeline-git`, outside the worktree. Diffs are taken against the baseline commit recorded in state as `baseline_sha`.
- Gemini agents return structured output through ADK's `set_model_response` tool (`app/models.py` reports `output_schema_and_tools=False`), because native output schema + tools loops on the tool call on gemini-3.8-flash. That tool call counts toward the per-run tool-call cap.
- Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PLANNER_MODEL`, `CODER_MODEL`, `REVIEWER_MODEL` | `gemini-3.8-flash` | Model per role (`provider/model` strings go through LiteLLM) |
| `RUN_BUDGET_USD` | `1.00` | Per-run cost cap |
| `MAX_TOOL_CALLS_PER_RUN` | `75` | Per-run tool-call cap |
| `RUN_TIMEOUT_S` | `1500` | Wall-clock cap per run; must stay below `SANDBOX_TTL_S`. On expiry the driver cancels the run, records a `budget` failure and releases the sandbox. Not a positive number: error before the run starts |
| `ENVIRONMENT_BACKEND` | `docker` | Sandbox backend |
| `SANDBOX_IMAGE` | `issue-to-pr-sandbox:dev` | Docker image for the sandbox |
| `SANDBOX_TTL_S` | `1800` | Seconds after which a local sandbox removes itself (self-destruct) |
| `BENCH_TASKS_DIR`, `BENCH_REPOS_DIR` | `bench/tasks`, `bench/repos` | Bench task and repo locations |
| `RUNS_DIR` | `runs` | Per-run outputs (`events.jsonl`, `record.json`, `patch.diff`) |
| `TRACE_TO_CLOUD` | unset | `1` exports each local run's spans to Cloud Trace in `GOOGLE_CLOUD_PROJECT` (needs the Cloud Trace API and credentials). Spans include prompt and tool content |
| `BQ_ANALYTICS_ENABLED` | unset | `1` enables the BigQuery analytics plugin and creates its dataset (also needs `GOOGLE_CLOUD_PROJECT`). Owner approval required |

## Workflow

This project follows the `agents-cli` lifecycle: scaffold → build → evaluate → deploy → observe.

- Requires `agents-cli` ≥ 1.7.0. Upgrade with `uv tool upgrade google-agents-cli`, then run `agents-cli update`.
- Use `uv` for everything Python (`uv sync`, `uv run ...`). Never call `pip` directly.
- Before writing agent code, study the reference recipes listed in spec §17 (`google/adk-samples` → `core/python/...`).
- Run `agents-cli infra single-project` **before** the first `agents-cli deploy`.

## Testing conventions

- **pytest checks code correctness:** graph routing (using the scripted fake `BaseLlm`), guardrails, budget math, schemas, and the environment backends. pytest must never assert on real LLM output content.
- **Agent behaviour** is measured by `agents-cli eval run` (quality metrics) and the `bench/` benchmark (the resolve rate).
- Write a failing test before implementing deterministic code.

## Commands

| Task | Command |
|---|---|
| Install deps | `uv sync` |
| Lint | `agents-cli lint` |
| Unit tests | `uv run pytest tests/unit` |
| Quick smoke run (calls a real model) | `agents-cli run '{"task_id": "tc-001", "run_id": "smoke-1"}'` |
| Interactive UI | `agents-cli playground` |
| Quality evals | `agents-cli eval run` |
| Deploy (approval required) | `agents-cli deploy` |
| Build sandbox image | `make sandbox-image` |
| Docker-backed tests | `make test-docker` |
| Validate bench tasks | `uv run python -m bench.validate` |
| Run bench (spends credits per run) | `uv run python -m bench.run [--tasks a,b \| --split dev\|heldout] [--system multi\|single] [--preset flash\|pro\|mixed] [--repeats N] [--concurrency N] [--out results] [--quiet] [--confirm-heldout] [--skip-validate]`. Defaults: `--split dev --system multi --preset flash --repeats 1 --concurrency 1`. Runs the matrix (`bench/matrix.py`): one model run per task per repeat, so cost scales with tasks x repeats. Results go to `results/<stamp>-<system>-<preset>.json`. Tasks are validated first (`--skip-validate` bypasses); held-out tasks need `--confirm-heldout`; `single` does not accept `mixed`; infra failures are rerun up to twice; progress is printed live, `--quiet` silences it |
| Run bench with traces in the GCP console | `TRACE_TO_CLOUD=1 uv run python -m bench.run --tasks tc-001` |
| Build a results report | `uv run python -m bench.report results/<file>.json [...] --out docs/results/<name>.md [--title "..."]` |

The smoke-run prompt must be `RunRequest` JSON (`task_id`, `run_id`). It calls a real model, and a sandbox started this way is not released by the driver: only its TTL (`SANDBOX_TTL_S`) cleans it up.

## Keeping this file current

After a structural change (new top-level directories, new env vars, new commands, callback or plugin re-wiring), update this file in the same commit.
