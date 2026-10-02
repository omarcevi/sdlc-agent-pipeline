# Week 3C design: CI and CD on GitHub Actions

Date: 2026-10-02. Status: owner decisions given and design sections approved in conversation on 2026-10-02; written spec awaiting owner review.
Parent spec: `2026-09-29-sdlc-agent-pipeline-design.md` (§13 CI, §15 Week 3). Where this document is more specific, it wins for Week 3C once approved; the parent spec's §19 then records the amendments in §10.
Depends on: Week 3A (`2026-10-01-week3a-cloud-design.md`: single-project Terraform root, project pin, state backup, `scripts/stage_deploy.py`, the deployed bench graph) and Week 3B (`.github/workflows/pages.yaml`, its action pins and workflow tests).
Repository: public at `github.com/omarcevi/sdlc-agent-pipeline` (numeric id `1401169707`), default branch `main`, pushed to directly by the owner.

## Owner decisions (2026-10-02)

1. **Free checks on every push, paid jobs on demand.** Every pull request and every push to `main` runs lint, the unit tests, the Docker sandbox tests and task validation, at $0 on GitHub's free runners for public repositories. The smoke benchmark, the evals and full benchmarks run only when the owner dispatches them. This replaces the parent spec's "smoke bench and evals on every push to main" (about $2 per push; the owner pushes many times a day).
2. **Deploys on a release tag, behind the owner's approval click.** Pushing a `v*` tag starts a deploy job in a protected `production` environment that waits for the owner to approve it in GitHub, then deploys with a CI identity and runs the deployed smoke task.
3. **Cloud identity:** Workload Identity Federation, no JSON keys. Two service accounts with narrow roles (§4), in the single-project Terraform root.
4. **The scaffold's three workflows are replaced.** `pr_checks.yaml`, `staging.yaml` and `deploy-to-prod.yaml` assume separate staging and prod projects and a WIF setup that was never built; the owner disabled them on 2026-10-02. They are deleted.

## 1. Why

- **Parent §13 is not built.** Nothing checks a push today; a broken test or lint error reaches `main` unnoticed.
- **Hiring managers look for CI and CD.** A green check on each commit, and a release that deploys only after a human approves it, are what a production team expects.
- **The repository is public now,** so free runners make the free checks cost nothing, and the paid path must be safe to have in a public repository.

## 2. Success criteria

1. A push to `main` or a pull request runs `ci.yaml`: lint, the non-Docker tests, the Docker tests and `bench.validate` (20 `ok`), and is green on the current `main`.
2. `paid.yaml` runs only by manual dispatch, only from `main`, refuses held-out work before anything runs, prints its worst-case cost, refuses more than $25 without an explicit input, and a smoke dispatch resolves at least 2 of its 3 tasks.
3. A `v*` tag starts `release.yaml`, which waits for the owner's approval, deploys through WIF, and passes the deployed smoke run.
4. No JSON key exists. Only the paid and release jobs can obtain Google credentials, through two service accounts whose roles are exactly those of §4.
5. Every action is pinned to a full commit SHA; every workflow is read-only by default; every job has a timeout.
6. No CI job opens, prints or runs a held-out task's content.
7. The free checks cost $0; the verification in §9 spends about $1.30 in model calls.

## 3. Workflows

### 3.1 `ci.yaml` (free)

- **Triggers:** `pull_request` and `push` to `main`, with `paths-ignore` for `docs/**`, `**/*.md` and `web/**` (the replay site has its own workflow); `workflow_dispatch`.
- **Concurrency:** group `ci-${{ github.ref }}`, `cancel-in-progress: true`.
- **Permissions:** `contents: read`.
- **Jobs** (each with `timeout-minutes`):
  - `lint` (10 min): checkout, `setup-uv`, `uv sync --locked`, install `google-agents-cli==1.7.0` with `uv tool install`, `agents-cli lint`.
  - `unit` (20 min): `uv sync --locked`, `uv run pytest -q --tb=no -rf -m "not docker"`.
  - `docker` (40 min): `make sandbox-image`, `uv run pytest -q --tb=no -rf -m docker`, `uv run python -m bench.validate` (must print 20 `ok`).
