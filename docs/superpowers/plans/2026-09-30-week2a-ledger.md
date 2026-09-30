# SDD ledger — plan: docs/superpowers/plans/2026-09-30-week2a-measurement.md

Branch: week2-measurement (from main @ ae9141e). Spec: docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md. Carry-overs: docs/superpowers/plans/2026-09-29-week1-ledger.md.
Owner is away; commit signing works without prompts (1Password approval remembered 24 h, Mac kept awake). Task 7 spends credits: STOP before it and wait for the owner.

## Pre-flight scan

| Tasks | Produces → consumes | Finding |
|---|---|---|
| T1 → T4 | `run_tests(..., baseline_sha)` bound from state; baseline graph reuses `run_tests` | OK: `provision_sandbox` already sets `baseline_sha`. |
| T1 → T5 | `audit_patch`, `"audit"` on rows in `bench/run.py::run_task` | T5 replaces `run_task` with `matrix.run_spec`; the plan says the row keeps `audit`. OK. |
| T3 → T5 | `PRESETS`, `role_models`, `solo_model_name` | T5 adds `workflow_for` to the same file. OK (sequential). |
| T4 → T5 | `build_baseline_workflow` | OK. |
| T5 → T6 | row fields | T6 also loads legacy rows; independent of T5's code. OK. |
| T1 ↔ T4 | both add a docker test; plan puts both in `tests/integration/test_pipeline_docker.py` | Merge-conflict risk when run in parallel. Ruling below. |
| T3 ↔ T4 | both list `tests/unit/test_agents.py` | Same risk. Ruling below. |
| T1 ↔ T4 | both append to spec §19 | Same risk. Ruling below. |
| T4, T6 (, T5) | all edit `AGENTS.md` | Different sections; git should merge. Controller resolves if not. |

| Task | Self-consistency | Finding |
|---|---|---|
| T1 | tests vs behaviour | OK. The `--confcutdir` flag is not pinned by a test (no reachable attack path: a patch cannot write outside the repo). Accepted as defensive. |
| T2 | tests vs behaviour | OK. |
| T3 | tests vs interfaces | OK. |
| T4 | edges vs tests | OK. |
| T5 | CLI vs tests | OK. |
| T6 | summary fields vs tests | OK. |
| T7 | needs owner approval | Stop before Step 2. |

## Rulings (pre-flight)
- Ruling: run Tasks 1, 2, 3, 4 and 6 in PARALLEL, each in its own git worktree, then Task 5 alone — the owner asked to parallelise to save time, and these five touch disjoint source files; the skill's "never parallel implementers" rule exists to prevent conflicts, which worktree isolation plus the file rulings below address — cost if wrong: merge conflicts the controller must resolve, or a task built against code another task changed.
- Ruling: T4 puts its docker test in a NEW file `tests/integration/test_baseline_docker.py` and its solo-agent contract tests in `tests/unit/test_baseline.py` (not `test_pipeline_docker.py` / `test_agents.py`) — avoids same-file conflicts with T1 and T3 — cost if wrong: none.
- Ruling: T3 puts its `RoleModels.from_names` tests in `tests/unit/test_presets.py` (not `test_agents.py`) — same reason — cost if wrong: none.
- Ruling: T4 does not edit the spec; the controller adds the baseline §19 line after the merges — avoids a §19 conflict with T1 — cost if wrong: none.
- Ruling: implementers use sonnet (behaviour-spec briefs need judgment; Week 1 showed haiku was slower and weaker); reviewers sonnet; final review opus.

