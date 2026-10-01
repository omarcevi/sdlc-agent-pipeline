# Week 3A design: cloud sandboxes and deployment

Date: 2026-10-01. Status: scope, the main choices, and the budget and tooling questions decided by the owner on 2026-10-01; full design awaiting owner review.
Parent spec: `2026-09-29-sdlc-agent-pipeline-design.md` (§5 architecture, §7 sandbox and security, §7.3 backends, §10 observability, §12 deployment, §14 cost, §15 milestones, §19 amendments). Where this document is more specific, it wins for Week 3A once approved; the parent spec's §19 then records the amendments listed in §12.
Spike this builds on: `docs/spikes/2026-09-30-s1-agent-runtime-sandbox.md` and its probe `spikes/s1_sandbox_probe.py`.
Plan: to be written after this design is approved (`docs/superpowers/plans/2026-10-01-week3a-cloud.md`).

Terms. An **engine** is an Agent Runtime instance (`projects/<p>/locations/<l>/reasoningEngines/<id>`). A **template** is a sandbox environment template under an engine. A **sandbox** is one sandbox environment created from a template. The **shim** is the FastAPI server in `sandbox_image/runtime/server.py`, which runs inside every sandbox, local and cloud.

## Decisions

### Decided by the owner (2026-10-01)