- `--tb=no -rf`: a failure lists the failing test names only; the held-out tasks are collected by these suites, and CI logs are public (AGENTS.md rule 5).
- No step reads a secret; no step has `id-token: write`.

### 3.2 `paid.yaml` (manual)

- **Trigger:** `workflow_dispatch` only, with inputs:
  - `kind`: `smoke` | `eval` | `bench`;
  - for `bench`: `system` (`multi` | `single`), `preset` (`flash` | `pro` | `mixed`), `tasks` (comma-separated dev task ids, empty means the whole dev split), `repeats` (1 to 5);
  - `accept_over_25`: boolean, default false.
- **Guards, in a first job that needs no credentials:** the ref is `refs/heads/main`; no task id matches `-h[0-9][0-9]` and the split is never `heldout`; the worst case (`runs × RUN_BUDGET_USD`) is printed, and above $25 the run stops unless `accept_over_25` is true. `smoke` is the three easy dev tasks `tc-001`, `tc-003`, `sr-001` on `multi`/`flash`, worst case $3; `eval` is `make eval`'s five cases, worst case $5.
- **Concurrency:** group `paid`, `cancel-in-progress: false` (one paid run at a time; a second waits).
- **Permissions:** `contents: read`, and `id-token: write` on the run job only.
- **Run job** (90 min): WIF login as `ci-runner` (`google-github-actions/auth`), `uv sync --locked`, install `google-agents-cli==1.7.0` (the eval needs it), `make sandbox-image`, then:
  - `smoke`: `bench.run --tasks tc-001,tc-003,sr-001 --system multi --preset flash --out results/ci`, and the job fails when fewer than 2 of 3 are resolved;
  - `eval`: `make eval`;
  - `bench`: `bench.run` with the inputs, `--quiet` off.
  The results directory and `runs/*/record.json` are uploaded as a workflow artifact (14-day retention); nothing is committed.
- Environment: `ENVIRONMENT_BACKEND=docker` (the sandboxes run on the runner), `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION=global` from repository variables, tracing off.

### 3.3 `release.yaml` (tag)

- **Trigger:** `push` of tags `v*`.
- **Permissions:** `contents: read` at the top; the deploy job adds `id-token: write`; the release step `contents: write`.
- **Deploy job** (45 min), `environment: production`: the job waits for the owner's approval. Then WIF login as `deployer`, `uv sync --locked`, install `google-agents-cli==1.7.0`, `uv run python scripts/stage_deploy.py deploy`, then `scripts/stage_deploy.py smoke` (passes on `patch written`).
- **Release job** (5 min), after a successful deploy: `gh release create <tag> --generate-notes`.
- `scripts/stage_deploy.py` reads the three Terraform outputs it needs (`agent_runtime_resource_name`, `sandbox_caller_email`, `sandbox_image_repository`) from the environment variable `ITP_TF_OUTPUTS` (a JSON object) when it is set, and from `terraform output -json` otherwise. The release job sets it from repository variables. The deploy's other rules are unchanged (staging tree without `.env` or held-out tasks, `--update-only`).

### 3.4 Unchanged and removed

- `pages.yaml` is unchanged.
- `pr_checks.yaml`, `staging.yaml` and `deploy-to-prod.yaml` are deleted. Their tests, if any, go with them.

## 4. Cloud identity

Added to `deployment/terraform/single-project/` in a new file `cicd.tf`, applied with `make infra-plan` and `make infra-apply` (project pin and state backups as in Week 3A, owner approval).

