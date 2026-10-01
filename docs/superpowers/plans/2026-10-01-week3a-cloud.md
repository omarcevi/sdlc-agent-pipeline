# Week 3A: Cloud Sandboxes and Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Spec approved by the owner on 2026-10-01 (commit `141324f`), including the budget and tooling decisions 8 to 12. This plan awaits owner review.

**Goal:** Run the pipeline's code in Agent Runtime sandboxes from the laptop with the same `Environment` contract as Docker, deploy the bench graph to Agent Runtime with Cloud Trace and BigQuery analytics, put every cloud resource under Terraform or one script, and cap the project's spend at $500 with alerts and a hard stop.

**Architecture:** `AgentRuntimeEnvironment` (`app/environment/agent_runtime.py`) is a second implementation of the existing `Environment` protocol. A control-plane adapter (`SandboxControl`) wraps the SDK's synchronous sandbox calls in worker threads. A data-plane `httpx.AsyncClient` talks to the vendored shim through the platform proxy, carrying a short-lived JWT for `sandbox-caller`. `ENVIRONMENT_BACKEND` selects the backend. The existing release paths (the driver's `finally`, `SandboxReleasePlugin`) call its `close()`; the TTL and `scripts/sandbox_infra.py sweep` catch everything else. Terraform owns the platform. `deployment/terraform/single-project` holds the engine (also the sandbox host), the imported repository and service account, and the data expiry. A separate root, `deployment/terraform/budget`, holds the budget, the topic and the `budget-guard` function, and it outlives teardown. The deploy uploads a staging tree (`build/deploy/`) that holds no held-out task, hidden test, solution or `.env`, with explicit `--update-env-vars` and `--update-only`.

**Tech Stack:** Python 3.12, `uv`, google-adk 2.8.0 (unchanged), google-cloud-aiplatform 1.165.1 (`agentplatform.Client`, `agent_engines.sandboxes`), httpx, pytest + pytest-asyncio, Docker (local sandbox image `issue-to-pr-sandbox:dev`), Terraform ≥ 1.11 (installed with Homebrew in Task 10), the scaffold's `hashicorp/google ~> 7.28.0` provider plus `hashicorp/archive`, Cloud Run functions (Python 3.12, `functions-framework`), `gcloud`, agents-cli 1.7.0, model `gemini-3.8-flash`.

**Spec:** `docs/superpowers/specs/2026-10-01-week3a-cloud-design.md` (this plan implements it); parent spec `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` §7.3, §10, §12, §14, §19; spike `docs/spikes/2026-09-30-s1-agent-runtime-sandbox.md`.

## How to read the tasks

As in Week 2C, this plan fixes what must be exact and leaves the implementation to the implementer, test-first. Exact means: names, signatures, env vars, CLI flags, Make targets, Terraform resource names, fixed strings, and the tests that must exist. A test listed here must exist with the behaviour described; its code is the implementer's.

Tasks 1 to 9 are free code work: they create nothing in the cloud and call no model. Tasks 10 to 16 are the owner-approval chain; they list exact commands. Every step that creates, changes or deletes a cloud resource, deploys, installs software or spends money is marked **[OWNER APPROVAL]** with its estimated cost. It needs the owner's explicit approval in the session, given at the time. A step marked **[OWNER REVIEW]** changes a project rule's wording; the owner approves the exact text before it is committed.

## Global Constraints

- Python 3.12 via `uv` only (`uv run ...`). Never call `pip`.
- Work on branch `week3a-cloud`. Never commit to `main` directly.
- Cost guards and loop bounds keep their values: `RUN_BUDGET_USD=1.00`, `MAX_TOOL_CALLS_PER_RUN=75`, `RUN_TIMEOUT_S=1500`, `MODEL_CALL_TIMEOUT_S=480`, `SANDBOX_TTL_S=1800`, 3 test-fix returns, 2 review returns. Prompts, graph edges and model names do not change.
- New defaults: `SANDBOX_READY_TIMEOUT_S=240`. Per command: the 120 s default timeout and the 10,000-character cap in the tools. Template: port 8080 TCP, limits and requests `cpu: "2"`, `memory: "2Gi"`, `internet_access: false`, sandbox `ttl` `"1800s"`, token lifetime 1800 s. Deployed engine: `min_instances 0`, `max_instances 1`, concurrency 4, cpu `1`, memory `4Gi`.
- Budget: $500 converted to TRY at the day's rate (rounded down to whole lira), `currency_code = "TRY"`, `credit_types_treatment = "EXCLUDE_ALL_CREDITS"`, filtered to this project, custom period from 2026-09-29 with no end date, thresholds 0.5, 0.8 and 1.0, Pub/Sub topic `issue-to-pr-budget`, function `budget-guard` that disables billing when cost ≥ budget.
- **Spend rule ($500).** Before every paid step, read the running total in `docs/results/<date>-3a-run-log.md`, then add the step's upper estimate. If the sum would pass $500, stop and ask the owner. The 3A total is also capped at $5; whichever limit is reached first stops the work.
- **Latency gate.** Steps that call a model (Task 14 Step 3, Task 15 Step 4) wait until three consecutive direct tool-using calls to `gemini-3.8-flash` each take under 15 s (2C ledger rule).
- Sandboxes get no credentials and no network. The `sandbox-caller` JWT lives only inside `AgentRuntimeEnvironment` and its HTTP client's headers, never in `os.environ`, session state, events, records, prompts, span attributes, logs, error text or a sandbox. The GitHub token rules are unchanged.
- Benchmark integrity and the seal: held-out task directories (`bench/tasks/*-h[0-9][0-9]/`) are never opened, printed, diffed, uploaded or staged. New code selects them by directory name only and prunes them before descending. Held-out tasks never run on `ENVIRONMENT_BACKEND=agent_runtime`. Review diffs use `':(exclude,glob)bench/tasks/*-h[0-9][0-9]/**'`. Run `uv run python -m bench.validate` after touching `bench/`.
- pytest never calls a real model, GCP or GitHub, except tests marked `cloud` or `cloud_slow` when `ITP_CLOUD_TESTS=1` (Task 13 only).
- Never run `agents-cli deploy` from the project root; only `make deploy` (from `build/deploy/`). Never publish to the budget topic by hand; `make budget-guard-test` is the only test path and it is a dry run.
- New Makefile targets, scripts and Terraform files take the project from `GOOGLE_CLOUD_PROJECT` or Terraform outputs, and never write the project id or number into a committed file. The controller never reads or writes `.env` or `~/.config/issue-to-pr/`; the owner pastes lines into `.env`.
- Commits: plain messages, owner identity only, no `Co-Authored-By` or other AI attribution. Never bypass commit signing.
- Before each commit: `uv run ruff format <files> && uv run ruff check --fix <files>`, `agents-cli lint` passes (write its output to a file and check the exit status), bare `uv run pytest -q` passes (`--tb=no` when held-out tasks are collected).
- `AGENTS.md` and the `Makefile` are written in Task 9 for all of Tasks 1 to 8, because those tasks run in parallel. Each task's report lists the env vars, commands and wiring Task 9 must add. This replaces the per-task AGENTS.md rule of Week 2C for 3A only.

## Review Focus

Five failure modes the spec implies that the per-feature tests would not catch. Each has a test added to the task that owns the code.

1. **A cloud sandbox outlives its run on a driver or plugin path.** Backend unit tests prove that `close()` deletes. They do not prove that the driver's `finally`, the wall-clock cancel, a crash, or `SandboxReleasePlugin` reach `close()` for the cloud backend. Expected: after a normal run, a `RUN_TIMEOUT_S` expiry, a crash and an agents-cli run, the fake control plane has recorded exactly one delete per created sandbox. Tests (Task 2, `tests/unit/test_cloud_backend_release.py`): `test_driver_deletes_the_cloud_sandbox_after_a_normal_run`, `test_driver_deletes_the_cloud_sandbox_when_the_wall_clock_cap_expires`, `test_driver_deletes_the_cloud_sandbox_after_a_crash`, `test_release_plugin_deletes_the_cloud_sandbox_under_agents_cli`.
2. **The sandbox JWT escapes the backend through the pipeline.** The backend's canary covers errors and logs, not what the graph does with the environment. Expected: in a full bench-graph run (FakeLlm, the cloud backend on fakes), a sentinel token appears in shim request headers and nowhere else: not in session state, `events.jsonl`, `record.json`, any model request, any log record, `os.environ` or any span (in-memory exporter). Test (Task 2): `tests/unit/test_cloud_token_canary.py::test_sandbox_token_reaches_only_request_headers_in_a_pipeline_run`.
3. **Sealed content leaves the laptop.** Expected: the staging tree built from the real repository holds no path under a held-out directory, `hidden_tests/`, `solution/`, `shortcut/` or `.env`; the stager never opens a held-out directory; `bench.run` refuses held-out tasks on the cloud backend with a fixed message. Tests: Task 8 `test_real_tree_staging_has_no_sealed_paths` (counts only, pruned walk), `test_stage_skips_heldout_dirs_without_opening_them`; Task 2 `test_bench_run_refuses_heldout_on_the_cloud_backend`.
4. **The hard stop fires when it must not, or not when it must.** Expected: a message built by the check script can never disable billing, even with the dry-run attribute removed; an Eventarc-shaped real over-budget message disables billing once; below-budget, other-budget and malformed messages do nothing. Tests (Task 5): `test_check_script_message_cannot_disable_billing`, `test_eventarc_shaped_messages_end_to_end`.
5. **The deployed agent runs with the wrong configuration.** Possible mistakes: the Docker backend, analytics on locally, span content captured, `.env` values copied, or a second engine created. Expected: the deploy command has exactly the specified `--update-env-vars` keys and values, `--update-only`, and runs from `build/deploy/`, which has no `.env`. Tests (Task 8): `test_deploy_env_is_exact`, `test_deploy_never_runs_from_the_project_root`, `test_staged_tree_has_no_dotenv`. The existing `tests/unit/test_agent_entrypoint.py::test_analytics_is_off_when_only_the_project_is_set` stays.

