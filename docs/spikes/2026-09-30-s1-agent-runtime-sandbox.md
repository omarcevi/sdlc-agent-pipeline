# Spike S1: Agent Runtime Sandbox from our own image

- **Date:** 2026-09-30
- **Question:** Can a sandbox in Agent Runtime, built from our container image, take an uploaded repo and run `pytest` with no internet access? How long does it take to start?
- **Answer:** Yes. Proceed with an `AgentRuntimeSandbox` backend in Week 3.
- **Probe:** `spikes/s1_sandbox_probe.py` (add `--hold-seconds N` to keep the sandbox alive for inspection).
- **Cost:** a few cents.

## Resources created

| Resource | Name | Kept? |
|---|---|---|
| Artifact Registry repo | `us-central1-docker.pkg.dev/cloud-agents-project/issue-to-pr` | Kept |
| Image | `.../issue-to-pr/sandbox:v0.1.0` (Cloud Build, 63 s) | Kept |
| Service account | `sandbox-caller@cloud-agents-project.iam.gserviceaccount.com` (`roles/aiplatform.user`; the owner's user holds `roles/iam.serviceAccountTokenCreator` on it) | Kept |
| Agent Runtime instance | `issue-to-pr-sandbox-host` = `projects/666312056831/locations/us-central1/reasoningEngines/2290264578715549696` | Kept (empty host, no agent code) |
| Sandbox template | `.../sandboxEnvironmentTemplates/6774574923844157440` (our image, port 8080, internet off) | Kept |
| Sandboxes | four, one per probe run | All deleted |

Four duplicate templates created by an early probe bug were deleted; the API still lists them as `DELETED`.

## Results

| Check | Result |
|---|---|
| Container is ours | Commands run as uid 1000; the environment has the image's variables (`PYTEST_ADDOPTS`, `PYTHONDONTWRITEBYTECODE`) |
| Upload repo (`POST /files/zip`) | HTTP 200 in about 0.6 s |
| Run tests (`POST /exec`, `python -m pytest -q`) | exit 0, `1 passed` |
| `git init` + commit, `rg` | Work |
| Internet access | Blocked (`socket.create_connection` to 1.1.1.1 times out) |
| Download repo (`GET /files/zip`) | HTTP 200, both files returned |
| No host credentials | Environment holds only image and Kubernetes service variables |

## Timings

| Step | Time |
|---|---|
| Create a template (one-time per image) | 1–4 minutes |
| Create a sandbox from an existing template | about 2 s |
| Until our server answers commands | 5.6 s with a warm template; more than 30 s on the first sandbox after a new template |
| Upload + pytest on a tiny repo | under 1 s each |

## Gotchas

1. **The platform answers `/healthz` itself.** It returns plain `OK` about 0.3 s after creation, before our container is listening. A request sent then fails with `502 Bad Gateway: Unable to reach the sandbox environment`. Readiness must be a call that reaches our server, for example `POST /exec {"command": "true"}`, retried until it returns 200.
2. **Template display names are not returned.** `templates.create(display_name=...)` succeeds, but `list`/`get` return no display name, so a "find by name" check never matches and every run creates a new template. Match on `custom_container_environment.custom_container_spec.image_uri` and state `ACTIVE` instead.
3. **The console does not show this.** The Agent Platform "Deployments" page lists deployed agents, not an empty host instance, and there is no page for sandboxes or templates. Use the API, or the audit log: Logs Explorer with `protoPayload.serviceName="aiplatform.googleapis.com"` and `protoPayload.methodName:"Sandbox"`.
4. **Sandboxes run on Google-managed Kubernetes**, not as compute in the project, so nothing appears under Compute Engine, GKE or Cloud Run.
5. **Default size is 2 CPUs and 4 GB** (the local Docker sandbox uses 2 CPUs and 2 GB). Set the template's resources explicitly if laptop and cloud results must be comparable.
6. **Three request headers are required:** `Authorization: Bearer <token for the caller service account>`, `X-Sandbox-Routing-Token`, `X-Sandbox-Port`.
7. `vertexai.Client` prints a deprecation warning pointing to `agentplatform.Client`.
8. Not checked: whether the root filesystem is writable in the cloud sandbox (it is read-only locally), and the sandbox's behaviour at TTL expiry.

## Decisions for Week 3

- Build `AgentRuntimeSandbox` behind the existing `Environment` protocol, using the HTTP shim's `/exec`, `/files` and `/files/zip` endpoints.
- Readiness = a no-op `exec` that returns 200.
- Create the template once per image version in a setup script; at run time only create sandboxes.
- Set template resources to match the local sandbox limits.
- Reuse the resources listed above; pass their names through configuration.