- `google_iam_workload_identity_pool` `github-actions`.
- `google_iam_workload_identity_pool_provider` `github-oidc`: issuer `https://token.actions.githubusercontent.com`; attribute mapping `google.subject = assertion.sub`, `attribute.repository_id`, `attribute.ref`, `attribute.environment`, `attribute.event_name`; attribute condition `assertion.repository_id == "1401169707"` (a variable `github_repository_id`), so a renamed or recreated repository cannot use it.
- `google_service_account` `ci-runner`: project roles `roles/aiplatform.user` and `roles/serviceusage.serviceUsageConsumer`. `roles/iam.workloadIdentityUser` is granted to the principal set with `attribute.ref/refs/heads/main` of the pool; the workflow additionally checks `event_name == workflow_dispatch`.
- `google_service_account` `deployer`: project roles `roles/aiplatform.user` and `roles/serviceusage.serviceUsageConsumer`; `roles/iam.serviceAccountUser` on the `issue-to-pr-app` service account only. `roles/iam.workloadIdentityUser` is granted to the principal set with `attribute.environment/production`.
- Outputs: `wif_provider` (full resource name), `ci_runner_email`, `deployer_email`.
- If the first release fails on a missing permission, the missing role is added in Terraform, re-planned and re-applied with the owner's approval, never granted by hand (as with the budget guard's `roles/browser`).
- **On GitHub** (owner-approved `gh` calls): repository variables `GCP_PROJECT_ID`, `WIF_PROVIDER`, `CI_RUNNER_SA`, `DEPLOYER_SA`, `ITP_TF_OUTPUTS`; environment `production` with the owner as required reviewer and a deployment rule allowing tags `v*` only.

## 5. Safety

- **Held-out tasks:** `paid.yaml` refuses them before any credential is obtained; `bench.run` refuses them on its own as well unless `--confirm-heldout`, which no workflow passes. Test runs use `--tb=no -rf`.
- **Credentials:** short-lived WIF tokens only; fork pull requests receive no secrets and cannot dispatch workflows; `id-token: write` exists only in the paid run job and the deploy job.
- **Spend:** per-run caps ($1.00, 100 tool calls, wall clock), the dispatch cost guard, one paid run at a time, and the project's $500 hard stop.
- **Supply chain:** actions pinned to full SHAs with the tag in a comment (re-pin rule as in AGENTS.md), `uv sync --locked`, `agents-cli` pinned to 1.7.0, read-only defaults.
- **Public logs:** progress lines show tool calls on dev-task code that is already public; no `.env`, token or held-out content reaches a log. The project id and number appear in repository variables and logs; the owner accepted them as public (parent §19, 2026-10-02).

## 6. Tests

pytest, static, test-first (`tests/unit/test_ci_workflows.py`), in the style of `tests/unit/test_pages_workflow.py`:

- every `uses:` in every workflow is pinned to a 40-hex SHA with a tag comment;
- top-level permissions are `contents: read`; `id-token: write` appears only in `paid.yaml`'s run job and `release.yaml`'s deploy job;
- every job has `timeout-minutes`;
- `ci.yaml`: triggers, `paths-ignore`, concurrency, the three jobs and their commands, `--tb=no -rf`, no secret references;
- `paid.yaml`: dispatch only, the guard job runs before the run job (`needs`), the held-out pattern and the $25 rule are present, `concurrency` without cancel, smoke tasks and gate;
- `release.yaml`: tag trigger, `environment: production`, deploy before release;
- the three scaffold workflows are gone.

The guard logic (held-out refusal, worst-case cost, the $25 rule) lives in a small script, `scripts/ci_guard.py` (standard library only), with unit tests for each refusal; the workflow calls it.

`scripts/stage_deploy.py`: unit tests that `ITP_TF_OUTPUTS` replaces `terraform output` when set, that a missing key is the same error as today, and that malformed JSON is refused.

Terraform: static tests in `tests/unit/test_terraform_single_project.py` for the pool, the provider's attribute condition on the repository id, the two accounts' exact roles, and the two principal-set bindings.

## 7. Changes to existing files