---

## File Structure

```
app/
  environment/agent_runtime.py     NEW  AgentRuntimeEnvironment, SandboxSettings, SandboxControl, SdkSandboxControl (Task 1)
  environment/factory.py           MOD  agent_runtime branch, check_environment_config (Task 2)
  driver.py                        MOD  calls check_environment_config at run start (Task 2)
  live.py                          MOD  refuses a bad backend config, exit 2 (Task 2)
bench/
  run.py                           MOD  config check; held-out refused on the cloud backend (Task 2)
  probes.py, review_probe.py       MOD  config check (Task 2)
scripts/
  sandbox_infra.py                 NEW  image, template, prune-templates, sweep, delete-engine, env (Task 4)
  budget_guard_check.py            NEW  dry-run wiring check for the hard stop (Task 5)
  stage_deploy.py                  NEW  stage, deploy, smoke (Task 8)
deployment/terraform/single-project/
  apis.tf, service.tf, storage.tf, telemetry.tf, outputs.tf, variables.tf   MOD (Task 6)
  sandbox.tf                       NEW  repository, sandbox-caller, token-creator bindings, imports (Task 6)
deployment/terraform/budget/       NEW  root: providers.tf, variables.tf, apis.tf, budget.tf, guard.tf, outputs.tf (Task 7)
deployment/terraform/budget/function/   NEW  guard.py, main.py, requirements.txt (Task 5)
Dockerfile                         MOD  COPY ./bench ./bench (Task 8)
Makefile                           MOD  all 3A targets (Task 9)
AGENTS.md                          MOD  rules, wiring, env vars, commands (Task 9)
pyproject.toml                     MOD  markers cloud, cloud_slow; aiplatform lower bound (Task 2)
.env.example                       MOD  commented sandbox lines (Task 2)
tests/
  conftest.py                      MOD  scrub new vars, snapshot cloud settings, gate cloud tests (Task 2)
  unit/sandbox_fakes.py            NEW  FakeSandboxControl, FakeShim (Task 1)
  unit/test_agent_runtime_environment.py   NEW (Task 1)
  unit/test_environment_config.py, test_cloud_backend_release.py, test_cloud_token_canary.py,
       test_cloud_marker_gate.py   NEW (Task 2); unit/test_bench_run.py MOD (Task 2)
  integration/test_environment_contract.py NEW  shared Docker and cloud behaviour (Task 3)
  integration/test_docker_environment.py   MOD  Docker-only remainder (Task 3)
  integration/test_agent_runtime_sandbox.py NEW  cloud-only checks (Task 3)
  integration/header_echo_server.py        NEW  stdlib server for the header check (Task 3)
  unit/test_sandbox_infra.py       NEW (Task 4)
  unit/test_budget_guard.py, test_budget_guard_check.py   NEW (Task 5)
  unit/test_terraform_single_project.py    NEW (Task 6)
  unit/test_terraform_budget_root.py       NEW (Task 7)
  unit/test_stage_deploy.py        NEW (Task 8)
docs/results/
  <date>-3a-run-log.md             NEW  running total, rate, versions, resources, checks (Tasks 10–16)
  <date>-3a-cloud-parity.md        NEW  parity report (Task 14)
docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md   MOD  §19 block (Task 16)
```

## Order, parallelism and file ownership

| Task | Depends on | Spec | Parallel group | Owns (only this task edits these) |
|---|---|---|---|---|
| 1 Cloud sandbox backend | — | §4.1, §4.3–§4.6 | A | `app/environment/agent_runtime.py`, `tests/unit/sandbox_fakes.py`, `tests/unit/test_agent_runtime_environment.py` |
| 2 Backend selection, config, entry points | 1 (interfaces; merges after 1) | §4.2, §4.7, §8 | A | `app/environment/factory.py`, `app/driver.py`, `app/live.py`, `bench/run.py`, `bench/probes.py`, `bench/review_probe.py`, `tests/conftest.py`, `pyproject.toml`, `.env.example`, Task 2 test files |
| 3 Contract suite and opt-in cloud tests | 1, 2 | §7.2, §7.3 | B | `tests/integration/test_environment_contract.py`, `test_docker_environment.py`, `test_agent_runtime_sandbox.py`, `header_echo_server.py` |
| 4 Sandbox infrastructure script | — | §4.7, §5.2, §5.3, §5.5 | A | `scripts/sandbox_infra.py`, `tests/unit/test_sandbox_infra.py` |
| 5 Budget guard function and check | — | §5.4 | A | `deployment/terraform/budget/function/*`, `scripts/budget_guard_check.py`, their tests |
| 6 Terraform: single-project | — | §5.1, §6.4 | A | `deployment/terraform/single-project/*`, `tests/unit/test_terraform_single_project.py` |
| 7 Terraform: budget root | 5 (interfaces; merges after 5) | §5.4 | A | `deployment/terraform/budget/*.tf`, `tests/unit/test_terraform_budget_root.py` |
| 8 Deploy staging tree | — | §6.1–§6.3 | A | `scripts/stage_deploy.py`, `Dockerfile`, `tests/unit/test_stage_deploy.py` |
| 9 Make targets, AGENTS.md, review | 1–8 | §5, §10, §11 | — | `Makefile`, `AGENTS.md` |
| 10 Terraform and the budget root **[OWNER APPROVAL]** | 9 | §5.4, decisions 8–12 | — | `docs/results/<date>-3a-run-log.md` (opens it) |
| 11 Platform infrastructure **[OWNER APPROVAL]** | 10 | §5.1 | — | — |
| 12 Image, template, spike leftovers **[OWNER APPROVAL]** | 11 | §5.2, §5.3 | — | — |
| 13 Cloud opt-in tests and the gate **[OWNER APPROVAL]** | 12 | §7.3 | — | — |
| 14 Parity **[OWNER APPROVAL]** | 13 | §7.4, §7.6 | — | `docs/results/<date>-3a-cloud-parity.md` |
| 15 Deploy and deployed smoke **[OWNER APPROVAL]** | 14 | §6, §7.5 | — | — |
| 16 Teardown rehearsal and close-out | 15 | §5.5, §12 | — | parent spec §19 |

Group A (Tasks 1, 2, 4, 5, 6, 7, 8) runs in parallel: no two of them edit the same file.

- Task 2 consumes Task 1's interfaces, and Task 7 consumes Task 5's function directory, entry point and env names. Each pair agrees the interfaces below before starting, so both tasks start at once. Task 2 merges after Task 1, and Task 7 after Task 5.
- Group B (Task 3) starts once Tasks 1 and 2 are merged.
- Task 9 merges last and ends with the whole-branch review.
- Tasks 10 to 16 run in order, one at a time.

---

### Task 1: Cloud sandbox backend

**Files:**
- Create: `app/environment/agent_runtime.py`, `tests/unit/sandbox_fakes.py`, `tests/unit/test_agent_runtime_environment.py`

**Interfaces:**
- Consumes: `app.environment.base` (`Environment`, `ExecResult`, `InfraError`, `WORKDIR`, `DEFAULT_TIMEOUT_S`).
- Produces:

  ```python
  SANDBOX_PORT = "8080"
  SANDBOX_OWNER = "issue-to-pr"
  DEFAULT_READY_TIMEOUT_S = 240.0
  READY_POLL_S = 2.0
  READY_ATTEMPT_TIMEOUT_S = 15.0
  CREATE_GRACE_S = 60.0
  TIMEOUT_EXIT_CODES = (124, 137)
  TEMPLATE_NOT_USABLE = "sandbox template is not usable"

  @dataclass(frozen=True)
  class SandboxSettings:
      engine: str; template: str; caller_sa: str; project: str
      location: str            # parsed from engine, never from GOOGLE_CLOUD_LOCATION
      ttl_s: int; ready_timeout_s: float
      @classmethod
      def from_env(cls, environ: Mapping[str, str] | None = None) -> "SandboxSettings"   # ValueError, fixed messages (Task 2 lists them)

  @dataclass(frozen=True)
  class SandboxHandle:
      name: str                # full resource name; never leaves the environment object
      hostname: str            # connection_info.load_balancer_hostname
      routing_token: str

  class SandboxControl(Protocol):
      async def check_template(self, template: str) -> None          # InfraError(TEMPLATE_NOT_USABLE)
      async def sign_token(self, service_account: str, lifetime_s: int) -> str
      async def create(self, *, engine: str, template: str, ttl_s: int, display_name: str) -> SandboxHandle
      async def delete(self, name: str) -> None                      # "not found" is success

  class SdkSandboxControl:      # agentplatform.Client(project, location, http_options={"api_version": "v1beta1"});
      def __init__(self, settings: SandboxSettings) -> None          # public sync calls via asyncio.to_thread

  class AgentRuntimeEnvironment:
      env_id: str               # "itp-<12 hex>"
      @classmethod
      async def start(cls, *, settings: SandboxSettings | None = None,
                      control: SandboxControl | None = None,
                      transport: httpx.AsyncBaseTransport | None = None,
                      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                      clock: Callable[[], float] = time.monotonic) -> "AgentRuntimeEnvironment"
      async def exec(self, command: str, *, timeout: float = DEFAULT_TIMEOUT_S, cwd: str = WORKDIR) -> ExecResult
      async def read_file(self, path: str) -> str
      async def write_file(self, path: str, content: str) -> None
      async def upload_dir(self, local_dir: Path, dest: str = WORKDIR) -> None
      async def close(self) -> None
      async def proxy_request(self, method: str, path: str, *, port: str = SANDBOX_PORT, **kwargs) -> httpx.Response
          # for tests/integration only; nothing under app/ calls it
  ```

