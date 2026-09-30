# Week 1 execution ledger

Archived copy of the controller's working ledger for `docs/superpowers/plans/2026-09-29-week1-core-loop.md`: every task's completion, every review finding that was deferred, and every ruling made on the owner's behalf, in the order they happened. Items marked deferred or parked feed the Week 2 plan.


Branch: week1-core-loop (in place, from main @ 785a6f9). Spec: docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md
Execution: Tasks 1–4 inline by controller (approval-gated / interactive); Tasks 5–14 via subagents.

## Setup rulings
- Ruling: work on feature branch `week1-core-loop` in the main checkout instead of a separate worktree — user said "continue with subagent"; a branch keeps main untouched without moving .env/uv state — cost if wrong: owner wanted a worktree; trivially convertible.
- Ruling: committed removal of vim swap file accidentally included in 785a6f9 (0120184) — cost if wrong: none.

## Pre-flight scan

| Tasks | Produces → consumes | Finding |
|---|---|---|
| T1 → all | pyproject test config, conftest pops GOOGLE_CLOUD_PROJECT | rsync must exclude `.git` defensively (scaffold has none today). Ruling below. |
| T1 conftest ↔ T6 conftest append | env pop, then `from app.environment import registry` | OK: pop precedes app import. |
| T5 schemas → T10, T11, T12, T14 | RunRequest, IssueTask, Plan, PatchResult, Diff, TestReport, Review, RunRecord | Names and fields agree. |
| T6 env/registry/fakes → T7, T8, T9, T11, T12 | Environment protocol, registry.register/get/release/clear, FakeLlm, FakeEnvironment, fake_tool_context, make_bench_task | Agree. FakeEnvironment `test -e` handler matches write_file's command. |
| T7 tools → T8 guardrails | WRITE_TOOL_NAMES, resolve_repo_path, PathError | Agree. |
| T7 tools → T10 agents | READ_ONLY_TOOLS / CODER_TOOLS tuples | Agree (agents wrap in list()). |
| T9 budget → T12 driver | BudgetPlugin().usage(session_id), BudgetExceeded | Agree. |
| T10 agents → T11/T12 | output_key plan/patch/review; state keys issue_text, plan, diff_text | fetch_issue sets issue_text; collect_diff sets diff_text; planner output_key sets plan. Agree. |
| T11 nodes → T12 pipeline | routes actionable/declined, pass/fail/exhausted, approve/changes/exhausted | Agree with edge dicts. |
| T11 task_store → T13, T14 | materialize(task, dest, with_solution, with_hidden_tests), task_dir, list_tasks | Agree. |
| T12 driver → T14 bench.run | run_pipeline(RunRequest) → RunRecord.patch_path | Agree. |
| T13 bench._pytest → T14 score | run_pytest(cwd, *paths) | Agree. |

| Task | Self-consistency | Finding |
|---|---|---|
| T1 | steps vs files | OK. |
| T2 | spike in sibling worktree | OK; throwaway. |
| T3 | tests vs Dockerfile | OK. |
| T4 | probe vs shim endpoints (/healthz, /files/zip, /exec) | OK per adk-samples @2f902cb. Owner approval required. |
| T5 | tests vs schemas | OK (`__test__ = False` on TestReport). |
| T6 | tests vs docker.py | OK. |
| T7 | tests vs tools | OK. |
| T8 | tests vs check_command | DEFECT: regex split on `|` ignores quoting, so `rg "a|b" src` is refused as unparsable. Ruling below. |
| T9 | tests vs pricing/budget math | OK (0.015 at 10k in / 2k out Flash). |
| T10 | placeholder test vs instructions | OK. |
| T11 | tests vs nodes | OK (`test_files.__test__ = False` honoured by pytest for functions). |
| T12 | e2e tests vs pipeline/driver | OK; relies on ADK propagating plugin exceptions (verified by probe). |
| T13 | validate rules vs 5 tasks | OK by construction; validate run is the check. |
| T14 | score tests vs tc-001 | OK. |

## Rulings (pre-flight)
- Ruling: T1 rsync adds `--exclude .git` — protects repo history if a future scaffold version inits git — cost if wrong: none.
- Ruling: T8 `check_command` must segment with a quote-aware tokenizer (shlex with punctuation_chars, newline as separator) instead of the regex split; add allowed-case tests `rg "foo|bar" src` and `python -c "print(1); print(2)"` — the plan's version blocks ordinary quoted-pipe commands, which hurts the coder for no safety gain — cost if wrong: slightly more complex guard code; sandbox remains the real boundary.

