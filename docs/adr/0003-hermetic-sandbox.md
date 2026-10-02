# ADR 3: Hermetic, credential-free sandbox; git on the orchestrator side

Date: 2026-10-02. Status: accepted.
Sources: [parent spec](../superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md) §7 and §19 (2026-10-02, Week 3A items 1 to 3); [`AGENTS.md`](../../AGENTS.md) hard rules 2 and 3; [`app/environment/docker.py`](../../app/environment/docker.py), [`app/environment/agent_runtime.py`](../../app/environment/agent_runtime.py), [`app/nodes/finish.py`](../../app/nodes/finish.py); [Week 3A run log](../results/2026-10-02-3a-run-log.md) (cloud sandbox checks).

## Context

The coder runs shell commands that a model wrote, guided by issue text, which is untrusted input. The issue can carry a prompt injection, and the code under test can do anything pytest lets it do. If the place where that code runs can reach the network or holds the GitHub token, one bad issue can leak the token or push to the repository.

## Decision

Model-written code runs only in a sandbox with no network and no credentials. Everything that needs either runs in orchestrator-side function nodes.

- **Local backend.** One Docker container per run: `--network none`, `--cap-drop ALL`, `no-new-privileges`, a read-only root filesystem with tmpfs `/workspace` and `/tmp`, a non-root user, `--cpus 2 --memory 2g --pids-limit 256`, and a self-destruct after `SANDBOX_TTL_S` (1800 s by default).
- **Cloud backend.** One Agent Runtime sandbox per run, from a template with internet access off; `start()` refuses a template that is not active with internet off. To reach it the orchestrator holds one more credential, a JWT for the `sandbox-caller` service account that lasts `SANDBOX_TTL_S`, kept only in its HTTP client's request headers.
- **Tools go through `Environment`.** Agent tools call the sandbox's `exec`, `read_file` and `write_file`; none touches the host filesystem or starts a host process. A command runs under `timeout` (120 s by default). A guardrail plugin also refuses `git push`, `git remote`, `curl`, `wget` and package installs, and returns the reason to the model.
- **The token stays with the orchestrator.** It is read from the file `GITHUB_TOKEN_FILE` names, never from the environment, and only by `fetch_issue` (live graph), `open_pr`, `report_failure` and the driver's failure comment. It never enters prompts, session state, traces, logs or a sandbox.
- **Git with a remote runs on the host only.** In the sandbox, git only records a baseline commit, takes diffs and restores protected files, with its git directory outside the worktree and no remote. Live intake downloads the base branch as a tarball through the GitHub API, pinned to a commit. Delivery applies the patch with host `git apply` to that archive and publishes through the Git Data API (blobs, tree, commit, branch, pull request): no clone, no push. Scoring also runs patches in fresh sandboxes, so model-written code never runs on the host.

## Consequences

- A compromised agent has nothing to steal and nowhere to send it, and tests check it. On Docker, the sandbox sees none of the host's credential variables and cannot read the token file. A token canary test runs whole live runs with a sentinel token and finds it only in the `Authorization` header sent to GitHub. In the cloud, the Week 3A checks found no token from the metadata server, no request header reaching the container, and no credential in the sandbox's identity or environment.
- Agents cannot install packages or look anything up. Everything a task needs must be in the image ([ADR 4](0004-one-sandbox-image.md)) or the repository.
- The host side needs its own care. The archive is extracted by `app/archive.py`, which refuses links, any `.git` path, case-folded duplicate names, and more than 5000 files or 50 MB, and host git runs with `safe.bareRepository=explicit` (git 2.38 or newer), so an archive that looks like a repository is never treated as one.
- The two backends isolate differently: Docker's root filesystem is read-only, the cloud sandbox's is writable. Nothing in the pipeline depends on it.
- The sandbox limits what an injection can reach; it does not stop one. Issue text still reaches every agent (inside `<issue>` delimiters, marked as untrusted data), so a run can be steered into a wrong patch. Live mode answers that with two more layers: only issues whose `agent-ok` label was last added by an allowed login, and a human who approves the exact patch before a pull request opens.
- The spec's first plan, a token in Secret Manager or `.env` and an orchestrator-side clone that pushes a branch (§7.4, §7.6), was replaced by the token file and the Git Data API. The deployed agent serves the bench graph only and holds no GitHub token at all.