- `tests/unit/sandbox_fakes.py`: `FakeSandboxControl` (records `check_template`, `sign_token`, `create`, `delete` calls; per-call failure injection; a `create` that can block until released, for cancellation tests; signs a configurable sentinel token) and `FakeShim` (an `httpx.MockTransport` handler emulating `/exec`, `/files`, `/files/zip`; queued responses per path; records every request with headers).

**Required behaviour** (spec §4.3, §4.4)

1. `start()`:
   1. Check the template once per process (a module-level set; a test fixture resets it).
   2. Sign one token for `caller_sa` with `lifetime_s = ttl_s`.
   3. Create with `ttl = f"{ttl_s}s"`, `owner = SANDBOX_OWNER` and `display_name = env_id`.
   4. Poll readiness: `POST /exec {"command": "true", "timeout": 10}` every `READY_POLL_S`, each attempt bounded by `READY_ATTEMPT_TIMEOUT_S`, until HTTP 200 with `exit_code == 0`, for at most `ready_timeout_s`. Never call `/healthz`. On expiry raise `InfraError(f"sandbox did not become ready in {n:g} s")`.
   5. Run `mkdir -p /workspace/repo`.
2. A failure or cancellation after `create` returns deletes the sandbox before re-raising. If `start()` is cancelled while `create` runs, it waits for `create` (shielded, at most `CREATE_GRACE_S`), deletes the sandbox `create` returned, and only then re-raises the cancellation.
3. `exec` sends `{"command": "timeout -k 5 <n> sh -c " + shlex.quote(command), "cwd": cwd, "timeout": n + 15}`, where `n = max(1, ceil(timeout))`, with an HTTP read timeout of `n + 30`. `timed_out` is true when the shim says so or the exit code is in `TIMEOUT_EXIT_CODES`. Any status other than 200, or a transport error, raises `InfraError`. No retry.
4. `read_file`: `GET /files?path=`; the base64 content is decoded as UTF-8 with `errors="replace"`. 404 raises `FileNotFoundError(path)`; 400 and 403 raise `OSError(<detail>)`; 5xx and transport errors are retried twice, then raise `InfraError`.
5. `write_file`: `POST /files?path=` with `{"content_b64": ...}`. 404, 400 and 403 raise `OSError`; 5xx and transport errors raise `InfraError`. No retry.
6. `upload_dir`: refuses any symlink in `local_dir` (`InfraError("upload refused: links are not supported")`), zips the tree in memory, `POST /files/zip?path=<dest>`. Any failure raises `InfraError`.
7. Every request carries `Authorization: Bearer <token>`, `X-Sandbox-Routing-Token` and `X-Sandbox-Port`. Error text holds the method, the path, the status and at most 200 characters of the body, never headers. `repr()` of the environment shows `env_id` only.
8. `close()` deletes once, is idempotent, and treats "not found" as success. Any other delete failure is raised (the release paths log it). It also closes the HTTP client.
9. `agentplatform` is imported only inside `SdkSandboxControl`, so Docker runs and unit tests never import it.

**Tests** (`tests/unit/test_agent_runtime_environment.py`, `FakeSandboxControl` + `FakeShim`, fake `sleep` and `clock`)

- `test_start_creates_from_the_template_with_ttl_owner_and_display_name`; `test_start_signs_one_token_for_the_caller_sa_with_the_ttl_lifetime`; `test_template_is_checked_once_per_process`; `test_unusable_template_is_an_infra_error_and_creates_nothing`; `test_start_makes_the_repo_directory`.
- `test_readiness_retries_502_until_the_shim_answers`; `test_readiness_never_calls_healthz`; `test_readiness_timeout_deletes_the_sandbox_once`.
- `test_failure_after_create_deletes_the_sandbox`; `test_cancellation_during_create_deletes_the_sandbox`; `test_cancellation_during_readiness_deletes_the_sandbox`.
- `test_every_request_carries_the_three_headers`; `test_exec_wraps_the_command_in_timeout_and_quotes_it`; `test_exec_sets_the_shim_backstop_and_http_timeout`; `test_exec_reports_timeout_exit_codes` (124, 137, shim `timed_out`); `test_exec_non_200_and_transport_errors_are_infra_errors`; `test_exec_is_not_retried`.
- `test_read_file_decodes_with_replacement`; `test_read_file_maps_404_to_file_not_found`; `test_read_file_maps_400_and_403_to_os_error`; `test_read_file_retries_5xx_twice_then_infra_error`; `test_write_file_sends_base64_and_maps_errors`; `test_write_file_is_not_retried`.
- `test_upload_dir_posts_a_zip_of_the_tree`; `test_upload_dir_refuses_links`.
- `test_close_deletes_once_and_is_idempotent`; `test_close_treats_not_found_as_success`; `test_close_raises_a_failing_delete`.
- `test_env_id_is_short_and_hides_the_resource_name`; `test_error_text_never_holds_headers`; `test_token_canary_in_the_backend` (the sentinel token appears in request headers only: not in exception text, `ExecResult`, log records at DEBUG, `os.environ` or `repr`).
- `test_settings_take_the_location_from_the_engine` (with `GOOGLE_CLOUD_LOCATION=global` set).

- [ ] **Step 1:** Write `sandbox_fakes.py` and the tests. Run them; capture the failures (RED).
- [ ] **Step 2:** Implement `agent_runtime.py` until green.
- [ ] **Step 3:** Lint, full suite. Report for Task 9: the runtime-wiring bullet (§4.3–§4.6) and the env rows.
- [ ] **Step 4:** Commit: `feat: Agent Runtime sandbox backend behind the Environment protocol`.

---

### Task 2: Backend selection, configuration and entry points

**Files:**
- Create: `tests/unit/test_environment_config.py`, `tests/unit/test_cloud_backend_release.py`, `tests/unit/test_cloud_token_canary.py`, `tests/unit/test_cloud_marker_gate.py`
- Modify: `app/environment/factory.py`, `app/driver.py`, `app/live.py`, `bench/run.py`, `bench/probes.py`, `bench/review_probe.py`, `tests/conftest.py`, `tests/unit/test_bench_run.py`, `pyproject.toml`, `.env.example`

**Interfaces:**
- Consumes: Task 1's `SandboxSettings.from_env`, `AgentRuntimeEnvironment.start`, `tests/unit/sandbox_fakes.py`.
- Produces:

  ```python
  # app/environment/factory.py
  BACKENDS = ("docker", "agent_runtime")
  def check_environment_config(environ: Mapping[str, str] | None = None) -> None   # ValueError, fixed messages
  async def start_environment() -> Environment
  ```

  Fixed messages (exact):
  - `unsupported ENVIRONMENT_BACKEND=<value>; use docker or agent_runtime`
  - `<NAME> must be set for ENVIRONMENT_BACKEND=agent_runtime` (for `SANDBOX_ENGINE`, `SANDBOX_TEMPLATE`, `SANDBOX_CALLER_SA`, `GOOGLE_CLOUD_PROJECT`)
  - `SANDBOX_ENGINE must look like projects/<p>/locations/<l>/reasoningEngines/<id>`
  - `SANDBOX_TEMPLATE must be a template under SANDBOX_ENGINE`
  - `SANDBOX_CALLER_SA must be a service account email ending in .iam.gserviceaccount.com`
  - `SANDBOX_READY_TIMEOUT_S must be a positive number below RUN_TIMEOUT_S`
  - `bench.run`: `held-out tasks never run on ENVIRONMENT_BACKEND=agent_runtime`
- `tests/conftest.py` gains:
  - the scrub list gains `SANDBOX_ENGINE`, `SANDBOX_TEMPLATE`, `SANDBOX_CALLER_SA`, `SANDBOX_READY_TIMEOUT_S`;
  - `CLOUD_SETTINGS: dict[str, str]`, a snapshot of those four plus `GOOGLE_CLOUD_PROJECT`, taken before the scrub and only when `ITP_CLOUD_TESTS=1`;
  - a `pytest_collection_modifyitems` hook that adds a skip, reason `"cloud test: set ITP_CLOUD_TESTS=1 (make test-cloud)"`, to every item marked `cloud` or `cloud_slow` unless `ITP_CLOUD_TESTS == "1"`.
- `pyproject.toml`:
  - markers `"cloud: talks to real Agent Runtime sandboxes; skipped unless ITP_CLOUD_TESTS=1 (make test-cloud)"` and `"cloud_slow: like cloud, takes minutes (templates, TTL)"`;
  - `google-cloud-aiplatform[evaluation,agent-engines]>=1.165.1,<2.0.0`, the locked version, which ships `agentplatform`.
- `.env.example` gains, commented out: `ENVIRONMENT_BACKEND=agent_runtime`, `SANDBOX_ENGINE=projects/<project>/locations/us-central1/reasoningEngines/<id>`, `SANDBOX_TEMPLATE=<SANDBOX_ENGINE>/sandboxEnvironmentTemplates/<id>`, `SANDBOX_CALLER_SA=sandbox-caller@<project>.iam.gserviceaccount.com`. One line says they are printed by `uv run python scripts/sandbox_infra.py env`. Another says never to set `BQ_ANALYTICS_ENABLED` here.

**Required behaviour** (spec §4.2, §4.7, §8)

