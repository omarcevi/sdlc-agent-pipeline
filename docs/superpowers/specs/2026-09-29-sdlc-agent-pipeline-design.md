# Issue-to-PR Agent Pipeline — Design Spec

- **Date:** 2026-09-29
- **Author:** Omar Elcircevi
- **Status:** Draft, awaiting review
- **Working name:** `issue-to-pr` (rename freely; nothing in the design depends on it)

## 1. Summary

A multi-agent system built on Google ADK 2.x that takes a GitHub issue and produces a tested pull request. A graph `Workflow` wires specialist LLM agents (Planner, Coder, Reviewer) together with deterministic function nodes (fetching the issue, running tests, collecting the diff, opening the PR). Generated code runs inside hermetic sandboxes: Docker locally, Agent Runtime Sandboxes in the cloud. The project is measured with a hidden-test benchmark against a single-agent baseline, and shown off through a replayable web UI.

The repository is a portfolio piece. It should demonstrate two things: building agents that do real SDLC work, and shipping them with production discipline (evals, tracing, security, cost control, CI).

## 2. Audience and success criteria

| Audience | Time spent | What they must see |
|---|---|---|
| Recruiter | about 30 s | README with a one-line pitch, a demo GIF, a replay link, and the results table above the fold; keywords (multi-agent, ADK, evals, CI/CD, Agent Runtime) |
| Hiring manager | about 5 min | Architecture diagram, ADRs explaining the key decisions, honest benchmark numbers (dev and held-out splits, cost per issue, multi- vs single-agent), clickable traces |

Measurable success criteria:

1. The benchmark report covers 20 tasks across 3 repos: resolve rate on the dev split (15) and held-out split (5), $/issue, and median wall time, for the multi-agent pipeline and the single-agent baseline, with at least 2 model presets and 3 repeats each.
2. No fixed pass-rate target. The deliverable is an honest measurement. CI smoke gate: at least 2 of 3 easy tasks resolved.
3. The public replay page loads without any backend and costs $0 to run.
4. The live pipeline, deployed to Agent Runtime, opens a real PR on a demo repo after human approval.
5. Credentials never reach a sandbox, and a unit test enforces this.
6. Total cloud spend stays at or below $500 of the $1,000 GDE credits.

## 3. Scope

**In scope:** Python repos tested with pytest; the three demo repos built for this project; GitHub as the forge; Gemini-first with provider-agnostic model config; local and Agent Runtime sandbox backends; a web UI with replay and live modes; CI; deployment to Agent Runtime.

**Non-goals:** languages other than Python; large real-world open-source repos; official SWE-bench scoring; fine-tuning; multi-tenant SaaS; a published GitHub App; chat or voice interfaces.

**Stretch (first to cut):** the issue-label trigger (applying the `agent-ok` label starts a run automatically via Pub/Sub).

## 4. Constraints