## Progress
Task 3: implemented 9c66385 on worktree-agent-ac9d53a2f7274263c (251 passed); review dispatched
Task 3: complete (commits ae9141e..9c66385, review clean; fast-forwarded into week2-measurement). ⚠️ resolved: the implementer's report file was never written to the workspace (worktree agents may not be able to write into the main checkout); controller verified instead — test_presets/test_budget/test_agents 26 passed on the merged branch; full suite runs after the wave merges.
Task 3: minor (deferred): test_cost_pro_preview_long_context_rate duplicates an existing test.
Task 3: minor (deferred): solo_model_name special-cases the name "mixed"; a general check would be "all roles share one model".
Task 2: implemented 4650dd5 on worktree-agent-aea81542ef04077f3 (257 passed); review dispatched
Task 2: complete (review clean; cherry-picked onto week2-measurement). ⚠️ resolved by controller: unit suite passes on the merged branch (imports and `_forbid_exporters` exist).
Task 2: minor (deferred): Gemini also returns 400 for an invalid API key or an unsupported region, which are now labelled `agent` (plan-mandated rule); refine with the error's status/message, or document the caveat where failure kinds are described.
Task 2: minor (deferred): the root-span ERROR test is a regression guard (passed before implementation).
Task 4: implemented f88d0de on worktree-agent-a4946d2eee79f7226 (254 passed incl. docker baseline test); review dispatched
Task 6: implemented f48c33c on worktree-agent-ad9f74531f511ae0c (250 passed); DONE_WITH_CONCERNS (report written inside the worktree — copied by controller; crashed rows counted only under "crashed"; extra `flagged` field on Summary); review dispatched
Task 1: implemented 6cdfecf on worktree-agent-ab1722c965be026e9 (276 passed, docker 42, validate ok x5); concerns: run_tests implemented before its unit tests (no RED for those); changed existing docker rename test; audit also flags deleted steering files. Review dispatched (opus).
Task 6: complete (review clean; cherry-picked onto week2-measurement). All three implementer concerns accepted by the reviewer.
Task 6: minor (deferred): a repeat where every run is infra/crashed is skipped, and if all are skipped the table shows 0% instead of "n/a".
Task 6: minor (deferred): category counts pool all repeats without saying so.
Task 6: minor (deferred): rows missing task_id/category/resolved/failure_kind raise KeyError (hardening).
Task 6: minor (deferred): legacy rows pool into repeat 1, so the legacy rate is resolved ÷ runs, not ÷ tasks.
Task 4: complete (review clean; cherry-picked onto week2-measurement). ⚠️ resolved by controller: node definitions and retry config are identical in pipeline.py and baseline.py; no BASELINE_MODEL mention remains; baseline unit + docker tests pass on the merged branch (12 passed), which also confirms the per-turn cap exemption and empty-diff behaviour.
Task 4: minor (deferred): test_baseline imports helpers from tests.unit.test_pipeline (move shared helpers to tests/fakes.py).
Task 4: minor (deferred): test_baseline_workflow_shape checks name and schema only, not the edge set.
Task 4: minor (deferred): duplicated `bench` fixture; the four FunctionNode definitions are repeated in pipeline.py and baseline.py (a shared helper would guarantee parity).
Task 1: review (opus): implementation of the brief approved (restore verified against real git in 12 cases; ordering correct on every path; audit correct); 1 Important, brief-level: code run during the visible phase can pre-plant files in /workspace/hidden (or leave a background process), so three no-fix patches score resolved=True, one with a clean audit.
Ruling: Task 1 Important — reviewer is right and the plan's "upload hidden tests later" design is insufficient. Score in TWO fresh sandboxes: A runs the visible tests; only if they pass, B (fresh) gets clean copy + patch + restored protected files + hidden tests and runs only the hidden tests. No patch code has executed in B before the hidden tests are in place. Reword the scorer docstring and spec §9.1 to state what is and is not covered — cost if wrong: one extra container start per scored run (~2 s).
Task 1: minor (deferred → Task 5 covers it): _crash_row has no "audit" key (Task 5 rebuilds rows with every field always present).
Task 1: minor (deferred): _audit swallows OSError and returns [] (indistinguishable from clean).
Task 1: minor (deferred): audit header regex misses git's quoted headers for non-ASCII paths; `+++` exclusion skips added lines starting with `++`; duplicate flags for a token on several lines.
Task 1: minor (deferred): _violations computed twice; "Put new tests in new files" appended to .github-only violations.
Task 1: minor (deferred): no docker test of the .github restore against real git; no test of _restore failing → InfraError; no test for a deleted steering file; rename test could assert the renamed copy is in the patch.
Task 1: first commit cherry-picked onto week2-measurement as f3363ba (its implementation of the brief was approved) so Task 5 can build on the audit; fix round 1 (two-sandbox scoring) in progress in the Task 1 worktree.
Wave 1 merged state verified by controller: bare `uv run pytest -q` → 320 passed; bench.validate ok ×5; agents-cli lint passes (HEAD f3363ba).
Ruling: start Task 5 in a worktree from f3363ba while the Task 1 fix is built — the fix touches only bench/score.py, its tests and the spec, none of which Task 5 edits — cost if wrong: a small merge conflict.
Owner approval (2026-09-30, in session): "task 7 is a go i approve just document what you do" — Task 7's paid comparison run (~$12) is approved; owner is away and wants everything done documented.
Ruling: run the final whole-branch code review (Tasks 1–6) BEFORE Task 7's paid run, then Task 7, then a short review of its docs — a scoring or runner bug found after the run would mean paying for it twice — cost if wrong: the final review happens slightly earlier than the skill's order.
Ruling: document the run as it happens in a run log committed with the results (commands, times, cost per step, anything unexpected), in addition to the report's hand-written "What happened" section, and update the owner's Obsidian note.
Task 5: implemented 6252125 on worktree-agent-ace6e88d299a3c5c3 (334 passed, lint ok); concerns: tests written with the implementation (no RED); final row's run_id is the last attempt's; `main` runs validation and guards before load_dotenv; one ADK UserWarning in a test. Report copied by controller from the hand-back. Review dispatched (sonnet).
Task 5: review (sonnet): spec compliant, approved; concurrency, retry bound, crash rows, durability and report compatibility verified. 1 Important: `main` selects and validates tasks before `load_dotenv()`, so `BENCH_TASKS_DIR`/`BENCH_REPOS_DIR` set only in `.env` would validate one tree and run another (regression; latent — the owner's `.env` sets only GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION). Fix round 1 dispatched to the implementer.
Task 5: minor (deferred): only cost_usd is summed across infra attempts; tokens, tool calls and duration are the last attempt's.
Task 5: minor (deferred): the final row's run_id is the last attempt's; earlier attempts' run dirs are not linked from the row.
Task 5: minor (deferred): `gather` without return_exceptions — a failed results write aborts the matrix.
Task 5: minor (deferred): a pipeline exception before a RunRecord exists gives cost_usd 0 (per brief); that run's spend is uncounted.
Task 5: minor (deferred): no test that CancelledError propagates; none for infra retry followed by a crash; ADK UserWarning noise in test output (pre-existing).
Task 1: fix round 1/5 (118bd1d, two-sandbox scoring): re-review (opus) — all findings addressed, no new Critical/Important. The three no-fix attack patches score False at the fix and True on the old scorer (tests not vacuous). Cherry-picked as 203d20d.
Task 1: complete (commits f3363ba, 203d20d).
Task 1: minor (deferred): closing sandbox B on non-success paths is not pinned by a test (mutation survived); the "no hidden-named file under WORKDIR" assertion now runs on the second env only.
Task 1: out-of-scope (for final-review triage): a patch that replaces a protected test file with a symlink to /workspace/hidden makes scoring raise InfraError (crashed row) instead of scoring False; it cannot produce True; audit is clean.
Task 1: out-of-scope (admitted residual, documented in spec §9.1): a no-fix patch that monkeypatches pytest internals from package code during the hidden run scores True with a clean audit. Must be stated in the results write-up; read every resolved patch in Task 7.
Task 1: out-of-scope (deferred): DockerEnvironment.close ignores `docker rm -f`'s exit code.
Task 5: fix round 1/5 (8659e35): load_dotenv() moved to right after parse_args; RED `assert 2 == 0`, then green; two CLI tests added. Controller read the 23-line diff and confirmed it (no separate re-review: one moved line plus tests; the final review covers it). Cherry-picked as 56334b3 + 527addb.
Task 5: complete (commits 56334b3, 527addb).
Controller: spec §19 block for Tasks 2–5 (baseline definition, presets, 400/413 rule, matrix CLI) committed. Merged state verified: `uv run pytest -q` → 341 passed; bench.validate ok ×5; agents-cli lint exit 0.
Ruling: the Task 5 one-line fix was verified by the controller instead of a reviewer dispatch — the diff is a single moved call plus tests and the whole-branch opus review follows immediately — cost if wrong: the final review catches it one step later.

## Final whole-branch review (opus), ae9141e..b52a36b, before Task 7
Verdict: no Critical. GO for the paid run with conditions; merge with fixes. Per-run isolation, Pro price lookup, caps, scoring and runner robustness verified; Docker at concurrency 3 verified on this machine.
Important findings: I1 empty model answer from solo/planner/reviewer becomes a crashed row with cost 0 (tolerated in coder turns); I2 report can be read as stronger than it is (false repeats caveat for Pro rows, 0% for an all-infra group, unresolved `none` rows in no column, budget caps merged, unlabelled pooling and $/run, fake range for 1 repeat); I3 caps bind multi harder (25/turn is coder-only; Week 1 coder turns used 25, 25, 25, 13, 19, 18 calls; 75/run is shared by three agents); I4 decline detection asymmetric (`SoloResult.declined` defaults False; solo can decline on any of 4 turns); I5 solo and the pro preset have never met a real model; I6 a scoring InfraError discards a paid run; I7 no matrix-wide spend ceiling (realistic $10–18).
Ruling: fix wave before the paid run — A (sonnet, worktree): I1, I4 (`declined` required), crash rows keep their spend (RunCrashed), scoring retries InfraError ×2, a failed write/print cannot cancel in-flight runs, status line shows resolved. B (sonnet, worktree, parallel): I2 report fixes incl. duplicate-row guard. Both change numbers or their reading, and cost nothing to do first — cost if wrong: ~20 minutes before the run.
Ruling: I3 is by design (spec §6.3 and the approved plan; cost guards may not change without the owner). Run as planned; disclose prominently in the write-up with budget failures broken down by cap and a sensitivity figure excluding per-turn-cap failures. Whether to rerun with other caps is the owner's decision — cost if wrong: the first comparison understates multi; a rerun costs ~$5 on Flash.
Ruling: I4 second half (solo may decline on any of its turns, planner decides once) is disclosed, not changed — changing it means changing graph edges.
Ruling: I5 smoke first, into `results/smoke/` so it cannot be pooled into the report (~$0.25): single/flash on tc-005 + tc-002, then pro on tc-005 for each system. It is part of the approved Task 7 and counts against its budget.
Ruling: I7 stop rule — the owner approved "about $12". Run the four invocations one after another; after each, add up the cost; do not start the next if the cumulative total is above $15, and report instead. Pro invocations run last (they are the expensive ones and the first to be cut).
Ruling: post-run, read EVERY resolved patch (not only flagged ones), break budget failures down by reason, and check every "model rejected the request" row.
Final-review minors (deferred unless listed in the fix wave): rows lack model names/caps/commit (record them in the run log by hand); second-resolution stamp; Ctrl-C during provisioning leaks until TTL; IssueTask JSON carries run_id; spec drift §5.3/§6.3/§8/§9.1 and AGENTS.md status (controller fixes in the Task 7 docs commit); no solo test in set_model_response mode; no single-system trap test through run_spec; cost is a list-price estimate (cached tokens billed at full rate, so it overstates).
Fix wave dispatched: A and B, in parallel worktrees from b52a36b.
Fix wave A: implemented 27f440d (353 passed incl. docker; lint ok). Detection of an empty answer = router-input ValidationError title (`dynamic node 'route_plan|route_review|route_solo'`) AND the matching agent was the last to finish. RunCrashed carries the record. Scoring retries ×2 with a 2 s pause. Scoped re-review dispatched (opus) with an explicit GO/NO-GO for the paid run.
Fix wave B: implemented ac08d7e (356 passed; lint ok): six buckets summing to runs, per-row tasks/repeats/runs/counted, n/a instead of 0%, no range for one repeat, cost columns, "Runs that did not resolve", "Budget failures by cap", duplicate-row error. Controller read the new report module and the rendered sample; cherry-picked onto week2-measurement. The report is offline (not on the paid path); it is re-checked against the real result files in Task 7 Step 4.
Fix wave B: minor (deferred): the results table is 18 columns wide; the realistic test prints its Markdown on every run (capsys.disabled()).
Fix wave A: re-review (opus): A2–A6 addressed, no new Critical/Important. A1 NOT fully addressed: a reply with `content=None` (MAX_TOKENS, SAFETY, prompt block, STOP with no content object) from planner/reviewer/solo still ends as a crashed infra row (cost kept), because the router's own validation error is titled `Plan`/`Review`/`SoloResult`, not `dynamic node '...'`. Paid run: NO-GO until fixed. Fix round 2 dispatched to the implementer (plus: swallow ValueError around the status print).
Fix wave A: minor (deferred): on the crash path, an error while building the crash record hides the original exception; no final results rewrite when the failing write is the last one; bench/run.py skips flush_traces() and the summary line if run_matrix raises at the end.
Fix wave A: fix round 2 (bc42eda): re-review (opus) — A1 closed (86 probe cases: 9 empty shapes × 3 agents × 2 capability modes plus second visits and stale-agent cases), no false positives, no new Critical/Important. Paid run: GO. Cherry-picked as 6bf052f + 5d86208.
Fix wave A: minor (deferred): `except (OSError, ValueError)` around the status print also swallows a ValueError from formatting the line.
Merged state before the paid run (HEAD 5d86208): `uv run pytest -q` → 379 passed; bench.validate ok ×5; agents-cli lint exit 0.
Owner (2026-09-30, in session): the Cloud Trace Input/Output tab is empty because only the trace exporter is on; filling it needs `enable_cloud_logging=True` plus OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT, which sends prompt content to Cloud Logging. Owner said "ok not now" — deferred, not approved. Content is visible today on the `call_llm` span's Attributes tab.
Task 7: smoke runs passed ($0.14): single/flash tc-005 + tc-002 resolved 2/2; pro tc-005 declined for both systems. Invocation 1 (multi/flash ×3) started 13:37Z.
Task 7: invocation 1 multi/flash ×3: 12/15, $2.01 (tc-001 0/3, all "coder exceeded 25 tool calls in one turn"; the fix and tests were already written each time). Invocation 2 single/flash ×3: 15/15, $2.27 (tc-001 used 31, 38, 30 tool calls). All 21 resolved Flash patches read by the controller: genuine source fixes, no tampering.
Task 7: invocation 3 multi/pro at concurrency 3 FAILED operationally: 429 RESOURCE_EXHAUSTED from Vertex on tc-001/002/003 — all three attempts each burned within seconds (infra reruns have no pause); tc-005 declined; tc-004 retry2 hung for 23 minutes on a reviewer model call (TCP connection ESTABLISHED, no client timeout), spend about $0.46. Controller sent SIGINT at 14:43Z: clean exit, no sandboxes left, 4 rows on disk. Partial file moved to results/aborted/ (not reported).
Ruling: rerun the Pro invocations at --concurrency 1 on the SAME commit (no code change mid-benchmark), wrapped in a watchdog that sends SIGINT if the log is quiet for 600 s — cost if wrong: another few cents of 429 rows, or a lost in-flight run on a stall.
Finding (fix before merge): model calls have no request timeout, so a hung connection hangs a run forever; infra reruns start immediately, so a rate-limit window burns all attempts. Both belong in a follow-up fix (HTTP timeout on the model client; a pause before an infra rerun).
Task 7: invocation 3 rerun (multi/pro, concurrency 1) hung again at the same stage: tc-001, planner and coder done, tests passed, reviewer read two files, then its model call did not return for 21 minutes. 2 of 2 multi/pro attempts that reached the reviewer hung there. The watchdog's SIGINT had no effect (a job started with `&` in a non-interactive shell ignores SIGINT — controller's wrapper bug). Owner: "a stuck container is not good dont we have a failsafe for this?" Controller stopped the process with SIGTERM at 15:08Z and removed the sandbox by hand. Partial multi/pro file now really in results/aborted/ (the earlier `git mv -k` had silently done nothing).
Finding: there is no time failsafe for a run. Caps count dollars and tool calls; model calls have no timeout; the driver waits forever. The sandbox TTL removes the container after 30 minutes but does not end the run.
Ruling: fix wave C (sonnet, worktree): RUN_TIMEOUT_S wall-clock cap per run (default 1500 s, below the sandbox TTL) recorded as a budget failure, and a pause before an infra rerun (30 s × attempt). A new guard, not a change to an existing default; the owner can adjust the value. No per-call model timeout and no output-token limit: a timeout classified as infra would rerun a deterministic hang three times and hide it from the resolve rate — cost if wrong: a transient hang costs one run (recorded as budget) instead of being retried.
Ruling: no more Pro runs today. Multi/pro is reported as "did not complete" (reviewer call hung in 2 of 2 attempts); single/pro is not run without its comparison partner. Diagnosing the Pro reviewer hang needs real calls (a spike; hypothesis: runaway generation of the structured answer up to the output-token limit) and is the owner's call. Today's report is the Flash comparison. Spend so far ≈ $5.5 plus the two hung calls (unknown, at most ≈ $0.8 each).
Fix wave C: implemented c13decc (391 passed; lint ok). Review (opus): spec compliant, approved, Safe to merge: YES. Verified against the real google-genai client with a local server that never answers (API-key and Vertex modes), a hung tool call and a hung function node: each ends at the cap as failed/budget with spend kept, record written and sandbox released; outer cancellation still propagates; a transport TimeoutError is not mistaken for the cap.
Fix wave C: fix round 2 dispatched (small): release a half-provisioned sandbox on cancellation (intake.py `except BaseException`), a test pinning the non-cap TimeoutError path, AGENTS.md wording (cap must stay below SANDBOX_TTL_S; cancellation is cooperative).
Fix wave C: minor (deferred): the cap is cooperative (a task that swallows cancellation still blocks; a hard guarantee needs a process-level watchdog); no jitter on the infra pause; the default 30.0 is not asserted and slot-holding during the pause is untested; 0.3 s test has a timing margin; a bad RUN_TIMEOUT_S under bench.run becomes one crashed row per spec rather than an up-front CLI error; `{timeout_s:g}` rounds to six digits.
Task 7: Flash report generated (docs/results/2026-09-30-week2a-comparison.md) with a hand-written "What happened" section; run log completed with totals (about $5.16); README "Results so far" added; Obsidian note updated. Spend correction: earlier messages said about $5.75; the sum of recorded rows plus event-log estimates is about $5.16.
Fix wave C: fix round 2 (875af3f): intake releases a half-provisioned sandbox on cancellation (RED: two tests failed on `env.closed`); transport-timeout test pinned (mutation check: replacing `cap.expired()` with True fails it); AGENTS.md wording. Controller read the diff. Cherry-picked as a2a4420 + a1ad929.
Merged state (HEAD a1ad929): `uv run pytest -q` → 394 passed; bench.validate ok ×5; agents-cli lint exit 0.
Task 7: complete (commit a6784ac "docs: first multi-agent vs single-agent comparison": report, run log, README, spec §19 block, AGENTS.md status, result files incl. results/smoke and results/aborted). A fact-check of the documents against the raw data was dispatched afterwards.
Task 7: docs fact-check (sonnet) against the raw run data: generated report regenerates byte-identical; all counts, costs, times and the spec §19 block verified. 2 wrong, 5 imprecise, all fixed by the controller: "five easy tasks" (three easy, two medium) in the README and the report; "13 to 18 probing calls" (20 to 22 calls before the first edit, 13 to 17 shell, 11 to 15 on fromisoformat); planner/reviewer call counts needed "non-trap tasks, counting the structured-answer call"; the 429 attempts took under a minute, not seconds; running total $4.41 not $4.42; 13:31 not 13:30.