1. `check_environment_config` does nothing new for `docker`. For `agent_runtime` it validates as listed, and the run-timeout comparison uses the driver's `run_timeout_s()` default.
2. `start_environment` returns `DockerEnvironment.start()` or `AgentRuntimeEnvironment.start()`.
3. The config check is a configuration error before any run, never an infra failure:
   - `bench.run`, `bench.probes`, `bench.review_probe` and `app.live` call it after `load_dotenv()`. On `ValueError` they print `error: <message>` and exit 2.
   - The driver calls it in `_run` next to `run_timeout_s()`.
4. `bench.run` refuses held-out tasks with the backend `agent_runtime`, with the fixed message above, before `--confirm-heldout` is considered. It exits 2 and names no task.

**Tests**

- `test_environment_config.py`: `test_docker_needs_nothing_new`; `test_unknown_backend_is_refused`; `test_agent_runtime_requires_engine_template_caller_and_project`; `test_engine_must_have_the_engine_form`; `test_template_must_lie_under_the_engine`; `test_caller_must_be_a_service_account_email`; `test_ready_timeout_must_be_positive_and_below_run_timeout`; `test_factory_starts_the_chosen_backend`; `test_entry_points_check_the_environment_config` (parametrised over `bench.run`, `bench.probes validate`, `bench.review_probe`, `app.live`: each exits 2 with the fixed message before any run or sandbox start).
- `test_bench_run.py`: `test_bench_run_refuses_heldout_on_the_cloud_backend` (synthetic held-out task; the output names no task); `test_bad_cloud_config_exits_2_before_any_run`.
- `test_cloud_backend_release.py` (Review Focus 1; bench graph with `FakeLlm`, the factory patched to start `AgentRuntimeEnvironment` on `FakeSandboxControl` + `FakeShim`): `test_driver_deletes_the_cloud_sandbox_after_a_normal_run`; `test_driver_deletes_the_cloud_sandbox_when_the_wall_clock_cap_expires` (tiny `RUN_TIMEOUT_S`, a model call that never returns); `test_driver_deletes_the_cloud_sandbox_after_a_crash`; `test_release_plugin_deletes_the_cloud_sandbox_under_agents_cli` (an `App` with `app.agent`'s plugin list and the bench graph on `FakeLlm`, under `InMemoryRunner`; `app.agent.root_agent` itself is not used, since it builds real models).
- `test_cloud_token_canary.py` (Review Focus 2): `test_sandbox_token_reaches_only_request_headers_in_a_pipeline_run`.
- `test_cloud_marker_gate.py`: `test_cloud_tests_are_skipped_unless_opted_in` (uses `pytester`; covers `cloud` and `cloud_slow`).

- [ ] **Step 1:** Tests (RED).
- [ ] **Step 2:** Implement until green. `uv run python -m bench.validate` still prints `ok` for every task.
- [ ] **Step 3:** Lint, full suite (`--tb=no`). Report for Task 9: the new env rows, the testing-convention change and the held-out rule.
- [ ] **Step 4:** Commit: `feat: choose the sandbox backend; config errors fail before any run`.

---

### Task 3: Contract suite and opt-in cloud tests

**Files:**
- Create: `tests/integration/test_environment_contract.py`, `tests/integration/test_agent_runtime_sandbox.py`, `tests/integration/header_echo_server.py`
- Modify: `tests/integration/test_docker_environment.py` (keeps only the Docker-only tests)

**Interfaces:**
- Consumes: `DockerEnvironment.start`, `AgentRuntimeEnvironment.start(settings=SandboxSettings.from_env(tests.conftest.CLOUD_SETTINGS))`, `AgentRuntimeEnvironment.proxy_request`, `app.tools.files.list_dir`, `tests.fakes.fake_tool_context`.
- Produces:
  - A fixture `env`, parametrised `docker` (marks `docker` plus the existing skip without Docker) and `agent_runtime` (mark `cloud`).
  - `header_echo_server.py`: standard library only. It serves port 8081 and answers each request with JSON `{"authorization": bool, "x_sandbox_routing_token": bool, "x_sandbox_port": bool}`. It never echoes a value.

**Required behaviour** (spec §7.2, §7.3)

1. These tests move to the contract suite unchanged in substance:
   - `test_upload_and_run_pytest`, `test_read_write_roundtrip`, `test_missing_file_raises_file_not_found`, `test_timeout_is_reported`, `test_no_network`;
   - `test_read_file_on_a_directory_is_an_os_error_not_infra`, `test_write_file_failures_are_os_errors_not_infra`, `test_stderr_mentioning_not_running_returns_normally`;
   - `test_sigterm_ignoring_command_is_reported_timed_out`, `test_sub_second_timeout_is_still_enforced`, `test_closed_sandbox_raises_infra_error`, `test_list_dir_tool_against_a_real_sandbox`.

   New in the suite: `test_children_of_a_timed_out_command_are_stopped`.
2. These stay Docker-only in `test_docker_environment.py`: `test_root_filesystem_is_read_only`, `test_sandbox_self_destructs_after_its_ttl`, `test_sandbox_has_no_credentials_from_the_host`, `test_sandbox_cannot_see_the_token_file`.
3. Cloud-only tests in `test_agent_runtime_sandbox.py`. Each deletes what it creates in a `finally`, and none prints the token.
   - `test_metadata_server_gives_no_token` (`cloud`): both `metadata.google.internal` and `169.254.169.254`; prints a boolean only.
   - `test_authorization_header_does_not_reach_the_container` (`cloud_slow`):
     - creates a test-only template with ports 8080 and 8081 and the configured template's image;
     - starts `header_echo_server.py` in the background, through the shim's `POST /processes` or `setsid` via `exec` (the test's docstring says which);
     - calls `proxy_request("GET", "/", port="8081")` and asserts `authorization` is false.
     - If the platform does not route to 8081, it fails with `"platform did not route to a second port; the header question stays open"`.
   - `test_identity_and_environment_hold_no_credentials` (`cloud`): uid 1000; variable names only, checked with the existing `CREDENTIAL_NAME` pattern.
   - `test_root_filesystem_writability_is_recorded` (`cloud`): prints `root filesystem writable: <bool>` and asserts nothing.
   - `test_sandbox_is_gone_after_its_ttl` (`cloud_slow`): `ttl` 120 s; deleted or gone within 10 minutes.

**Tests:** the ones above. `make test-docker` runs the Docker half; the cloud half only runs in Task 13.

- [ ] **Step 1:** Move the Docker tests into the contract suite with the `docker` parameter only; `make test-docker` still green.
- [ ] **Step 2:** Add the `agent_runtime` parameter, the cloud-only file and the echo server. Run `uv run pytest -q`: every cloud test is skipped, with the reason from Task 2.
- [ ] **Step 3:** Lint. Commit: `test: one environment contract for Docker and Agent Runtime; opt-in cloud checks`.

---

### Task 4: Sandbox infrastructure script

**Files:**
- Create: `scripts/sandbox_infra.py`, `tests/unit/test_sandbox_infra.py`

**Interfaces:**
- Consumes: `terraform -chdir=deployment/terraform/single-project output -json` (outputs `agent_runtime_resource_name`, `sandbox_image_repository`, `sandbox_caller_email`), `gcloud`, `git`.
- Produces:

  ```
  uv run python scripts/sandbox_infra.py image
  uv run python scripts/sandbox_infra.py template [--find-only]
  uv run python scripts/sandbox_infra.py prune-templates [--all] [--dry-run]
  uv run python scripts/sandbox_infra.py sweep [--all] [--dry-run]
  uv run python scripts/sandbox_infra.py delete-engine --name NAME --expect-display-name NAME [--dry-run]
  uv run python scripts/sandbox_infra.py env
  ```
  Common flag: `--engine NAME` (default `SANDBOX_ENGINE`, else the Terraform output). Exit codes: 0 done; 1 a check failed (dirty tree, no match with `--find-only`, display name mismatch, a template with internet); 2 usage or missing configuration.

  ```python
  SWEEP_GRACE_S = 60
  TERMINAL_STATES = frozenset({"STATE_DELETED", "STATE_DEPROVISIONING"})
  TEMPLATE_PORT = 8080
  TEMPLATE_RESOURCES = {"limits": {"cpu": "2", "memory": "2Gi"}, "requests": {"cpu": "2", "memory": "2Gi"}}
  def image_tag(repo_root: Path) -> str                     # first 12 chars of `git rev-parse HEAD:sandbox_image`
  def image_uri(repository: str, tag: str) -> str           # "<repository>/sandbox:<tag>"
  def matching_template(templates: Iterable[Template], image: str) -> str | None
  def expired(sandboxes: Iterable[Sandbox], *, now: datetime, ttl_s: int, grace_s: int = SWEEP_GRACE_S) -> list[str]
  class Platform(Protocol):   # the SDK behind it; a fake in tests
      def list_templates(self, engine: str) -> list[Template]; def get_template(self, name: str) -> Template
      def create_template(self, engine: str, image: str, display_name: str) -> str; def delete_template(self, name: str) -> None
      def list_sandboxes(self, engine: str) -> list[Sandbox]; def delete_sandbox(self, name: str) -> None
      def get_engine(self, name: str) -> Engine; def delete_engine(self, name: str, *, force: bool) -> None
  ```

**Required behaviour** (spec §5.2, §4.7, §5.3, §5.5)

1. `image`:
   - refuses (exit 1, `"sandbox_image/ has uncommitted changes"`) when `git status --porcelain -- sandbox_image` is not empty;
   - does nothing when `gcloud artifacts docker images describe <uri>` succeeds;
   - otherwise runs `gcloud builds submit sandbox_image --tag <uri> --project $GOOGLE_CLOUD_PROJECT`;
   - prints the URI.
