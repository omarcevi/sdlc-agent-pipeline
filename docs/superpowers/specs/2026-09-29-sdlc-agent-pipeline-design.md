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
6. S3 is answered by source: `Gemini` on Vertex and `LiteLlm` support `output_schema` together with tools, and other models get ADK's `set_model_response` tool automatically. The Week 1 spike confirms it live.
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