- **New:** `.github/workflows/ci.yaml`, `paid.yaml`, `release.yaml`; `deployment/terraform/single-project/cicd.tf`; `scripts/ci_guard.py`; tests above.
- **Deleted:** `.github/workflows/pr_checks.yaml`, `staging.yaml`, `deploy-to-prod.yaml`.
- **Modified:** `scripts/stage_deploy.py` (`ITP_TF_OUTPUTS`); `deployment/terraform/single-project/variables.tf` (`github_repository_id`) and outputs; `AGENTS.md`; `README.md` (CI badge).
- **Unchanged:** prompts, graphs, caps, models, scoring, `bench.run`'s behaviour, `pages.yaml`.

## 8. AGENTS.md changes

- Runtime wiring: the three workflows and what each runs; WIF and the two accounts.
- Commands: how to dispatch a paid run (`gh workflow run paid.yaml -f kind=smoke`) and how to release (`git tag v… && git push origin v…`, then approve in GitHub).
- Hard rule 8: a `v*` tag push and a paid dispatch are owner actions; agents never push tags or dispatch paid workflows without the owner's approval in the session.
- Environment table: `ITP_TF_OUTPUTS`.

## 9. Verification (in order; each paid or cloud step needs the owner's approval)

1. Free: the tests in §6 pass; push; `ci.yaml` is green on `main`.
2. `make infra-plan` shows only the pool, the provider, the two accounts and their grants; `make infra-apply`; set the repository variables and the `production` environment.
3. Dispatch `paid.yaml` with `kind=smoke` (about $1; gate ≥ 2/3).
4. Push tag `v0.3.0`; the owner approves; the deploy and the deployed smoke run pass (about $0.30). The deployed agent now runs with the 100-call cap.
5. README badge; AGENTS.md; parent §19; push.

## 10. Spec amendments (parent §19, when the work lands)

1. §13: free checks (lint, unit, Docker tests, task validation) on every push and pull request; smoke benchmark, evals and full benchmarks by manual dispatch only; deploys on `v*` tags after the owner approves in a protected environment.
2. §12: CI uses Workload Identity Federation with two service accounts (`ci-runner`, `deployer`) in the single-project root; no staging/prod split.

## 11. Out of scope

- Branch protection and required checks (the owner pushes to `main` directly).
- Nightly or scheduled paid runs.
- Running CI jobs on Agent Runtime sandboxes (Docker on the runner is enough and free).
- A separate staging deployment.
- Dependabot and automatic dependency updates.

## 12. Risks

| Risk | Mitigation |
|---|---|
| `agents-cli deploy` needs a permission `deployer` lacks | The first release shows it; the role is added in Terraform with the owner's approval (§4). |
| The Docker job is slow on free runners (image build plus about 70 tests) | 40-minute timeout; Docker layer caching only if it proves needed. |
| A paid dispatch with a wide `bench` spends more than intended | Worst-case print, the $25 rule, one paid run at a time, the $500 hard stop. |
| The public logs show something private | No step reads `.env` or a secret except WIF; held-out tasks never run; `--tb=no -rf`. |
| A tag pushed by mistake | Nothing deploys without the owner's approval click. |

## 13. Build order

1. **[free]** `scripts/ci_guard.py` and its tests.
2. **[free]** `scripts/stage_deploy.py` `ITP_TF_OUTPUTS` and its tests.
3. **[free]** `cicd.tf`, variables and outputs, with the Terraform tests; `make tf-fmt-check`, `terraform validate`.
4. **[free]** The three workflows, the deletions and `tests/unit/test_ci_workflows.py`; action SHAs from read-only `git ls-remote`.
5. **[free]** AGENTS.md and README.
6. **[OWNER APPROVAL]** Push; `ci.yaml` green.
7. **[OWNER APPROVAL]** `infra-plan`, `infra-apply`, repository variables, `production` environment.
8. **[OWNER APPROVAL: about $1]** Smoke dispatch.
9. **[OWNER APPROVAL: about $0.30]** Tag `v0.3.0`, approve, deploy, smoke.
10. Parent §19; push.