2. `template`:
   - reuses the first template whose `image_uri` equals the current image and whose state ends with `ACTIVE`;
   - when ports are returned, they must be exactly `[8080]`;
   - a returned `internet_access` of true exits 1 (`"matching template allows the internet: refusing"`);
   - otherwise it creates a template (display name `issue-to-pr-sandbox-<tag>`, port 8080 TCP, `TEMPLATE_RESOURCES`, `internet_access: false`);
   - `--find-only` never creates and exits 1 without a match;
   - prints `SANDBOX_TEMPLATE=<name>`.
3. `prune-templates` deletes templates whose image is not the current one; `--all` deletes every template.
4. `sweep` deletes every sandbox whose state is not in `TERMINAL_STATES` and whose `create_time` is older than `SANDBOX_TTL_S + SWEEP_GRACE_S`. `--all` ignores age. It prints one line per sandbox and a final `swept <n>`.
5. `delete-engine` deletes only when the engine's display name equals `--expect-display-name`, with `force=True`.
6. Every deleting subcommand with `--dry-run` lists what it would delete and deletes nothing.
7. `env` prints `SANDBOX_ENGINE=...`, `SANDBOX_TEMPLATE=...` (via the `--find-only` logic), `SANDBOX_CALLER_SA=...` and `# ENVIRONMENT_BACKEND=agent_runtime`.
8. The script never writes `.env` and never prints a token.

**Tests** (`tests/unit/test_sandbox_infra.py`, a fake `Platform`, a recording fake for subprocess calls)

- `test_image_tag_is_the_sandbox_image_tree_id`; `test_image_refuses_uncommitted_changes`; `test_image_skips_an_existing_tag`; `test_image_builds_a_missing_tag`.
- `test_template_reuses_an_active_match`; `test_template_ignores_inactive_or_other_images`; `test_template_requires_port_8080_only_when_ports_are_returned`; `test_template_refuses_a_match_with_internet`; `test_template_creates_with_2_cpu_2gi_port_8080_no_internet`; `test_find_only_never_creates_and_fails_without_a_match`.
- `test_prune_keeps_the_current_image`; `test_prune_all_deletes_every_template`.
- `test_sweep_never_deletes_a_sandbox_younger_than_ttl_plus_grace`; `test_sweep_skips_deleted_and_deprovisioning`; `test_sweep_all_ignores_age`; `test_dry_run_deletes_nothing` (parametrised over the deleting subcommands).
- `test_delete_engine_requires_the_exact_display_name`; `test_delete_engine_forces_children`.
- `test_env_prints_the_four_lines_without_secrets`.

- [ ] **Step 1:** Tests (RED). **Step 2:** Implement until green.
- [ ] **Step 3:** Lint, full suite. Report for Task 9: the Make targets `sandbox-cloud`, `sweep-sandboxes` and the teardown steps, and the commands rows.
- [ ] **Step 4:** Commit: `feat(infra): sandbox image, template, sweeper and engine cleanup script`.

---

### Task 5: Budget guard function and wiring check

**Files:**
- Create: `deployment/terraform/budget/function/guard.py`, `deployment/terraform/budget/function/main.py`, `deployment/terraform/budget/function/requirements.txt`, `scripts/budget_guard_check.py`, `tests/unit/test_budget_guard.py`, `tests/unit/test_budget_guard_check.py`

**Interfaces:**
- Produces:

  ```python
  # guard.py — standard library only
  DRY_RUN_ATTRIBUTE = "itp_dry_run"
  PERMISSIONS = ("resourcemanager.projects.deleteBillingAssignment", "resourcemanager.projects.get")
  @dataclass(frozen=True)
  class GuardConfig:
      project_id: str; budget_id: str; billing_account: str
      @classmethod
      def from_env(cls, environ: Mapping[str, str]) -> "GuardConfig"    # GUARD_PROJECT_ID, GUARD_BUDGET_ID, GUARD_BILLING_ACCOUNT
  @dataclass(frozen=True)
  class Decision:
      action: Literal["ignore", "none", "dry_run", "disable"]; reason: str
      cost: float | None; budget: float | None; currency: str | None
  def decide(data: bytes, attributes: Mapping[str, str], config: GuardConfig) -> Decision

  # main.py — imports the cloud libraries only inside make_clients()
  class Clients(Protocol):
      def billing_enabled(self, project_id: str) -> bool                      # Cloud Billing projects.getBillingInfo
      def disable_billing(self, project_id: str) -> None                      # projects.updateBillingInfo, billingAccountName ""
      def granted_permissions(self, project_id: str, permissions: Sequence[str]) -> set[str]   # testIamPermissions
  def make_clients() -> Clients
  def handle(event_data: dict, config: GuardConfig, clients: Clients, log: Callable[[dict], None]) -> dict
  @functions_framework.cloud_event
  def stop_billing(cloud_event) -> None       # entry point
  ```

  Log line keys (one JSON line per message): `severity`, `decision`, `reason`, `cost`, `budget`, `currency`, `budget_id`, `message_id`, `result`. Dry-run lines add `billing_enabled` and `permissions` (a map of the two names to booleans). `result` ∈ `"ignored"`, `"no action"`, `"dry run"`, `"already disabled"`, `"billing disabled"`, `"error"`. Reasons (exact): `"malformed"`, `"other budget"`, `"below budget"`, `"dry run"`, `"not from Cloud Billing"`, `"cost at or above budget"`.
- `requirements.txt`: `functions-framework`, `google-cloud-billing` and `google-cloud-resource-manager`, each pinned with `==` to the version current when the task is done.
- `scripts/budget_guard_check.py`:

  ```
  uv run python scripts/budget_guard_check.py [--timeout-s 180]
  ```
  ```python
  def build_test_message(budget_id: str, amount: float, currency: str = "TRY") -> tuple[str, dict[str, str]]
      # payload with costAmount == budgetAmount == amount; attributes {budgetId, itp_dry_run: "1"}; never billingAccountId
  def publish_args(topic: str, message: str, attributes: dict[str, str], project: str) -> list[str]   # gcloud pubsub topics publish ...
  def passed(log_line: dict) -> bool          # decision == "dry_run" and both permissions true
  ```
  It reads `budget_topic`, `budget_id` and `budget_amount_try` from `terraform -chdir=deployment/terraform/budget output -json`. It publishes once, then polls `gcloud logging read` for the `budget-guard` line with the returned message id, for up to `--timeout-s`. It prints the line and exits 0 only when `passed`. There is no option that drops the dry-run attribute.

**Required behaviour** (spec §5.4): `decide` applies the rules in this order:

1. malformed → `ignore`;
2. `budgetId` differs → `ignore`;
3. `costAmount < budgetAmount` → `none`;
4. `itp_dry_run == "1"` → `dry_run`;
5. `billingAccountId` differs or is missing → `ignore`;
6. otherwise → `disable`.

`handle` acts as follows:

- On `dry_run` it calls `billing_enabled` and `granted_permissions`, never `disable_billing`.
- On `disable` it calls `billing_enabled`. If billing is already off, it logs `already disabled`; otherwise it calls `disable_billing` once.
- An API error on the disable path is logged with `result: "error"` and raised.
- Every message writes exactly one log line, and no log line holds a credential.

**Tests**

- `test_budget_guard.py` (`guard.py` loaded by path; `main.py` loaded with fake `Clients` and with `functions_framework` stubbed):
  - `test_malformed_payload_is_ignored`; `test_other_budget_is_ignored`; `test_below_budget_does_nothing`; `test_cost_equal_to_budget_acts`;
  - `test_dry_run_never_disables`; `test_missing_billing_account_never_disables`; `test_wrong_billing_account_never_disables`; `test_real_over_budget_message_disables`;
  - `test_dry_run_never_calls_update_billing_info`; `test_real_path_disables_once`; `test_already_disabled_project_is_left_alone`; `test_api_error_is_logged_and_raised`; `test_each_message_writes_exactly_one_log_line`;
  - `test_guard_imports_only_the_standard_library`;
  - Review Focus 4: `test_eventarc_shaped_messages_end_to_end`, CloudEvent data shaped as Eventarc delivers it (`message.data` base64, `message.attributes`, `message.messageId`), over all six outcomes.
- `test_budget_guard_check.py`: `test_build_test_message_always_sets_dry_run_and_never_billing_account`; `test_publish_args_are_exact`; `test_passed_needs_dry_run_and_both_permissions`; `test_check_fails_when_no_log_line_arrives`; Review Focus 4 `test_check_script_message_cannot_disable_billing` (the built message through `guard.decide` with a matching config gives `dry_run`, and with `itp_dry_run` removed gives `ignore`).

- [ ] **Step 1:** Tests (RED). **Step 2:** Implement until green.
- [ ] **Step 3:** Lint (the function directory is linted; `ty` ignores its unresolved cloud imports per `pyproject.toml`). Report for Task 9: `budget-guard-test` and the hard-stop rule text.
- [ ] **Step 4:** Commit: `feat(infra): budget guard function that disables billing at 100%, with a dry-run check`.

---

### Task 6: Terraform — single-project root

**Files:**
- Create: `deployment/terraform/single-project/sandbox.tf`, `tests/unit/test_terraform_single_project.py`
- Modify: `deployment/terraform/single-project/apis.tf`, `service.tf`, `storage.tf`, `telemetry.tf`, `outputs.tf`, `variables.tf`