- **Time:** 3 weeks.
- **Budget:** $1,000 of GDE GCP credits, target spend ≤ $500. The Gemini Flash promotional price ($0.75/M in, $3.75/M out) ends 2027-01-01, so the full benchmark runs before then.
- **Stack:** Python 3.12, ADK ≥ 2.x (the graph Workflow API needs Python ≥ 3.11), `uv`, and `agents-cli` ≥ 1.7.0. The installed version is 1.3.1 and must be upgraded before scaffolding.
- **Cloud:** a GCP project with billing (the user's). Credits may not cover partner models (Claude); check Billing → Credits → scope before running any non-Gemini preset.

## 5. Architecture

### 5.1 Workflow graph

```
START → fetch_issue (fn) → provision_sandbox (fn)
      → planner (LLM, read-only tools) → route_plan (fn)
          ├─ declined → report_failure (fn)
          └─ actionable → coder (LLM, read/edit/bash tools)
                → collect_diff (fn) → run_tests (fn)
                     ├─ fail (incl. empty diff / protected-file edit), attempts ≤ 3 → coder
                     ├─ fail, attempts exhausted → report_failure
                     └─ pass → reviewer (LLM, read-only tools) → route_review (fn)
                                 ├─ changes, rounds ≤ 2 → coder
                                 ├─ changes, rounds exhausted → report_failure
                                 └─ approve → bench: deliver_patch (fn)
                                              live (week 2): human_gate (RequestInput) → open_pr (fn)
```

Sandbox teardown runs in the run driver's `finally` block (plus the TTL), not as a graph node, so it also runs when the workflow raises.

- Deterministic steps are function nodes; judgment steps are `LlmAgent` nodes. No node trusts what a model says about the world: reviewers see the real `git diff`, and routing uses real test exit codes.
- LLM nodes cannot emit routes, so a function router follows the planner and the reviewer.
- The budget plugin (§6.3) can abort any LLM step; the driver records the run as `failed` with `failure_kind=budget`.

### 5.2 Components

```
app/
├── agent.py            root_agent = Workflow(...): graph only, readable in about 30 lines
├── schemas.py          IssueTask, Plan, PatchResult, Diff, TestReport, Review, RunLedger
├── agents/             planner.py, coder.py, reviewer.py
├── nodes/              fetch_issue, provision_sandbox, collect_diff, run_tests,
│                       route_plan, route_review, deliver_patch, human_gate, open_pr, report_failure
├── tools/              read_file, list_dir, grep, edit_file, write_file, bash
├── environment/        Environment protocol, DockerEnvironment, AgentRuntimeSandbox
├── guardrails.py       before_tool_callback chain
├── budget.py           BudgetPlugin (runner-wide ADK plugin)
├── pricing.py          model → $/M input and output tokens (maintained by hand)
├── models.py           role → model mapping via env vars and LiteLlm
├── github_client.py    issues, branches, PRs, comments
└── baseline.py         single-agent LlmAgent with the same tools
sandbox_image/          Dockerfile + runtime shim (adapted from long-horizon-harness)
bench/                  repos/, tasks/, runner, scorer, report generator
web/                    React + Vite UI; FastAPI live proxy
docs/                   architecture diagram, ADRs, results
```

Exact paths may shift to match the `agents-cli` scaffold layout. Component boundaries stay fixed.

### 5.3 Tools per agent

| Agent | Tools | Output |
|---|---|---|
| Planner | `read_file`, `list_dir`, `grep` | `Plan` (via `output_schema`) |
| Coder | `read_file`, `list_dir`, `grep`, `edit_file` (exact string replace), `write_file` (new files only), `bash` | `PatchResult` |
| Reviewer | `read_file`, `list_dir`, `grep`; receives `Diff` + `TestReport` in context | `Review` (via `output_schema`) |
| Baseline | all Coder tools | `PatchResult` |

All tools call the active `Environment`. None touches the host filesystem or spawns host processes.

## 6. Data flow

### 6.1 Schemas (Pydantic, stored in session state)

- `IssueTask`: `repo`, `issue_number`, `title`, `body`, `base_ref`, `run_id`, `mode` (`bench` | `live`).
- `Plan`: `actionable: bool`, `decline_reason: str | None`, `summary`, `files_to_inspect: list[str]`, `steps: list[str]`, `test_strategy`.
- `PatchResult`: `summary`, `files_changed: list[str]`, `tests_added: list[str]`, `notes`. Informational only, never trusted for routing.
- `Diff`: `unified_diff`, `files: list[str]`, `insertions`, `deletions`, produced by running `git diff` inside the sandbox.
- `TestReport`: `passed: bool`, `exit_code`, `failed_tests: list[str]`, `output_tail` (last 200 lines), `duration_s`.
- `Review`: `verdict` (`approve` | `request_changes`), `comments: list[{file, line, severity, issue}]`, `must_fix: list[str]`.
- `RunLedger`: `test_attempts`, `review_rounds`, `tokens_in`, `tokens_out`, `cost_usd`, `outcome` (`pr_opened` | `patch_written` | `declined` | `failed`), `failure_kind` (`agent` | `budget` | `infra` | `none`).

### 6.2 Loop bounds

- `test_attempts`: incremented on each failing `run_tests`. At most 3 returns to the coder.
- `review_rounds`: incremented on each `request_changes`. At most 2 returns to the coder. Code changed after a review goes back through `collect_diff` → `run_tests`.
- Both counters span the whole run and are independent.

### 6.3 Budget plugin

- A runner-wide `BasePlugin` accumulates `usage_metadata` from every model response and prices it with `pricing.py`.
- **Per-run caps, shared by both systems:** `RUN_BUDGET_USD` (default `1.00`) and `MAX_TOOL_CALLS_PER_RUN` (default `75`).
- **Per-turn sub-limit, multi-agent only:** `MAX_TOOL_CALLS_PER_TURN` (default `25`), applied to multi-agent coder turns. The baseline does all its work in one turn, so only the per-run caps apply to it.
- When a cap is hit, the plugin raises `BudgetExceeded`, which aborts the run. Workflow edges cannot route exceptions, and ADK wraps plugin exceptions in `RuntimeError`, so the driver walks `__cause__` and records `failure_kind=budget`. The ledger lives on the plugin, because state changes from a failing callback are not persisted.
- Because the plugin is runner-wide, both systems face identical per-run caps, which keeps the comparison fair.

### 6.4 Failure handling

| Kind | Examples | Handling |
|---|---|---|
| Infra | sandbox gone, GitHub 5xx, network timeout | `RetryConfig` (3 attempts, exponential backoff) on function nodes. If still failing, `failure_kind=infra`; the benchmark reruns these and **never counts them as agent failures** |
| Malformed LLM output | schema validation fails | ADK `ReflectAndRetryModelPlugin`, `max_retries=2` |
| Agent | loop bounds exhausted, reviewer rejects twice | `report_failure`, counted in results |
| Budget | cost or tool-call cap | `report_failure`, counted in results, reported separately |

`report_failure` posts a summary comment on the issue in live mode (what was tried, the last test output, the reason) and writes it to the results record in bench mode. It never opens a PR.

### 6.5 Visible status events

Every function node yields a short user-visible `Event(content=...)` (for example `tests: 12 passed, 1 failed`) before its `Event(output=...)`. This feeds the web UI and satisfies `agents-cli eval run`, which rejects events that have no content.

## 7. Sandbox and security

### 7.1 Principle

**The sandbox never holds credentials and never has network access.** Every GitHub interaction happens in orchestrator-side function nodes. A compromised agent has nothing to steal and nowhere to send it.

### 7.2 Image

`sandbox_image/Dockerfile`: `python:3.12-slim` plus git, uv, ripgrep and pytest, plus a small FastAPI runtime shim (file ops, zip upload/download, process exec with timeout), adapted from `adk-samples/core/python/long-horizon-harness/horizon/sandbox/runtime/` (Apache-2.0; attribution goes in `NOTICE`). It runs as a non-root `sandbox` user in `/workspace`. The same image backs both environments.

### 7.3 Backends

| | `DockerEnvironment` (local) | `AgentRuntimeSandbox` (cloud) |
|---|---|---|
| Network | `--network none` | template `egress_control_config.internet_access=false` |
| Limits | `--cpus 2 --memory 2g --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges`, read-only root filesystem except `/workspace` and `/tmp` | CPU and memory set in the sandbox template |
| Lifetime | container removed by the driver's `finally` | 30-min TTL, the driver's `finally`, and a `make sweep-sandboxes` orphan sweeper |
| Per command | 120 s timeout, output capped at 10k chars | same, enforced by the `Environment` interface |

The `Environment` protocol follows the harness recipe's contract: tools dispatch by method or capability flag and never check the concrete backend class. Backend selection: `ENVIRONMENT_BACKEND=docker|agent_runtime`.

### 7.4 Repo flow

1. `provision_sandbox` downloads the repo archive at `base_ref` via the GitHub API (live mode), or copies `bench/repos/<repo>` with the task's `plant/` overlay (bench mode). It uploads the copy and commits a git baseline inside the sandbox.
2. `collect_diff` runs `git diff` inside the sandbox. This needs no network.
3. `open_pr` applies the diff in an orchestrator-side temporary clone, pushes a branch, and opens the PR using the token.

### 7.5 Guardrails (`before_tool_callback`)

A blocked call returns `{"error": "<reason>"}` so the model can adapt.

1. **Path guard:** writes are allowed only under the repo root. Test files that existed at `base_ref` are read-only, and so is `.github/`. New test files are allowed.
2. **Command policy:** refuse `git push`, `git remote`, `curl`, `wget`, `pip install`, `uv add`, and `rm` on paths outside the repo.
3. **Output pruning:** tool outputs over the cap are truncated head and tail with a marker.

### 7.6 Secrets

A fine-grained GitHub PAT scoped to the three demo repos (contents RW, pull requests RW, issues RW). It lives in Secret Manager when deployed and in `.env` locally (git-ignored). Only `fetch_issue`, `open_pr` and `report_failure` read it. It never appears in prompts, state, traces or the sandbox. A GitHub App is out of scope.

### 7.7 Prompt injection

Issue text is untrusted input. There are four layers of defence:

1. Live mode processes only issues labelled `agent-ok`, which only collaborators can apply.
2. Issue text is placed in prompts inside explicit data delimiters with instructions to treat it as data.
3. The hermetic sandbox (§7.1).
4. A human approves before any PR opens.

## 8. Models

- `LiteLlm` via env vars: `PLANNER_MODEL`, `CODER_MODEL`, `REVIEWER_MODEL`, `BASELINE_MODEL`. The default for every role is the latest Gemini Flash available in the project's `GOOGLE_CLOUD_LOCATION`, chosen at scaffold time by listing models, not hardcoded from memory.
- Bench presets:
  - `flash`: every role on Flash.
  - `pro`: every role on Gemini 3.1 Pro.
  - `mixed`: Pro for planner and reviewer, Flash for coder.
  - Optional `claude`: only if the credits cover partner models.
- `temperature=0` for all agents in bench mode.

## 9. Benchmark and evals

### 9.1 Benchmark (`bench/`)

- **Repos:** three new stdlib + pytest Python projects: a CLI task manager, a markdown-to-HTML library, and an inventory/pricing module. Each is about 1–3k LOC, with visible tests that pass at base.
  - The canonical source is `bench/repos/<name>/`, plain directories versioned by this repo's git.
  - Public GitHub copies are published for live demos, with `demo/<task-id>` branches that have the plant applied.
- **Tasks (20):** 8 bug fixes, 6 small features, 3 behaviour-preserving refactors, 3 traps (ambiguous or impossible issues). Each task is tagged easy, medium or hard. The held-out split (5) is stratified so it includes at least one task of each category.
- **Task format:** `bench/tasks/<id>/` contains:
  - `task.yaml`: `repo` (a directory under `bench/repos`), issue title and body, category, difficulty, and `split` (`dev` | `heldout`).
  - `plant/`: overlay files that introduce the bug; absent if not needed.
  - `solution/`: overlay files with the reference fix; absent for traps.
  - `hidden_tests/`: never present in the sandbox during the run.
- **`bench validate`** must pass before any scored run, and after any change to a repo. For each non-trap task it checks that:
  - visible tests pass at base + plant;
  - hidden tests **fail** at base + plant;
  - visible and hidden tests pass at base + plant + solution.
- **Tempting tasks:** two dev tasks are built so that a shortcut passes the visible tests but fails the hidden ones. They are used for `review_catch_rate`.
- **Runner:** `bench run --system multi|single --preset flash|pro|mixed --repeats 3 [--split dev|heldout] [--concurrency 4]`. It runs in bench mode against Docker sandboxes: no GitHub writes, no human gate, and `open_pr` writes a patch file instead.
- **Scoring:**
  - Normal tasks: apply the patch to a clean checkout (base + plant), then run visible and hidden tests. Resolved means all pass. (Clean checkout = base with `plant/` overlaid.)
  - Trap tasks: resolved means the outcome is `declined`.
  - `infra` failures are rerun up to 2 times and never scored as agent failures.
- **Threat model:** scoring runs the patch's code, so a patch that attacks the test process (a shadowing `pytest.py`, an exit hook in package code) can fake a pass. Results are valid for patches that do not do that. Every patch is audited for these patterns (`bench/audit.py`) and flagged rows are reported separately; the audit never changes `resolved`. Scoring runs the visible tests in one fresh sandbox and, only if they pass, the hidden tests in a second fresh one, where nothing but `git apply` and file writes happens before the hidden tests are in place. Code the patch runs during the visible phase therefore cannot reach them; what remains possible is patch code attacking the test process during the hidden run itself, which the audit flags when it uses the listed patterns.
- **Split discipline:** prompts are tuned only against `dev`. `heldout` runs once, at the end. Both numbers are published.
- **Output:** `results/<timestamp>.json`, from which a generated Markdown table and charts are committed to `docs/results/`.

### 9.2 Quality evals (`agents-cli eval`)

- 5 dev tasks as eval cases, including both tempting tasks. Custom metrics in `tests/eval/eval_config.yaml`:
  - `plan_quality`: LLM judge; right files identified, sensible test strategy.
  - `review_catch_rate`: code metric; on the tempting tasks, did the reviewer request changes?
  - `multi_turn_trajectory_quality`: built-in; coder tool-use efficiency.
  - `pr_description_quality`: LLM judge.
- Iterate with `eval run`, then `eval compare`.

### 9.3 Unit tests (pytest, no LLM, no GCP)

- A scripted fake `BaseLlm` returns canned responses and tool calls, to exercise graph routing. Examples: 3 test failures lead to `report_failure`; `not_actionable` leads to `report_failure`; approve leads to `open_pr`.
- Also covered: guardrail decisions, budget cutoff, schema parsing, pricing math, both `Environment` backends (the Docker one gated on Docker being available), and a test asserting that the sandbox environment contains no token or credential variables.
- Rule: pytest never asserts on real LLM output content. Behaviour belongs to evals and the benchmark.

## 10. Observability

- **Cloud Trace** (default on Agent Runtime) with the span tree `invoke_workflow` → `invoke_agent` → `call_llm` / `execute_tool`. Custom attributes on the root span: `run_id`, `issue_id`, `preset`, `system`, `cost_usd`, `outcome`.
- **BigQuery Agent Analytics plugin**, enabled at scaffold time (`--bq-analytics`), for SQL over LLM and tool events.
- **Prompt-response logging** to GCS/BigQuery: on when deployed. Trace spans keep `NO_CONTENT`.
- Infra order: `agents-cli infra single-project` **before** the first `agents-cli deploy`.

## 11. Web UI

- **One renderer, two event sources.** It shows the graph with nodes lighting up as they run, a live coder tool-call feed, a diff viewer, test output, a cost meter, and links to the trace and the PR. Built with React, Vite, Tailwind, React Flow and a diff-view component.
- **Replay mode (public):** a static build plus recorded run event logs (JSON), deployed to this repo's GitHub Pages. No backend and $0.
- **Live mode (private):** a FastAPI proxy on Cloud Run (min instances 0) behind IAP, allowlisted to the owner's Google account. It streams from the Agent Runtime deployment and adds Approve/Reject for `human_gate`, following the `ambient-expense-agent` approval-proxy pattern: find the pending `RequestInput` in session events, then resume via a function response.
- A `record` flag on live and bench runs saves the event stream as a replay file.

## 12. Deployment and infrastructure

1. `uv tool upgrade google-agents-cli` (to ≥ 1.7.0), then `agents-cli update`.
2. `agents-cli scaffold create` with `agent_runtime` as the deployment target, `--bq-analytics`, and GitHub Actions for CI/CD.
3. `agents-cli infra single-project --project <PROJECT_ID>`.
4. Build `sandbox_image` with Cloud Build into Artifact Registry, then create the sandbox template with a script (`internet_access=false`).
5. Put the GitHub PAT in Secret Manager.
6. `agents-cli deploy` with `min_instances=0` where supported.
7. `make teardown` destroys the deployment, sandboxes, Cloud Run UI and template. Terraform-managed pieces are destroyed with Terraform.
8. GCP budget alerts at $250, $500 and $750.

## 13. CI (GitHub Actions, Workload Identity Federation, no JSON keys)

| Trigger | Jobs | Est. cost |
|---|---|---|
| Pull request | `agents-cli lint` (ruff, ty, codespell) and unit tests | $0 |
| Push to main | the above, plus a smoke bench (3 easy dev tasks, `flash`, gate ≥ 2/3) and `agents-cli eval run` (5 cases) | about $2 |
| Manual dispatch | full benchmark (chosen system, preset and split) | about $60 per preset |
| Tag `v*` | `agents-cli deploy` | — |

## 14. Cost plan

| Item | Estimate |
|---|---|
| Development runs (150–300 × Flash at about $0.25–0.60) | $75–150 |
| Benchmark (20 tasks × 2 systems × 3 repeats on Flash, plus one Pro set) | $60–180 |
| Agent Runtime, sandboxes, Cloud Run, Artifact Registry, Build, Secret Manager, Trace, BigQuery | $10–40 |
| **Total** | **about $150–400** |

Controls: the per-run budget cap, sandbox TTL plus sweeper, no always-on instances, public replay instead of public live runs, budget alerts, and running the benchmark before the 2027-01-01 price change.

## 15. Milestones (3 weeks)

**Week 1: core loop, local**

- Day 1, de-risking spikes:
  - (S1) custom-image Agent Runtime Sandbox: provision, zip upload, `pytest` exec, teardown, and provisioning latency.
  - (S2) `agents-cli eval run` accepts a Workflow whose function nodes emit content events.
  - (S3) an `LlmAgent` node with both tools and `output_schema` under `LiteLlm`. If unsupported, the coder returns `PatchResult` through a `finish` tool.
- Days 2–3: schemas, `DockerEnvironment`, tools, guardrails, the budget plugin (pulled forward from week 2), and the graph with the fake model plus unit tests.
- Days 4–5: real agents on Flash; demo repo #1 with 5 tasks end-to-end in bench mode.

**Week 2: measure**

- Repos #2–3 and the remaining tasks (including all 5 held-out tasks) with hidden tests; parallel runner with presets and repeats; report; baseline.
- Prompt iteration on the dev split only; quality evals.
- GitHub integration (`fetch_issue`, `open_pr`, `report_failure`) and `human_gate`.

**Week 3: ship**

- `AgentRuntimeSandbox` backend; infra and deploy; tracing and BigQuery.
- Web UI in replay and live modes, with recorded replays.
- CI workflows.
- Final benchmark runs, then the held-out run (once).
- README, architecture diagram, ADRs and demo GIF.

**Cut line, in order:** the label trigger, then the `mixed` preset, then live UI mode (replay stays). Never cut: the Agent Runtime sandbox backend, the benchmark, or the held-out split.

**Delivery:** this spec is implemented through three plans, one per week: (1) spikes and the core loop, (2) benchmark and measurement, (3) cloud, UI, CI and docs. Each plan is written only after the previous one lands, so it reflects what the spikes actually found.

## 16. Risks

| Risk | Mitigation |
|---|---|
| Custom-image sandbox cannot run pytest or is slow to provision | Spike S1 on day 1. Fallbacks, in order: ADK Cloud Run sandbox executor, then GKE code executor. Docker remains the benchmark backend regardless |
| `eval run` rejects workflow events | Content events on every node (§6.5), verified by spike S2. Fallback: bench-only metrics and `adk eval` directly |
| Tools and `output_schema` cannot coexist on a node | Spike S3; `finish`-tool fallback |
| Runaway cost | Budget plugin, TTLs, alerts, replay-only public UI |
| Benchmark too easy or too hard | Mixed difficulty tiers; adjust task set on dev split before the held-out run |
| Credits don't cover Claude | Gemini-only presets; Claude preset optional and paid separately if wanted |

## 17. Reference recipes

- `core/python/long-horizon-harness`: `Environment` interface and sandbox provider, the sandbox runtime shim and Dockerfile, the guardrail callback contract, the per-user secret store pattern, and `ResumabilityConfig`.
- `core/python/ambient-expense-agent`: graph `Workflow` with mixed function and LLM nodes, `RequestInput` HITL, the approval proxy frontend, and the Pub/Sub trigger (stretch).
- `core/python/deep-search`: plan-approval step and critique/refine loop patterns.

## 18. Architecture decision records to write

1. Graph Workflow over autonomous multi-agent chat.
2. Deterministic function nodes for tests and diffs (never trust model claims).
3. Hermetic, credential-free sandbox; orchestrator-side git.
4. One sandbox image for local and cloud.
5. Hidden-test benchmark with held-out split and trap tasks.
6. Runner-wide budget plugin for fair baseline comparison.
7. Replay-first public demo.

## 19. Amendments

**2026-09-29, from Week 1 planning.** Based on reading the ADK 2.8.0 source and running API probes against it:

1. Budget caps abort the run through `BudgetExceeded` (§6.3). Sandbox teardown moved into the driver's `finally` block (§5.1).
2. Router function nodes follow LLM nodes (§5.1). Routed edges use the dict form `(node, {"route": target})`.
3. `run_tests` fails an attempt deterministically on an empty diff or an edit to a protected file.
4. Bench tasks use `plant/` and `solution/` overlay directories instead of patch files (§9.1).
5. The budget plugin moved into Week 1 (§15).
6. S3 was expected to pass from reading the source: `Gemini` on Vertex and `LiteLlm` declare support for `output_schema` together with tools, and other models get ADK's `set_model_response` tool automatically. **Superseded by item 8 of the 2026-09-30 block:** the live spike showed the native mode loops on gemini-3.8-flash.
7. All Week-1 tasks are in the `dev` split. Held-out tasks are written in Week 2.

**2026-09-30, from the Week 1 final review.**

1. Scoring runs inside a sandbox, with hidden tests outside the repo and patch-supplied pytest config ignored; protected test files are restored before scoring (§9.1).
2. The pipeline's git dir lives outside the worktree and diffs are taken against a recorded baseline SHA with `--no-renames` (§7.4).
3. BigQuery analytics is opt-in via `BQ_ANALYTICS_ENABLED=1` (§10).
4. The local Docker sandbox self-destructs after `SANDBOX_TTL_S` (default 1800 s) (§7.3).
5. `ReflectAndRetryModelPlugin(max_retries=2)` covers malformed function calls; output-schema validation failures are recorded as agent failures (amends §6.4).
6. Model API and transport errors are classified as infra (§6.4).
7. Deferred to Week 3: enforcing the output cap inside the `Environment` (§7.3).
8. Spike S3 result: on gemini-3.8-flash, native output-schema + tools loops on the tool call (13 of 18 runs); agents use ADK's `set_model_response` tool instead (9 of 9 runs finished in 2 model calls).

**2026-09-30, from Week 2A, Task 1.**

1. `run_tests` restores protected test files and `.github/` paths itself (`git checkout <baseline_sha> -- <paths>`; files added under `.github/` are removed). The attempt still counts as failed, and the report says the paths have been restored and that new tests belong in new files. It no longer tells the coder to run `git checkout -- <file>`, which does nothing after the pipeline's `add -A`.
2. Scoring order is: apply patch, restore protected files, run the visible tests, and only if they pass upload and run the hidden tests (§9.1).
3. A test pins that the visible run ignores pytest config files shipped in a patch.
4. `bench/audit.py::audit_patch` flags patches that touch `pytest.py`, `conftest.py`, `sitecustomize.py`, `usercustomize.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `*.pth` or `pyproject.toml`, or add `os._exit`, `atexit`, `sys.exit(`, `pytest_collection_modifyitems`, `pytest_runtest_makereport` or `collect_ignore`. Result rows carry `audit: list[str]`; the audit never changes `resolved`.
5. Hidden tests run in a second fresh sandbox, so code run during the visible phase cannot reach them.

**2026-09-30, from Week 2A, Tasks 2 to 5.**

1. The single-agent baseline (`app/baseline.py`) is one `solo` agent with the same tools, sandbox, guardrails, budget and 3-return test-fix loop as the coder. It drops only the planner and the reviewer, so the comparison measures what those two roles add. The per-turn tool-call cap names the `coder` agent, so `solo` is bound by the per-run caps only (§9).
2. Bench runs take their models from a preset (`flash`, `pro`, `mixed`) instead of the role env vars; `mixed` has no single-agent form.
3. A model API `400` or `413` is recorded as an agent failure ("model rejected the request"); other model API and transport errors stay infra (amends item 6 of the Week 1 review block).
4. `bench.run` runs a matrix: `--system multi|single`, `--preset`, `--repeats`, `--concurrency`. An infra failure is rerun up to twice with its cost summed; a crashed run is recorded and not rerun. Tasks are validated before any run starts, and held-out tasks need `--confirm-heldout`.

**2026-09-30, from the Week 2A final review and the first comparison run.**

1. A model reply with no usable answer (empty content, or no content at all, for example at the token limit or from a safety filter) from the planner, the reviewer or the single agent is an agent failure, "model returned no structured answer". It was a runner crash before, and so left out of the resolve rate (amends §6.4).
2. `SoloResult.declined` is required, as `Plan.actionable` is (§6.1). The baseline returns a `SoloResult`, not a `PatchResult`.
3. An unclassified exception raises `RunCrashed`, which carries the run's record, so a crashed row keeps its cost. Scoring retries an infra error twice before giving up (§9.1).
4. New guard: `RUN_TIMEOUT_S` (default 1500 s, kept below the sandbox TTL) caps a run's wall-clock time. On expiry the driver cancels the run, records a budget failure ("run exceeded N s wall clock") and releases the sandbox. Before this, a model call that never returned hung the run forever; the caps counted dollars and tool calls only (amends §6.3). The cancellation is cooperative, not a hard kill.
5. A rerun after an infra failure waits 30 s times the attempt number first, so a rate-limit window does not use up every attempt (amends §6.4).
6. A sandbox that is still being provisioned is released on cancellation too (§7.3).
7. There is no `BASELINE_MODEL` variable (§8): the baseline's model comes from the bench preset. Result files are named `results/<stamp>-<system>-<preset>.json` (§9.1).
8. The baseline can take up to four turns (one plus three test-fix returns), not one as §6.3 says. The per-turn tool-call cap still names the `coder` agent only. The first comparison showed that this one difference accounts for the whole measured gap between the systems (3 of 15 multi-agent runs ended on it; the single agent used 30 to 38 calls on the same task). **Open decision for the owner:** apply the per-turn cap to both systems, to neither, or keep it and report it.
9. The report (`bench/report.py`) puts every run in exactly one of six buckets (resolved, unresolved, agent, budget, infra, crashed), states each row's own sample, prints `n/a` when nothing was counted, lists budget failures by cap, and refuses duplicate rows (§9.1).
10. Known and open: on `gemini-3.1-pro-preview` the reviewer's model call did not return in 2 of 2 runs that reached it. The cause is not known. The Pro comparison is not done.

**2026-09-30, owner decision after the first comparison.**

1. The per-turn tool-call cap (`MAX_TOOL_CALLS_PER_TURN`, 25, coder only) is removed. Both systems run under one set of per-issue caps: $1.00 (`RUN_BUDGET_USD`), 75 tool calls (`MAX_TOOL_CALLS_PER_RUN`) and 1500 s of wall clock (`RUN_TIMEOUT_S`). This closes item 8 of the previous block and amends §6.3. The per-run tool-call cap is what stops a tool-calling loop.
2. The comparison was rerun on Flash under the equal caps; its report sits next to the first one in `docs/results/`.


**2026-10-01, from Week 2B (task set and dev-split comparison).** Design: `2026-09-30-week2b-task-set-design.md`.

1. The benchmark has 20 tasks on three repositories (`taskcli`, `mdlite`, `stockroom`): 15 in the dev split, 5 held out. `task.yaml` gains `tempting` (a `shortcut/` overlay that passes the visible tests and fails the hidden ones) and `levers` (`multi_file`, `distant_symptom`, `doc_rule`, `regression_risk`, `shortcut`). `bench validate` checks both, and its output for a held-out task is one fixed line (§9.1).
2. Held-out tasks are sealed: written by one subagent, checked by another, never opened, printed or diffed by the main session. The exclusion pathspec is `':(exclude,glob)bench/tasks/*-h[0-9][0-9]/**'`; the first one, without `/**`, excluded only the directory names. Two seal breaches happened while the set was built (the author's progress note named one task's plant location; the broken pathspec showed two tasks' file names in a diff stat). All three affected tasks were replaced, and the replacements avoid the revealed modules.
3. `review_catch_rate` (§9.1) is not computed: the reviewer never rejected a patch in Weeks 2A and 2B, so the rate would be 0 of 0. What the reviewer does is measured by reading its verdicts by hand and, in Week 2C, by review probes (patches with known defects).
4. The comparison ran with `RUN_TIMEOUT_S=3000` and `SANDBOX_TTL_S=3300` for both systems, by owner decision, because the 1,500 s default would likely have stopped only multi-agent runs. The defaults are unchanged.
5. Result: multi 34/45 ($19.29), single 36/45 ($17.05). The $1.00 per-run cost cap binds on the three multi-file features for both systems (nine runs each): long single-agent and coder turns pass a million input tokens. Whether to raise the cap for features is an open question for the owner; it changes the default, so it needs approval (AGENTS.md rule 6).
6. New guard: `MODEL_CALL_TIMEOUT_S` (default 480 s, from a measured 237.7 s maximum) ends a model call that has not finished, retries it once, and on a second stall fails the run as infra (`ModelCallStalled`). `RunRecord.model_stalls` counts stalls, and the report lists runs that stalled twice. Two multi-agent runs in this comparison stalled at the provider and were ended by the wall-clock cap instead (amends §6.3, §6.4).

**2026-10-01, owner decision: the held-out run is dropped for now.**

1. The one-time held-out run (§9.1, §15) is not part of Week 3, and §15's "never cut: the held-out split" no longer holds. Reason: this is a demo repository, and prompts were never tuned on benchmark results (frozen through Weeks 2A and 2B; the Week 2C change only delimits issue text), so the dev-split numbers are not tuned to the tasks they measure.
2. The five held-out tasks stay sealed and unrun until the owner decides otherwise. If the repository is published first, they are published as they are, and the README says they were written sealed and never run.

**2026-10-02, from Week 3B (replay site).** Design: `2026-10-01-week3b-replay-ui-design.md`.

1. §11: replay mode is a static site in `web/` (Vite, React, TypeScript, Tailwind, React Flow, `react-diff-view`) built from a curated set of replay files. `bench/replay.py` converts them from `events.jsonl`, `record.json` and the results row, built from an allowlist of fields, scrubbed and checked for leaks by `bench/replay_check.py`; the `record` flag is not needed. The site is hosted on GitHub Pages from this repository by `.github/workflows/pages.yaml`. Live mode (the FastAPI proxy behind IAP with Approve/Reject) is not built in Week 3B.
2. §5.2: `web/` holds the static viewer only; there is no FastAPI live proxy.
3. §2 criterion 3 is met when the repository is on GitHub with Pages enabled (design §8.1).
4. §9.3 and §13: the replay tests (pytest, Vitest, one Playwright smoke test) and the Pages workflow.
5. §18: ADR 7 ("Replay-first public demo") is written from this design (`docs/adr/0007-replay-first-public-demo.md`).
6. Design §5.4's size estimate was low: the seven replays are 10 to 145 KB each, about 0.6 MB with `index.json`, all under the 300 KB warning.
7. Leak check and converter details settled while building (design §5.3, §5.5): an exact value (the project id, each `REPLAY_REDACT` literal) matches between boundaries of "not `[A-Za-z0-9]`", so `_` counts as a boundary (`<id>_cloudbuild` is caught), and after literal escapes such as `\n`; rewrite 6 escapes the same characters as `app/approval.py` (Cc, Cf, Zl, Zp, Cs and the default-ignorable ranges) as `<U+XXXX>`; a hit's path names an object key as `{key:N}` and its value as `{value:N}`; a JSON file with duplicate keys is refused; a string that is exactly a bench run id is exempt from `high-entropy` only; any file in a scanned directory that is not `*.json` is a hit ("not a replay file"), because Vite publishes all of `web/public`. `index.json` carries each replay's `allow` list but has none of its own, so a hit in it fails the build (fails closed).
8. Site details added by the plan (design §7): `stateAt(replay, graph, t)` takes the graph; a gap of 15 s or more (`WAIT_NOTE_S`) gets a feed note; clicking a feed line seeks to it; `@testing-library/dom` and `@types/node` are build-time dependencies; `package.json` `engines.node` is `">=24"` (`web/.nvmrc` pins 24, CI runs 24). The hash is written when the time settles, never per seek, and an error boundary keeps a failed replay from blanking the page.
9. `.github/workflows/pages.yaml`: on every run except a pull request, the build fails when the `REPLAY_REDACT` secret is empty (design §8.3 called it optional; it now is only for pull requests). The artifact is uploaded only from `main`, `npm ci` runs with `--ignore-scripts`, and both jobs time out after 20 minutes.
10. Owner decision (2026-10-02): when the repository goes public, its history is published as it is. The GCP project id (not a credential) is in several tracked documents and in history, and commits carry the owner's personal email; both are accepted as public. The replay tooling still keeps the project id and number out of replays, and hard rule 5 now names the repository going public as the one exception for the sealed held-out tasks.

**2026-10-02, from Week 3A (cloud).** Design: `2026-10-01-week3a-cloud-design.md`. Run log: `docs/results/2026-10-02-3a-run-log.md`.

1. §7.3: the cloud backend is `AgentRuntimeEnvironment` (`app/environment/agent_runtime.py`), selected with `ENVIRONMENT_BACKEND=agent_runtime`: one sandbox per run from one template (2 CPUs, 2 GiB, internet off, TTL 30 minutes), readiness through `/exec` (the platform answers `/healthz` too early), the repository uploaded as a zip, and the same `timeout` wrapper and exit-code rule as Docker (now shared in `base.py`). The root filesystem is writable in the cloud sandbox and read-only in Docker; nothing in the pipeline depends on it. Parity: the 12 reviewer probes are `ok` on both backends, and three dev tasks gave the same outcome on both (`docs/results/2026-10-02-3a-cloud-parity.md`).
2. §7.1 and §7.6: the only credential is a short-lived token for the `sandbox-caller` service account, held by the orchestrator in the HTTP client's headers. The cloud tests show that no token reaches the container from the metadata server, no request header reaches it, and its identity and environment hold none.
3. §5.1 and §7.3: the driver releases the sandbox on every path (normal end, wall-clock cap, crash, cancellation, provisioning failure); under agents-cli, `SandboxReleasePlugin` does it. `scripts/sandbox_infra.py sweep` deletes sandboxes older than the TTL.
4. §10: BigQuery agent analytics is on for the deployed agent only (tables partitioned with 30-day expiry, a 30-day lifecycle on the logs bucket); its rows hold the agents' messages. Trace spans of the deployed agent carry no message content (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT`).
5. §12: Terraform owns everything except the sandbox template and image (`scripts/sandbox_infra.py`); the spike's repository and service account were imported, its host engine and image deleted. The deploy uploads a staging tree (`make deploy`, `--update-only`; no `.env`, held-out task, hidden test, solution or shortcut). The engine has `min_instances = 0`. Teardown order: sandboxes, templates, engine, single-project root, Cloud Build bucket; the budget root stays. Step 8's alerts become one project budget of 24,000 TRY (about $489 at the Central Bank of Türkiye's rate of 2026-10-01; the owner set the round amount), cost before credits, from 2026-09-29, alerts at 50%, 80% and 100%, and a function that disables billing on the project at 100%, in its own Terraform root. The guard holds `roles/billing.projectManager` and `roles/browser` on the project, the second because the first lacks `resourcemanager.projects.get` (seen in the dry run). The root enables 12 APIs, not the 9 the design listed.
6. §2 criterion 6, §4 and §14: the project's spend limit is $500 before credits, enforced by the controller's spend rule and the hard stop.
7. §11: the deployed agent serves the bench graph only, until a live UI exists. Its smoke run (`tc-001`) wrote a patch.
8. §14: Week 3A spent about $3.50 on model calls (parity $2.39, a stopped first parity attempt about $0.60 to $0.90, the deployed smoke run $0.32), plus cents of cloud resources, read from the billing report.
9. Week 1 review item 7 (output cap in the `Environment`) is closed by the owner: the cap stays in the tools, because nodes such as `collect_diff` need full output.
10. Owner decision: `MAX_TOOL_CALLS_PER_RUN` is 100 from 2026-10-02 (it was 75). Resolved multi-agent runs in Week 2B needed up to 73 tool calls (median 39, p90 53), and `md-001` reached 75 on both backends in the parity run. `RUN_BUDGET_USD` stays $1.00: every resolved run cost at most $0.77, and the runs that reached $1.00 were the three multi-file features, whose context grew past a million input tokens. Results from Week 2B and earlier ran at 75 and are a different configuration from later ones. The deployed agent picks up the new default at its next deploy.
11. Found during the cloud steps and fixed: the Terraform output for the engine was its bare id, not the full resource name the tooling needs; a template the API had just deleted broke the template listing; the cloud tests' async fixtures ran in a different event loop from the tests; two `bench.run` started in the same second shared run ids (each stamp is now claimed exclusively). After the first deploy, a plan of the single-project root wanted to strip the engine's `class_methods` (written by the deploy) and replace the BigQuery logs table (whose schema the log sink extends); both are now in `ignore_changes`, and both roots plan no changes.

**2026-10-02, from Week 3C (CI and CD).** Design: `2026-10-02-week3c-ci-design.md`. Run log: `docs/results/2026-10-02-3c-run-log.md`.

1. §13: free checks on every push and pull request (`ci.yaml`: lint, the non-docker suite, the Docker suite and task validation, about 3 minutes, $0 on GitHub's runners for a public repository); smoke benchmark, evals and benchmarks only by manual dispatch (`paid.yaml`, guarded by `scripts/ci_guard.py` before any credential: `main` only, held-out tasks refused in any form, worst case printed, more than $25 needs an explicit input, at most 60 runs, 30 for `pro` and `mixed`); deploys on `v*` tags after the owner approves in the protected `production` environment (`release.yaml`, which first checks that the tag is on `main`). The scaffold's staging and production workflows are removed.
2. §12: CI uses Workload Identity Federation in the single-project root, no keys: a pool and a GitHub provider that trusts only this repository's numeric id; `ci-runner` (Vertex AI user, service usage consumer) can be used only by `paid.yaml` on `main`, and `deployer` (the same two roles, plus acting as the agent's own account) only by jobs in the `production` environment. Both narrower than the design's first draft, by controller ruling the owner was told about: `ci-runner` was to be usable by any workflow on `main`.
3. Bench dispatches get 360 minutes and two workers for `flash` (one for `pro` and `mixed`); smoke and evals keep 90 minutes.
4. Found by the first CI runs and fixed: two unit tests started a real Docker sandbox (now faked, and a test guard fails any non-docker test that reaches Docker); git 2.50's background maintenance after a commit raced a test that copied the repository, a race the pipeline's own host-side git (delivery, demo export, probes) shared, so every host git call now turns automatic maintenance off.
5. Verified in the cloud on 2026-10-02: the first CI run green in about 3 minutes; a smoke dispatch resolved 3 of 3 ($0.61) through `ci-runner`; a held-out dispatch was refused before any credential; release `v0.3.0` deployed through `deployer` after the owner's approval and its deployed smoke run wrote a patch ($0.23). Week 3C spent $0.84.

**2026-10-02, from Week 3D (the showcase).** Design: `2026-10-02-week3d-showcase-design.md`.

1. §15 (README, architecture diagram, ADRs and demo GIF): the README is the landing page (pitch, a result line, a replayed run, how it works, what was measured, engineering highlights, how to run it); `docs/architecture.md` shows the whole deployed system as a generated overview image (`docs/architecture/overview.py`, official GCP icons, kept in sync with its source and the Terraform names by tests) and four Mermaid views (one run, CI/CD and identity, observability and cost, the agent graph generated from the code); decision records 1 to 6 join ADR 7 in `docs/adr/`.
2. Owner decision: the demo GIF is replaced for now by a screenshot of a successful run on the replay site (`docs/media/replay-md-001.png`, linked to the live replay); the live run it needed stays on hold with the other Week 2C live runs.
3. The replay site's graph now fits its panel on a laptop screen (React Flow's minimum zoom lowered to 0.2); before, `route_review` and `deliver_patch` were off screen.
4. Superseded body text noted while writing the ADRs: §7.4 (a temporary clone and a push) and §7.6 (the token in Secret Manager or `.env`) describe an earlier plan; the code follows the Week 2C design (patch applied to the pinned source archive, published through the Git Data API with no clone or push; the token only in the file named by `GITHUB_TOKEN_FILE`, and no token in the deployed agent).
5. Correction to Week 3C item 1: `ci.yaml` does not run on every push and pull request; it skips those that change only `docs/`, root Markdown files or `web/` (the site has its own Pages workflow).
6. Deviations from the Week 3D design, from its reviews: `diagrams` is not a `docs` dependency group (design §4.1 and §7), because adding it to `uv.lock` downgraded the locked `graphviz` package; `make diagrams` runs it in a throwaway `uv run --no-project` environment. The README puts a one-line result under the pitch (design §3 has the results only in "What I measured"), stated with the stall caveat. The README says that live mode has not yet run on a real issue. The overview image is linked full size because its labels cannot be read at README width; a wider layout is left for later.