1. **Scope: both, staged.** Stage 1 adds a cloud sandbox backend (`ENVIRONMENT_BACKEND=agent_runtime`), so bench and live runs started on the laptop execute code in Agent Runtime sandboxes. Stage 2 deploys the pipeline itself to Agent Runtime with agents-cli: the bench graph only, with Cloud Trace and BigQuery analytics. The live graph, with its approval step, stays local until a live UI exists. That UI is not part of 3A.
2. **Infrastructure: Terraform plus one script.** `agents-cli infra single-project` (the scaffold's Terraform under `deployment/terraform/`) owns the platform pieces. One small idempotent script owns what Terraform cannot create: the sandbox template (matched by image URI and state `ACTIVE`, per spike gotcha 2) and the Cloud Build of the sandbox image. The spike's hand-made leftovers are deleted, so nothing in the project is hand-made. `make teardown` removes everything (the owner later made one exception, the budget root: decision 10); `make sweep-sandboxes` deletes sandboxes older than the TTL.
3. **Budget alerts** on the billing account the project uses (the owner confirmed the credits sit on it), created with Terraform if possible. The amounts and thresholds first given ($250, $500, $750) are replaced by decision 8.
4. **BigQuery agent analytics** is on for the deployed agent only and off for local runs. Its tables expire after 30 days. Trace spans stay content-free when deployed.
5. **Backend details.** The same `Environment` interface as Docker, so tools, guardrails and scoring do not change. Per run, one sandbox from one template. Readiness is a no-op command that reaches our server, retried, because the platform answers `/healthz` itself too early. The repository travels as a zip. Per command: a 120 s default timeout and the 10,000-character output cap, as today. The sandbox is deleted when the run ends, by the existing release path. Template resources: 2 CPUs and 2 GB, as the local sandbox. Internet off. The only credential is a short-lived token for the `sandbox-caller` service account, held by the orchestrator and never inside the sandbox. TTL 30 minutes.
6. **Verification.** Unit tests against a fake HTTP server; an opt-in test, marked so that it never runs in CI or by default, against a real sandbox; a parity check (the same 3 dev tasks on Docker and on cloud sandboxes; the results should match); a smoke run against the deployed agent. Expected cost about $2–5, plus under $1 a day idle. Paid checks wait until Gemini tool-call latency is normal again (§7.6).
7. **Not in 3A:** the live UI, CI (Week 3C), any change to prompts, caps or model names, and the held-out run (dropped for now, parent §19).

### Budget and tooling, decided by the owner (later on 2026-10-01)

8. **One project budget of $500, in TRY.** The billing account "My Main Billing" bills in Turkish lira (the owner checked with gcloud). The project's spend limit is $500, not $1,000. The budget is $500 converted to TRY at the rate on the day it is applied; the rate and its source go into the 3A log. It counts cost before credits (`EXCLUDE_ALL_CREDITS`). Alerts at 50%, 80% and 100%.
9. **Hard stop at 100%.** A Pub/Sub budget notification triggers a small Cloud Run function that disables billing on the project (Google's documented "disable billing usage with notifications" pattern). Specified in §5.4.
10. **The budget, the topic and the function live in their own Terraform root**, which survives `make teardown`. `make teardown-budget` removes them. This is the owner's exception to decision 2's "removes everything".
11. **Spend rule for the controller.** Before any step that would push the running total of the project's spend past $500, stop and ask the owner (§9).
12. **Terraform is installed with Homebrew** (`brew install hashicorp/tap/terraform`) at the infrastructure step, not before, and the installed version is recorded in the 3A log.

### Made by this design (the owner reviews them with the design)

Each is explained where it is used and listed again, with the alternatives, in §16.

- A. Sandboxes and templates live under the engine that Terraform creates for the deployed agent (`issue-to-pr`), not under a separate host engine (§5.1).
- B. The spike's Artifact Registry repository and `sandbox-caller` service account are imported into Terraform, not deleted and recreated. The spike's empty host engine, its template and its image tag are deleted (§5.3).
- C. (Settled by the owner: decisions 8 to 10.) The design adds two details: the budget is filtered to this project, and it uses a custom period that starts on 2026-09-29 with no end date, so it measures the project's whole spend, not one calendar month (§5.4).
- D. "Tables expire after 30 days" is implemented as 30-day partition expiry plus a 30-day lifecycle rule on the logs bucket, not as whole-table expiry (§6.4).
- E. The deploy uploads a staging directory that holds only what the image needs. It never uploads held-out tasks, hidden tests, reference solutions or `.env` (§6.2).
- F. The deployed agent's configuration is passed explicitly with `--update-env-vars`; nothing is copied from `.env` (§6.3).

## 1. Why

- **The parent spec promised it.** §1 and §7.3 put sandboxes in Agent Runtime in the cloud; §2 criterion 4 and §12 deploy the pipeline there; §15 lists the backend as "never cut".
- **The spike settled feasibility, not the design.** Spike S1 showed that a sandbox from our image runs `pytest` on an uploaded repository with the internet blocked, and that it is ready in about 6 s from a warm template. It left open how runs use it, how it is cleaned up, which credential it needs, and how it behaves at TTL expiry.
- **Hand-made cloud state is a liability.** The spike made an engine, a template, an image, a repository and a service account by hand. Nothing records them except the spike report, and nothing removes them.

## 2. Success criteria

1. With `ENVIRONMENT_BACKEND=agent_runtime`, a bench run on the laptop provisions a cloud sandbox, runs the pipeline, scores the patch in fresh cloud sandboxes, and deletes every sandbox it made. After `bench.run` exits, the engine lists no sandbox in `STATE_PROVISIONING` or `STATE_RUNNING`.
2. Parity, model-free: `bench.probes validate` prints `ok` for all 12 reviewer probes on the cloud backend, as it does on Docker (§7.4).
3. Parity, with the model: the same 3 dev tasks give the same outcome per task on both backends, and the cloud runs have no infra failure (§7.4).
4. The opt-in cloud tests pass, including: no internet, no credentials from the metadata server, no `Authorization` header reaching the container, and a sandbox gone after its TTL (§7.3).
5. Every resource in the project is created by Terraform, by `scripts/sandbox_infra.py`, or by the running code (sandboxes, sessions, the analytics table). The spike's hand-made engine, template and image tag are gone. `terraform plan` shows no changes after apply.
6. One project budget of $500 in TRY exists, counting cost before credits from 2026-09-29, with alerts at 50%, 80% and 100%. The hard stop is wired: a dry-run test message is logged by the function as "would disable billing", with the permissions it needs present, and a real budget notification appears in the function's log. No test disables billing.
7. The deployed bench graph completes a smoke run on `tc-001` with outcome `patch_written`, using a cloud sandbox that is deleted afterwards. Its trace is in Cloud Trace with no prompt or response text in span attributes, and its events are in the BigQuery table `agent_events`.
8. No file from a held-out task directory, and no `hidden_tests/`, `solution/` or `shortcut/` file, is uploaded by the deploy (§6.2). Held-out tasks never run on the cloud backend (§8).
9. Unit tests pass without GCP; the cloud tests never run unless asked for.
10. Week 3A spends at most $5, and the idle project costs under $1 a day. No step pushed the project's running spend past $500 without the owner's yes. Prompts, caps and model names are unchanged.

## 3. Starting point

**Code.** `app/environment/base.py` defines the `Environment` protocol (`exec`, `read_file`, `write_file`, `upload_dir`, `close`, `env_id`) and its error contract (`InfraError` for backend failures, `FileNotFoundError` and `OSError` for path problems, a failing command is not an error). `app/environment/factory.py::start_environment` knows only `docker`. `app/environment/registry.py` maps the `sandbox_id` in session state to the live object; `registry.release` pops it and calls `close()`. Releases happen in three places: `provision_sandbox`'s `except BaseException` (a sandbox that fails or is cancelled while it is being set up), the driver's `finally` (`app/driver.py::_release_sandbox`) and, for runs under agents-cli, `SandboxReleasePlugin` (`app/sandbox_release.py`, `after_run_callback` and `on_run_error_callback`). Scoring (`bench/score.py`) and probe checks (`bench/probes.py::check_patch`) start and close their own sandboxes through the same factory. Every path any caller uses lies under `/workspace`.

**The shim** (vendored unmodified, `sandbox_image/runtime/server.py`) serves `POST /exec` (`{command, timeout, cwd, env}` to `{exit_code, stdout, stderr, timed_out}`), `GET`/`POST /files?path=` (base64 content), `GET`/`POST /files/zip?path=`, and more. It refuses any path outside `/workspace` with 403. On timeout, `/exec` kills only the shell it started, not the shell's children, and returns `exit_code -1, timed_out true`. `/files/zip` extraction does not keep file modes.

**The sandbox SDK** (google-cloud-aiplatform 1.165.1 in `.venv`). `client.agent_engines.sandboxes` has `create(name=<engine>, config={...})` (waits for completion by default), `get`, `list(name=<engine>, config=...)`, `delete`, and `generate_access_token(service_account_email, timeout=3600)`. The last one signs a JWT with the service account through the IAM Credentials `signJwt` call, audience `https://aiplatform.googleapis.com/`. `client.agent_engines.sandboxes.templates` has `create`, `list`, `get` and `delete`. A template's `custom_container_environment` takes `custom_container_spec.image_uri`, `ports` and `resources` (`limits` and `requests`, keys `cpu` and `memory`); its `egress_control_config.internet_access` turns the internet on or off. Sandbox states are `STATE_PROVISIONING`, `STATE_RUNNING`, `STATE_DEPROVISIONING`, `STATE_TERMINATED` and `STATE_DELETED`. `client.agent_engines.delete(name=..., force=True)` also deletes an engine's child resources. The async module (`AsyncSandboxes`) has only private methods. `vertexai.Client` warns that it is deprecated in favour of `agentplatform.Client`, which takes the same `project`, `location` and `http_options` arguments.

**agents-cli 1.7.0** (`~/.local/share/uv/tools/google-agents-cli/`):

- `infra single-project` runs `terraform init` and `terraform plan` in `deployment/terraform/single-project`; `--apply` applies with `-auto-approve`. It passes `-var project_id=<p>` when `--project` is given and no var file, so `vars/env.tfvars` is not read. The root has no backend block: state is local and git-ignored. Terraform is not installed on this machine; it is installed at the infrastructure step (decision 12).
- `deploy` for `agent_runtime` builds the project's `Dockerfile` remotely. It uploads every file under the current directory that `.gcloudignore` (or `.gitignore`) does not exclude (`deploy/agent_runtime.py::_packaged_files`). It copies the project-root `.env` into the deployed environment variables and prints every plain variable with its value (`_build_runtime_env_vars` and the "Environment Variables" listing). It drops `GOOGLE_CLOUD_PROJECT`, which the platform reserves. On an update it keeps the engine's existing variables, and sizing flags left out keep their live values. `--update-only` fails instead of creating an engine. `--image` is refused for Agent Runtime.
- The project's `.gcloudignore` includes `.gitignore`, which excludes `.env` and `runs/` but not `bench/`, `results/` or `docs/`. The `Dockerfile` copies only `pyproject.toml`, `README.md`, `uv.lock` and `app/`, so the image has no bench data and the deployed bench graph could not load a task today.

**Terraform** (`deployment/terraform/single-project`, never applied). It enables APIs, creates the app service account `issue-to-pr-app` with roles, the logs bucket `<project>-issue-to-pr-logs`, the BigQuery dataset `issue_to_pr_telemetry` with a connection, an external `completions` table, a logs table and a view, a log sink, and an engine `google_vertex_ai_reasoning_engine.app` named `issue-to-pr` with placeholder source. The engine is created with `min_instances = 1`, `max_instances = 10`, 4 CPUs and 8 GiB; `lifecycle.ignore_changes` covers its `deployment_spec`, so later deploys own those values. The engine's environment sets `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT`, the GenAI completion upload to `gs://<logs bucket>/completions`, and the `BQ_ANALYTICS_*` dataset, bucket and connection, but not `BQ_ANALYTICS_ENABLED`. `providers.tf` already defines a `billing_override` provider alias (billing project and user project override), which is unused.

**Spike leftovers** (spike report, "Resources created"): the Artifact Registry repository `issue-to-pr` in `us-central1` with image `sandbox:v0.1.0`; the service account `sandbox-caller` with `roles/aiplatform.user`, and the owner's user holding `roles/iam.serviceAccountTokenCreator` on it; the empty host engine `issue-to-pr-sandbox-host` with one template. The report gives their full names.

## 4. Stage 1: the `agent_runtime` sandbox backend

### 4.1 Shape

- `app/environment/agent_runtime.py` holds `AgentRuntimeEnvironment`, which satisfies the `Environment` protocol. Two parts are kept apart so that each can be tested alone:
  - the **control plane**: create, get, delete and list sandboxes, sign the token. These are the SDK's public synchronous calls, run with `asyncio.to_thread`, behind a small `SandboxControl` adapter. Unit tests replace the adapter with a fake.
  - the **data plane**: one `httpx.AsyncClient` per sandbox, talking to the shim through the platform's proxy. Unit tests give it an `httpx.MockTransport`.
- `start_environment()` returns `AgentRuntimeEnvironment.start()` for `agent_runtime` and keeps `DockerEnvironment.start()` for `docker`.
- The client is `agentplatform.Client(project=GOOGLE_CLOUD_PROJECT, location=<the engine's location>, http_options={"api_version": "v1beta1"})`, built once per process. The location is read from `SANDBOX_ENGINE`, never from `GOOGLE_CLOUD_LOCATION`, which is `global` on Agent Runtime for Gemini.
- `env_id` is `itp-<12 hex>`, as for Docker. The full sandbox resource name, which holds the project number, stays inside the object. So session state, events, `events.jsonl` and progress lines carry only the short id.

### 4.2 Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT_BACKEND` | `docker` | `docker` or `agent_runtime` |
| `SANDBOX_ENGINE` | unset | Full resource name of the engine that holds the template and the sandboxes. Required for `agent_runtime` |
| `SANDBOX_TEMPLATE` | unset | Full resource name of the template, printed by `make sandbox-cloud`. Must lie under `SANDBOX_ENGINE`. Required for `agent_runtime` |
| `SANDBOX_CALLER_SA` | unset | Email of the `sandbox-caller` service account whose token the orchestrator signs. Required for `agent_runtime` |
| `SANDBOX_TTL_S` | `1800` | Existing. Docker: self-destruct. Cloud: the sandbox's `ttl` and the token's lifetime |
| `SANDBOX_READY_TIMEOUT_S` | `240` | How long `start()` waits for the shim to answer; the spike's probe used the same bound |
| `GOOGLE_CLOUD_PROJECT` | (existing) | The project for the client |
| `SANDBOX_IMAGE` | `issue-to-pr-sandbox:dev` | Existing; Docker only. The cloud image is fixed by the template |

`check_environment_config()` (in `factory.py`) checks the settings of the chosen backend: the three required names are set; `SANDBOX_ENGINE` has the engine form; `SANDBOX_TEMPLATE` starts with `SANDBOX_ENGINE` followed by `/sandboxEnvironmentTemplates/`; `SANDBOX_CALLER_SA` ends in `.iam.gserviceaccount.com`; `SANDBOX_READY_TIMEOUT_S` is a positive number below `RUN_TIMEOUT_S`. A bad setting raises `ValueError` with a fixed message before any run starts, like `RUN_TIMEOUT_S` today. `bench.run`, `bench.probes`, `bench.review_probe`, `app.live` and the driver call it at start. A configuration error is never an infra failure, so the matrix never reruns it.

`tests/conftest.py` removes the new variables before any test imports the package, as it does for the others.

### 4.3 One sandbox, start to finish

`start()`:

1. Once per process: `templates.get(SANDBOX_TEMPLATE)` must show state `ACTIVE`, and, when the field is returned, `internet_access` must not be true. Otherwise `InfraError("sandbox template is not usable")`.
2. Sign the token: `generate_access_token(SANDBOX_CALLER_SA, timeout=SANDBOX_TTL_S)`. It lasts as long as the sandbox may live, and `RUN_TIMEOUT_S` stays below the TTL, so it never expires during a laptop run.
3. Create: `sandboxes.create(name=SANDBOX_ENGINE, config={"sandbox_environment_template": SANDBOX_TEMPLATE, "ttl": f"{SANDBOX_TTL_S}s", "owner": "issue-to-pr", "display_name": env_id})`, in a worker thread.
4. Readiness: `POST /exec {"command": "true", "timeout": 10}` every 2 s, each attempt with a 15 s HTTP timeout, until one returns HTTP 200 with `exit_code` 0, for at most `SANDBOX_READY_TIMEOUT_S`. `/healthz` is never used (spike gotcha 1). On expiry: `InfraError("sandbox did not become ready in N s")`.
5. `mkdir -p /workspace/repo`, as Docker's `start()` does.

If anything after step 3 fails, or the task is cancelled, `start()` deletes the sandbox before it re-raises. A cancellation during step 3 cannot stop the worker thread. So `start()` waits for that thread, shielded and for at most 60 s, deletes the sandbox it returns, and only then re-raises the cancellation. Anything that still escapes is left to the TTL and the sweeper (§4.7).

`close()` calls `sandboxes.delete` and closes the HTTP client. It is idempotent, and it treats "not found" as success. It does not wait for the delete operation to finish.

### 4.4 Operations

| `Environment` call | Shim call | Result and errors |
|---|---|---|
| `exec(command, timeout=120, cwd=WORKDIR)` | `POST /exec` with `command = "timeout -k 5 <n> sh -c " + shlex.quote(command)`, `cwd`, and `timeout = n + 15` as the shim's backstop; HTTP read timeout `n + 30` | HTTP 200 gives an `ExecResult`; `timed_out` is true when the shim says so or the exit code is 124 or 137. This is Docker's exact rule and command wrapper, so a command's children are stopped too (the shim alone kills only the shell). Any other status, or a transport error, raises `InfraError` |
| `read_file(path)` | `GET /files?path=` | Content decoded as UTF-8 with `errors="replace"`, as Docker decodes `cat`. 404 raises `FileNotFoundError`; 400 and 403 raise `OSError(<detail>)`; 5xx or transport raise `InfraError`. A transport error or 5xx is retried twice, because reading is safe to repeat |
| `write_file(path, content)` | `POST /files?path=` with base64 content; the shim creates parent directories | 404, 400 and 403 raise `OSError`; 5xx or transport raise `InfraError`. Not retried |
| `upload_dir(local_dir, dest)` | Zip `local_dir` in memory, `POST /files/zip?path=<dest>` | Any failure raises `InfraError`, as Docker's does. Links are refused before zipping (bench repositories have none; live archives refuse them already) |
| `close()` | control plane `delete` | See §4.3 |

`exec` and `write_file` are not retried: a lost reply may mean the command already ran. Every request carries `Authorization: Bearer <token>`, `X-Sandbox-Routing-Token` and `X-Sandbox-Port: 8080` (spike gotcha 6). Error messages hold the method, the path, the status and at most 200 characters of the response body. They never hold headers.

The output cap stays where it is today, in the tools (`truncate` in `app/tools/`). It cannot move into `exec`: `collect_diff` needs the whole `git diff` output. §16, question 8 proposes closing the deferred Week 1 item on this.

Downloading a directory (`GET /files/zip`) worked in the spike, but no caller needs it: the diff is taken inside the sandbox. It is not added to the protocol.

### 4.5 Parity with Docker

| | Docker | Cloud | Effect |
|---|---|---|---|
| Image | `sandbox_image/Dockerfile`, built locally (arm64 on this laptop) | the same Dockerfile, built by Cloud Build (amd64) | Same files and pinned Python packages; apt package builds may differ by date |
| CPU, memory | `--cpus 2 --memory 2g` | template `limits` and `requests`: `cpu: "2"`, `memory: "2Gi"` | Same |
| Network | `--network none` | template `internet_access: false` | Same, checked by the opt-in test, which also checks the metadata server |
| User | uid 1000 | uid 1000 (spike) | Same |
| Root filesystem | read-only except `/workspace`, `/tmp` | unknown (spike gotcha 8) | The opt-in test records it; a difference is reported, not fixed |
| pids limit, dropped capabilities | `--pids-limit 256`, `--cap-drop ALL`, `no-new-privileges` | not configurable in a template | The platform's own isolation instead |
| Per-command timeout | `timeout -k 5 n` | the same wrapper | Same exit codes |
| Upload | tar, keeps modes and links | zip, loses modes, links refused | No effect on bench repositories (no links, no executable files) |
| Workspace size | tmpfs 512 MB | container disk | Not expected to matter (bench repositories are 284 KB) |
| Lifetime | `sleep <TTL>` plus `docker rm -f` | platform TTL plus delete | §4.7 |

### 4.6 The one credential

- **What it is.** A JWT signed by `sandbox-caller` through `signJwt`, audience `https://aiplatform.googleapis.com/`, lifetime `SANDBOX_TTL_S`. It is a bearer credential. Because its audience is the API's own address, whoever holds it can most likely call that API as `sandbox-caller` (`roles/aiplatform.user`) until it expires, not only the sandbox proxy. It is treated as that powerful.
- **Who signs it.** On the laptop, the owner's user credentials, which hold `roles/iam.serviceAccountTokenCreator` on `sandbox-caller`. When deployed, the app service account `issue-to-pr-app`, which gets the same role on `sandbox-caller`. The app service account already holds `roles/aiplatform.user` itself, so this grants it nothing new.
- **Where it lives.** In one attribute of the `AgentRuntimeEnvironment` object and in the headers of its HTTP client, for the life of the sandbox. Never in `os.environ`, session state, events, `record.json`, prompts, span attributes, log records, exception text or the sandbox.
- **What would break that.** The platform's proxy could forward the `Authorization` header to the container. Then the shim would see it, and so could code an agent runs, since it runs as the same user. The spike did not check this. The opt-in tests do (§7.3), and Stage 1 stops if the header arrives (§16, question 6).

### 4.7 Cleanup on every exit path

| How the run ends | What deletes the sandbox |
|---|---|
| Normal end, on the laptop | The driver's `finally` (`registry.release` → `close()`), as for Docker |
| Normal end, under agents-cli (local server or deployed) | `SandboxReleasePlugin.after_run_callback` |
| An exception in the graph (infra, budget, agent, crash) | Laptop: the driver's `finally`. Under agents-cli: `on_run_error_callback` |
| `RUN_TIMEOUT_S` expiry or Ctrl-C on the laptop | The driver's `finally`, which releases even when cancelled (existing `_withdraw` logic) |
| Failure or cancellation while the sandbox is being set up | `start()` deletes its own sandbox (§4.3); `provision_sandbox` releases a registered one (existing `except BaseException`) |
| The client goes away during a run under agents-cli (read timeout, disconnect) | Not known to reach the plugin; the TTL and the sweeper |
| The process dies (kill, laptop sleep, container recycled) | The TTL and the sweeper |
| `delete` itself fails | Logged, never replaces the run's result (existing); the TTL and the sweeper |
| The platform's TTL does not remove it | The sweeper |
| Scoring and probe sandboxes | `bench.score._release` and `check_patch`'s `finally` (existing), then the TTL and the sweeper |

Decision 5 names the driver's release path. A run the driver does not drive (agents-cli's local server, and so the deployed agent) has had its own path since Week 2C, `SandboxReleasePlugin`. Both end in `registry.release`, so the cloud backend needs no new release code; it only implements `close()`.

**The sweeper.** `make sweep-sandboxes` runs `scripts/sandbox_infra.py sweep`. It lists the sandboxes under `SANDBOX_ENGINE` and deletes every one that is not `STATE_DELETED` or `STATE_DEPROVISIONING` and whose `create_time` is more than `SANDBOX_TTL_S + 60` seconds ago. A sandbox still in use is never that old (`RUN_TIMEOUT_S` < TTL), so the sweep is safe while runs are going. It prints one line per deletion and a count. `--all` deletes regardless of age; only teardown uses it. The sweep needs no approval at the time: it only removes sandboxes the platform should already have removed. It is run after every paid session and by teardown.

## 5. Infrastructure

### 5.1 What Terraform owns

The scaffold's resources stay (§3), with these changes in `deployment/terraform/single-project`:

- `apis.tf`: add `artifactregistry.googleapis.com` and `iamcredentials.googleapis.com` (the spike enabled them by hand or by default; now they are declared).
- `service.tf`: the engine is created with `min_instances = 0`, `max_instances = 1`, `container_concurrency = 4`, `cpu = "1"`, `memory = "4Gi"` (agents-cli's own defaults for CPU and memory). With `min_instances = 1` the placeholder engine would bill from the day Stage 1 starts. The engine is also the sandbox host (decision A): its resource name, the existing output `agent_runtime_resource_name`, is `SANDBOX_ENGINE`.
- New `sandbox.tf`:
  - `google_artifact_registry_repository` `issue-to-pr` (Docker format, `us-central1`), imported (decision B).
  - `google_service_account` `sandbox-caller`, imported, with `google_project_iam_member` for `roles/aiplatform.user` (the role the spike proved).
  - `google_service_account_iam_member` granting `roles/iam.serviceAccountTokenCreator` on `sandbox-caller` to the app service account and to the person running Terraform. The latter comes from `data.google_client_openid_userinfo`, so no email is written into the repository; if that data source returns no email (it needs the email scope on the credentials), an `operator_member` variable is the fallback.
  - The imports are `import` blocks (Terraform 1.11, already required by `providers.tf`). They are removed in the commit after the first apply, because an import block for a missing object fails a later apply after teardown.
  - New outputs: `sandbox_caller_email`, `sandbox_image_repository`.
- `storage.tf`: the logs bucket gets `force_destroy = true` (teardown must be able to remove it with objects in it) and a lifecycle rule that deletes objects older than 30 days (decision D).
- `telemetry.tf`: the dataset gets `default_partition_expiration_ms = 2592000000` (30 days) and `delete_contents_on_destroy = true` (the analytics plugin creates `agent_events` outside Terraform, which would block a destroy). The logs table's own `time_partitioning` gets the same `expiration_ms`.
- Always run with `--project`: `agents-cli infra single-project --project $GOOGLE_CLOUD_PROJECT [--apply]`. The Makefile's `infra-plan` and `infra-apply` targets do that and refuse when the variable is empty. New Makefile targets, scripts and Terraform files take the project from `GOOGLE_CLOUD_PROJECT` or Terraform outputs and never write it down.

The `cicd` root is untouched (3C).

### 5.2 The script: `scripts/sandbox_infra.py`

One file, standard library plus the SDK, run with `uv run`. Every subcommand is safe to run twice.

| Subcommand | What it does |
|---|---|
| `image` | The tag is the tree id of `sandbox_image/` at `HEAD` (`git rev-parse HEAD:sandbox_image`, first 12 characters), so the same content always has the same tag. It refuses when `sandbox_image/` has uncommitted changes. If `<repository>/sandbox:<tag>` exists (`gcloud artifacts docker images describe`), it does nothing; otherwise `gcloud builds submit sandbox_image --tag <uri>` (the spike's build took 63 s). Prints the URI |
| `template [--find-only]` | Lists the templates under `SANDBOX_ENGINE`, fetches each, and reuses the first one whose `image_uri` equals the current image and whose state is `ACTIVE` (spike gotcha 2: display names are not returned). When the API returns them, the ports must be exactly 8080; a returned egress setting that allows the internet makes the script refuse the match. Otherwise it creates one: port 8080 TCP, `resources` limits and requests `cpu: "2"`, `memory: "2Gi"`, `internet_access: false`. That takes 1 to 4 minutes. `--find-only` never creates and fails when nothing matches. Prints `SANDBOX_TEMPLATE=<name>` |
| `prune-templates [--all]` | Deletes templates under `SANDBOX_ENGINE` whose image is not the current one. Run after an image change, once no run uses the old template. `--all` deletes every template; only teardown and the idle-cost fallback (§9) use it |
| `sweep [--all]` | §4.7 |
| `delete-engine --name N --expect-display-name D [--dry-run]` | Deletes one engine with `force=True` (children included), only when its display name is exactly `D`. Used once for the spike's host engine (§5.3) and by teardown |
| `env` | Prints the four lines the laptop's `.env` needs (`SANDBOX_ENGINE`, `SANDBOX_TEMPLATE`, `SANDBOX_CALLER_SA`, and `ENVIRONMENT_BACKEND` commented out). None is a secret. The owner pastes them; the script never writes `.env` |

`make sandbox-cloud` runs `image` then `template`. The script takes `SANDBOX_ENGINE` and the repository from `terraform output -json` in `deployment/terraform/single-project`. The script is operator tooling, not an agent tool, so it may run `gcloud` and `git` as subprocesses (hard rule 3 governs agent tools).

### 5.3 Removing the spike's leftovers

After Terraform has imported the repository and the service account, and after the new image and template exist:

1. `scripts/sandbox_infra.py delete-engine --name <the host engine's resource name from the spike report> --expect-display-name issue-to-pr-sandbox-host`. This also deletes its template and any sandboxes. The four duplicate templates are already `DELETED`.
2. `gcloud artifacts docker images delete .../issue-to-pr/sandbox:v0.1.0`.
3. The owner's `serviceAccountTokenCreator` binding needs nothing: Terraform declares the same binding, and `google_service_account_iam_member` is non-authoritative.

Each step runs with `--dry-run` or a listing first, and the plan marks it **[OWNER APPROVAL]**.

### 5.4 Budget and hard stop

Everything here lives in its own Terraform root, `deployment/terraform/budget/` (decision 10), with local, git-ignored state like the other root. It does not depend on the single-project root, so it can be applied first and outlives `make teardown`. Its provider block copies `single-project/providers.tf`, including the `billing_override` configuration that the Budget API needs when called with user credentials, and adds `hashicorp/archive` to zip the function source.

**What this costs if it fires.** Disabling billing stops every billed service in the project at once: the deployed agent and the demo, sandboxes, Gemini calls, Cloud Build, Cloud Run, BigQuery, Cloud Storage and the function itself. Google may delete resources of a project that stays without billing. Re-enabling billing is a manual step by the owner: link the project to the billing account again in the console or with `gcloud billing projects link`. It needs the Billing Account User role on the account (or administrator) and Project Billing Manager or Owner on the project. Services may need to be started again by hand, and the deployed agent may need a new deploy. The stop is a backstop: cost data reaches budgets with a delay of hours, so spend can pass the limit before it fires. The controller's spend rule (§9) is the first guard.

**The budget** (`google_billing_budget`, through `billing_override`):

- `billing_account` from `data.google_project` (`billing_account` attribute), so no account id is written down; a `billing_account` variable is the fallback.
- `budget_filter.projects` = this project only. `credit_types_treatment = "EXCLUDE_ALL_CREDITS"`: by default a budget counts cost after credits, which stays near zero while credits last, so nothing would ever fire.
- `budget_filter.custom_period` starting 2026-09-29, with no end date, so the budget measures the project's whole spend, not one calendar month (the default period). That a custom period starting in the past counts the cost already incurred is from the Budget API's documentation, not tested; the first real notification shows it (its `costAmount` includes spend from before the apply).
- `amount.specified_amount`: `currency_code = "TRY"`, `units` = the required variable `budget_amount_try`, which is $500 at the day's rate, rounded down to whole lira. `make budget-plan BUDGET_TRY=<n>` passes it, and the rate, its source and the date go into the 3A log. The rate drifts. The budget is re-applied with a new rate when the owner asks, and the spend rule counts dollars, not lira.
- `threshold_rules` at 0.5, 0.8 and 1.0 (`CURRENT_SPEND`). Emails go to the billing account's administrators and users, the budget default.
- `all_updates_rule.pubsub_topic` = the topic below, `schema_version = "1.0"`. Cloud Billing then publishes the budget's status to the topic several times a day, whether or not a threshold was crossed (per the Cloud Billing documentation).

**The topic.** `google_pubsub_topic` `issue-to-pr-budget`. Per the Cloud Billing documentation, `billing-budget-alert@system.gserviceaccount.com` must be able to publish to it when the budget is set up through the API; a `google_pubsub_topic_iam_member` grants it `roles/pubsub.publisher` on the topic. No other publisher is granted. Project owners can publish anyway, through their own role.

**The function** (`budget-guard`, a Cloud Run function in `us-central1`; the Terraform provider still calls the resource `google_cloudfunctions2_function`):

- Source in the repository, `deployment/terraform/budget/function/`: `guard.py` (the decision, standard library only), `main.py` (about 60 lines that wire `guard.py` to the clients) and `requirements.txt` (`functions-framework`, `google-cloud-billing`, `google-cloud-resource-manager`, pinned). `data "archive_file"` zips the directory into a `google_storage_bucket_object` in a bucket of this root, `<project>-budget-guard-source` (`force_destroy = true`).
- `build_config`: runtime `python312`, entry point `stop_billing`. A dedicated build service account, `budget-guard-build`, with `roles/cloudbuild.builds.builder`, the role the scaffold grants the default compute account for the same purpose. So this root does not depend on the other root's grant.
- `service_config`: service account `budget-guard`, `max_instance_count = 1`, `min_instance_count = 0`, 256 MB, 60 s timeout, `ingress_settings = "ALLOW_INTERNAL_ONLY"` (Eventarc delivery counts as internal, per the Cloud Run documentation; `budget-guard-test` would show otherwise), no unauthenticated access. Environment: `GUARD_PROJECT_ID`, `GUARD_BUDGET_ID` (the last segment of the budget's `name`) and `GUARD_BILLING_ACCOUNT` (the account id from `data.google_project`).
- `event_trigger`: event type `google.cloud.pubsub.topic.v1.messagePublished` on the topic, `retry_policy = "RETRY_POLICY_RETRY"`, trigger service account `budget-guard`. Eventarc creates the subscription; Terraform removes it with the function.

What `guard.decide(data, attributes, config)` returns, for each message, in this order:

1. The payload is not JSON with numeric `costAmount` and `budgetAmount` and a `currencyCode`: `ignore` ("malformed").
2. `attributes["budgetId"]` is not `GUARD_BUDGET_ID`: `ignore` ("other budget").
3. `costAmount < budgetAmount`: `none` ("below budget", with both amounts).
4. `attributes["itp_dry_run"] == "1"`: `dry_run`.
5. `attributes["billingAccountId"]` is not `GUARD_BILLING_ACCOUNT`: `ignore` ("not from Cloud Billing"). Cloud Billing's own messages carry this attribute; a hand-made test message without it can never disable billing.
6. Otherwise: `disable`.

What `main.stop_billing` does with it:

- `ignore` and `none`: log and return, so the message is acknowledged.
- `dry_run`: call Cloud Billing `projects.getBillingInfo`, and Resource Manager `testIamPermissions` on the project for `resourcemanager.projects.deleteBillingAssignment` and `resourcemanager.projects.get`. Log "DRY RUN: would disable billing on <project>: cost X TRY >= budget Y TRY; billing enabled: <bool>; permissions: deleteBillingAssignment=<bool>, get=<bool>". It never calls `updateBillingInfo`.
- `disable`: `getBillingInfo`. If billing is already off, log "already disabled" and return: running twice changes nothing. Otherwise call `projects.updateBillingInfo` with an empty `billingAccountName`, then log "billing disabled on <project>: cost X TRY >= budget Y TRY". An API error is logged and raised, so Eventarc delivers the message again. Budget notifications repeat during the day anyway.
- Every message produces exactly one structured log line (JSON on stdout: severity, decision, reason, cost, budget, currency, budget id, message id, result). Nothing else is logged, and no credential is ever in a payload.

**Service account and roles.** `budget-guard` runs the function and is the trigger's identity. It holds two roles:

- `roles/billing.projectManager` (Project Billing Manager) **on the project**. It is the narrowest predefined role with `resourcemanager.projects.deleteBillingAssignment`, which unlinking the project from its billing account needs (from Google's IAM role reference, not checked here). If `getBillingInfo` turns out to need more (`resourcemanager.projects.get`), the dry run shows it (`get=false`) and `roles/browser` is added.
- `roles/run.invoker` on the function's own Cloud Run service, so the trigger can call it.

It holds nothing on the billing account: unlinking needs only the project permission. Linking again needs the billing account, and only the owner does that. Granting roles on the project needs `resourcemanager.projects.setIamPolicy`, so `make budget-apply` is run by the owner, who is the project's Owner. Creating the budget needs budget rights on the billing account, which the owner holds as its administrator (not checked here). If the first apply reports a missing grant for Eventarc or the build (some projects need extra service-agent grants), the error names it, and the fix goes into this root.

**Proving it is wired, without disabling anything.** `make budget-guard-test` runs `scripts/budget_guard_check.py`:

1. It publishes one message to the topic with `gcloud pubsub topics publish`: a payload whose `costAmount` equals `budgetAmount` (so step 3 passes), the attributes `budgetId=<GUARD_BUDGET_ID>` and `itp_dry_run=1`, and no `billingAccountId`. The script has no option to leave out `itp_dry_run`. Even if the attribute were lost, rule 5 above would ignore the message.
2. It waits up to 3 minutes for the function's log line with that message id (`gcloud logging read`), prints it, and exits 1 unless the decision is `dry_run` and both permissions are true.

That proves topic → trigger → function → permissions. The other half, Cloud Billing → topic, is proven by the next real notification: within a day the function logs one "below budget" line with the project's actual cost. The 3A log records both lines.

**Unit tests** (`tests/unit/test_budget_guard.py`, loading `guard.py` by path, so no cloud library is needed): every rule above, in order. The tests cover a malformed payload; another budget id; cost one unit below budget; cost equal to budget, which acts; a dry run never giving `disable`; a missing or wrong `billingAccountId` never giving `disable`; and the real path. `main.py` imports the cloud libraries only inside its client factory, so a second test can load it with fake clients and drive `main.stop_billing`: the dry run never calls `updateBillingInfo`, the real path calls it once, an already disabled project calls it zero times, and each case writes one log line.

**APIs** enabled by this root (`disable_on_destroy = false`): `billingbudgets`, `cloudbilling`, `pubsub`, `cloudfunctions`, `run`, `cloudbuild`, `eventarc`, `artifactregistry`, `logging`.

**Make targets:** `budget-plan` and `budget-apply` (`BUDGET_TRY=<n>`, `-var project_id=$GOOGLE_CLOUD_PROJECT`), `budget-guard-test`, and `teardown-budget` (§5.5).

### 5.5 Teardown

`make teardown`, in this order, each step printing what it removes:

1. `sandbox_infra.py sweep --all`.
2. `sandbox_infra.py prune-templates --all`, which deletes every template under the engine.
3. `sandbox_infra.py delete-engine --name <agent_runtime_resource_name> --expect-display-name issue-to-pr`. Force-delete also removes sessions and other children, which could otherwise block Terraform's own delete of the engine (§15).
4. `terraform -chdir=deployment/terraform/single-project destroy -var project_id=$GOOGLE_CLOUD_PROJECT`. The engine is already gone, so Terraform drops it from its state. Everything else goes: bucket, dataset, connection, sink, service accounts, bindings, the Artifact Registry repository with its images.
5. Delete the `gs://<project>_cloudbuild` bucket that `gcloud builds submit` creates.
6. Print that the budget root (budget, topic, function, their service accounts and source bucket) remains, and that `make teardown-budget` removes it.

`make teardown-budget` runs `terraform destroy` in `deployment/terraform/budget`, then deletes what Cloud Run functions created on their own while building (the `gcf-artifacts` Artifact Registry repository and any `gcf-v2-*` buckets, listed before deletion). It removes the hard stop, so it is the last step of a teardown and needs the owner's approval at the time.

APIs stay enabled (`disable_on_destroy = false` in the scaffold). Cloud Trace and Cloud Logging keep their default retention; nothing there costs money at this volume. `make teardown-dry-run` prints steps 1 to 5 without changing anything (`terraform plan -destroy` and the script's listings). The dry run is part of 3A; a real teardown happens only when the owner asks, at the end of Week 3 at the earliest.

## 6. Stage 2: deploying the bench graph

### 6.1 What is deployed

The container that `Dockerfile` builds: `uvicorn app.fast_api_app:app` on port 8080. It serves `app.agent.app`: the bench graph (`build_workflow(RoleModels.from_env())`) with `BudgetPlugin`, `ReflectAndRetryModelPlugin`, `GuardrailPlugin`, `SandboxReleasePlugin` and, when `BQ_ANALYTICS_ENABLED=1`, the BigQuery analytics plugin. The bench graph's `fetch_issue` refuses `mode="live"` without reading any token, so the deployed agent cannot reach GitHub. No token, no `GITHUB_TOKEN_FILE` and no `LIVE_*` setting is deployed.

The `Dockerfile` gains `COPY ./bench ./bench`, so `load_task` and `materialize` find the dev tasks. Since `pyproject.toml` lists `bench` as a wheel package, the image build is checked locally first (`make deploy-check`, free).

### 6.2 What is uploaded

`make deploy-stage` runs `scripts/stage_deploy.py`, which rebuilds `build/deploy/` (git-ignored through `build/`) from scratch with exactly:

- `Dockerfile`, `pyproject.toml`, `uv.lock`, `README.md`, `agents-cli-manifest.yaml`, `deployment_metadata.json`;
- `app/` and `bench/*.py`, without caches;
- `bench/repos/`;
- for each directory in `bench/tasks/` whose name does not match `*-h[0-9][0-9]` (skipped by name, so nothing in it is opened): `task.yaml` and `plant/` only. Each copied `task.yaml` must say `split: dev`, or the stager stops with a fixed message.

It never copies `hidden_tests/`, `solution/`, `shortcut/`, `bench/review_probes/`, `results/`, `runs/`, `docs/`, `tests/`, `spikes/` or `.env`. `make deploy` then runs agents-cli from `build/deploy/`. agents-cli finds the manifest there, packages only that tree, and finds no `.env` to copy or print. Afterwards `make deploy` copies `deployment_metadata.json` back to the project root, so the next deploy finds the engine by id.

A unit test runs the stager on a synthetic tree (a repository, a dev task with all four overlays, a directory named like a held-out task) and checks the exact file list. A second test runs it on the real tree and checks, without printing names, that no staged path matches the held-out pattern, `hidden_tests/`, `solution/`, `shortcut/` or `.env`; it walks with `os.walk`, pruning held-out directories by name, as the Week 2C boundary test does.

### 6.3 How the deployed agent gets its configuration

`make deploy` runs, from `build/deploy/`:

```
agents-cli deploy --project $GOOGLE_CLOUD_PROJECT --update-only \
  --min-instances 0 --max-instances 1 --concurrency 4 --cpu 1 --memory 4Gi \
  --update-env-vars ENVIRONMENT_BACKEND=agent_runtime,SANDBOX_ENGINE=<engine>,SANDBOX_TEMPLATE=<template>,SANDBOX_CALLER_SA=<email>,BQ_ANALYTICS_ENABLED=1,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false,OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT
```

- `<engine>` and `<email>` come from `terraform output`; `<template>` from `sandbox_infra.py template --find-only`, which creates nothing.
- `--update-only` because Terraform owns the engine: a deploy must never create a second one.
- The span-content variables are already the defaults of Terraform and agents-cli; passing them makes the intent visible and testable.
- Everything Terraform put on the engine (logs bucket, Gemini location `global`, telemetry, `BQ_ANALYTICS_*` targets) stays, because a deploy keeps existing variables.
- The platform sets `GOOGLE_CLOUD_PROJECT` itself.
- Model names are not set, so the defaults of `app/models.py` apply (rule 7).
- `SANDBOX_TTL_S`, `SANDBOX_READY_TIMEOUT_S` and the caps keep their defaults.

The `.env` on the laptop never sets `BQ_ANALYTICS_ENABLED`; AGENTS.md says so (§11).

### 6.4 Observability when deployed

- **Cloud Trace.** `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` makes `fast_api_app` export ADK's spans. `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` (read by `google/adk/telemetry`) and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` keep prompts and responses out of span attributes. Local `TRACE_TO_CLOUD=1` runs are unchanged and still carry content.
- **Prompt and response logging.** Terraform's completion hook uploads model inputs and outputs as JSON lines to `gs://<logs bucket>/completions/`, readable through the `completions` table and `completions_view`. This is the parent spec's "prompt-response logging, on when deployed" (§10). It holds repository code and issue text, never a credential (the bench graph has none). The bucket's 30-day rule removes it.
- **BigQuery agent analytics.** The plugin writes to `issue_to_pr_telemetry.agent_events`, a table partitioned by day on `timestamp`, which it creates itself, plus `v_*` views. With the dataset's default partition expiry, partitions older than 30 days are dropped. Content offloaded to GCS (`BQ_ANALYTICS_GCS_BUCKET`, the logs bucket) follows the bucket rule.
- **Why partition expiry and not table expiry** (decision D): table expiry deletes a whole table 30 days after it was created. That would remove Terraform's tables and view, and the sink's target, after a month, and Terraform would recreate them on the next apply. Partition expiry removes data older than 30 days and keeps the structure.

### 6.5 How a deployed run differs from a laptop run

| | Laptop (`bench.run`, `app.live`) | Deployed (agents-cli serving) |
|---|---|---|
| Runner | `app/driver.py` | `fast_api_app` with ADK's `Runner` |
| Wall-clock cap `RUN_TIMEOUT_S` | Yes | No (no driver); the sandbox TTL, `MODEL_CALL_TIMEOUT_S`, the 120 s command timeout and the cost and tool-call caps bound a run (§16, question 7) |
| Root span `issue_to_pr.run` with outcome and cost | Yes, with `TRACE_TO_CLOUD=1` | No; ADK's invocation span is the root |
| `record.json`, `patch.diff` | `runs/<run-id>/` on the laptop | Written inside the container and lost with it; the diff is in the session's state (`diff`) |
| Sandbox release | The driver's `finally` | `SandboxReleasePlugin` |
| Sessions | In memory | Agent Runtime sessions when the platform sets `GOOGLE_CLOUD_AGENT_ENGINE_ID` (`app/app_utils/services.py`) |
| Scoring | `bench.matrix` scores each run | None; resolve rates come from laptop runs |

## 7. Verification

### 7.1 Unit tests (no GCP, no network)

In `tests/unit/test_agent_runtime_environment.py`, with a fake `SandboxControl` and `httpx.MockTransport`:

- Readiness: 502 responses, then 200, start once; never 200 within the bound gives `InfraError` and exactly one delete; `/healthz` is never called.
- Each row of §4.4: the request sent (method, path, query, JSON body, the `timeout` wrapper and its quoting, the three headers) and the mapping of each status and transport error.
- Timeouts: exit codes 124 and 137, and the shim's `timed_out`, all give `timed_out=True`.
- `upload_dir`: the zip's member names and contents; a link refuses.
- `close()`: idempotent; "not found" is success; a failing delete is raised to the caller, which logs it (existing behaviour of the release paths).
- Cancellation during create: the sandbox that the create returns is deleted, then the cancellation propagates.
- The token canary: a sentinel token from the fake signer appears in request headers and nowhere else: not in any exception text, `ExecResult`, log record (`caplog` at DEBUG), `os.environ`, or `repr` of the environment.
- `env_id` has the form `itp-<12 hex>`; the resource name does not appear in it.
- `check_environment_config()`: each refusal, with its fixed message; nothing is checked for `docker` beyond today.
- The factory picks the backend and raises the existing error for unknown names.
- `scripts/sandbox_infra.py`: template matching (image and `ACTIVE`; a match with the internet on is refused), sweep age and state filtering, `delete-engine`'s display-name check, all with a fake client.
- `scripts/stage_deploy.py`: §6.2.
- `app/agent.py` builds no analytics plugin when `BQ_ANALYTICS_ENABLED` is unset (existing test kept).

### 7.2 One contract suite for both backends

The behaviour tests in `tests/integration/test_docker_environment.py` move into a suite that takes the backend as a fixture parameter: upload and run `pytest`, read and write round trip, missing file, directory read is `OSError`, write failures are `OSError`, timeout reported, a SIGTERM-ignoring command still times out, sub-second timeout, closed sandbox raises `InfraError`, the `list_dir` tool, no network, no host credentials. The Docker parameter keeps the `docker` marker. The cloud parameter has the `cloud` marker (§7.3). Expectations that differ by backend (read-only root filesystem) are written as backend-specific checks. Docker-only tests (self-destruct by `sleep`, the token file) stay Docker-only.

### 7.3 Opt-in tests against a real sandbox

- Markers `cloud` and `cloud_slow` (registered in `pyproject.toml`). `tests/conftest.py` skips every test with either marker unless `ITP_CLOUD_TESTS=1`, so a plain `uv run pytest`, `make test`, `make test-docker` and CI never run them. `make test-cloud` sets the variable, runs `-m "cloud or cloud_slow"`, and needs the four settings of §4.2.
- Besides the contract suite, `tests/integration/test_agent_runtime_sandbox.py` checks:
  1. **No metadata credentials:** a request from inside the sandbox to `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token`, and the same path on `169.254.169.254`, with `Metadata-Flavor: Google`, fails or returns no token. Only a boolean is printed.
  2. **No forwarded `Authorization` header:** the shim cannot report request headers, and it cannot be replaced in place (the entrypoint `exec`s uvicorn, so it is the container's main process). The test therefore creates a test-only template that declares ports 8080 and 8081, starts a small server on 8081 through the shim's `POST /processes`, sends it one request with the token and `X-Sandbox-Port: 8081`, and checks the server's answer, which reports as booleans which headers arrived. The token is never printed. The sandbox and the test template are deleted in a `finally`. While the test template exists it has the same image as the real one, so the script's template matching also requires the ports to be exactly 8080 when the API returns them (§5.2). If the platform does not route to a second declared port, the question stays open and the report says so. Marked `cloud_slow` (creating a template takes 1 to 4 minutes).
  3. **Identity and environment:** uid 1000, and the environment's variable names contain nothing that looks like a credential (names only, as in the spike).
  4. **Root filesystem:** whether `/` is writable. Recorded in the test output, not asserted.
  5. **TTL:** a sandbox created with `ttl` 120 s is `STATE_DELETED` or gone within 10 minutes. Slow, so also marked `cloud_slow`.
- Cost: cents (about ten sandboxes and one short-lived test template). Run once in Stage 1, and again after any image or template change.

### 7.4 Parity

Both halves run one sandbox job at a time (`bench.probes validate` is sequential; `bench.run` gets `--concurrency 1`), back to back, on the same commit.

- **(a) Model-free.** `ENVIRONMENT_BACKEND=agent_runtime uv run python -m bench.probes validate` applies each of the 12 committed probe patches (8 dev tasks) in fresh cloud sandboxes and runs the visible and hidden tests, exactly as on Docker, where all 12 print `ok`. Every probe must print `ok` on the cloud backend too: 6 bad probes fail the hidden tests and 6 good ones pass them. No model is called; cost is about 24 sandboxes.
- **(b) With the model.** `uv run python -m bench.run --tasks tc-003,md-001,sr-002 --system multi --preset flash --repeats 1 --concurrency 1`, once with `ENVIRONMENT_BACKEND=docker` and once with `agent_runtime`, each with its own `--out` directory (`results/3a-parity/docker`, `results/3a-parity/cloud`). In the Week 2B comparison all three were resolved in every run by both systems; they cover all three repositories and an easy feature, a medium bug and a hard bug. "Match" means the same outcome bucket per task (`bench.report`'s six buckets), no cloud run ending in infra, and no infra rerun on the cloud side. A model may still vary at temperature 0. If a task differs, it is run once more on both backends; if it still differs, the cloud run's `events.jsonl` is read for sandbox errors before anything is concluded. Result rows do not record the backend, so `bench.report` would pool the two files as one configuration: each file gets its own generated report, and the hand-written report `docs/results/<date>-3a-cloud-parity.md` compares them task by task, with wall times per backend (sandbox start time is the expected difference).

### 7.5 Smoke run against the deployed agent

```
uv run python scripts/agents_cli_eval.py run '{"task_id": "tc-001", "run_id": "cloud-smoke-1"}' \
  --url https://us-central1-aiplatform.googleapis.com/reasoningEngines/v1/<engine>/api --mode adk
```

The existing launcher raises agents-cli's 120 s per-read timeout, which a silent planner call can exceed. Pass: the last event says `patch written`. Then the owner checks in the console that the trace's model-call spans hold no prompt or response text and that `agent_events` has rows for the run, and `make sweep-sandboxes` finds nothing to delete.

### 7.6 When paid checks may run

On 2026-10-01, single tool-using calls to gemini-3.8-flash took 80 to 260 s (the owner's measurement). The 2C ledger (`.superpowers/sdd/2026-10-01-week2c-live-mode/progress.md`, probe smoke 1) records a direct call with one tool declaration at 264 s, and plain text calls at 3 s. Parity (b) and the smoke run wait for the ledger's gate: three consecutive direct tool calls under 15 s. Everything else (unit tests, Terraform, the image, the template, the opt-in tests, parity (a), the deploy itself) calls no model and does not wait.

## 8. Security

| Threat | Controls |
|---|---|
| Code in the sandbox reaches the internet | Template `internet_access: false`; the opt-in test (spike: connection to 1.1.1.1 timed out) |
| Code in the sandbox gets a Google credential from the metadata server | Opt-in test 1; Stage 1 stops if it does |
| Code in the sandbox reads the token from a forwarded header | Opt-in test 2; Stage 1 stops if the header arrives (§16, question 6) |
| The token leaks from the orchestrator | Held in one object; never in environment, state, events, records, prompts, spans, logs or error text; the unit canary test |
| The token is used after a run | Lifetime `SANDBOX_TTL_S` (30 min) |
| A leftover sandbox keeps running and billing | TTL, every release path (§4.7), the sweeper, teardown |
| Held-out tasks leave the laptop | The deploy uploads only the staging tree (§6.2). `bench.run` refuses `--confirm-heldout` when `ENVIRONMENT_BACKEND=agent_runtime`, with a fixed message, so hidden tests of held-out tasks never reach a cloud sandbox. `bench.probes` already refuses held-out ids |
| `.env` values reach the deployed agent or the terminal | No `.env` in the staging tree; explicit `--update-env-vars` |
| A deploy creates a second engine | `--update-only` |
| Someone else calls the deployed agent | The Agent Runtime endpoint is called with the caller's Google access token (agents-cli's `_remote.py` sends it), so only identities with access to the project get in (platform behaviour, not tested here); the agent can only run bench tasks and refuses live requests |
| A template with the internet on is reused | The script refuses such a match; `start()` checks the template once per process |
| Wrong engine deleted | `delete-engine` needs the exact resource name and display name, and has `--dry-run` |
| Runaway spend | Unchanged caps; the spend rule (§9); budget alerts at 50%, 80% and 100%; the hard stop at 100%; `max_instances = 1`; `min_instances = 0` |
| A mistaken or forged message on the budget topic disables billing | Only Cloud Billing's account and project owners can publish. The real path needs the right `budgetId` and `billingAccountId` and cost at or above budget. The test script always sets `itp_dry_run=1` and leaves out `billingAccountId` |
| The guard's service account is misused | It can only unlink the project from billing (Project Billing Manager), never link it to an account: it holds nothing on the billing account |

Hard rule 2 holds: the sandbox gets no credential and no network. The new credential is the orchestrator's, like the GitHub token, and it is kept the same way.

## 9. Cost

| Item | Estimate |
|---|---|
| Cloud Build of the sandbox image (about 1 minute) | cents |
| Template creation, opt-in tests, parity (a), sweeps (about 40 sandboxes of a few minutes) | under $1 (the spike's four sandboxes cost a few cents) |
| Parity (b): 6 multi-agent runs on Flash on easy tasks (Week 2B average $0.43 per run) | $1.50–3 |
| Deploy: remote image build | cents |
| Smoke run | about $0.30 |
| Budget guard: one function build, a few invocations a day, Pub/Sub messages | cents |
| **Total** | **about $2–5** |
| Idle: Artifact Registry storage, logs bucket, BigQuery storage, the guard's invocations; Agent Runtime at `min_instances = 0` | well under $1 a day |

Whether an idle template or engine bills anything cannot be seen from the code (§15). One day after Stage 1 setup, the owner checks the billing report for the project; if idle cost is over $1 a day, the template is deleted between sessions (`prune-templates --all`; recreating one takes 1 to 4 minutes). The plan stops and reports if 3A's spend passes $5.

**The spend rule (decision 11).** The 3A log (`docs/results/<date>-3a-run-log.md`, like the Week 2 run logs) keeps a running total of the project's spend in dollars, before credits. It opens with the figure from the project's billing report, converted at the rate recorded for the budget (decision 8), with its date. After that it adds each paid step's cost from its result files, and replaces the total with a newer billing-report figure when the owner gives one. Before every paid step, the controller adds the step's highest likely cost (the upper end of its estimate in §13) to the running total. If the sum would pass $500, the controller stops and asks the owner, whatever the step's own estimate. The 3A cap of $5 applies as well; whichever is reached first stops the work.

## 10. Changes to existing behaviour

- `app/environment/factory.py`: the `agent_runtime` branch and `check_environment_config()`. Docker behaviour is unchanged.
- `bench.run`, `bench.probes`, `bench.review_probe`, `app.live` and the driver call `check_environment_config()` at start. `bench.run` refuses `--confirm-heldout` with the cloud backend.
- `Dockerfile`: `COPY ./bench ./bench`.
- `tests/conftest.py`: removes the new variables; skips `cloud` and `cloud_slow` tests unless `ITP_CLOUD_TESTS=1`.
- `pyproject.toml`: markers `cloud` and `cloud_slow`; the `google-cloud-aiplatform` lower bound rises to a version that ships `agentplatform` (the lock already has 1.165.1).
- `Makefile`: `infra-plan`, `infra-apply`, `budget-plan`, `budget-apply`, `budget-guard-test`, `sandbox-cloud`, `test-cloud`, `sweep-sandboxes`, `deploy-stage`, `deploy-check`, `deploy`, `smoke-deployed`, `teardown-dry-run`, `teardown`, `teardown-budget`.
- Terraform as in §5.1, plus the new root `deployment/terraform/budget/` with the function source in `deployment/terraform/budget/function/` (§5.4).
- New scripts `scripts/sandbox_infra.py`, `scripts/stage_deploy.py` and `scripts/budget_guard_check.py`, each with unit tests; `tests/unit/test_budget_guard.py`.
- No change to the graph, the nodes, the tools, the guardrails, the plugins, the prompts, the caps or the model names. Week 2 results and reports are untouched.

## 11. AGENTS.md changes

In the same commit as the code they describe:

- **Status:** the Agent Runtime sandbox backend is built; the bench graph is deployed (after Stage 2); the live graph stays local.
- **Hard rule 2** gains: with `ENVIRONMENT_BACKEND=agent_runtime`, the orchestrator holds one more credential, a JWT for `sandbox-caller` that lasts `SANDBOX_TTL_S`. Only `AgentRuntimeEnvironment` holds it, and it goes only into the request headers to the sandbox proxy, never into state, events, logs, traces, error text or the sandbox.
- **Hard rule 5** gains: held-out tasks never run on the cloud backend, and the deploy uploads only `build/deploy/` (`make deploy`); never run `agents-cli deploy` from the project root.
- **Hard rule 6** gains: the project's spend limit is $500, before credits. Before any step that would push the running total past $500, stop and ask the owner; the running total is kept in the current week's log (§9). At 100% of the budget a function disables billing on the project, which stops everything in it, including the deployed demo; re-enabling billing is the owner's manual step. Never publish to the budget topic by hand; `make budget-guard-test` is the only test path, and it is a dry run.
- **Hard rule 8** gains: the `make` targets that create resources or spend credits (`infra-apply`, `budget-apply`, `budget-guard-test`, `sandbox-cloud`, `test-cloud`, `deploy`, `smoke-deployed`, `teardown`, `teardown-budget`) need the owner's approval at the time; `sweep-sandboxes`, `infra-plan`, `budget-plan`, `deploy-stage`, `deploy-check` and `teardown-dry-run` do not.
- **Runtime wiring:** a bullet on the cloud backend (§4: start, readiness, the `exec` wrapper, the zip upload, the error mapping, `close()`, the sweeper); a bullet on the deployed agent (§6: bench graph only, release by `SandboxReleasePlugin`, no `RUN_TIMEOUT_S`, analytics on, spans without content, configuration by `make deploy` only).
- **Environment variables:** rows for `SANDBOX_ENGINE`, `SANDBOX_TEMPLATE`, `SANDBOX_CALLER_SA`, `SANDBOX_READY_TIMEOUT_S` and `ITP_CLOUD_TESTS`; `ENVIRONMENT_BACKEND` becomes `docker` or `agent_runtime`; `SANDBOX_TTL_S` covers both backends and the token; `SANDBOX_IMAGE` is Docker only; `BQ_ANALYTICS_ENABLED` is set by `make deploy` on the deployed agent and never in `.env`.
- **Workflow:** Terraform 1.11 or newer, installed with `brew install hashicorp/tap/terraform` (the installed version is in the 3A log); `agents-cli infra single-project` always gets `--project`; Terraform state is local in `deployment/terraform/single-project` and `deployment/terraform/budget` (git-ignored) and must not be lost, or teardown cannot find what it made. `make teardown` leaves the budget root; `make teardown-budget` removes it and is always last.
- **Commands:** the new `make` targets, replacing the bare `agents-cli deploy` row; the smoke command of §7.5; the cloud parity commands of §7.4.
- **Testing conventions:** the `cloud` markers and `ITP_CLOUD_TESTS`; tests never touch GCP unless that variable is set.

## 12. Spec amendments this design asks for

Recorded in the parent spec's §19 when the work lands:

1. §7.3: the cloud backend as built (§4.4, §4.5), template resources 2 CPU and 2 GiB, `AgentRuntimeSandbox` named `AgentRuntimeEnvironment`, readiness through `/exec`, the `timeout` wrapper in both backends, and the root filesystem difference if the opt-in test finds one.
2. §7.1 and §7.6: the orchestrator's `sandbox-caller` JWT (§4.6).
3. §5.1 and §7.3: the release paths and the sweeper (§4.7).
4. §10: BigQuery analytics on when deployed only; 30-day partition expiry and bucket lifecycle; span content off when deployed.
5. §12: Terraform owns everything but the template and the image; the deploy uploads a staging tree with explicit configuration and `--update-only`; `min_instances = 0`; the teardown order. Step 8 (budget alerts at $250, $500 and $750) becomes one project budget of $500 in TRY, cost before credits, alerts at 50%, 80% and 100%, and a function that disables billing at 100%, all in their own root that `make teardown` leaves (§5.4).
6. §2 criterion 6, §4 and §14: the project's spend limit is $500 before credits, enforced by the controller's spend rule (§9) and the hard stop; $500 of the credits are not planned for.
7. §11: the deployed agent serves the bench graph only until the live UI exists.
8. §14: Week 3A's cost.
9. Week 1 review item 7 (output cap in the `Environment`): closed; the cap stays in the tools (§4.4), if the owner agrees (§16, question 8).

## 13. Build order

Tags: **[free]** costs nothing and creates nothing in the cloud. **[OWNER APPROVAL: resources]** creates, changes or deletes cloud resources. **[OWNER APPROVAL: spend]** spends credits. The plan repeats the tags; approval is asked at the time, in the session (hard rule 8). Before every step tagged spend, the controller applies the spend rule (§9): if the running total plus the step's upper estimate would pass $500, it stops and asks the owner.

1. **[free]** Backend: `app/environment/agent_runtime.py`, the factory branch, `check_environment_config()` and its callers, `bench.run`'s held-out refusal, conftest and markers. Tests first (§7.1).
2. **[free]** The contract suite (§7.2) and the opt-in tests (§7.3), written but not run against the cloud. `make test-docker` passes.
3. **[free]** `scripts/sandbox_infra.py` with its unit tests, and the Makefile targets.
4. **[free]** The budget guard: `guard.py` and `main.py` with their unit tests, and `scripts/budget_guard_check.py` (§5.4).
5. **[free]** Terraform files for both roots (§5.1, §5.4), including the `import` blocks. They are not run yet: Terraform is installed at step 8 (decision 12).
6. **[free]** `scripts/stage_deploy.py` with its tests (§6.2), the `Dockerfile` change, and `make deploy-check` (a local `docker build` of the staging tree).
7. **[free]** AGENTS.md (§11). Whole-branch review before anything paid (paid-run lessons).
8. **[free]** `brew install hashicorp/tap/terraform`; `terraform version` goes into the 3A log. `terraform fmt -check` and `terraform validate` in both roots; fixes are committed. The 3A log opens with the running total of the project's spend (§9).
9. **[OWNER APPROVAL: resources]** The budget root first, so the hard stop exists before anything else is created or spent. Record the day's USD/TRY rate, its source and the resulting `BUDGET_TRY` in the 3A log. Then `make budget-plan BUDGET_TRY=<n>` (the owner reads it), `make budget-apply BUDGET_TRY=<n>`, and `make budget-guard-test` (dry run; it must print `dry_run` with both permissions true). Within a day, record the first real notification's log line (§5.4).
10. **[OWNER APPROVAL: resources]** `make infra-plan` (read-only; the owner reads the plan, including the two imports), then `make infra-apply`. Remove the `import` blocks in the next commit.
11. **[OWNER APPROVAL: resources and spend, cents]** `make sandbox-cloud`: Cloud Build of the image, then the template. The owner adds the printed lines to `.env`.
12. **[OWNER APPROVAL: resources]** Remove the spike's leftovers (§5.3), each step after its dry run.
13. **[OWNER APPROVAL: spend, cents]** `make test-cloud`. **Gate:** if opt-in test 1 or 2 fails, stop and bring the options of §16, question 6 to the owner.
14. **[OWNER APPROVAL: spend, under $1]** Parity (a). Then `make sweep-sandboxes`.
15. **[OWNER APPROVAL: spend, $1.50–3; waits for the latency gate of §7.6]** Parity (b), its report and run log. Then `make sweep-sandboxes`.
16. One day later **[free]**: the owner checks idle cost in the billing report (§9), and the running total is updated from it.
17. **[OWNER APPROVAL: resources]** `make deploy`.
18. **[OWNER APPROVAL: spend, about $0.30; waits for the latency gate]** `make smoke-deployed`, then the console checks of §7.5 and `make sweep-sandboxes`.
19. **[free]** `make teardown-dry-run`; its output goes into the run log. Parent spec §19 amendments (§12); AGENTS.md status.

## 14. Out of scope

The live UI and anything it needs (Cloud Run proxy, IAP, persistent sessions for approvals, Secret Manager for the GitHub token); deploying the live graph or the single-agent baseline; CI and its Workload Identity Federation (3C); the replay UI (3B); any change to prompts, caps, loop bounds or model names; the held-out run; running the full benchmark on cloud sandboxes (only parity); narrowing the scaffold's broad roles on the app service account (`roles/storage.admin`, `roles/bigquery.dataOwner`); a remote Terraform state backend; Artifact Registry cleanup policies; publishing this repository; the Pro reviewer hang.

## 15. Risks

| Risk | Mitigation |
|---|---|
| The proxy forwards `Authorization` into the container | Opt-in test 2 before any other cloud use; Stage 1 stops if it does (§16, question 6) |
| The metadata server hands out a credential inside the sandbox | Opt-in test 1; Stage 1 stops if it does |
| The platform's TTL does not remove sandboxes (spike gotcha 8, unchecked) | Opt-in TTL test; the sweeper; teardown |
| A run under agents-cli whose client goes away never reaches `SandboxReleasePlugin` | TTL and sweeper; the smoke run uses the launcher's long read timeout |
| Agent Runtime limits how long one request may stream; a run lasts up to 25 minutes | Not visible in the code. The smoke run uses `tc-001`, which finishes in minutes. If a limit cuts runs, it is documented and long runs stay on the laptop |
| The deployed agent cannot reach the sandbox proxy or IAM Credentials (outbound access from Agent Runtime) | The smoke run shows it; the failure is `infra`, and no model is called before the sandbox exists |
| Terraform's engine delete fails on child resources (sessions, templates) | Teardown force-deletes the engine through the SDK first (§5.5) |
| The imports do not match the hand-made objects (repository format, account settings) | `infra-plan` shows every difference before apply; the owner reads it |
| `data.google_client_openid_userinfo` or `data.google_project` returns no email or billing account | Fallback variables (`operator_member`, `billing_account`) |
| Budget notifications lag the real cost by hours, so spend passes $500 before the stop fires | The spend rule (§9) stops work well before the limit; the stop is the backstop |
| The custom budget period does not count spend from before the apply | The first real notification's `costAmount` shows it. If it does not count it, the budget amount is lowered by the spend already recorded in the 3A log, with the owner's yes |
| The hard stop fires: every service in the project stops, including the deployed demo | Stated plainly in §5.4 and AGENTS.md. Re-enabling billing is the owner's manual step, and a redeploy may follow |
| Project Billing Manager is not enough for `getBillingInfo`, or the documented roles differ | The dry run reports both permissions; `roles/browser` is the named fix for a missing `resourcemanager.projects.get` |
| Eventarc or the function build needs a grant this root lacks | The first apply names it; it is added to the budget root, never by hand |
| The USD/TRY rate moves, so the TRY budget no longer means $500 | The spend rule counts dollars; the budget is re-applied with a new rate when the owner asks |
| Sandbox or template quotas are lower than a run needs (a run uses 1 sandbox, then 2 for scoring) | Parity at concurrency 1; quotas are not raised in 3A |
| Cloud and Docker differ where no test looks (root filesystem, process limits, architecture, apt package builds) | The contract suite, parity (a) and (b); differences are reported in the parity report |
| `uv sync --frozen` in the image fails now that `bench/` is present | `make deploy-check` builds it locally first, for free |
| An agents-cli upgrade changes packaging or environment handling | The staging tree makes the upload explicit whatever the CLI does; AGENTS.md pins the behaviour described here to agents-cli 1.7.0 |
| The laptop's default quota project is not `cloud-agents-project` (owner's note) | The client is built with the project explicitly; the spike's `generate_access_token` worked from this laptop; opt-in tests confirm |
| `agentplatform.Client` behaves differently from the spike's `vertexai.Client` | Same constructor and the same sandbox module; the opt-in tests run it before anything else |
| An idle template or engine bills | The check one day after setup (§9) |
| Gemini latency stays high | Paid checks wait (§7.6); the free steps do not |
| Deploying while a sandbox is in use | Deploy only when no run is active; the plan says so |

## 16. Open questions, with recommended answers

1. **Which engine hosts sandboxes and templates?** (A) The Terraform engine `issue-to-pr`, which is also the deployed agent; or (B) a second, Terraform-made host engine. **Recommendation: A.** One engine, created before Stage 1 by `infra-apply`, removed once by teardown. B adds a resource for no gain. The cost of A: a deploy updates the engine that holds the template, which should not touch child resources (not verified; the deploy happens when no run is active).
2. **The spike's repository and service account: import or recreate?** **Recommendation: import** (`import` blocks, removed after the first apply). Recreating would delete and rebuild the image and reuse a deleted service account's name for no benefit. The spike's host engine, its template and the `v0.1.0` tag are deleted.
3. **Budget scope and currency.** Settled by the owner: decision 8 ($500 in TRY at the day's rate, cost before credits, alerts at 50%, 80% and 100%) and decision 9 (the hard stop at 100%). The design adds the project filter and the custom period from 2026-09-29 (§5.4).
4. **Should the budget outlive `make teardown`?** Settled by the owner: decision 10. The budget, the topic and the function live in their own root, which `make teardown` leaves and `make teardown-budget` removes.
5. **What "tables expire after 30 days" means.** **Recommendation:** 30-day partition expiry on the dataset and its partitioned tables, plus a 30-day object lifecycle on the logs bucket (§6.4). Table expiry would delete Terraform's tables and view after a month.
6. **If the proxy forwards `Authorization` into the container (opt-in test 2 fails).** The spike could not tell. **Recommendation:** stop Stage 1 and bring three options to the owner: (a) a 5-minute token re-signed by the backend before it expires, which narrows the window but does not close it; (b) a custom role for `sandbox-caller` with only the permission the proxy checks, once that permission is known; (c) keep the cloud backend for parity only and run nothing from issues on it. Hard rule 2 rules out going ahead unchanged.
7. **Should the deployed agent get a wall-clock cap?** It has no driver, so `RUN_TIMEOUT_S` does not apply. **Recommendation: not in 3A.** A deployed run is bounded by the cost and tool-call caps, the model-call and command timeouts, and the sandbox TTL, after which every command fails as infra. The live UI will need a driver-like runner on the server; the cap belongs there.
8. **Close the deferred Week 1 item "enforce the output cap inside the `Environment`"?** **Recommendation: close it.** The tools already cap at 10,000 characters, and the nodes need full output (`collect_diff`'s `git diff`).
9. **Staging directory or `.gcloudignore` for the deploy?** A `.gcloudignore` allowlist would also keep held-out tasks out of the upload, but agents-cli would still copy `.env` into the deployed agent and print its values, and a local image build would see a different context. **Recommendation: the staging directory** (§6.2).
10. **Deployed size.** **Recommendation:** 1 CPU, 4 GiB, `min_instances` 0, `max_instances` 1, concurrency 4. The orchestrator mostly waits on the model and the sandbox; one instance bounds cost. The Terraform placeholder is created with the same values.
11. **A narrower role than `roles/aiplatform.user` for `sandbox-caller`?** The permission the proxy checks is not known. **Recommendation:** keep the role the spike proved; revisit only if question 6 needs it.
12. **Download as zip.** Decision 5 mentions it. No caller needs it (§4.4). **Recommendation:** leave it out of the protocol until a caller does.
13. **Which tasks for parity (b)?** **Recommendation:** `tc-003` (easy feature), `md-001` (medium bug), `sr-002` (hard bug): one per repository, and resolved in every Week 2B run by both systems. `tc-001` is left out because one of its Week 2B runs ended on a provider stall; it stays the smoke task for the deployed agent, as in AGENTS.md. Multi-agent only: the backends, not the systems, are being compared.