**Interfaces** (exact resource names):
- `apis.tf`: `local.services` gains `artifactregistry.googleapis.com`, `iamcredentials.googleapis.com`.
- `service.tf`: `google_vertex_ai_reasoning_engine.app` `deployment_spec`: `min_instances = 0`, `max_instances = 1`, `container_concurrency = 4`, `resource_limits = { cpu = "1", memory = "4Gi" }`. The env block and `lifecycle.ignore_changes` are unchanged.
- `sandbox.tf`:
  - `google_artifact_registry_repository.sandbox` (`repository_id = "issue-to-pr"`, `format = "DOCKER"`, `location = var.region`);
  - `google_service_account.sandbox_caller` (`account_id = "sandbox-caller"`);
  - `google_project_iam_member.sandbox_caller_aiplatform_user` (`roles/aiplatform.user`);
  - `google_service_account_iam_member.app_sa_signs_sandbox_tokens` and `google_service_account_iam_member.operator_signs_sandbox_tokens` (`roles/iam.serviceAccountTokenCreator` on `sandbox_caller`; the operator is `"user:${data.google_client_openid_userinfo.me.email}"`, or `var.operator_member` when that is set);
  - `data "google_client_openid_userinfo" "me"`;
  - `import` blocks with `to = google_artifact_registry_repository.sandbox`, `id = "projects/${var.project_id}/locations/${var.region}/repositories/issue-to-pr"`, and `to = google_service_account.sandbox_caller`, `id = "projects/${var.project_id}/serviceAccounts/sandbox-caller@${var.project_id}.iam.gserviceaccount.com"`.
- `variables.tf`: `variable "operator_member"` (string, default `""`).
- `storage.tf`: `google_storage_bucket.logs_data_bucket` gains `force_destroy = true` and `lifecycle_rule { condition { age = 30 } action { type = "Delete" } }`.
- `telemetry.tf`: `google_bigquery_dataset.telemetry_dataset` gains `default_partition_expiration_ms = 2592000000` and `delete_contents_on_destroy = true`; `google_bigquery_table.genai_logs_table.time_partitioning` gains `expiration_ms = 2592000000`.
- `outputs.tf`: `sandbox_caller_email`, `sandbox_image_repository` (`"${var.region}-docker.pkg.dev/${var.project_id}/issue-to-pr"`).

**Required behaviour:** Terraform is not installed until Task 10 (decision 12). This task writes the files and a text-level test; `terraform fmt`, `init` and `validate` run in Task 10 Step 2, and any fixes are committed there.

**Tests** (`tests/unit/test_terraform_single_project.py`; plain text checks with a fixed failure message):
- `test_engine_is_created_scaled_to_zero_and_small`
- `test_logs_bucket_expires_objects_after_30_days_and_can_be_destroyed`
- `test_dataset_expires_partitions_after_30_days`
- `test_sandbox_resources_and_imports_exist`
- `test_new_apis_are_enabled`
- `test_no_tf_file_names_the_project` (the `project_id` value in `vars/env.tfvars` appears in no `.tf` file)

- [ ] **Step 1:** Tests (RED). **Step 2:** Edit the Terraform until green.
- [ ] **Step 3:** Lint, full suite. Commit: `feat(infra): single-project Terraform for cloud sandboxes, scale-to-zero engine and 30-day expiry`.

---

### Task 7: Terraform — budget root

**Files:**
- Create: `deployment/terraform/budget/providers.tf`, `variables.tf`, `apis.tf`, `budget.tf`, `guard.tf`, `outputs.tf`, `tests/unit/test_terraform_budget_root.py`

