# Week 2C design: GitHub live mode and quality evals

Date: 2026-10-01. Status: Decisions answered by the owner on 2026-10-01 (all recommended options); full design and plan awaiting owner review.
Parent spec: `2026-09-29-sdlc-agent-pipeline-design.md` (§5.1 graph, §6.4 failures, §7 security, §9.2 quality evals, §11 web UI). Where this document is more specific, it wins for Week 2C once approved; the parent spec's §19 then records the amendments listed in §10.
Plan: `docs/superpowers/plans/2026-10-01-week2c-live-mode.md`.

## Decisions for the owner

The owner answered all ten decisions on 2026-10-01 with the recommended options: 1A, 2A, 3A, 4A, 5A, 6A, 7A, 8B, 9A, 10A. Each decision below records the choice; the other options stay as the record of what was considered and why it was rejected. The rest of this document and the plan are written for the chosen options. Steps that spend credits, create a GitHub organisation, repository or token, or push still need the owner's approval at the time (plan, **[OWNER APPROVAL]**).

1. **Where do the public demo repositories live?**
   - A. A new free GitHub organisation used only for this (for example `issue-to-pr-demo`), with repositories `taskcli`, `mdlite` and `stockroom`.
   - B. The owner's personal account (`omarcevi/taskcli-demo`, `omarcevi/mdlite-demo`, `omarcevi/stockroom-demo`).
   - C. One repository holding all three projects in subdirectories.

   Recommendation: A. The token's resource owner then holds nothing but the three demo repositories, the personal profile stays clean, and the names read as a demo. B works too and is one step shorter. C does not fit the pipeline, which treats the repository root as the project root and runs one test suite.

   **Decision: A (owner, 2026-10-01).**

2. **Which GitHub credential does the pipeline use?**
   - A. A fine-grained personal access token. Resource owner: the demo organisation (or account). Repository access: only the three demo repositories. Repository permissions: Contents read and write, Issues read and write, Pull requests read and write, Metadata read; every other permission "No access" (Workflows and Administration included). Expiry: 30 days. Stored in a file outside the repository with mode 600.
   - B. A private GitHub App installed on the three repositories: pull requests show a bot identity and installation tokens last one hour, but it adds a private key to guard and JWT signing code.
   - C. A classic personal access token. Not recommended: its `repo` scope covers every repository the owner can reach.

   Recommendation: A, as the parent spec says (§7.6), with one change: the token lives in a file named by `GITHUB_TOKEN_FILE`, not in `.env`. A file read only when a GitHub client is built keeps the token out of the process environment, which every child process inherits (the docker CLI, the agents-cli server, git). The pipeline never uses the `gh` CLI's token, `GITHUB_TOKEN` or `GH_TOKEN`.

   **Decision: A (owner, 2026-10-01).** The token's resource owner is the demo organisation of decision 1A.

3. **How does a person approve the pull request?**
   - A. In the graph: a `human_gate` node yields ADK's `RequestInput`, the run pauses, and the driver resumes it with the person's decision. In Week 2C the owner answers at the terminal; in Week 3 the web UI answers the same request.
   - B. Outside the graph: the run ends at the patch, and a separate `approve <run-id>` command opens the pull request.
   - C. On GitHub: the run posts the patch on the issue and waits for a label or a comment from the owner.

   Recommendation: A. It is what the parent spec drew (§5.1, §11), the installed ADK 2.8.0 supports it (§4.1), the approval is part of the run's events and trace, and Week 3 needs nothing new. B leaves the approval out of the graph and the trace. C publishes the patch before anyone has approved it.

   **Decision: A (owner, 2026-10-01).**

