# ADR 4: One sandbox image for local Docker and Agent Runtime

Date: 2026-10-02. Status: accepted.
Sources: [parent spec](../superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md) §7.2, §7.3 and §19 (2026-10-02, Week 3A item 1); [`sandbox_image/`](../../sandbox_image/Dockerfile), [`app/environment/base.py`](../../app/environment/base.py); [cloud parity](../results/2026-10-02-3a-cloud-parity.md), [Week 3A run log](../results/2026-10-02-3a-run-log.md).

## Context

The benchmark runs on local Docker: it costs nothing, starts fast, and works on a laptop and on CI runners. The deployed agent runs on Agent Runtime and runs code in Agent Runtime sandboxes instead. If the two places had different toolchains (another Python, another pytest, no `git` or `rg`), a patch could pass in one and fail in the other, and the benchmark numbers would say nothing about the deployed system.

## Decision

One build context, `sandbox_image/`, defines the sandbox for both backends, and one contract suite tests both.

- **The image.** `python:3.12-slim` with git, procps and ripgrep, pinned pytest 9.0.2 and uv 0.11.21, and a small FastAPI runtime shim copied unmodified from `google/adk-samples` (long-horizon-harness, Apache-2.0, credited in `sandbox_image/NOTICE`). It runs as the non-root `sandbox` user in `/workspace`.
- **Local.** `make sandbox-image` builds it as `issue-to-pr-sandbox:dev`. `DockerEnvironment` starts it with `sleep <ttl>` instead of the shim and runs commands with `docker exec`.
- **Cloud.** `make sandbox-cloud` builds the same directory with Cloud Build, tagged with the git tree id of `sandbox_image/` (it refuses uncommitted changes), and creates one template for it: 2 CPUs, 2 GiB, internet off, 30-minute TTL. `AgentRuntimeEnvironment` calls the shim over HTTPS (`/exec`, `/files`, `/files/zip`) and checks readiness with `/exec`, because the platform answers `/healthz` too early.
- **One contract.** Both backends implement the `Environment` protocol with one error contract (`InfraError` for a broken sandbox, `FileNotFoundError` or `OSError` for a bad path, a non-zero exit code for a failing command) and run each command as `timeout -k 5 <n> sh -c '<command>'`, where exit 124 or 137 means "timed out". `tests/integration/test_environment_contract.py` runs the same 13 tests on both. `ENVIRONMENT_BACKEND` picks the backend; no node or tool knows which one it has.

## Consequences

- Cloud results are comparable with local ones. In Week 3A all 13 contract tests passed on both backends, all 12 reviewer probes validated on both, and three dev tasks, one run each, ended in the same outcome on both: 2 of 3 resolved, $1.20 on Docker and $1.19 in the cloud. Three tasks show that the backends agree on those tasks; they cannot show a small difference in resolve rates.
- The cloud is slower. Each command is an HTTPS round trip instead of a local `docker exec`, and the three cloud runs took 0 to 90 s longer (+87%, +31% and +1%).
- "One image" means one build context, not one artifact. The base image and the apt packages are not pinned to a digest, so a local build and the cloud build can differ in patch versions; the Python tools are pinned.
- The local backend never starts the shim. Unit tests use a fake of it; of the tests, only the cloud tests (`make test-cloud`, run with the owner's approval because they create sandboxes) run the real one.
- The known differences are written down, not smoothed over: the cloud root filesystem is writable where Docker's is read-only, and deleting `/workspace/repo` makes the shim answer HTTP 500, which is an `InfraError` in the cloud and a failed command on Docker.
- Changing the image means a new cloud build and template (`make sandbox-cloud`, owner approval), not only a local rebuild.