**Interfaces:**
- Consumes: Task 5's directory `deployment/terraform/budget/function/`, entry point `stop_billing`, env names `GUARD_PROJECT_ID`, `GUARD_BUDGET_ID`, `GUARD_BILLING_ACCOUNT`.
- Produces (exact resource names):
  - `providers.tf`: the `google`, `google.billing_override` and `google.api_bootstrap` configurations copied from `single-project/providers.tf`, plus `hashicorp/archive`.
  - `variables.tf`: `project_id` (string); `region` (default `"us-central1"`); `budget_amount_try` (number, no default); `billing_account` (string, default `""`, the fallback); `budget_start` (object with `year = 2026, month = 9, day = 29`).
  - `apis.tf`: `google_project_service.budget_services` for `billingbudgets`, `cloudbilling`, `pubsub`, `cloudfunctions`, `run`, `cloudbuild`, `eventarc`, `artifactregistry` and `logging` (`.googleapis.com`), each with `disable_on_destroy = false`.
  - `budget.tf`:
    - `data "google_project" "project"`;
    - `google_pubsub_topic.budget` (`name = "issue-to-pr-budget"`);
    - `google_pubsub_topic_iam_member.billing_publishes` (`roles/pubsub.publisher`, `serviceAccount:billing-budget-alert@system.gserviceaccount.com`);
    - `google_billing_budget.project` (`provider = google.billing_override`):
      - `budget_filter`: `projects = ["projects/${data.google_project.project.number}"]`, `credit_types_treatment = "EXCLUDE_ALL_CREDITS"`, `custom_period { start_date { … var.budget_start } }`;
      - `amount.specified_amount`: `currency_code = "TRY"`, `units = tostring(floor(var.budget_amount_try))`;
      - `threshold_rules` at `0.5`, `0.8` and `1.0`;
      - `all_updates_rule`: `pubsub_topic = google_pubsub_topic.budget.id`, `schema_version = "1.0"`.
  - `guard.tf`:
    - `google_service_account.guard` (`account_id = "budget-guard"`) and `google_service_account.guard_build` (`account_id = "budget-guard-build"`);
    - `google_project_iam_member.guard_billing_project_manager` (`roles/billing.projectManager`) and `google_project_iam_member.guard_build_builder` (`roles/cloudbuild.builds.builder`);
    - `google_storage_bucket.guard_source` (`"${var.project_id}-budget-guard-source"`, `force_destroy = true`);
    - `data "archive_file" "guard_source"`: from `${path.module}/function`, written to a git-ignored path under `build/`; and `google_storage_bucket_object.guard_source`;
    - `google_cloudfunctions2_function.guard`:
      - `name = "budget-guard"`, `location = var.region`;
      - `build_config`: runtime `python312`, entry point `stop_billing`, `service_account` = guard_build;
      - `service_config`: `service_account_email` = guard, `max_instance_count = 1`, `min_instance_count = 0`, `available_memory = "256M"`, `timeout_seconds = 60`, `ingress_settings = "ALLOW_INTERNAL_ONLY"`, `all_traffic_on_latest_revision = true`;
      - `environment_variables`: `GUARD_PROJECT_ID`, `GUARD_BUDGET_ID = element(split("/", google_billing_budget.project.name), 3)` and `GUARD_BILLING_ACCOUNT`;
      - `event_trigger`: `event_type = "google.cloud.pubsub.topic.v1.messagePublished"`, `pubsub_topic`, `retry_policy = "RETRY_POLICY_RETRY"`, `service_account_email` = guard;
    - `google_cloud_run_service_iam_member.guard_invoker` (`roles/run.invoker` on the function's service, for the guard).
  - `outputs.tf`: `budget_topic`, `budget_id`, `budget_amount_try`, `guard_function`.

**Required behaviour:** The root never refers to `single-project` files or state. It grants nothing on the billing account. As in Task 6, Terraform is run first in Task 10.

**Tests** (`tests/unit/test_terraform_budget_root.py`, text checks):
- `test_budget_is_500_dollars_in_try_before_credits_from_the_project_start`
- `test_billing_service_can_publish_to_the_topic`
- `test_guard_function_is_internal_single_instance_and_retries`
- `test_guard_roles_are_project_billing_manager_and_invoker_only` (no `google_billing_account_iam_*` resource)
- `test_budget_root_does_not_reference_the_single_project_root`
- `test_guard_source_is_the_function_directory`
- `test_no_tf_file_names_the_project`

- [ ] **Step 1:** Tests (RED). **Step 2:** Write the root until green.
- [ ] **Step 3:** Lint, full suite. Report for Task 9: `budget-plan`, `budget-apply`, `teardown-budget`, and the state note.
- [ ] **Step 4:** Commit: `feat(infra): budget root with a $500 TRY budget and the billing hard stop`.

---

### Task 8: Deploy staging tree

**Files:**
- Create: `scripts/stage_deploy.py`, `tests/unit/test_stage_deploy.py`
- Modify: `Dockerfile` (`COPY ./bench ./bench` after `COPY ./app ./app`)

**Interfaces:**
- Consumes: Terraform outputs `agent_runtime_resource_name` and `sandbox_caller_email`; `scripts/sandbox_infra.py template --find-only` (imported function); `scripts/agents_cli_eval.py`.
- Produces:

  ```
  uv run python scripts/stage_deploy.py stage [--out build/deploy]
  uv run python scripts/stage_deploy.py deploy
  uv run python scripts/stage_deploy.py smoke [--run-id cloud-smoke-1] [--task tc-001]
  ```
  ```python
  STAGE_DIR = Path("build/deploy")
  HELDOUT_DIR = re.compile(r"-h[0-9]{2}$")
  ROOT_FILES = ("Dockerfile", "pyproject.toml", "uv.lock", "README.md", "agents-cli-manifest.yaml", "deployment_metadata.json")
  def stage(repo_root: Path, out: Path) -> list[str]           # relative staged paths; rebuilds `out` from scratch
  def deploy_env(*, engine: str, template: str, caller_sa: str) -> dict[str, str]
  def deploy_args(*, project: str, engine: str, template: str, caller_sa: str) -> list[str]
  def passthrough_url(engine: str) -> str   # https://<location>-aiplatform.googleapis.com/reasoningEngines/v1/<engine>/api
  ```
  `deploy_env` returns exactly `ENVIRONMENT_BACKEND=agent_runtime`, `SANDBOX_ENGINE`, `SANDBOX_TEMPLATE`, `SANDBOX_CALLER_SA`, `BQ_ANALYTICS_ENABLED=1`, `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT`. `deploy_args` is `["agents-cli", "deploy", "--project", P, "--update-only", "--min-instances", "0", "--max-instances", "1", "--concurrency", "4", "--cpu", "1", "--memory", "4Gi", "--update-env-vars", <comma-joined KEY=VALUE>]`.

**Required behaviour** (spec §6.2, §6.3)

1. `stage` copies only:
   - `ROOT_FILES`;
   - `app/` and `bench/*.py`, without `__pycache__`, `*.pyc` or `.pytest_cache`;
   - `bench/repos/`;
   - for each `bench/tasks/<id>/` whose name does not match `HELDOUT_DIR`: `task.yaml` and `plant/`.

   It skips held-out directories by name before descending, and never opens anything in them. A copied `task.yaml` whose `split` is not `dev` stops it with `"staging refused: a task outside the dev split"` (exit 1).
2. `deploy`:
   - stages;
   - finds the template with `--find-only`, and exits 1 with `"no ACTIVE template for the current sandbox image"` when there is none;
   - runs `deploy_args` with `cwd=build/deploy`;
   - afterwards copies `build/deploy/deployment_metadata.json` back to the project root.

   It refuses with `"deploy runs only from build/deploy"` if asked to run anywhere else.
3. `smoke` runs `uv run python scripts/agents_cli_eval.py run '{"task_id": "<task>", "run_id": "<run-id>"}' --url <passthrough_url> --mode adk`. It exits 0 only when the last event's text starts with `patch written`.
4. The staged tree never holds `.env`, so agents-cli neither copies nor prints its values.

**Tests** (`tests/unit/test_stage_deploy.py`)

- `test_stage_copies_exactly_the_allowed_files` (synthetic tree: a repository; a dev task with `plant/`, `solution/`, `hidden_tests/` and `shortcut/`; a held-out-named directory; `.env`)
- `test_stage_skips_heldout_dirs_without_opening_them` (Review Focus 3; file access under a held-out path raises in the test)
- `test_stage_refuses_a_task_yaml_that_is_not_dev`
- `test_stage_rebuilds_from_scratch`
- `test_real_tree_staging_has_no_sealed_paths` (Review Focus 3; the real repository into `tmp_path`; pruned `os.walk`; asserts counts with a fixed message and prints no path)
- `test_staged_tree_has_no_dotenv` (Review Focus 5)
- `test_deploy_env_is_exact` (Review Focus 5)
- `test_deploy_args_use_update_only_and_the_sizes`
- `test_deploy_never_runs_from_the_project_root` (Review Focus 5; fake subprocess records `cwd`)
- `test_deploy_copies_metadata_back`
- `test_deploy_refuses_without_a_template`
- `test_passthrough_url_takes_the_location_from_the_engine`
- `test_smoke_passes_only_on_patch_written`
- `test_dockerfile_copies_bench`

- [ ] **Step 1:** Tests (RED). **Step 2:** Implement until green.
- [ ] **Step 3:** `uv run python scripts/stage_deploy.py stage`, then `docker build -t issue-to-pr-agent:check build/deploy` (local, free). The build must succeed with `bench/` present.
- [ ] **Step 4:** Lint, full suite. Report for Task 9: `deploy-stage`, `deploy-check`, `deploy`, `smoke-deployed`, and the deploy rule. Commit: `feat(deploy): staging tree with explicit configuration; no sealed files or .env uploaded`.

---

### Task 9: Make targets, AGENTS.md and the whole-branch review

**Files:**
- Modify: `Makefile`, `AGENTS.md`

**Interfaces** (`Makefile`; `PROJECT ?= $(GOOGLE_CLOUD_PROJECT)`; every cloud target first runs `require-project`, which exits 2 with `error: GOOGLE_CLOUD_PROJECT is not set` when empty):

| Target | Command |
|---|---|
| `infra-plan` | `agents-cli infra single-project --project $(PROJECT)` |
| `infra-apply` | `agents-cli infra single-project --project $(PROJECT) --apply` |
| `budget-plan` | `terraform -chdir=deployment/terraform/budget init -input=false && terraform -chdir=deployment/terraform/budget plan -input=false -var project_id=$(PROJECT) -var budget_amount_try=$(BUDGET_TRY)`; refuses without `BUDGET_TRY` |
| `budget-apply` | the same with `apply -input=false` (no `-auto-approve`: Terraform asks) |
| `budget-guard-test` | `uv run python scripts/budget_guard_check.py` |
| `sandbox-cloud` | `uv run python scripts/sandbox_infra.py image && uv run python scripts/sandbox_infra.py template` |
| `test-cloud` | `ITP_CLOUD_TESTS=1 uv run --env-file .env pytest -m "cloud or cloud_slow" -q` |
| `sweep-sandboxes` | `uv run python scripts/sandbox_infra.py sweep` |
| `deploy-stage` | `uv run python scripts/stage_deploy.py stage` |
| `deploy-check` | `deploy-stage`, then `docker build -t issue-to-pr-agent:check build/deploy` |
| `deploy` | `uv run python scripts/stage_deploy.py deploy` |
| `smoke-deployed` | `uv run python scripts/stage_deploy.py smoke` |
| `teardown-dry-run` | `sandbox_infra.py sweep --all --dry-run`; `prune-templates --all --dry-run`; `delete-engine --name <agent_runtime_resource_name> --expect-display-name issue-to-pr --dry-run`; `terraform -chdir=deployment/terraform/single-project plan -destroy -input=false -var project_id=$(PROJECT)`; `gcloud storage ls gs://$(PROJECT)_cloudbuild` |
| `teardown` | the same five without `--dry-run` (`terraform ... destroy -input=false`, `gcloud storage rm -r gs://$(PROJECT)_cloudbuild`), then prints `budget root kept: make teardown-budget removes it` |
| `teardown-budget` | `terraform -chdir=deployment/terraform/budget destroy -input=false -var project_id=$(PROJECT) -var budget_amount_try=0`, then lists, and after confirmation deletes, the `gcf-artifacts` repository and `gcf-v2-*` buckets |

**Required behaviour** (`AGENTS.md`, spec §11):
- Hard rules 2, 5, 6 and 8 as in spec §11, including the $500 rule, the hard-stop consequence, "never publish to the budget topic by hand", "never run `agents-cli deploy` from the project root", and which targets need approval. **[OWNER REVIEW]** of the exact wording.
- Runtime wiring: one bullet for the cloud backend, one for the deployed agent.
- The env table: `SANDBOX_ENGINE`, `SANDBOX_TEMPLATE`, `SANDBOX_CALLER_SA`, `SANDBOX_READY_TIMEOUT_S`, `ITP_CLOUD_TESTS`; updated `ENVIRONMENT_BACKEND`, `SANDBOX_TTL_S`, `SANDBOX_IMAGE`, `BQ_ANALYTICS_ENABLED`.
- Workflow: Terraform via Homebrew; `--project` always; local state in both roots; teardown order.
- Commands: every target above; the cloud parity commands (Task 14).
- Testing conventions: the markers and `ITP_CLOUD_TESTS`.
- The agents-cli 1.7.0 behaviour the deploy relies on.

**Tests:** `make -n` of each target prints the command above (checked by hand in Step 2; no new pytest).

- [ ] **Step 1:** Merge Tasks 1 to 8 (controller). Full suite, `make test-docker`, `agents-cli lint`, `uv run python -m bench.validate`.
- [ ] **Step 2:** Makefile targets; `make -n` each; `make deploy-check` (local, free).
- [ ] **Step 3 [OWNER REVIEW]:** AGENTS.md as above; the owner approves the hard-rule text.
- [ ] **Step 4:** Whole-branch review against the spec and this plan's Review Focus, with `':(exclude,glob)bench/tasks/*-h[0-9][0-9]/**'`. Fix findings before Task 10.
- [ ] **Step 5:** Commit: `docs: AGENTS.md and Make targets for cloud sandboxes, deploy and the budget hard stop`.

---

### Task 10: Terraform and the budget root **[OWNER APPROVAL]**

Controller and owner task. Implements decisions 8 to 12. Estimated cost: cents.

**Files:**
- Create: `docs/results/<date>-3a-run-log.md`
- Modify: Terraform files only if `validate` finds errors

- [ ] **Step 1 [OWNER APPROVAL: installs software, $0]:** `brew install hashicorp/tap/terraform`. Record `terraform version` in the run log; it must be 1.11 or newer.
- [ ] **Step 2:** `terraform fmt -check -recursive deployment/terraform`. Then `terraform -chdir=deployment/terraform/single-project init -input=false && terraform -chdir=deployment/terraform/single-project validate`, and the same for `deployment/terraform/budget`. `init` downloads providers and creates no resource. Fix and commit any finding: `fix(infra): terraform fmt and validate`.
- [ ] **Step 3:** Open the run log: decisions 8 to 12 with their date; the running total (the owner reads the project's cost before credits from the billing report; the log records it, its date and its currency).
- [ ] **Step 4:** The owner names the rate source. Record the USD/TRY rate, its source, the date and `BUDGET_TRY = floor(500 × rate)`.
- [ ] **Step 5 [OWNER APPROVAL: creates the budget, topic, function, service accounts and bucket; cents]:** Spend rule check. `make budget-plan BUDGET_TRY=<n>`; the owner reads the plan. Then `make budget-apply BUDGET_TRY=<n>`; the owner types `yes`.
- [ ] **Step 6 [OWNER APPROVAL: one Pub/Sub message, $0]:** `make budget-guard-test`. It must print `dry_run`, `billing_enabled: true` and both permissions true.
  - If `get` is false, add `google_project_iam_member.guard_browser` (`roles/browser`) to `guard.tf`, re-apply (Step 5), test again, and commit.
  - If the apply named a missing Eventarc or build grant, add it to the budget root, never by hand.
- [ ] **Step 7 (next day):** Record the guard's first real "below budget" line (`gcloud logging read` filtered on the `budget-guard` service). Check that its `costAmount` includes spend from before the apply. If it does not, bring the fallback (spec §15: lower the amount by the recorded spend) to the owner.
- [ ] **Step 8:** Commit the log: `docs: week 3A budget and hard stop`.

---

### Task 11: Platform infrastructure **[OWNER APPROVAL]**

Estimated cost: $0 to cents (the engine has `min_instances = 0`).

- [ ] **Step 1:** `make infra-plan` (read-only). The owner reads it: two imports (repository, `sandbox-caller`), the engine with `min_instances = 0`, nothing destroyed.
- [ ] **Step 2 [OWNER APPROVAL: creates the platform resources]:** Spend rule check. `make infra-apply`.
- [ ] **Step 3:** Remove both `import` blocks. `make infra-plan` must show `No changes`. Commit: `chore(infra): drop applied import blocks`.
- [ ] **Step 4:** Run log: the resources created, by type and name, without the project id or number.

---

### Task 12: Sandbox image, template and the spike's leftovers **[OWNER APPROVAL]**

Estimated cost: cents (one Cloud Build of about a minute; template creation).

- [ ] **Step 1 [OWNER APPROVAL: Cloud Build and a template; cents]:** Spend rule check. `make sandbox-cloud`; it prints `SANDBOX_TEMPLATE=...`.
- [ ] **Step 2:** `uv run python scripts/sandbox_infra.py env`. The owner pastes the printed lines into `.env`; the controller does not open `.env`.
- [ ] **Step 3 [OWNER APPROVAL: deletes resources]:** the spike's leftovers (spec §5.3):
  - `uv run python scripts/sandbox_infra.py delete-engine --name <host engine from the spike report> --expect-display-name issue-to-pr-sandbox-host --dry-run`, then the same without `--dry-run`;
  - `gcloud artifacts docker images list <sandbox_image_repository>`, then `gcloud artifacts docker images delete <sandbox_image_repository>/sandbox:v0.1.0`.
- [ ] **Step 4:** Run log: the image tag, that the template is ACTIVE, and what was deleted. Commit: `docs: week 3A sandbox image and template`.

---

### Task 13: Cloud opt-in tests and the gate **[OWNER APPROVAL]**

Estimated cost: cents (about ten sandboxes and one short-lived test template).

- [ ] **Step 1 [OWNER APPROVAL: spend, cents]:** Spend rule check. `make test-cloud`.
- [ ] **Step 2 — Gate:** If `test_metadata_server_gives_no_token` or `test_authorization_header_does_not_reach_the_container` fails, or reports that the platform does not route to a second port, stop. Bring spec §16 question 6's options to the owner. Nothing in Tasks 14 to 16 runs until the owner decides.
- [ ] **Step 3:** `uv run python scripts/sandbox_infra.py sweep --all --dry-run` lists no sandbox.
- [ ] **Step 4:** Run log: every test result, including root-filesystem writability. Commit: `docs: week 3A cloud sandbox checks`.

---

### Task 14: Parity **[OWNER APPROVAL]**

Estimated cost: under $1 (part a) plus $1.50 to $3 (part b).

**Files:**
- Create: `results/3a-parity/docker/*.json`, `results/3a-parity/cloud/*.json`, `docs/results/<date>-3a-parity-docker.md`, `docs/results/<date>-3a-parity-cloud.md`, `docs/results/<date>-3a-cloud-parity.md`

- [ ] **Step 1 [OWNER APPROVAL: spend, under $1]:** Spend rule check.
  - `uv run python -m bench.probes validate` (Docker, free): 12 `ok`.
  - `ENVIRONMENT_BACKEND=agent_runtime uv run python -m bench.probes validate`: 12 `ok`.
  - `make sweep-sandboxes`.
- [ ] **Step 2 [OWNER APPROVAL: about $0.001] — Latency gate:** three consecutive direct `google-genai` calls to `gemini-3.8-flash`, each with one tool declaration (the 2C watcher's check), each under 15 s. If they are slower, check every 5 minutes for up to 3 hours, then report and stop.
- [ ] **Step 3 [OWNER APPROVAL: spend, $1.50–3]:** Spend rule check with $3 as the step's estimate. Start each run directly in a terminal so SIGINT works, with output to `runs/logs/3a-parity-<backend>.log`, and give the owner the `tail -f` command:

  ```
  uv run python -m bench.run --tasks tc-003,md-001,sr-002 --system multi --preset flash --repeats 1 --concurrency 1 --out results/3a-parity/docker
  ENVIRONMENT_BACKEND=agent_runtime uv run python -m bench.run --tasks tc-003,md-001,sr-002 --system multi --preset flash --repeats 1 --concurrency 1 --out results/3a-parity/cloud
  ```
- [ ] **Step 4:** Compare per task: the same outcome bucket; no cloud run ending in infra; no infra rerun on the cloud side. If a task differs, run it once more on both backends. If it still differs, read the cloud run's `events.jsonl` for sandbox errors before concluding anything.
- [ ] **Step 5:** Generate one report per backend:

  ```
  uv run python -m bench.report results/3a-parity/docker/<file>.json --out docs/results/<date>-3a-parity-docker.md
  uv run python -m bench.report results/3a-parity/cloud/<file>.json --out docs/results/<date>-3a-parity-cloud.md
  ```

  Then write `docs/results/<date>-3a-cloud-parity.md` by hand: the probe results, the per-task comparison, wall times per backend, and cost.

  Check spec criterion 1: `uv run python scripts/sandbox_infra.py sweep --all --dry-run` lists no sandbox that is still provisioning or running. Then `make sweep-sandboxes`, and add the spend to the running total.
- [ ] **Step 6 (next day):** The owner reads the idle cost in the billing report. The running total is replaced by the report's figure. If idle cost is over $1 a day, `uv run python scripts/sandbox_infra.py prune-templates --all` between sessions (spec §9).
- [ ] **Step 7:** Commit: `docs: week 3A cloud parity`.

---

### Task 15: Deploy and the deployed smoke run **[OWNER APPROVAL]**

Estimated cost: cents (remote build) plus about $0.30 (smoke).

- [ ] **Step 1:** `make deploy-check` (local, free).
- [ ] **Step 2 [OWNER APPROVAL: deploys; cents]:** No run is active. `make deploy`. The deploy prints the parameters and the env names and values from `deploy_env`; none comes from `.env`.
- [ ] **Step 3:** Latency gate, as in Task 14 Step 2 **[OWNER APPROVAL: about $0.001]**.
- [ ] **Step 4 [OWNER APPROVAL: spend, about $0.30]:** Spend rule check. `make smoke-deployed`; it passes when the last event says `patch written`.
- [ ] **Step 5:** The owner checks in the console:
  - the trace's model-call spans hold no prompt or response text;
  - `issue_to_pr_telemetry.agent_events` has rows for the run.

  Then `make sweep-sandboxes` deletes nothing. If the platform cut the stream before the run ended, record it (spec §15) and do not retry beyond one attempt.
- [ ] **Step 6:** Run log: deploy time, smoke outcome, cost, the two console checks. Commit: `docs: week 3A deployed smoke run`.

---

### Task 16: Teardown rehearsal and close-out

No credits. Read-only cloud calls.

**Files:**
- Modify: `docs/results/<date>-3a-run-log.md`, `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` (§19), `AGENTS.md` (Status line)

- [ ] **Step 1:** `make teardown-dry-run`; its output goes into the run log. Nothing is deleted. A real `make teardown` or `make teardown-budget` happens only when the owner asks.
- [ ] **Step 2:** Parent spec §19 gains a dated block with design §12's nine amendments (with item 9 only if the owner agreed to close the Week 1 output-cap item).
- [ ] **Step 3:** `AGENTS.md` Status: cloud backend built; bench graph deployed; live graph local; budget hard stop in place.
- [ ] **Step 4:** Check the exit criteria below. Commit: `docs: week 3A close-out and spec amendments`.

---

## Exit criteria

- `uv run pytest -q`, `make test-docker` and `agents-cli lint` pass; `bench.validate` prints `ok` for all 20 tasks; the cloud tests are skipped without `ITP_CLOUD_TESTS=1`.
- `make test-cloud` passed, including the metadata and header checks (or the owner decided otherwise at the Task 13 gate).
- `bench.probes validate` printed 12 `ok` on both backends. The 3 parity tasks gave the same outcome on both, with no cloud infra failure.
- The deployed bench graph's smoke run ended `patch written`; its trace holds no prompt text; `agent_events` has its rows; no sandbox is left.
- The budget ($500 in TRY, before credits, from 2026-09-29, alerts at 50/80/100%) exists. The guard's dry run passed, and a real notification reached it.
- `terraform plan` shows no changes in either root. The spike's host engine and `v0.1.0` image are gone. `make teardown-dry-run` lists everything teardown would remove.
- No held-out file was opened, staged, uploaded or run in the cloud. The 3A spend is within $5, and the project's running total never passed $500 without the owner's yes.

## Not in this plan

Everything in spec §14. Also: a real `make teardown` or `make teardown-budget`; re-applying the budget at a new exchange rate; narrowing `sandbox-caller`'s role (spec §16 question 11); a wall-clock cap for the deployed agent (question 7); live runs on the cloud backend (they are on hold since Week 2C).