4. **Who can start a live run, and on which issues?**
   - A. Owner only. Runs are started by the owner from the CLI on the owner's machine. The issue must be written by a login listed in `LIVE_ALLOWED_USERS` and carry the `agent-ok` label, added by such a login.
   - B. The owner starts runs, but on issues written by anyone once the owner has labelled them. This needs an extra check that the issue body was not edited after the label was added (GitHub's REST issue object does not say when the body was last edited; the GraphQL `lastEditedAt` field does).
   - C. A run starts automatically when the label is applied (the parent spec's stretch trigger).

   Recommendation: A for Week 2C. Issue text from strangers is the main prompt-injection path, and A removes it while the other defences are new. B can follow after a red-team pass. C stays a stretch goal.

   **Decision: A (owner, 2026-10-01).**

5. **How does the pipeline identify itself in the pull requests and comments it writes?**
   - A. Openly. Pull request titles start with `[issue-to-pr]`. Pull request bodies and issue comments end with "Opened by the issue-to-pr pipeline (run `<run-id>`, model `<model>`), approved by @<login>". Commits carry the author `issue-to-pr pipeline <issue-to-pr@example.invalid>`.
   - B. As the owner: the owner's name on commits and no pipeline marker.

   Recommendation: A. AGENTS.md hard rule 1 forbids crediting AI tools for work in this repository. The demo pull requests are the product's own output, and saying which system opened them is the honest description. The owner should confirm this reading of rule 1 before Task 3 is built.

   **Decision: A (owner, 2026-10-01).** Plan Task 3 adds one clarifying sentence to hard rule 1 (the pipeline's own demo pull requests, comments and commits name the pipeline openly; that is the product's identity, not AI attribution of work in this repository). The owner reviews its exact wording before it is committed.

6. **Which bench tasks get public `demo/<task-id>` branches?**
   - A. All 15 dev tasks. An issue is created only when a task is about to be shown.
   - B. A curated four (for example `md-001`, `sr-001`, `sr-003`, `md-005`).

   Under both options, held-out tasks are never published, and neither are `solution/`, `shortcut/` or `hidden_tests/` of any task.
   Recommendation: A. The branches cost nothing, any dev task can be shown, and the issue list stays short.

   **Decision: A (owner, 2026-10-01).**

7. **When do the issue-text delimiters (parent spec §7.7, layer 2) go in?**
   - A. Now, in Task 2, for bench and live mode alike. Every agent sees the issue only between `<issue>` and `</issue>`, with those tags escaped inside the text, and the planner's user message no longer carries the raw issue as JSON. Benchmark numbers after this change belong to a new prompt version and are not pooled with the Week 2A and 2B numbers.
   - B. Later, together with any prompt tuning, before the final benchmark runs. Until then live mode relies on owner-only issues (decision 4A).

   Recommendation: A. The change is small, the parent spec promised it, both systems get it equally, and no benchmark run is planned between now and the final runs that would need the old prompts.

   **Decision: A (owner, 2026-10-01).**

8. **How many reviewer probes?**
   - A. The two tempting shortcuts only (`md-002`, `sr-002`), as the parent spec wrote: a catch rate over two patches.
   - B. About six bad and six good patches on dev tasks. Bad: the two shortcuts, single-agent patches from the Week 2B comparison that pass the visible tests and fail the hidden ones, and hand-written bad patches if fewer than six exist. Good: single-agent patches from the same comparison that resolved. Every probe is checked mechanically against the visible and hidden tests.
   - C. B plus bad patches written by a model on request, to get more probes cheaply.

   Recommendation: B. Two patches cannot support a rate; without good patches as controls, a reviewer that rejects everything would score 100%; and real agent mistakes are the most realistic bad patches. C would measure the reviewer against a model's idea of a bad patch.

   **Decision: B (owner, 2026-10-01).**

9. **Which eval metrics, and what budget?**
   - A. `agents-cli eval` on 5 dev tasks with `plan_files_recall`, `plan_quality`, `pr_description_quality` and `tool_call_count`, plus the built-in `multi_turn_trajectory_quality` marked exploratory; reviewer probes with 3 repeats; the review audit of the 2B runs; one round each (a second only if a harness bug voids the first); no prompt tuning in Week 2C. Cap for all of Week 2C, live runs included: $30.
   - B. Leaner: no built-in managed metric and no PR description judge; probes run once; cap $15.
   - C. A plus a prompt-tuning loop on the dev cases until the metrics stop improving; cap $60, and the dev-split comparison must be rerun afterwards.

   Recommendation: A. Week 2C builds the instruments; prompt tuning is a separate step whose effect the benchmark measures.

   **Decision: A (owner, 2026-10-01).** The Week 2C cap is $30.

10. **May the driver post a failure comment when a live run ends on a budget cap, an infra error or a crash?** In those cases the graph's `report_failure` node never runs, because the run is aborted by an exception.
    - A. Yes, through the same function `report_failure` uses (`post_failure_comment`), so the token is still read only in the code of the three nodes. AGENTS.md hard rule 2 gains one clause saying so.
    - B. No. Only the graph's `report_failure` comments; an issue whose run hit a cap or crashed gets no reply.

    Recommendation: A. A public issue left silent after a run looks broken. The comment says what happened and nothing more.

    **Decision: A (owner, 2026-10-01).**

## 1. Why

- **Live mode is a stated success criterion.** The parent spec's criterion 4 is a real pull request on a demo repository after human approval. Today every run ends at `runs/<run-id>/patch.diff`.
- **The reviewer has not been measured.** Across the Week 2A runs the reviewer approved 21 of 21 patches, and every one was correct. The Week 2B pilot showed that the two tempting tasks did not tempt the single agent: it fixed `md-002` and `sr-002` at the root. Natural runs may therefore never show the reviewer a patch that passes the visible tests and is wrong. To learn whether it catches one, it has to be shown such patches on purpose, next to good ones.
- **The eval harness has nothing real to grade.** `tests/eval/datasets/basic-dataset.json` still holds the scaffold's greeting, weather and capital-city cases, which are not valid `RunRequest` input for the workflow.

## 2. Success criteria

1. A live run on a demo issue opens a real pull request only after a person approves the exact patch at the terminal. A rejected or timed-out approval opens nothing and comments on the issue once.
2. A rerun or a retried step never opens a second pull request or posts a second comment for the same run, and a rerun on an issue that already has an open pipeline pull request is refused before any model call.
3. A canary test passes: a sentinel token placed in the token file is sent in GitHub request headers and appears nowhere else: not in session state, `events.jsonl`, `record.json`, any model request, any sandbox command, file or environment variable, any span, any log record, or `os.environ`.
4. Every `demo/<task-id>` branch's tree is byte-identical to `bench/repos/<repo>` plus the task's `plant/` (checked by git tree hash), and a live run's patch can be scored with the same hidden tests as a bench run.
5. `agents-cli eval run` produces plan and pull request description metrics for 5 dev tasks; the reviewer probes give a catch rate and a false-alarm rate with their sample sizes; the review audit gives a confusion matrix for the reviewer's verdicts in the Week 2B comparison runs.
6. No held-out task is published, put in an eval dataset or a probe, audited, or opened.
7. Bench mode is unchanged: same graph edges, same outcomes, existing tests pass. The only prompt change is the issue-text delimiters (decision 7A).
8. Week 2C spends at most $30 (decision 9A).

## 3. Live mode

### 3.1 A live run, end to end

```
owner, at the terminal
  uv run python -m app.live --repo <org>/mdlite --issue 3 --base demo/md-001 --approver <login>
    run_pipeline(RunRequest(mode="live", ...), approver=TerminalApprover(<login>))

START → fetch_issue[live] → provision_sandbox → planner → route_plan
          ├─ declined   → report_failure ──────────────────────────── issue comment
          └─ actionable → coder → collect_diff → run_tests → reviewer → route_review
                          (test-fix and review loops exactly as in bench mode)
                          └─ approve → deliver_patch → human_gate  [run pauses]
                                 driver: release the sandbox, show the approver the
                                 real diff and test report, wait, resume the run
                              → route_approval
                                   ├─ approved → open_pr ──────────── pull request
                                   └─ rejected → report_failure ───── issue comment
```

- The live graph is `build_workflow(models, live=True)`. The bench graph (`live=False`, the default) keeps its current edges exactly, with `deliver_patch` as its end.
- The live graph is built only by the `app.live` CLI. `app/agent.py`, and so `agents-cli run`, `playground` and `eval`, serve the bench graph only. The bench graph's `fetch_issue` refuses a request with `mode="live"` without touching the token, so an agents-cli or API caller cannot make it read GitHub.
- The single-agent baseline has no live mode. It is a measuring device.

### 3.2 Run request

```python
class RunRequest(BaseModel):
    run_id: str
    mode: Literal["bench", "live"] = "bench"
    task_id: str | None = None        # bench: required
    repo: str | None = None           # live: required, "owner/name"
    issue_number: int | None = None   # live: required
    base_ref: str | None = None       # live: branch; default = the repository's default branch
```

Existing bench requests (`{"task_id": ..., "run_id": ...}`) stay valid. `IssueTask` gains optional `issue_number`, `base_ref`, `base_sha`, `base_tree_sha` and `html_url`, and its `mode` accepts `live`. For a live run its `task_id` is `"<owner>/<name>#<number>"`.

### 3.3 GitHub client and the token

**Where the token is read.** `app/github_client.py` holds the only code that reads the token: `load_token()` reads the file named by `GITHUB_TOKEN_FILE` (default `~/.config/issue-to-pr/github-token`) and refuses a file that is missing, empty, or readable by group or others. `GitHubClient.from_token_file()` is called only inside `fetch_live_issue` (the live graph's `fetch_issue` node), `open_pr` and `post_failure_comment` (used by `report_failure`, and by the driver for live runs that end on a cap, an infra error or a crash: decision 10A). A static test pins these call sites.

**How it stays out of everything else.**

| Place | How |
|---|---|
| Prompts and model requests | No node passes client objects, headers or the token into state or instructions. The canary test inspects every model request. |
| Session state, events, `record.json` | The client is built inside the node and closed there; it is never stored in state. Node outputs carry issue data and URLs only. |
| Traces | Spans carry prompts and tool content (AGENTS.md); neither ever holds the token. The token goes in the `Authorization` header only, never in a URL, and no HTTP client instrumentation is enabled. The canary test inspects an in-memory span exporter. |
| Logs and errors | `GitHubError` text is method, path, status and GitHub's message, never headers or full URLs with query strings. A logging filter on the client's logger replaces the token value if it ever appears. |
| Sandbox | The token is never in `os.environ`, so it cannot reach a container even by accident. The existing Docker test that the sandbox has no host credentials gains the new variable name. |
| Redirects | The tarball endpoint redirects to `codeload.github.com`. httpx drops the `Authorization` header when a redirect leaves the origin; a test pins it. |

**Rate limits and retries.** A personal access token gets 5,000 REST requests per hour, and GitHub adds secondary limits on writes; a live run makes fewer than 30 requests. Reads, and writes that are safe to repeat (below), are attempted up to 3 times on HTTP 5xx and transport errors, with exponential backoff and jitter. On 429, or 403 with rate-limit headers, the client waits `retry-after` (at most 60 s) or until `x-ratelimit-reset` if that is at most 60 s away, else raises. Exhausted retries raise `GitHubUnavailable`, a subclass of `InfraError`, so node `RetryConfig` and the driver classify it as infra. Other 4xx responses raise `GitHubError` (`GitHubConfigError` for a missing or bad token, 401, 403 without rate-limit headers, or 404 on the repository) and are not retried.

**Idempotency: a rerun never opens two pull requests.**

1. Before anything costs money, `fetch_issue` refuses the run if the issue already has an open pipeline pull request (head branch starting `issue-to-pr/<issue-number>-`). The owner closes it to run again.
2. The app.live CLI takes a per-issue lock file (`runs/.locks/<owner>__<name>__<issue>.lock`, created exclusively, stale when its process is gone), so two concurrent runs on one issue cannot both pass check 1.
3. Every write is check-then-create, so a retry after a lost response finds the first attempt's result:
   - blobs, trees and commits are content-addressed and safe to repeat;
   - the branch is `issue-to-pr/<issue-number>-<run-id>`; an existing ref at the same commit is success, at a different commit an error (never a force update);
   - before creating a pull request the client lists open pull requests for that head; a 422 "already exists" is answered by listing again;
   - comments carry a hidden marker `<!-- issue-to-pr run=<run-id> kind=<kind> -->`, and the client posts only if no comment with that marker exists.
4. The client refuses to create or move any ref outside `refs/heads/issue-to-pr/`, so the pipeline can never touch `main` or `demo/*`.

### 3.4 `fetch_issue` in live mode

In this order; a failed check raises `RunRefused` with a fixed reason, before any model call and without writing to GitHub. The driver records outcome `refused` (new), failure kind `none`, cost 0. GitHub being unavailable is not a refusal: it is an infra failure, retried as usual.

1. The repository is in `LIVE_REPOS` (comma-separated `owner/name`; empty means live mode is off).
2. The token loads.
3. The issue exists and is open.
4. Its author is in `LIVE_ALLOWED_USERS` (decision 4A).
5. It carries `agent-ok`, and the most recent `labeled` event for that label has an actor in `LIVE_ALLOWED_USERS`.
6. No open pipeline pull request exists for it (§3.3).
7. `base_ref` (given, or the default branch) resolves to a commit; its SHA and tree SHA are pinned in the issue record.
8. The source archive at that commit is downloaded, with a 50 MB cap, to `runs/<run-id>/source.tar.gz`; state key `source_archive` holds the path.

Issue comments are never read. Only the title and body enter the run, through `issue_text`.

### 3.5 `provision_sandbox` in live mode

It extracts `source.tar.gz` on the host with `tarfile`'s `data` filter (no absolute paths, no `..`, no device files), refuses links of any kind, more than 5,000 files or more than 50 MB, strips the archive's single top-level directory, and then does exactly what bench mode does: upload, git baseline, protected test paths. No repository code runs on the host.

### 3.6 `open_pr`

1. Checks that the SHA-256 of the full diff in state equals `patch_sha256` (written by `deliver_patch`) and the hash in the approval decision.
2. Refuses a diff that touches `.github/` or a protected test file. Both are already impossible after `run_tests`; this is a second line.
3. Extracts the pinned source archive into a temporary directory and applies the patch with host `git apply --check`, then `git apply`. Without `--unsafe-paths`, git refuses a patch that writes outside the directory. Git runs without credentials and runs no hooks.
4. Builds the commit through the Git Data API: a blob per added or changed file, a tree on top of the pinned base tree (deletions as null entries), and a commit whose parent is the pinned base SHA. Then it creates the branch and opens the pull request against `base_ref`.
5. Its outcome is `pr_opened` with the pull request URL.

There is no clone and no `git push`. The parent spec's §7.4 step 3 ("an orchestrator-side temporary clone, pushes a branch") would put the token in a remote URL or a credential helper, where git error messages and process listings can show it.

### 3.7 `report_failure` in live mode

Bench behaviour is unchanged. In live mode it also posts one comment on the issue (marker `kind=failure`) for: a decline by the planner, an agent failure (tests or review rounds exhausted), and a rejected or timed-out approval (outcome `rejected`, new, failure kind `none`). The driver (decision 10A) posts the same kind of comment through `post_failure_comment` for budget, infra and crashed live runs, once the issue has been fetched. A comment that cannot be posted, after the client's retries, is logged and recorded as `comment_posted: false`; it never changes the run's outcome, because the outcome is what happened to the issue, not to the comment. A refused run (§3.4) never comments: a stranger's unlabelled issue should not learn that the pipeline exists.

### 3.8 What gets published

Pull request titles, bodies, commit messages and comments are public. They are built by `app/pr_text.py` from structured data, with these rules:

- **Real data** (diff stat, test exit code, failed test ids, run id, cost, tool calls, models, approver) is rendered from fields the pipeline computed. File paths are rendered as code spans with backticks escaped.
- **Model-written text** (the plan summary, the coder's summary, the reviewer's comments) appears only inside fenced code blocks whose fence is longer than any run of backticks or tildes in the text. Inside a code block GitHub renders no mentions, issue references, closing keywords, links, images or HTML. Each block is capped (2,000 characters), the body at 20,000.
- The body refers to the issue once, from the template: "Proposed fix for #<n>". It never uses a closing keyword, so merging can never close another issue.
- The title is "[issue-to-pr] <issue title>" on one line, control characters removed, at most 120 characters. The issue title is the allowlisted author's own text (decision 4A).
- A hidden marker carries the run id and the kind of text (`pr` or `failure`); the footer gives the patch hash.

`deliver_patch` renders the same body into `runs/<run-id>/pr_body.md` in both modes, so the approver sees it before anything is published and the eval judge can grade it (§6.2).

## 4. The human approval step

### 4.1 Recommendation: `RequestInput` from a function node

The installed google-adk 2.8.0 supports a pause inside a graph workflow:

- `google/adk/events/request_input.py`: `RequestInput(interrupt_id, payload, message, response_schema)`.
- `google/adk/workflow/_function_node.py`: a function node may yield a `RequestInput` (`_PASSTHROUGH_OUTPUT_TYPES`). With `rerun_on_resume=False`, the default, "the node will be marked as completed and the resuming input will be treated as the node's output".
- `google/adk/workflow/_base_node.py` and `workflow/utils/_workflow_hitl_utils.py`: the request becomes a model-role event with a function call named `adk_request_input`, whose id is listed in `long_running_tool_ids`; the run then ends its current pass.
- `google/adk/runners.py`: a new message whose parts are function responses resumes the paused invocation (`_extract_resume_inputs`, `_resolve_invocation_id_from_fr`), and a message may not mix function responses with text.
- `google/adk/workflow/_workflow.py`: "Non-resumable sessions reconstruct the same state by replaying prior events", so an in-process resume on the same `InMemoryRunner` session needs no `ResumabilityConfig`.
- `google/adk/workflow/_llm_agent_wrapper.py`: LLM nodes run `single_turn` with `include_contents='none'` and inject no synthetic input on resume, so the pause leaves the agents' views untouched.

The design:

- `human_gate` (function node, after `deliver_patch`) yields a short status message, then `RequestInput(interrupt_id="approve-<run-id>", message=<summary>, payload=ApprovalRequest, response_schema=ApprovalDecision)`.
- `ApprovalDecision` is `approved: bool`, `approver: str`, `patch_sha256: str`, `note: str | None`.
- `route_approval` validates the decision and routes `approved` only if `approved` is true and the hash equals the patch in state. Everything else routes `rejected`, with the reason in state.

### 4.2 How the driver pauses and resumes

`run_pipeline(..., approver=None)` gains an `approver: Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]`.

1. The first pass runs under `RUN_TIMEOUT_S`, as today.
2. If that pass ends with an `adk_request_input` call pending, the driver releases the sandbox (nothing after the gate needs it), then awaits the approver under `APPROVAL_TIMEOUT_S` (new, default 3600 s). Expiry counts as a rejection ("approval timed out"). A live graph run without an approver ends at once as `failed / infra`, "approval needed but no approver", rather than waiting.
3. The driver resumes the same session with a user message holding only the function response (id `approve-<run-id>`, name `adk_request_input`, the decision as its response). The resumed pass has its own 300 s cap. Its events are appended to the same `events.jsonl`.
4. `duration_s` excludes the wait; the new `approval_wait_s` records it.

The in-memory session means the process must stay alive while it waits; that is fine for a terminal run in Week 2C. Waiting across process restarts, for the Week 3 web UI on Agent Runtime, needs a persistent session service and is Week 3's concern.

The first test of the gate task pins ADK's behaviour on a minimal workflow: after the resume, no node that completed before the gate runs again (model call count and sandbox starts unchanged). If that test cannot be made to pass, the task stops and reports, and the rejected option 3B is the fallback, which would need the owner's approval.

### 4.3 What the approver sees, and what the approval binds to

`TerminalApprover` prints, in this order: the repository, issue, base branch and planned branch; the real test result (exit code) and the reviewer's verdict; the diff stat and the full patch from `patch.diff` (the first 400 lines, then the file path); the planned pull request title and body, with model-written parts marked as such; the run's cost and tool calls so far. It then asks the approver to type `approve`; anything else rejects. It refuses to run when stdin is not a terminal. There is no flag that approves automatically.

The decision carries the hash of the patch the approver was shown, and `open_pr` checks it again (§3.6): approval is for that patch and nothing else.

### 4.4 Alternatives rejected

| Option | Why not |
|---|---|
| Tool confirmation (`FunctionTool(require_confirmation=...)`, `tool_context.request_confirmation`) | Confirms an LLM's tool call. `open_pr` is a deterministic function node, and making it a model tool would break hard rule 4. |
| `LongRunningFunctionTool` | Same: a model tool. |
| Out-of-band `approve <run-id>` command (rejected option 3B) | Works, but the approval is outside the graph and the trace, and the Week 3 UI would need a second mechanism. |
| Approval on GitHub (rejected option 3C) | The patch would be public before approval, and the run would have to poll GitHub. |
| Opening a draft pull request and treating "ready for review" as approval | A draft pull request is already published. |

## 5. Public demo repositories

### 5.1 Layout

Per repository (`taskcli`, `mdlite`, `stockroom`, in a new GitHub organisation used only for the demo: decision 1A):

- `main`: `bench/repos/<repo>` exactly. The repository description explains the demo: planted bugs live on `demo/*` branches, and pull requests are opened by the pipeline after a person approves them.
- `demo/<task-id>`, one per dev task of that repository (all 15 dev tasks across the three repositories: decision 6A): a single commit with no parent, whose tree is exactly `bench/repos/<repo>` plus the task's `plant/`, as `materialize(task)` builds it. With no parent, the public history does not show the bug being introduced.
- Nothing else in the tree: no extra README, no CI workflow. The agents must see the same files in a live run as in a bench run, or the two are not comparable and the live patch cannot be scored. (CI on the demo repositories would need `.github/` in every demo branch; out of scope.)
- Issues are created one at a time, for the runs actually shown, with the task's `title` and `body` verbatim, and the `agent-ok` label added by the owner.
- Recommended hardening, in the repository settings: a ruleset that blocks deletion, force pushes and updates on `main` and `demo/**`, and Actions disabled. The pipeline creates only `issue-to-pr/*` branches anyway (§3.3).

### 5.2 How they are created

`bench/demo.py` (no network, no credentials):

- `export --repo <repo> --out <dir>` builds a local git repository with `main` and every `demo/<task-id>` branch, using the owner's git identity, and prints each branch's tree SHA.
- `trees` prints `<task-id> <tree-sha>` for every dev task, computed by materialising the task and running `git write-tree` in a temporary repository.
- `verify --dir <dir>` compares the fetched `origin/demo/<task-id>` trees with the expected ones after the owner pushes.
- `issue --task <task-id> --repo <owner>/<name> --out <dir>` writes the issue title and body to files and prints the `gh issue create` command for the owner to run.

The tool refuses held-out tasks by construction: it lists dev tasks only and has no option to include others. Creating the organisation, the repositories and the token, pushing, and creating issues are done by the owner with the owner's own `gh` login, at steps marked **[OWNER APPROVAL]** in the plan. The pipeline token cannot create repositories.

### 5.3 Scoring a live run with the bench's hidden tests

- `fetch_issue` records `base_ref`, `base_sha` and `base_tree_sha` in `record.json`. `deliver_patch` writes `patch.diff` in live mode too.
- `uv run python -m bench.score_run --run-id <id> [--task <task-id>]` takes the task from `--task` or from a base ref of the form `demo/<task-id>`. It refuses held-out tasks with a fixed message, and refuses when the run's `base_tree_sha` differs from the task's expected tree ("the run's source is not this task's demo state; not scored"). Otherwise it scores the patch exactly as the matrix does (`bench.score.is_resolved`; a trap is resolved by a decline) and writes `results/live/<run-id>.json`, a matrix-shaped row plus `mode`, `pr_url` and the approval.
- If the fetched issue title or body differs from the task's, the row is marked `issue_text_differs`: it is scored, but not comparable with bench runs.

### 5.4 What is never published

Held-out tasks in any form; `hidden_tests/`, `solution/` and `shortcut/` of any task; run artifacts other than what the pull request and comments show; trace links (they would expose the GCP project id). This repository itself has no GitHub remote yet. It contains every task, held-out ones included, so publishing it is a separate decision for Week 3, to be taken only after the held-out run.

## 6. Quality evals

### 6.1 What is measured where

| Metric | Harness | Ground truth | Cost per round |
|---|---|---|---|
| Resolve rate | `bench.run` (unchanged) | hidden tests | per the benchmark |
| `plan_files_recall` | `agents-cli eval` | reference solution's module list (dev) | in the case runs |
| `plan_quality` | `agents-cli eval`, LLM judge | judge rubric plus reference module list | cents |
| `pr_description_quality` | `agents-cli eval`, LLM judge | judge rubric plus the issue | cents |
| `tool_call_count` | `agents-cli eval` | the trace | free |
| `multi_turn_trajectory_quality` | `agents-cli eval`, managed built-in | Vertex eval service | cents; exploratory |
| `review_catch_rate`, `review_false_alarm_rate` | `bench.review_probe` | probes checked against visible and hidden tests | $1–3 |
| Review confusion matrix on real runs | `bench.review_audit` | hidden tests, re-run on every reviewed diff | $0 (Docker only) |

**Why the reviewer probes run in the bench harness and not in `agents-cli eval`.** A probe needs a prepared patch in a fresh sandbox, a guaranteed sandbox release, the wall-clock cap, infra reruns and repeats, all of which `bench.matrix` and the driver already provide. agents-cli 1.7.0 always boots the agent named by `agent_directory` (`app`) for local inference (`eval/cmd_generate.py`), so the probe graph would need a switch in `app/agent.py`. The CLI also reuses an already running local server (`run/_local_server.py`), so such a switch could silently run the wrong graph. And the eval CLI has no per-case timeout (spike S2). The parent spec's `review_catch_rate` keeps its name and meaning; it moves harness. `agents-cli eval` keeps the judged qualities of real pipeline runs.

### 6.2 `agents-cli eval`: real runs of the pipeline

- **Dataset** `tests/eval/datasets/pipeline-dev.json`, built by `uv run python -m bench.evalsets --write` and checked by `--check` in a unit test. Five dev tasks (decision 9A): `md-001` (bug, distant symptom), `md-002` and `sr-002` (bugs, tempting), `sr-003` (feature, multi-file), `sr-005` (refactor, regression risk). No trap: a plan metric on a trap means nothing, and declining is scored by the benchmark.
- **Case shape.** `prompt` is the `RunRequest` JSON `{"task_id": "<id>", "run_id": "eval-<id>"}`. `reference` (which `eval generate` carries onto the trace) holds JSON with `task_id`, `category`, `issue_title`, `issue_body` and `solution_files`: the `.py` modules under the task's `solution/` that are not test files, as names only. The agents never see `reference`. No hidden test file, test name or solution content goes into a dataset.
- **Metrics**, one self-contained file each under `tests/eval/metrics/`. agents-cli inlines a `custom_function_file` and runs it with `exec` (`eval/eval_utils.py`), so a metric may import only the standard library and `google.genai`, and cannot import a sibling or use `__file__`.
  - `plan_files_recall`: the planner's answer is the first `set_model_response` call in the trace whose arguments have `actionable`. Score: the share of `solution_files` named in `files_to_inspect` after path normalisation. No plan in the trace scores 0, with that as the explanation.
  - `plan_quality`: a `gemini-3.8-flash` judge at temperature 0 with a fixed rubric (finds the cause or the modules to change; concrete, ordered steps; a test strategy that would catch the bug and its neighbours; no edits to existing tests; respects the repository's written rules). Score 1 to 5.
  - `pr_description_quality`: the same judge on the final response, which in bench mode is now the rendered pull request body (§3.8). It checks that the description matches the issue and the diff stat, reports the tests honestly and claims nothing the body's own data does not support. Score 1 to 5.
  - `tool_call_count`: function calls in the trace, `set_model_response` included.
  - `multi_turn_trajectory_quality`: the managed built-in, reported as exploratory because the trace has no agent map for a workflow root (spike S2).
- **Running it.** The release plugin (below) is added to `app/agent.py` first. `agents-cli run --stop-server` comes before every eval run, because a reused server keeps whatever it was started with. `--concurrency 2` is always passed: the default is the number of CPU cores (`eval/cmd_run.py`), and each case starts a Docker sandbox.
- **Sandbox release under agents-cli.** The driver releases sandboxes; the agents-cli entry point does not (AGENTS.md: only the TTL cleans up). A `SandboxReleasePlugin` in `app/agent.py` releases the sandbox in state from `after_run_callback` and `on_run_error_callback`. This closes an item deferred since Week 1.
- **Scaffold placeholders go.** `basic-dataset.json` (greeting, weather, capital) and `response_quality.py` are deleted. `eval_config.yaml` becomes the pipeline config. Bare `agents-cli eval run` then fails with a clear "specify --dataset" message, and AGENTS.md lists the full command.

### 6.3 Reviewer probes: `review_catch_rate` and `review_false_alarm_rate`

- **A probe** is `bench/review_probes/<probe-id>/`, with `probe.yaml` (`task_id`, `kind: bad | good`, `source: shortcut | bench-run | hand-written`, `source_run`, `note`) and `patch.diff`. Probe ids are opaque (`rp-01`, ...). The kind is in `probe.yaml` only.
- **The probe graph** (`app/review_probe.py`, workflow name `issue_to_pr` so progress output works unchanged) takes `ProbeRequest`, a `RunRequest` with `probe_id`:

  ```
  START → load_probe → provision_sandbox → planner → route_plan
            ├─ declined   → report_failure
            └─ actionable → apply_probe_patch → collect_diff → run_tests
                              ├─ pass → reviewer → record_verdict
                              └─ fail or exhausted → probe_invalid
  ```

  `load_probe` sets the same state as bench `fetch_issue` for the probe's task. `apply_probe_patch` uploads `patch.diff` and applies it with `git apply` inside the sandbox. `record_verdict` ends with the message `review verdict: <verdict>`. `probe_invalid` records `failed / infra` ("probe patch failed the visible tests"): the validator already proved they pass, so this is the environment, not the reviewer. The planner runs so that the reviewer's `{plan}` is a real plan, as in the pipeline. A run where the planner declines has no verdict; it is listed and left out of both rates.
- **No label leaks.** Agents see only their instructions (issue text, plan, diff, test report) and their node input (§4.1). Run ids, node messages and patch text never contain the kind; the validator refuses a patch whose added lines contain `shortcut`, `probe`, `bad patch` or `good patch`.
- **Validation** (`uv run python -m bench.probes validate`, one line per probe, fixed strings): the task is in the dev split; the patch applies to base plus plant; it touches no protected test file and nothing under `.github/`; the visible tests pass; a bad probe fails the hidden tests; a good probe passes them.
- **Authoring rule, fixed before any probe is run** (decision 8B): target 6 bad and 6 good, over at least 4 tasks and both new repositories. Bad: the two `shortcut/` overlays; then single-agent `patch_written` runs from the 2B comparison that pass the visible tests and fail the hidden ones, in run-id order, at most 2 per task, until there are 6; then hand-written patches until there are 6. Good: one resolved single-agent patch per task. Single-agent patches are used because no reviewer has seen them; multi-agent patches would bias the sample towards the reviewer's past verdicts. No probe changes after the reviewer's results are seen.
- **Run:** `uv run python -m bench.review_probe --repeats 3 --preset flash --concurrency 2 --report docs/results/<date>-2c-review-probes.md`. It reuses `bench.matrix` (a `variant` on `RunSpec` keeps run ids unique when two probes share a task). Rows add `probe_id`, `kind`, `verdict` and `must_fix`. Catch rate is the share of counted bad-probe runs where the reviewer requested changes; false-alarm rate is the same share on good probes; infra and crashed runs are shown and excluded, as in the benchmark report. The report states the number of probes as well as runs: at temperature 0, repeats show variation, not a bigger sample.

### 6.4 Review audit of real benchmark runs

`uv run python -m bench.review_audit results/<multi>.json [...] --out docs/results/<date>-review-audit.md` reads each multi-agent dev run's `events.jsonl` and pairs every reviewer verdict with the diff it reviewed (the `diff` state written by `collect_diff` before that review). It scores each distinct diff with the visible and hidden tests in fresh sandboxes, the same way the scorer does, and reports a confusion matrix: bad and caught, bad and approved (a miss), good and sent back (a false alarm), good and approved, per task. It costs no model credits. It shows how the reviewer does on the coder's real patches, where §6.3 shows it on chosen ones. A "bad and caught" counts the verdict, not whether the reviewer's reasons were right; the report says so.

### 6.5 Held-out tasks stay out

`bench.evalsets`, `bench.probes`, `bench.review_probe`, `bench.review_audit`, `bench.demo` and `bench.score_run` select dev tasks only, refuse a held-out id with a fixed message that names nothing from the task, and never open a held-out task's directory beyond the `task.yaml` that `list_tasks()` already loads. Unit tests pin each refusal with a synthetic held-out task.

### 6.6 What is a pytest test and what is an eval

| pytest (no model, no GCP, no GitHub) | Eval or benchmark (real model, owner approval) |
|---|---|
| GitHub client against `httpx.MockTransport`; retries, rate limits, idempotency, redaction | Live demo runs |
| Live intake, delivery, gate and resume with `FakeLlm`, `FakeEnvironment`, a fake GitHub client and a fake approver | `agents-cli eval run` on `pipeline-dev` |
| The token canary and the token boundary (static) | `bench.review_probe` |
| Public text rendering (`app/pr_text.py`) | `bench.run` (resolve rate) |
| Metric functions on synthetic traces, judges with a fake client | |
| Dataset builder, probe validator rules, demo tree hashes, live scoring and audit maths on synthetic data; committed probes validated in Docker | `bench.review_audit` runs the scorer in Docker (free, no model) |

pytest never asserts on real model output (AGENTS.md). A `tests/conftest.py` fixture points `GITHUB_TOKEN_FILE` at a path that does not exist, so no test can reach GitHub with a real token.

## 7. Security and threat model updates

**New assets:** a token that can write to three public repositories; the owner's public reputation (pull requests and comments under the owner's account); the demo repositories' integrity.

**New entry points:** issue title and body on a public repository; the repository archive; model-written text that becomes public; the approval prompt.

| Threat | Controls |
|---|---|
| Prompt injection in issue text makes an agent exfiltrate data | Unchanged and sufficient: no network and no credentials in the sandbox, tools only through `Environment`, the shell guardrail. There is nothing to steal and nowhere to send it. |
| Injection makes the agent write a backdoor into a demo repository | Owner-only issues (4A); delimiters (7A); the reviewer; the person at the gate sees the real diff and must type `approve`; nothing is merged automatically. |
| A patch adds a GitHub Actions workflow, which would run with the repository's token on a same-repository branch | Guardrail and `run_tests` restore `.github/` (today); `open_pr` and the client refuse `.github/` paths; the token has no Workflows permission, so GitHub itself rejects such a commit; Actions disabled on the demo repositories. |
| Model text abuses the public pull request or comment (mentions that ping strangers, references that spam other repositories, closing keywords, phishing links, images) | Model text only inside fenced code blocks; template-only issue reference with no closing keyword; length caps (§3.8). |
| Injection persuades the approver (a reassuring summary over a bad diff) | The approver sees the real diff and test exit code before any model text; model text is labelled; the approval binds to the patch hash. |
| Model claims steer routing | Unchanged: routing uses real exit codes and real diffs. The gate adds a human, not a model, decision. |
| Token leak through state, prompts, traces, logs, errors, URLs, environment or sandbox | Token in a mode-600 file, read only inside the three nodes' code; header only; redaction; the canary test; the static boundary test. |
| A malicious archive attacks the host on extraction | `tarfile` `data` filter, links refused, size and file-count caps; no repository code runs on the host; host `git apply` refuses paths outside the tree. |
| A stranger triggers runs and spends the budget | No webhook or server endpoint starts live runs in 2C: only the owner's CLI. The repository allowlist, owner-authored issues, the label actor check, the per-run caps and one run per issue at a time. |
| An API caller asks the agents-cli server for a live run | The served bench graph refuses `mode="live"` before reading the token (§3.1). |
| The pipeline damages the demo branches | It can only create `issue-to-pr/*` refs, never force; rulesets protect `main` and `demo/**`; `bench.score_run` checks the tree before scoring. |
| The issue body is edited after the owner labelled it | Not possible with owner-only issues (4A: the author is the owner). The rejected option 4B, if adopted later, must add the `lastEditedAt` check. |

## 8. Cost

| Item | Estimate |
|---|---|
| Live demo runs: four (approve `md-001`, approve `sr-003`, the trap `md-005` declines, reject `sr-001` at the gate), multi-agent, Flash, at most $1.00 each by the cap | $1–4 |
| `agents-cli eval` on `pipeline-dev`, one round (5 runs, judges, the managed metric) | $2–5 |
| Reviewer probes, one round (about 12 probes × 3 repeats; planner and reviewer only) | $1–3 |
| Review audit | $0 (Docker only) |
| GitHub organisation, public repositories, token | $0 |
| **Total, one round each** | **about $4–12** |

The $30 cap (decision 9A) covers a possible second round. The controller stops and reports when the running total passes it. Every paid step is marked **[OWNER APPROVAL]** in the plan. The per-run caps ($1.00, 75 tool calls, 1,500 s), the sandbox TTL and the loop bounds are unchanged. Probes and evals use the `flash` preset only; the Pro reviewer hang is still undiagnosed.

## 9. Changes to existing behaviour

- The bench graph's edges are unchanged. `deliver_patch` now also writes `pr_body.md`, stores `patch_sha256` in state, and adds the pull request body after the first line of its message. `bench.progress` already prints only a message's first line.
- `app/agent.py` gains `SandboxReleasePlugin`, after `GuardrailPlugin`. The driver's plugin list is unchanged; it releases sandboxes itself.
- `RunRequest`, `IssueTask` and `RunRecord` gain optional fields whose defaults keep existing JSON valid. `Outcome` gains `pr_opened`, `rejected` and `refused`; bench rows never carry them.
- The only prompt change is decision 7A's: all four agents (planner, coder, reviewer, solo) see the issue between delimiters, and the planner's and solo agent's first user message becomes a fixed sentence instead of the issue as JSON (which also removes the run id from it, a Week 2A ledger item).
- Week 2A and 2B results and reports are untouched.

## 10. Spec amendments this design asks for

Recorded in the parent spec's §19 when the work lands:

1. §5.1: the live graph adds `deliver_patch → human_gate → route_approval → {open_pr, report_failure}`; the bench graph is unchanged; the live graph is built only by `app.live`.
2. §6.1 and §6.4: outcomes `pr_opened`, `rejected` (a person said no, or the approval timed out) and `refused` (live preconditions not met; no model call, no GitHub write).
3. §7.4 step 3: `open_pr` builds the commit through the Git Data API from the pinned base; there is no clone and no push.
4. §7.6: the token is read from a mode-600 file named by `GITHUB_TOKEN_FILE`, never from the environment; the permissions are listed in decision 2A.
5. §7.7: layer 1 is owner-authored issues plus the label actor check (decision 4A); layer 2, the delimiters, is implemented now (decision 7A).
6. §9.1: public demo branches are single commits whose tree equals base plus plant, checked by tree hash; live runs on them are scored by `bench.score_run`.
7. §9.2: the eval cases, the metric set, the reviewer probes in the bench harness with good controls and a false-alarm rate, and the review audit (§6).
8. AGENTS.md hard rule 1 gains one clarifying sentence (decision 5A): the pipeline's own demo pull requests, comments and commits name the pipeline openly; that is the product's identity, not AI attribution of work in this repository. The owner reviews its exact wording. Hard rule 2 gains the `post_failure_comment` clause (decision 10A). Hard rule 5 gains: held-out tasks are never published, put in an eval dataset or probe, or audited.

## 11. Out of scope

Week 3: the Agent Runtime sandbox backend, deployment to Agent Runtime, Secret Manager for the token, persistent sessions for approvals across restarts, the web UI (replay and live approval), CI for this repository, publishing this repository. Not planned: the label trigger and Pub/Sub (stretch), a GitHub App, automatic merging, CI on the demo repositories, live mode for the single-agent baseline, issues from non-allowlisted authors (the rejected option 4B), prompt tuning (decision 9A), the held-out run, the Pro reviewer hang.

## 12. Risks

| Risk | Mitigation |
|---|---|
| ADK's resume does not behave as the source reads (completed nodes run again, or the decision is not delivered) | The gate task's first test pins the behaviour on a minimal workflow before anything is built on it; the fallback is the rejected option 3B, with the owner's approval. |
| A GitHub API detail differs from what the client expects (fine-grained token permissions for the Git Data API, redirect handling, secondary limits) | The client is tested against recorded response shapes; the first live run is a cheap one; an error is infra or config, never a silent success. |
| A demo branch drifts from the bench task | Rulesets, `bench.demo verify` after every push, and the tree check in `bench.score_run`. |
| Public demo repositories attract issues from strangers | Ignored by design (4A); the repository description says runs are started by the owner. |
| The judges share a model family with the agents | Reported as a caveat; the deterministic metrics (`plan_files_recall`, probes, audit) do not depend on a judge. |
| Small samples (5 eval cases, about 12 probes) | Reports give counts with every rate, and the number of distinct probes. |
| Held-out content leaks through the new tooling | Dev-only selection, fixed refusal messages and a unit test per tool. |
| The approver rubber-stamps | The full diff comes first, the exact word `approve` is required, and the decision is bound to the patch hash. |
| The token expires or is revoked mid-demo | `fetch_issue` runs first, so the run is refused before any model call. |

## 13. Open verification items

Things the installed packages could not settle. Each is pinned by a test or a first cheap run in the plan.

1. That an in-process resume after `RequestInput` on `InMemoryRunner` skips the nodes completed before the gate. The source says replay; Task 4's first test proves it on a minimal workflow.
2. Whether ADK validates the resume response against `response_schema`. The design does not rely on it: `route_approval` validates.
3. That a fine-grained token without the Workflows permission is refused when a ref would point at a commit that changes `.github/workflows/`. This is from GitHub's documentation, not tested; the design does not rely on it, because `open_pr` and the client refuse `.github/` first.
4. How well `multi_turn_trajectory_quality` grades a workflow whose root is not an `LlmAgent`. Spike S2 warns that grading degrades; the metric is marked exploratory.
5. Whether GitHub's secondary write limits matter at this volume. They should not (fewer than 30 requests per run); the client handles 429 and 403 with `retry-after` either way.