## Progress
Task 1: complete (commits 0120184..27f65da, inline by controller; ruff extend-exclude docs added because ruff format checks markdown code blocks)
Task 2: BLOCKED on owner — project cloud-agents-project has billing disabled (403 BILLING_DISABLED on Gemini). Partial finding: Workflow runs under `agents-cli run`; function-node `Event(message=...)` renders. Spike worktree kept at <scratchpad>/itp-spike-s2 (branch spike/s2-eval) to resume Steps 3–7 once a billed project is chosen. Note: first `agents-cli run` cold start exceeded the CLI's 30 s server wait; second run fine.
Task 4 and Task 14 Step 7: also need the billed project + owner approval.
Ruling: proceed with Task 3 and Tasks 5–13 (no GCP dependency) while the billing question is open — they are independent of spike outcomes except S2's eval-format decision, which only affects Week 2 — cost if wrong: none for Week 1 code.
Task 3: complete (commits 27f65da..05ef91c, inline by controller; vendored shim excluded from ruff/ty)
Task 5: complete (commits 05ef91c..ff43a0c, review clean). ⚠️ resolved: the 1 pytest warning is ADK's own `BaseAgentConfig is deprecated` DeprecationWarning at import, not from schemas.
Task 5: minor (deferred): Diff.is_empty depends only on `files` (brief's contract) — later tasks must populate files consistently.
Ruling: silence ADK's import-time `BaseAgentConfig is deprecated` DeprecationWarning via pytest filterwarnings, done by Task 6's implementer — keeps test output pristine so real warnings stand out — cost if wrong: one hidden upstream deprecation notice.
Task 6: complete (commits ff43a0c..4014f34, review clean). ⚠️ resolved by controller: 18 passed (unit + docker env + image tests), fakes import cleanly, no leftover containers.
Task 6: minor (deferred): docker.py exec — `_DAEMON_ERRORS` includes "is not running", which agent command stderr could also contain → misreported InfraError; match on docker exit codes 125/126 or daemon-specific strings only.
Task 6: minor (deferred): docker.py exec — `str(int(timeout))` makes a sub-second timeout `0` (= no limit); use max(1, ceil(timeout)).
Task 6: minor (deferred): _run outer timeout kills only the docker client; in-container process ends via inner `timeout -k` (acceptable).
Task 7: complete (commits 4014f34..d73ec82, review clean). ⚠️ resolved: read-only protection for tests/.github is owned by Task 8 (GuardrailPlugin via WRITE_TOOL_NAMES) and Task 11 (run_tests rejects diffs touching protected paths); commits carry no trailers.
Task 7: minor (deferred): no behavioural tests for list_dir and grep (exit-code branches, WORKDIR prefix stripping, path refusal).
Task 7: minor (deferred): list_dir ignores exit_code/stderr (missing dir looks empty) and gives no truncation signal at 200 entries.
Task 7: minor (deferred): write_file does not catch env.write_file failures (e.g. path is a directory) → exception instead of {"error": ...}.
Task 8: complete (commits d73ec82..6431135, review clean; ruling applied — quote-aware tokenizer). ⚠️ resolved: WORKDIR and WRITE_TOOL_NAMES are as Tasks 6/7 define them; plugin registration is Task 12's job.
Task 8: minor (deferred): guardrails.py _segments — shlex default `commenters="#"` lets `ls # x\ncurl y` and `echo a#b\ncurl x` through; set `lexer.commenters = ""` and add both as refused test cases. (Defect originates in the controller's ruling prototype.)
Task 8: minor (deferred): `2>&1` tokenises into stray segments (harmless); add a one-line comment.
Task 9: implementer BLOCKED on 1Password commit signing (3 attempts); code staged, uncommitted. Also deviated: made long-context pricing exact-match to satisfy a contradictory test.
Ruling: Task 9 plan defect — brief's test `test_cost_prefix_and_provider_prefix` prices `gemini-3.1-pro-preview` at 1,000,000 input tokens and expects $2.00, but that is above the 200k long-context threshold, where the brief's own code (prefix lookup in LONG_CONTEXT_PRICES) correctly gives $4.00. Fix the TEST (use 100_000 tokens → $0.20) and keep the brief's prefix-based long-context lookup — a `-preview` variant of Pro must still get long-context pricing or the budget cap under-counts — cost if wrong: none; conservative pricing.
Task 9: fix applied per ruling (test corrected, prefix lookup restored; 59 unit tests pass, lint clean). Commit still blocked: 1Password signing fails from subagent AND controller.
Ruling: while signing is unavailable, continue with changes STAGED but uncommitted; implementers `git add` their files and do not commit; reviews use path-limited `git diff --cached` packages; controller creates the per-task commits in order (brief's messages) once signing works. Never bypass signing — cost if wrong: commits land later than the work; per-task history is reconstructed by path (tasks touch disjoint files).
Pending commits (in order): T9 "feat: per-run cost and tool-call budget plugin" — app/pricing.py app/budget.py tests/unit/test_budget.py
Task 9: complete (STAGED on 6431135, commit pending signing; review clean after fix round 1/5 — 1 plan-defect ruling applied). ⚠️ resolved: ADK 2.8 Context has `agent_name` and `session` (verified in planning probe; Task 12 e2e exercises real types); all three files fully staged.
Task 9: minor (deferred): budget turn counter is per session, reset on any agent start — revisit if agents ever interleave.
Task 9: minor (deferred): `last_model` single slot per session — wrong price if model calls ever run concurrently.
Task 9: minor (deferred): malformed env value (RUN_BUDGET_USD=abc) raises bare ValueError without naming the variable.
Task 9: minor (deferred): docstring should say one model call can overshoot the cap (checked before the next call).
Ruling: use sonnet (not haiku) for remaining implementers — haiku took 6.5 min on Task 9 and "fixed" a contradiction by weakening logic; sonnet finished Tasks 6/8 in ~1 min each — cost if wrong: slightly higher token price.
Signing restored. Task 9 committed by controller: 6431135..091eb7e. Deferred-commit mode ended; implementers commit again.
Task 10: complete (commits 091eb7e..0af339a, review clean; commit created by controller after signing returned). ⚠️ resolved: imports resolve (63 unit tests pass on HEAD).
Task 10: minor (deferred): make_model routes any name containing "/" to LiteLLM, so `models/gemini-...` would misroute; document or special-case.
Task 10: minor (deferred): test_role_models_from_env does not cover REVIEWER_MODEL.
Task 11: complete (commits 0af339a..b937967, review clean). ⚠️ resolved: Diff.is_empty and TestReport(exit_code=-1) exist per Task 5. Out-of-brief change accepted: ty rule `pydantic-discarded-extra-argument = "ignore"` (ADK Event convenience kwargs).
Task 11: minor (deferred, FIX BEFORE FIRST REAL BENCH RUN — benchmark integrity): verify.py DIFF_CMD/NUMSTAT_CMD lack `--no-renames`; `git mv tests/test_x.py tests/test_y.py` yields a `{a => b}` path that never matches protected_paths, bypassing the protected-file check. Add `--no-renames` to both and a rename test.
Task 11: minor (deferred): task_store.test_files rglob walks hidden/vendor dirs and protects only .py files under tests/ (fixtures stay writable).
Task 11: minor (deferred): deliver_patch builds path from run_id unsanitised (`..` would escape RUNS_DIR).
Task 11: minor (deferred): no tests for diff_text 20k truncation or the git-baseline/git-diff InfraError paths.
Task 12: implemented 60a83e0 (98 tests pass incl. docker e2e; brief code worked verbatim). Review: spec ✅, 1 Important (plan-mandated), 3 ⚠️ confirmed by controller, 10 minors.
Ruling: Task 12 Important — classify_failure books ANY pydantic ValidationError as agent "malformed model output" (e.g. a broken task.yaml) — reviewer is right; spec §6.4 separates agent failures from bugs/infra and the benchmark's attribution depends on it. Fix: classify ValidationError as `agent` only while an LLM agent is active (tracked by a small plugin via before/after_agent callbacks); otherwise re-raise — cost if wrong: a ValidationError inside an LLM agent's tool call is still booked as agent (rare).
Ruling: ⚠️ bare `uv run pytest -q` fails collecting scaffold tests/load_test/load_test.py (locust) — exclude via pytest addopts `--ignore=tests/load_test`, keep the file for the deploy phase — cost if wrong: none.
Ruling: ⚠️ AGENTS.md must be updated in Task 12 (plugin re-wiring, new env vars) per its own rule — controller supplies the text in the fix brief.
Task 12: minor (deferred): intake registers env before the state event; a cancellation in that window orphans a container (use run-keyed registry or `except BaseException`).
Task 12: minor (deferred): exception inside driver `finally` (docker rm timeout) replaces the classified failure and skips record.json — wrap release in its own try/log.
Task 12: minor (deferred, WEEK 2 DESIGN): runs via the agents-cli entry point (`agents-cli run`, playground, FastAPI, deployed runtime) never release the sandbox — only app/driver.py does; local Docker has no TTL. Consider an after_run_callback release plugin shared by both paths.
Task 12: minor (deferred): test_infra_failure_is_classified sleeps 0–3 s through retry backoff with jitter; use jitter=0 / zero-delay config in the test.
Task 12: minor (deferred): driver coverage gaps — no assertion that retries happen, no mid-run InfraError test.
Task 12: minor (deferred): one ADK UserWarning in test output ([EXPERIMENTAL] JSON_SCHEMA_FOR_FUNC_DECL) — add filterwarnings entry.
Task 12: minor (deferred): app/agent.py has no import test pinning root_agent.name and the two guard plugins.
Task 12: minor (deferred, OWNER ATTENTION): importing app.agent with GOOGLE_CLOUD_PROJECT set creates a BigQuery dataset (scaffold behaviour) — conflicts with AGENTS.md rule 8 before any deploy; `agents-cli run`/playground load .env and would trigger it.
Task 12: minor (deferred): ADK matches retryable exceptions by exact class name — InfraError subclasses would not retry (none today).
Task 12: minor (deferred): app/agent.py dropped the Apache-2.0 header while keeping scaffold-derived BigQuery block.
Task 12: fix round 1/5 (3 addressed, 0 open — ValidationError classification by origin, pytest addopts ignore load_test, AGENTS.md runtime wiring; commits 60a83e0..9ec1d98)
Task 12: complete (commits b937967..9ec1d98, review clean after 1 fix round). ⚠️ resolved: docker e2e verified by controller (105 passed on bare `uv run pytest -q`).
Task 13: complete (commits 9ec1d98..ba825c3, review clean). ⚠️ resolved: materialize/task_dir semantics are Task 11's, covered by its tests and by `bench.validate` printing ok ×5 (controller-run).
Task 13: minor (deferred): tc-003 hidden tests don't cover `--tag` combined with `--all`/`--sort` though the issue text requires it — weakens the task.
Task 13: minor (deferred): validate checks "hidden tests fail at base+plant" as a whole run (some hidden tests may pass on base, e.g. tc-004 test_cli_priority_unchanged) — by design.
Task 13: minor (deferred): tc-002 hidden test couples to format_task column layout via line.split()[3].
Ruling: Task 14 is split — subagent does Steps 1–6 and 9 (scorer, runner, tests, AGENTS.md commands); Steps 7–8 (real Flash run + results doc) stay with the controller and wait for the owner's billing decision — cost if wrong: none; they cannot run without a billed project anyway.
Task 14: code part complete (commits ba825c3..b4ffdbe, review clean; Steps 1–6 + 9). ⚠️ resolved by controller probe: a wrong-fix patch for tc-001 (README-only change) scores False, so hidden tests are collected at scoring time. Steps 7–8 (real Flash run + results doc) PENDING owner: billed GCP project + go-ahead.
Task 14: minor (deferred): test_score lacks negative tests (patch applies but fails hidden tests; patch fails git apply).
Task 14: minor (deferred): score_patch raises FileNotFoundError on a missing patch file.
Task 14: minor (deferred, WEEK 2): bench.run does not validate --tasks ids, and one crashing task aborts the run before results/<stamp>.json is written (earlier results lost).

## Final whole-branch review (opus, 05ef91c..b4ffdbe): merge WITH FIXES — 1 Critical, 9 Important, 12 Minor, 35 deferred items triaged
Owner said "continue with the work" after the controller listed the four recommended default/spec changes (BigQuery opt-in, 30-min local sandbox TTL, malformed-output retry plugin, scoring inside the sandbox).
Ruling: treat that as approval of those four recommendations — all four move in the safer direction and were presented with a recommendation — cost if wrong: owner reverts a small, isolated change.
Ruling: fix wave scope = C1, I1–I8, M1, M5, M9, M11, deferred items 6, 7, 21, 26, 27, 29, 30, 32, 33, 34, 35 — everything the reviewer marked "before merge" or "before first real benchmark run", done in ONE dispatch — cost if wrong: a larger fix diff to re-review.
Ruling: C1+I8 solved together by scoring inside a fresh sandbox with hidden tests outside the repo and pytest config isolated; validate keeps running trusted reference code on the host — cost if wrong: scoring needs Docker.
Ruling: I6 solved by keeping the pipeline's git dir outside the worktree (`--separate-git-dir`) and diffing against a recorded baseline SHA — cost if wrong: slightly unusual git layout in the sandbox.
Ruling: I7 — add ReflectAndRetryModelPlugin(max_retries=2) as the spec says, and amend spec §6.4 to state it covers malformed function calls, while schema-validation failures are agent failures — cost if wrong: none.
Ruling: I9 (output cap enforced inside Environment) deferred to Week 3 with the second backend, as the reviewer recommends — cost if wrong: a runaway command could buffer large output on the host for up to 120 s.
Ruling: M2 (routers emit no content event) waits for the S2 spike result; M3, M4, M6(-z), M7, M8, M10, M12 and deferred items 1,3,4,5,8–15,17–20,23–25,28,31 go to Week 2/3 — reviewer triaged them as deferrable.
Ruling: Task 4 (cloud resources) and Task 14 Step 7 (real run) still need an explicit owner yes (AGENTS.md rule 8); "continue" is not that.
Task 2 (spike S2/S3): RESULTS — S3: native output_schema+tools on gemini-3.8-flash loops on the tool call (13/18 runs hit an 8-call cap); ADK's set_model_response fallback finished 9/9 runs in 2 model calls. S2: `agents-cli eval run` works with a Workflow (output-only events are dropped from traces, not an error); /app-info 400 for a non-LlmAgent root → traces omit agent_data.agents; eval CLI has no per-case timeout. Findings doc written: docs/spikes/2026-09-30-s2-s3-workflow-eval.md (uncommitted until the fix wave finishes, to avoid index contention).
Ruling: S3 outcome → Gemini agents must use the set_model_response fallback (Gemini subclass declaring output_schema_and_tools=False in app/models.py); added to the running fix wave as item K — without it the real pipeline would burn its tool-call caps on every run — cost if wrong: one extra tool call per agent turn.
Ruling: S2 outcome → Week 2 uses `agents-cli eval run` as is (decision (a) in the plan); M2 (routers emit no content) stays deferred.
Final fix wave: DONE_WITH_CONCERNS — items A–K implemented in 8 commits (b4ffdbe..ea8e269); controller-verified: 221 passed, bench.validate ok ×5, no leftover containers. Scoped re-review dispatched (opus).
Task 2: complete (spike worktree and branch removed; findings doc committed by controller).
Final fix wave re-review (opus, b4ffdbe..ea8e269): ALL findings A–K ADDRESSED, no new Critical/Important. Implementer's five concerns accepted, with one correction (a patch-added tests/conftest.py CAN make hidden tests pass by rewriting them, because hidden tests are uploaded before the visible run). No second fix wave (process rule); residuals adjudicated below.
Final: parked — hidden tests uploaded before the visible run (bench/score.py) let a deliberately adversarial `tests/conftest.py` rewrite them → scored True — Ruling: real, requires informed sabotage (the agent never sees the hidden path); move the upload after the visible run at the start of Week 2 scorer hardening — cost if wrong: a sabotaging patch could score resolved in the Week 1 smoke run (not a published number).
Final: parked — scoring trusts the exit code of a process that runs patch code (repo-root `pytest.py`, `atexit os._exit(0)` in package code → scored True) — Ruling: not closable by pytest flags; OWNER DECISION for Week 2: state the threat model in spec §9.1 (results hold for patches that do not attack the test process) and add a deterministic audit of resolved rows' patches for pytest.py/conftest.py/sitecustomize.py/*.pth/os._exit/atexit — cost if wrong: an adversarial patch inflates the resolve rate until the audit exists.
Final: parked — `set_model_response` consumes one tool-call slot (coder's effective per-turn cap is 24 real calls; RunRecord.tool_calls includes framework calls) — Ruling: accept for now, it only tightens the guard; OWNER DECISION whether to exempt framework tools from the count — cost if wrong: slightly more budget aborts.
Final: parked — protected-test and `.github/` rejection messages advise `git checkout -- <file>`, which does not revert after the pipeline's `add -A` (needs `git checkout <baseline_sha> -- <file>`) — Ruling: real; fix first thing in Week 2 (include the baseline SHA or restore the files in the pipeline) — cost if wrong: a coder that edits a protected test via bash burns its three attempts in the smoke run.
Final: parked — a scoring crash row has no cost_usd, so the printed total understates spend (record.json keeps it) — Ruling: fix in Week 2 runner work.
Final: parked — no test fails if `-c /dev/null` or `--confcutdir` is removed from the scorer — Ruling: add with Week 2 scorer hardening.
Final: parked — "Cannot connect to the Docker daemon" is not detected as infra (scores False mid-scoring instead) — Ruling: add the prefix in Week 2; low likelihood locally.
Final: parked — all model API errors, including 4xx the agent caused (context overflow), are `infra` — Ruling: Week 2 reporting must show infra rows by cause so they cannot flatter the resolve rate.
Final: parked — `cd .. && rm -rf .pipeline-git` passes the guardrail → infra failure — Ruling: deliberate sabotage only; covered by the threat-model note.
Final: parked — Terraform does not set BQ_ANALYTICS_ENABLED, so deployed analytics stays off — Ruling: correct for now; set it in the Week 3 deploy task with owner approval.
Final: resolved by controller — spec §19 (2026-09-29 item 6) now says it is superseded by the spike result (commit below).
Final whole-branch review: CLEAN after one fix wave + one scoped re-review. Remaining plan work: Task 4 (S1 cloud spike) and Task 14 Steps 7–8 (first real run + results doc) — both wait for an explicit owner yes (AGENTS.md rule 8 / spend).
Owner approved Task 4 and the first real benchmark run in-session ("ok you have my approval"), and asked to follow along; controller runs these inline.
Task 4 (spike S1): complete — Agent Runtime Sandbox from our image works: upload zip, pytest (1 passed), git, rg, network blocked, download. Sandbox create ~2 s; ready in 5.6 s (warm template); template create 1–4 min. Gotchas: platform answers /healthz before the container listens (502 on early requests) → readiness must be a no-op exec; template display names not returned → match on image; console shows neither the empty host nor sandboxes (audit log does). Correction recorded: the two probe invocations the owner interrupted had already started and created the instance and two templates. 4 duplicate templates deleted; kept: engine 2290264578715549696, template 6774574923844157440, AR repo, image v0.1.0, SA sandbox-caller. Findings: docs/spikes/2026-09-30-s1-agent-runtime-sandbox.md.
Task 14 Steps 7–8: complete — first real run on gemini-3.8-flash: 5/5 resolved (tc-005 correctly declined), $0.80 total, no crashes/infra/budget failures. Results: results/20260930T11*.json, docs/results/2026-09-30-week1-smoke.md. Observation: coder used exactly 25 tool calls (= per-turn cap) in tc-001 and tc-004 — investigate in Week 2.
Task 14: complete. ALL PLAN TASKS COMPLETE (1–14). Final review clean after one fix wave.
Owner request (2026-09-30): wants to follow runs in the GCP console → chose "Cloud Trace for local runs". New small task outside the Week 1 plan: export spans from the local driver to Cloud Trace.
Tracing (owner-requested, outside the Week 1 plan; implemented inline by controller, test-first): app/tracing.py (opt-in TRACE_TO_CLOUD=1, quota pinned to GOOGLE_CLOUD_PROJECT because local ADC's quota project is another project), root span `issue_to_pr.run` in the driver, bench.run enables/flushes/prints link. 226 tests pass, lint clean. Verified live: traced run tc-002 → trace d199322974fc7c59fb72f3a7f094d508 with 112 spans in Cloud Trace. NOT independently reviewed yet.
Tracing review (sonnet, 4de44cf..7ff8fbc): Approved, no Critical/Important. Minor (deferred): catch DefaultCredentialsError in enable_cloud_trace with a clear message; add a test that the root span records an error and the exception still propagates.
Investigation: coder's "exactly 25 tool calls" in tc-001/tc-004 — both turns ended normally (no errors, final call was set_model_response); cap triggers on the 26th call. Looks coincidental; real finding is 4–5 redundant verification calls per task (pytest, git status, git diff, pytest) → Week 2 prompt tuning.
Live progress (owner-approved): implemented by subagent, commit 22363ed (242 tests pass). Controller follow-up: codespell skip for runs/ and results/ (model output tripped the spell-check).
Owner decision: Week 2 order = measurement first (carry-over fixes, parallel runner, presets, repeats, baseline, report), then task set, then GitHub live mode.
Live progress review (sonnet, 8327498..312dc91): Approved, no Critical/Important. Minor (deferred): whitespace-only pipeline text prints an empty line; a None tool name would print "None"; no direct run_tasks(progress=True) test.
Week 1 closed: merged to main by fast-forward; ledger archived to docs/superpowers/plans/2026-09-29-week1-ledger.md.
