# Week 3B: Replay UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Design approved by the owner on 2026-10-01 (commit `9aa0178`), with the recommended answer to each of its open questions (design §15: the repository goes on the owner's personal account, private until the go-public decision; the seven runs of design §6.2; no synthetic loop; caps in the manifest; `REPLAY_REDACT` created at go-live). This plan is written for those answers and awaits owner review.

**Goal:** Publish a static, $0 replay site that plays seven recorded Week 2B runs (multi-agent and single-agent) step by step: the graph lighting up, the tool calls, the real diff and test run, the reviewer's verdict and the cost climbing. Nothing private reaches a published file, and no held-out task is ever opened.

**Architecture:** `bench/replay.py` converts a run's `events.jsonl`, `record.json` and results row into one replay JSON file. It builds the file field by field from an allowlist, rewrites host paths, sandbox names, container ids and the project id, cuts long strings, and refuses to write when `bench/replay_check.py` (standard library only) finds a leak. It also exports both graphs from the code to `web/src/graph/graphs.json`. A curated manifest, `web/replays.yaml`, names the seven runs and their captions; the build writes `web/public/replays/<run-id>.json` and `index.json`. The site in `web/` (Vite, React, TypeScript, Tailwind v4, React Flow 12, `react-diff-view`) reads those files with no backend. Its player is a pure `stateAt(replay, graph, t)` that every component renders from. A GitHub Actions workflow builds, tests and deploys the site to GitHub Pages once the owner has put the repository on GitHub.

**Tech Stack:** Python 3.12, `uv`, google-adk 2.8.0 (unchanged; its `Event` model parses the logs), PyYAML (already a dependency), pytest. Node 24 (active LTS), npm, Vite, `@vitejs/plugin-react`, TypeScript, React, `@xyflow/react`, `react-diff-view`, `tailwindcss` with `@tailwindcss/vite`, Vitest with jsdom and Testing Library, Playwright (Chromium). GitHub Actions and GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-10-01-week3b-replay-ui-design.md` (this plan implements it); parent spec `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` §2, §11, §15, §19.

## How to read the tasks

As in Week 2C, this plan fixes what must be exact and leaves the implementation to the implementer, test-first. Fixed: names, signatures, schema fields, CLI flags, routes, component names, fixed strings and the tests that must exist. A Python test listed here must exist under that name; a Vitest or Playwright test must exist with that `it(...)` title. Its code is the implementer's. **[OWNER REVIEW]** marks a step where the owner approves content (the captions) before it is committed. **[OWNER APPROVAL]** marks a step that creates a GitHub repository, pushes, changes a repository's visibility or settings, or stores a secret; it needs the owner's explicit approval in the session, given at the time. Nothing in this plan spends credits.

## Global Constraints

- Python 3.12 via `uv` only (`uv run ...`). Never call `pip`.
- Node 24 (`web/.nvmrc` holds `24`; `package.json` `engines.node` is `">=24 <25"`). npm only. `web/package-lock.json` is committed, and CI installs with `npm ci`.
- Packages are added only in Task 5, in one go (exact list there). Any later dependency change is a separate commit approved by the controller, and it updates the lockfile.
- Work on branch `week3b-replay-ui`. Never commit to `main` directly.
- $0: no model call, no GCP call, no cloud resource. Network is used only for Task 5's `npm install`, Task 10's `npx playwright install chromium`, and Task 12's read-only lookup of action release SHAs. GitHub is otherwise touched only at Task 14's **[OWNER APPROVAL]** steps.
- Unchanged: the bench and baseline graph edges, prompts, model names, `RUN_BUDGET_USD=1.00`, `MAX_TOOL_CALLS_PER_RUN=75`, `RUN_TIMEOUT_S=1500`, `SANDBOX_TTL_S=1800`, 3 test-fix returns, 2 review returns, the driver, scoring, `bench.run`, and every Week 2 result and report.
- Week 3A owns `app/environment/`, `deployment/`, the Makefile's cloud targets and the deploy staging. This plan changes none of them, and none of the scaffold workflows (`pr_checks.yaml`, `staging.yaml`, `deploy-to-prod.yaml`).
- Held-out seal: `bench/tasks/*-h[0-9][0-9]/` is never opened, printed or diffed. `bench.replay` refuses a held-out id by its name before opening any file and never calls `app.task_store.list_tasks()`. Review diffs exclude `':(exclude,glob)bench/tasks/*-h[0-9][0-9]/**'`. Run pytest with `--tb=no` when held-out tasks are collected.
- Replay schema `1`. Files: `web/public/replays/<run-id>.json`, `web/public/replays/index.json`, `web/src/graph/graphs.json`. Manifest: `web/replays.yaml`.
- Caps (design §5.4): tool-call and tool-result strings 4,000 characters (3,000 head, 800 tail); lists 100 items; `plan`, `claim`, `review` strings and model `text` 4,000; decline reason 1,000; status `message` first line, 300; issue body 8,000; unified diff 60,000; test output tail 8,000; replay file warning above 300 KB, refusal above 1 MB.
- Cut marker: `\n[... <N> characters cut ...]\n`, with `N` written with comma thousands separators. A cut list ends with the item `[... <N> more items ...]`.
- Manifest caps for the cost meter: `cost_usd: 1.00`, `tool_calls: 75`, `wall_clock_s: 3000` (Week 2B ran with `RUN_TIMEOUT_S=3000`).
- The seven runs: `md-001-multi-flash-r1-20261001T062611Z`, `md-001-single-flash-r1-20261001T084838Z`, `sr-002-multi-flash-r1-20261001T062611Z`, `md-005-multi-flash-r1-20261001T062611Z`, `tc-005-single-flash-r1-20261001T084838Z`, `sr-003-multi-flash-r1-20261001T062611Z`, `sr-003-single-flash-r1-20261001T084838Z`.
- Leak-check rules (design §5.5): `host-path`, `project`, `gcp-resource`, `github-token`, `google-key`, `jwt`, `private-key`, `other-token`, `high-entropy`, `email`, `sandbox`. `allow` can never clear `host-path`, `project` or `private-key`. Exact values come from `GOOGLE_CLOUD_PROJECT` and `REPLAY_REDACT` (comma-separated).
- Routes: `#/` and `#/run/<run-id>[?t=<seconds>]`. Vite `base: './'`. Speeds `1, 2, 5, 10, 25, 50`, default `10`. "Skip long waits" is on by default and caps a wait at 1.5 s of real time.
- Production CSP, exactly: `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'`. Never `'unsafe-inline'`.
- No `dangerouslySetInnerHTML`, no Markdown rendering, no request to another origin, no cookies, storage or analytics in the site.
- pytest never asserts on what a model wrote, and never calls a model, GCP or GitHub. Tests over committed replays check structure, leaks and numbers that come from `record.json`.
- Commits: plain messages, owner identity only, no `Co-Authored-By` or other AI attribution. Never bypass commit signing.
- Before each commit, Python: `uv run ruff format <files> && uv run ruff check --fix <files>`; `agents-cli lint` passes (write its output to a file and check the exit status); bare `uv run pytest -q` passes. Web, in `web/`: `npm run typecheck` and `npm test` pass.
- Update `AGENTS.md` in the same commit when a task adds commands, env vars, directories, wiring or rules.

## Review Focus

These are the five ways this work can fail that the design implies but no task's ordinary tests would exercise. Each test below is added to its owning task's test list.

1. **A field nobody listed reaches a public file.** Expected: a replay holds only the fields of design §4.2 to §4.4. Anything a later ADK adds never appears, and neither does any field design §5.3 drops. Test: Task 3 `test_unknown_event_fields_never_reach_the_replay`. Each of these carries a sentinel string, and the sentinel must appear nowhere in the output: an unknown top-level event field, `custom_metadata`, an unknown `actions` key, an unknown state-delta key, `error_message`, `thought_signature`, `sandbox_id`, `baseline_sha`, `invocation_id`.
2. **The check passes without having checked.** Expected: a token that a cap cut in half is still refused. A check run with no exact values says so instead of passing quietly. A report never repeats what it found. Tests: Task 3 `test_a_token_cut_in_half_by_a_cap_is_still_refused`; Task 1 `test_cli_says_when_exact_value_rules_are_off`, `test_reports_never_contain_the_matched_text`.
3. **A held-out task is read through a side door.** Expected: the only held-out data the converter ever touches is a results row's `task_id`, and it never calls `list_tasks()`. Tests: Task 4 `test_heldout_rows_in_results_files_are_skipped_unread`, `test_list_tasks_is_never_called`.
4. **The page turns data into behaviour.** Expected: replay text never becomes markup or a link, and the URL hash never decides what is fetched. Tests: Task 8 `renders markup in replay text as plain text`; Task 9 `never fetches a run id that is not in the index`.
5. **The publishing workflow can do more than publish.** Expected: only the deploy job holds `pages: write` and `id-token: write`; nothing holds `contents: write`; a pull request never deploys; every action is pinned to a commit. Tests: Task 12 `test_only_the_deploy_job_has_pages_and_id_token`, `test_no_job_has_contents_write`, `test_pull_requests_never_deploy`, `test_every_action_is_pinned_to_a_full_sha`.

---

## File Structure

```
bench/
  replay_check.py            NEW  leak check, standard library only (Task 1)
  replay.py                  NEW  graphs (Task 2), converter (Task 3), manifest, build and check (Task 4)
  progress.py                MOD  _detail becomes call_detail (Task 3)
tests/unit/
  test_replay_check.py       NEW  (Task 1)
  test_replay_graphs.py      NEW  (Task 2)
  replay_events.py           NEW  builders for synthetic event logs with real ADK Events (Task 3)
  test_replay.py             NEW  (Task 3)
  test_replay_build.py       NEW  (Task 4)
  test_replay_files.py       NEW  committed replays (Task 4)
  test_pages_workflow.py     NEW  (Task 12)
web/
  package.json, package-lock.json, .nvmrc, tsconfig.json, index.html     NEW (Task 5)
  vite.config.ts             NEW  base, plugins, Vitest settings (Task 5)
  plugins/csp.ts, plugins/csp.test.ts                                     NEW (Task 5)
  playwright.config.ts       NEW  (Task 10)
  replays.yaml               NEW  manifest and captions (Task 4; captions confirmed in Task 11)
  public/favicon.svg         NEW  (Task 5)
  public/replays/            NEW  index.json and seven replays, written by bench.replay build (Task 4)
  src/main.tsx, App.tsx, routes.ts, routes.test.ts, styles.css            NEW (Task 5; App.tsx completed in Task 9)
  src/replay/types.ts, parse.ts, parse.test.ts                            NEW (Task 5)
  src/replay/state.ts, state.test.ts, clock.ts, clock.test.ts, usePlayer.ts   NEW (Task 6)
  src/graph/graphs.json      NEW  written by bench.replay graphs (Task 2)
  src/graph/layout.ts, layout.test.ts, GraphView.tsx, GraphView.test.tsx  NEW (Task 7)
  src/panels/*.tsx, *.test.tsx                                            NEW (Task 8)
  src/pages/RunList.tsx, RunPage.tsx, NotFound.tsx, *.test.tsx; src/data.ts, data.test.ts   NEW (Task 9)
  src/committed.test.ts      NEW  (Task 10)
  src/test/setup.ts, factories.ts, fixtures/   NEW  (Task 5)
  e2e/smoke.spec.ts          NEW  (Task 10)
.github/workflows/pages.yaml NEW  (Task 12)
docs/adr/0007-replay-first-public-demo.md   NEW (Task 13)
pyproject.toml               MOD  [tool.codespell] skip (Task 1)
.gitignore                   MOD  web/test-results/, web/playwright-report/ (Task 5)
AGENTS.md                    MOD  (Tasks 1, 2, 4, 5, 10, 12, 13)
docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md   MOD §19 (Task 13)
README.md                    MOD  replay link (Task 14)
```

The root `.gitignore` ignores any directory named `lib/`, `build/`, `parts/` or `env/`. No directory under `web/` uses those names.

## Order, parallelism and file ownership

| Task | Depends on | Owns (creates or is the only one to modify) | Can run in parallel with |
|---|---|---|---|
| 1 Leak check | — | `bench/replay_check.py`, `tests/unit/test_replay_check.py`, `pyproject.toml` | 2, 5 |
| 2 Graph export | — | `bench/replay.py` (created), `tests/unit/test_replay_graphs.py`, `web/src/graph/graphs.json` | 1, 5 |
| 3 Converter core | 1, 2 | `bench/replay.py` (conversion), `bench/progress.py`, `tests/unit/replay_events.py`, `tests/unit/test_replay.py` | 5, 6, 7, 8, 12 |
| 4 Manifest, build, the seven replays | 3 | `bench/replay.py` (manifest, build, check), `tests/unit/test_replay_build.py`, `tests/unit/test_replay_files.py`, `web/replays.yaml`, `web/public/replays/` | 6, 7, 8, 9, 12 |
| 5 Site scaffold, types, parser | — | the `web/` files in its Files list (`package.json`, the lockfile, configs, `plugins/`, `src/main.tsx`, `src/App.tsx` shell, `src/routes.ts`, `src/styles.css`, `src/replay/types.ts`, `src/replay/parse.ts`, `src/test/`), `.gitignore` | 1, 2, 3 |
| 6 Player core | 5 | `web/src/replay/state.ts`, `clock.ts`, `usePlayer.ts` and their tests | 3, 4, 7, 8, 12 |
| 7 Graph view | 2, 5 | `web/src/graph/layout.ts`, `GraphView.tsx` and their tests | 3, 4, 6, 8, 12 |
| 8 Panels | 5 | `web/src/panels/` | 3, 4, 6, 7, 12 |
| 9 Pages and routing | 6, 7, 8 | `web/src/pages/`, `web/src/data.ts`, `web/src/App.tsx` | 4, 12 |
| 10 Committed replays in the viewer; smoke test | 4, 9 | `web/src/committed.test.ts`, `web/playwright.config.ts`, `web/e2e/` | 12 |
| 11 Captions **[OWNER REVIEW]** | 10 | `web/replays.yaml` (captions), the rebuilt replays | — |
| 12 Pages workflow | 1, 5 | `.github/workflows/pages.yaml`, `tests/unit/test_pages_workflow.py` | 3, 4, 6, 7, 8, 9, 10 |
| 13 Docs close-out | 11, 12 | `docs/adr/0007-replay-first-public-demo.md`, parent spec §19 | — |
| 14 Go live **[OWNER APPROVAL]** | 13 | `README.md` | — |

Parallel groups: **A** = 1, 2, 5 (start at once). **B** = 3, 6, 7, 8, 12 (Task 7 also waits for Task 2). **C** = 4, 9. **D** = 10. Then 11, 13 and 14 in order.

Shared files: `bench/replay.py` is written by Tasks 2, 3 and 4 in sequence. `web/src/App.tsx` is created as a shell by Task 5 and completed by Task 9. `AGENTS.md` is touched by Tasks 1, 2, 4, 5, 10, 12 and 13; each adds only its own rows, and the controller merges them.

Interfaces that parallel tasks agree on before they start: the replay types of Task 5 (`web/src/replay/types.ts`), which mirror Task 3's output; `graphs.json`'s shape (Task 2); `ReplayState` and `FeedItem` (Task 5), which Task 6 produces and Tasks 7, 8 and 9 consume.

---

### Task 1: Leak check

**Files:**
- Create: `bench/replay_check.py`, `tests/unit/test_replay_check.py`
- Modify: `pyproject.toml` (`[tool.codespell] skip` gains `./web/node_modules/*,./web/dist/*,./web/public/replays/*,./web/test-results/*,./web/playwright-report/*`; the replays hold model text, as `runs/` does), `AGENTS.md` (environment table: `REPLAY_REDACT`)

**Interfaces:**
- Produces:

  ```python
  RULES = ("host-path", "project", "gcp-resource", "github-token", "google-key", "jwt",
           "private-key", "other-token", "high-entropy", "email", "sandbox")
  UNCLEARABLE = frozenset({"host-path", "project", "private-key"})
  ALLOWED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net", "example.invalid"})
  EXACT_RULES_OFF = "exact-value rules off: GOOGLE_CLOUD_PROJECT and REPLAY_REDACT are unset"

  @dataclass(frozen=True)
  class Hit:
      file: str     # the file name as given
      path: str     # JSON path: "$", "$.steps[3].result.stdout", '$.run["odd key"]'
      rule: str
      def __str__(self) -> str      # "<file>: <path>: <rule>"

  def exact_values(environ: Mapping[str, str] = os.environ) -> tuple[str, ...]
      # GOOGLE_CLOUD_PROJECT, then each comma-separated REPLAY_REDACT entry; stripped, empty ones dropped
  def scan_text(text: str, exact: Sequence[str] = ()) -> list[str]
      # the rules hit, in RULES order, each once
  def scan_value(value: object, *, file: str, exact: Sequence[str] = (),
                 allow: Collection[tuple[str, str]] = ()) -> list[Hit]
      # every string and every object key, with its JSON path
  def check_paths(paths: Sequence[Path], *, exact: Sequence[str] = ()) -> list[Hit]
      # directories expand to their *.json; `allow` is read from index.json entries (Task 4)
  def main(argv: Sequence[str] | None = None) -> int
  ```

- CLI: `python3 bench/replay_check.py [paths ...]`, default `web/public/replays`. It prints `EXACT_RULES_OFF` first when `exact_values()` is empty, one `Hit` per line, then `<n> files checked, <k> problems`. Exit 0 when clean, 1 on any hit, 2 when a file cannot be read or is not JSON (the message names the file only).

**Required behaviour**

1. Standard library only, with no import from `app` or `bench`, so CI can run it with a bare `python3`.
2. The rules match design §5.5, table by table:
   - `host-path`: `/Users/`, `/home/`, `/root/`, `/var/folders/`, `C:\Users\`.
   - `project`: each exact value as a whole word, case-insensitive.
   - `gcp-resource`: `projects/` followed by anything but `<project>`, and `.iam.gserviceaccount.com`.
   - `github-token`: `gh[pousr]_` followed by 20 or more of `[A-Za-z0-9]`, and `github_pat_`.
   - `google-key`: `AIza` followed by 35 of `[0-9A-Za-z_-]`, and `ya29.`.
   - `jwt`: `eyJ` plus base64url, then `.eyJ`.
   - `private-key`: `-----BEGIN` … `PRIVATE KEY-----`.
   - `other-token`: `AKIA` followed by 16 of `[0-9A-Z]`; `xox[abprs]-`; `sk-` followed by 20 or more letters or digits.
   - `high-entropy`: a run of 32 or more of `[A-Za-z0-9+/=_-]` that has upper-case letters, lower-case letters and digits, with Shannon entropy above 4.0 bits per character.
   - `email`: a local part starting with a letter or digit, and a domain with a dot and an alphabetic TLD of 2 or more letters, outside `ALLOWED_EMAIL_DOMAINS`.
   - `sandbox`: `itp-` followed by 12 hex digits, or a 64-hex word.
3. Not hits: `+@pytest.mark.parametrize`, `pipeline@localhost`, 40-hex git SHAs, `index 81ea946..68e5b97`, `/workspace/...`, `/tmp/...`, `<sandbox>`, `<container>`, `<project>`, `<redacted>`, `<repo>`, `<host-path>`.
4. `allow` clears a hit only when its exact `(path, rule)` pair is listed and the rule is not in `UNCLEARABLE`.
5. `check_paths` decodes each file and scans every string (the final-file pass). It also scans the raw file text with the `UNCLEARABLE` rules only, under path `$`.
6. Nothing printed, raised or returned contains the matched text.

**Tests** (`tests/unit/test_replay_check.py`)

- `test_each_rule_catches_its_sample` (parametrised over `RULES`, one planted sample each; `project` set through `exact`).
- `test_ordinary_content_is_clean` (a realistic replay fixture holding the items of behaviour 3).
- `test_reports_never_contain_the_matched_text` (Review Focus 2; covers `str(Hit)`, CLI output and exceptions).
- `test_allow_clears_one_path_and_one_rule_only`; `test_unclearable_rules_ignore_allow`.
- `test_object_keys_are_scanned`.
- `test_exact_values_come_from_both_variables`.
- `test_cli_says_when_exact_value_rules_are_off` (Review Focus 2).
- `test_cli_exit_codes` (0, 1 and 2).
- `test_module_uses_only_the_standard_library` (every import in the module's AST is in `sys.stdlib_module_names`).

- [ ] **Step 1:** Write the tests. Run them; capture the failures (RED).
- [ ] **Step 2:** Implement until green (GREEN).
- [ ] **Step 3:** `pyproject.toml` codespell skip. `AGENTS.md` environment table: `REPLAY_REDACT` (unset by default; comma-separated exact values, such as the project id and project number, that `bench/replay_check.py` refuses in replay files; set as an Actions secret at go-live). Lint, full suite.
- [ ] **Step 4:** Commit: `feat(bench): leak check for replay files`.

---

### Task 2: Graph export

**Files:**
- Create: `bench/replay.py`, `tests/unit/test_replay_graphs.py`, `web/src/graph/graphs.json`
- Modify: `AGENTS.md` (command)

**Interfaces:**
- Produces:

  ```python
  SCHEMA = 1
  GRAPHS_PATH = Path("web/src/graph/graphs.json")
  def export_graphs() -> dict
  def main(argv: Sequence[str] | None = None) -> int    # subcommand `graphs` now; `build`, `check` in Task 4
  ```

- `graphs.json` (design §5.6):

  ```
  {"schema": 1, "graphs": {"multi": GraphDef, "single": GraphDef}}
  GraphDef = {"source": "app.pipeline.build_workflow" | "app.baseline.build_baseline_workflow",
              "nodes": [{"id": str, "kind": "start" | "llm" | "function" | "router"}],
              "edges": [{"from": str, "to": str, "route": str | null}]}
  ```

- CLI: `uv run python -m bench.replay graphs [--out PATH] [--check]`. `--check` exits 1 with `graphs.json differs from the code; run: uv run python -m bench.replay graphs`.

**Required behaviour**

1. Builds `build_workflow(RoleModels(...))` (bench graph, `live=False`) and `build_baseline_workflow(...)` with a placeholder `BaseLlm` defined in `bench/replay.py` that raises if called. No model client is created.
2. Nodes: `START` first, then in order of first appearance in the edges. `kind`: `start` for `START`; `llm` for an agent node; `router` for a function node named `route_*`; `function` otherwise.
3. Edges: in the code's order, one entry per route of a routed edge.
4. JSON: `indent=2`, UTF-8, a trailing newline; byte-stable.

**Tests** (`tests/unit/test_replay_graphs.py`)

- `test_export_matches_the_pinned_edges` (the same edge lists as `tests/unit/test_graph_edges.py`).
- `test_node_kinds`.
- `test_committed_graphs_match_the_code`.
- `test_check_fails_on_a_stale_file`.
- `test_export_never_calls_a_model`.

- [ ] **Step 1:** Tests (RED); implement; green.
- [ ] **Step 2:** `uv run python -m bench.replay graphs`; commit the file; `--check` passes.
- [ ] **Step 3:** `AGENTS.md` commands: `uv run python -m bench.replay graphs [--check]`. Lint, full suite.
- [ ] **Step 4:** Commit: `feat(bench): export both graphs for the replay site`.

---

### Task 3: Converter core

**Files:**
- Create: `tests/unit/replay_events.py` (builders for synthetic logs made of real `google.adk.events.Event` objects, dumped as the driver dumps them, `model_dump_json(exclude_none=True)`, in the shapes of design §3), `tests/unit/test_replay.py`
- Modify: `bench/replay.py`, `bench/progress.py` (`_detail` becomes `call_detail(name: str, args: dict) -> str`; `format_event` uses it; `tests/unit/test_progress.py` passes unchanged)

**Interfaces:**
- Consumes: Task 1's `scan_value`, `Hit`; Task 2's `export_graphs` output; `app.schemas.RunRecord`; `app.task_store.TaskSpec`; `app.driver.PUBLIC_AGENT`, `PUBLIC_INFRA`, `PUBLIC_CRASH`.
- Produces:

  ```python
  class ReplayRefused(Exception):     # str() is one fixed message; never holds content
      hits: list[Hit]                 # set for "leak check failed", else []

  @dataclass(frozen=True)
  class RunInputs:
      run_id: str
      events_path: Path
      record: RunRecord
      row: dict            # the results row
      task: TaskSpec
      caps: dict           # {"cost_usd": float, "tool_calls": int, "wall_clock_s": int}

  def convert_run(inputs: RunInputs, *, graphs: dict, project: str | None, redact: Sequence[str],
                  repo_root: Path, home: Path) -> dict
  def scrub_text(text: str, *, project: str | None, redact: Sequence[str],
                 repo_root: Path, home: Path) -> str
  def cut_text(text: str, limit: int, *, head: int, tail: int) -> str
  def dumps(replay: dict) -> str      # indent=2, ensure_ascii=False, fixed key order, trailing newline
  ```

- The replay dict, exactly as design §4.2 and §4.3:
  - Top level: `schema`, `run`, `caps`, `outcome`, `steps`.
  - `run`: `run_id`, `task_id`, `repo`, `category`, `difficulty`, `tempting`, `levers`, `system`, `graph`, `preset`, `models`, `prompt_version` (default `"2a"`), `mode`, `recorded_at` (`YYYY-MM-DDTHH:MM:SSZ`), `issue` (`title`, `body`).
  - `outcome`: `outcome`, `failure_kind`, `reason`, `resolved`, `cost_usd`, `tool_calls`, `tokens_in`, `tokens_out`, `duration_s`, `test_attempts`, `review_rounds`, `audit`.
  - Every step: `i`, `t`, `kind`, `node`, `visit`, `cost_usd`, `tool_calls`. By kind:
    - `node`: `from`, `via`, `message`.
    - `model_call`: `agent`, `model`, `tokens_in`, `tokens_out`, `text`, `calls` (each `id`, `tool`, `label`, `args`).
    - `tool_result`: `call`, `tool`, `error`, `result`.
    - `plan`: `value` (a `Plan`).
    - `claim`: `agent` (`coder` or `solo`), `value` (a `PatchResult` or `SoloResult`).
    - `diff`: `value` (`files`, `insertions`, `deletions`, `unified_diff`, `cut`).
    - `tests`: `value` (a `TestReport`).
    - `review`: `value` (a `Review`).
    - `stop`: `text`.
    - `outcome`: no extra fields.
- Fixed refusal messages (Task 4 prefixes them with `entry <n>: `): `running totals do not match the record`, `node not in the graph`, `tool result without a call`, `record and results row disagree`, `leak check failed`.

**Required behaviour**

1. Event-to-step mapping as design §4.4, rules 1 to 7.
   - `t` is rounded to 0.01 s and never decreases.
   - Call ids are renumbered `c1`, `c2`, ... across the whole run, `set_model_response` included.
   - `label` is `f"{tool} {call_detail(tool, args)}".rstrip()`, and `answer` for `set_model_response`.
   - `tokens_out` is candidates plus thoughts.
   - Running `tool_calls` counts `function_response` parts.
2. The outcome step is at `max(last step t, record.duration_s)`. Its `node` is the last step's node.
3. Public reasons as design §4.5. "Written by `report_failure`" means the events hold `report_failure`'s output event with an `outcome` state delta. A row with `crashed: true` gets `PUBLIC_CRASH`.
4. Allowlist construction (design §5.3): only the fields listed above are read from an event. Nothing is copied wholesale.
5. Rewrites, in this order (design §5.3):
   1. `repo_root` → `<repo>`, then `home` → `~`.
   2. Other host paths → `<host-path>/`.
   3. `itp-` names → `<sandbox>`; 64-hex ids → `<container>`.
   4. `project` (whole word, case-insensitive) → `<project>`; `projects/<x>` → `projects/<project>`; each `redact` value → `<redacted>`.
   5. `runs/<run-id>/<file>` → `<file>`.
   6. Control characters other than tab and newline, and Unicode format characters → `<U+` plus the code point in upper-case hex, at least four digits, plus `>` (for example `<U+202E>`).
   `convert_run` callers pass `repo_root = Path(bench.replay.__file__).resolve().parents[1]` and `Path.home()`.
6. Caps from the Global Constraints, applied after the rewrites. The leak check (`scan_value`, exact values `project` and `redact`) runs on every string after the rewrites and before the cuts, and again on the finished dict. Any hit raises `ReplayRefused("leak check failed")` with the hits, whose `file` is `<run-id>.json`.
7. Invariants (design §4.4) raise `ReplayRefused` with the fixed messages:
   - final running cost within $0.0002 of `record.cost_usd`;
   - running tool calls equal to `record.tool_calls`;
   - every node in the graph named by `system`;
   - every `tool_result` follows its call;
   - record and row agree on `task_id`, `run_id` and `outcome`.
8. `dumps(convert_run(...))` is byte-identical across calls.

**Tests** (`tests/unit/test_replay.py`, synthetic logs from `tests/unit/replay_events.py`, synthetic tasks via `tests/fakes.py::make_bench_task`)

- Mapping:
  - `test_function_node_visit_becomes_a_node_step_with_its_status_line`
  - `test_from_and_via_follow_the_previous_visit_and_its_route`
  - `test_second_visit_counts_visits` (a `run_tests` → `fail` → `coder@2` log)
  - `test_model_call_renumbers_call_ids_and_labels_them`
  - `test_tokens_out_counts_candidates_and_thoughts`
  - `test_tool_result_carries_error_and_skips_set_model_response`
  - `test_answers_become_plan_claim_and_review_steps` (multi and single)
  - `test_diff_comes_from_unified_diff_never_diff_text`
  - `test_running_cost_and_tool_calls`
  - `test_error_event_becomes_a_stop_with_the_public_reason`
- Time:
  - `test_outcome_step_never_precedes_the_last_event` (last event at 541.85, `duration_s` 541.78)
  - `test_step_times_never_decrease`
  - `test_a_log_that_ends_without_an_outcome_still_gets_one` (the wall-clock shape: last event at 788 s, `duration_s` 3000)
- Refusals: `test_totals_that_differ_from_the_record_are_refused`; `test_unknown_node_is_refused`; `test_tool_result_without_a_call_is_refused`; `test_record_and_row_disagreement_is_refused`.
- Reasons: `test_public_reasons` (parametrised over every row of design §4.5, plus crashed).
- Scrubbing and caps:
  - `test_rewrites_in_order` (one case per rewrite)
  - `test_planted_project_id_is_rewritten_and_never_published` (`project` passed in, as the converter reads it from `GOOGLE_CLOUD_PROJECT`)
  - `test_caps_and_markers` (parametrised over every cap)
  - `test_diff_cut_keeps_true_counts`
- Review Focus 1: `test_unknown_event_fields_never_reach_the_replay`.
- Review Focus 2: `test_a_token_cut_in_half_by_a_cap_is_still_refused`.
- Determinism: `test_output_is_byte_identical_when_built_twice`.
- `test_call_detail_matches_the_progress_lines`.

- [ ] **Step 1:** `call_detail` rename; `test_progress.py` still green.
- [ ] **Step 2:** Mapping and time tests (RED); implement; green.
- [ ] **Step 3:** Reasons, scrubbing, caps and invariants tests (RED); implement; green.
- [ ] **Step 4:** Lint, full suite.
- [ ] **Step 5:** Commit: `feat(bench): replay converter with allowlisted fields, rewrites and caps`.

---

### Task 4: Manifest, build, check and the seven replays

**Files:**
- Create: `tests/unit/test_replay_build.py`, `tests/unit/test_replay_files.py`, `web/replays.yaml`, `web/public/replays/index.json`, `web/public/replays/<run-id>.json` (seven)
- Modify: `bench/replay.py`, `AGENTS.md`

**Interfaces:**
- Produces:

  ```python
  MANIFEST_PATH = Path("web/replays.yaml")
  OUT_DIR = Path("web/public/replays")
  RUN_ID = re.compile(r"^(md|sr|tc)-\d{3}-(multi|single)-(flash|pro|mixed)-r\d+-\d{8}T\d{6}Z$")

  @dataclass(frozen=True)
  class ManifestEntry:
      run_id: str
      caption: str
      pair: str | None
      caps: dict | None
      allow: tuple[tuple[str, str], ...]     # (JSON path, rule)

  @dataclass(frozen=True)
  class Manifest:
      note: str
      caps: dict
      replays: tuple[ManifestEntry, ...]

  def load_manifest(path: Path) -> Manifest
  def find_row(results_dir: Path, run_id: str) -> dict
  def build(manifest_path: Path, runs_dir: Path, results_dir: Path, out_dir: Path) -> list[str]
      # the lines it prints: "<run-id>: <n> steps, <k> KB", plus warnings
  ```

- Manifest (`web/replays.yaml`):
  - `note: str`
  - `caps: {cost_usd, tool_calls, wall_clock_s}`
  - `replays: [{run_id, caption, pair?, caps?, allow?: [{path, rule}]}]`
- `index.json` (design §4.6): `schema`, `note`, `replays[]`. Each entry has `run_id`, `file`, `caption`, `pair` (null when none), `task_id`, `issue_title`, `repo`, `category`, `system`, `preset`, `outcome`, `failure_kind`, `resolved`, `cost_usd`, `tool_calls`, `duration_s`, `recorded_at`, and `allow` only when non-empty. `allow` is an addition this plan makes: CI's standard-library check cannot read YAML. It keeps schema 1.
- CLIs (design §5.1):

  ```
  uv run python -m bench.replay build [--manifest web/replays.yaml] [--runs-dir runs] [--results-dir results] [--out web/public/replays]
  uv run python -m bench.replay check [paths ...]
  ```

  Both load `.env` (`dotenv.load_dotenv()`) for `GOOGLE_CLOUD_PROJECT` and `REPLAY_REDACT`. `check` delegates to `bench.replay_check`.
- Fixed refusal messages, `<n>` being the entry's 1-based position in the manifest:
  - `entry <n>: not a dev bench run`
  - `entry <n>: run files missing`
  - `entry <n>: record does not match the run id`
  - `entry <n>: no single results row`
  - `entry <n>: results row is not a scored dev run`
  - `entry <n>: pair is not the same task on the other system`
  - `entry <n>: caption must be one line of 1 to 140 characters`
  - `entry <n>: replay over 1 MB`
  - `entry <n>: ` followed by each Task 3 message
- Fixed warning: `entry <n>: replay is <k> KB (over 300 KB)`.

**Required behaviour**

1. Checks in design §5.2's order, per entry:
   1. `RUN_ID` and no `-hNN`, before any file is opened.
   2. `bench.probes.dev_task(task_id)`.
   3. `runs/<run-id>/record.json` and `events.jsonl` exist; the record's `task_id` equals the run id's task; its `mode` is `bench`.
   4. Exactly one row with this `run_id` in `results/*.json`, with `split == "dev"`, `crashed` false and `infra_retries == 0`. `find_row` skips a row whose `task_id` matches `-h\d\d$` before reading any other key.
   5. A `pair` is another entry for the same task with the other system.
   6. Caption rules.
2. `list_tasks()` is never called.
3. All or nothing: the build writes into a temporary directory and replaces the `*.json` of `--out` only when every entry succeeded. Removing an entry removes its file.
4. Entry caps override the manifest's caps, key by key.
5. Messages and printed lines never hold task text, issue text or matched secrets.

**Tests**

- `test_replay_build.py` (synthetic runs, results files and tasks in `tmp_path`, with `BENCH_TASKS_DIR` set; a guard fails the test on any `open`, `Path.open`, `read_text`, `read_bytes`, `exists`, `is_file`, `is_dir`, `stat`, `iterdir` or `glob` of a path under a `*-hNN` task directory):
  - `test_heldout_run_id_is_refused_before_any_file_is_opened`
  - `test_heldout_task_in_a_record_is_refused`
  - `test_heldout_rows_in_results_files_are_skipped_unread` (Review Focus 3). A held-out row with a sentinel in every field and no `run_id` sits next to the dev row; rows are loaded with an `object_pairs_hook` that records key access. The build succeeds, only that row's `task_id` is read, and the sentinel appears in no file, line or message.
  - `test_list_tasks_is_never_called` (Review Focus 3)
  - `test_run_id_shapes_are_refused` (a probe variant, `-retry1`, a live id, an unknown system)
  - `test_missing_run_files_are_refused`
  - `test_record_must_match_the_run_id_and_be_bench_mode`
  - `test_results_row_must_be_unique_scored_and_dev` (none, two, a non-dev split, crashed, `infra_retries` 1)
  - `test_pair_must_be_the_same_task_on_the_other_system`
  - `test_caption_rules`
  - `test_refusal_messages_name_only_the_entry_position`
  - `test_build_is_all_or_nothing`
  - `test_removing_an_entry_unpublishes_its_file`
  - `test_index_follows_the_manifest_and_copies_the_replay_summary`
  - `test_entry_caps_override_the_manifest_caps`
  - `test_allow_reaches_index_json_and_clears_only_its_rule`
  - `test_size_warning_and_refusal`
  - `test_check_command_reads_the_exact_values_from_dotenv`
- `test_replay_files.py` (the committed files; structural only):
  - `test_committed_replays_pass_the_leak_check`
  - `test_index_and_files_agree`
  - `test_no_heldout_task_and_every_task_is_dev` (by id, then `dev_task`)
  - `test_captions_are_one_line_of_at_most_140_characters`
  - `test_every_replay_is_within_the_size_limits`
  - `test_committed_replays_use_only_known_nodes`

- [ ] **Step 1:** Tests in `test_replay_build.py` (RED); implement; green.
- [ ] **Step 2:** Write `web/replays.yaml`:
  - `note: Seven of the 90 Week 2B dev-split runs (Gemini 3.8 Flash), chosen to show each way a run can end.`
  - the caps of the Global Constraints
  - the seven runs in design §6.2's order, with its draft captions verbatim, and `pair` on the two pairs (`md-001` multi and single; `sr-003` multi and single).
- [ ] **Step 3:** In the checkout that holds `runs/` (it is git-ignored, so a worktree passes `--runs-dir <main checkout>/runs`): `uv run python -m bench.replay build`, then `uv run python -m bench.replay check`. Expect seven lines, each roughly 3 to 100 KB (the design's estimate), and no warning. Build again; `git diff --stat web/public/replays` shows no change.
- [ ] **Step 4:** Write `test_replay_files.py`; green. `uv run python -m bench.replay graphs --check` still passes.
- [ ] **Step 5:** `AGENTS.md`:
  - Commands: `bench.replay build` and `check`, with the note that `build` reads `runs/` and so runs on the owner's machine.
  - Runtime wiring: the converter, the manifest and the output.
  - Hard rule 5 gains: "Never convert or publish a run of a held-out task; `bench.replay` refuses one by its id. A replay is published only after `bench.replay check` passes and the owner has watched it."
  Lint, full suite.
- [ ] **Step 6:** Commit code and data separately: `feat(bench): replay manifest, build and check`, `feat(web): the seven curated replays`.

---

### Task 5: Site scaffold, replay types and parser

**Files:**
- Create:
  - `web/package.json`, `web/package-lock.json`, `web/.nvmrc`, `web/tsconfig.json`, `web/index.html`, `web/vite.config.ts`
  - `web/plugins/csp.ts`, `web/plugins/csp.test.ts`
  - `web/public/favicon.svg`
  - `web/src/main.tsx`, `web/src/App.tsx` (a shell that renders the route's name), `web/src/routes.ts`, `web/src/routes.test.ts`, `web/src/styles.css`
  - `web/src/replay/types.ts`, `web/src/replay/parse.ts`, `web/src/replay/parse.test.ts`
  - `web/src/test/setup.ts` (a minimal `ResizeObserver` and anything else React Flow needs under jsdom), `web/src/test/factories.ts` (`makeReplay`, `makeState`, `makeIndex`)
  - `web/src/test/fixtures/` (`replay-multi.json`, `replay-single.json`, `graphs.json`, `index.json`; synthetic, in the exact schema)
- Modify: `.gitignore` (`web/test-results/`, `web/playwright-report/`), `AGENTS.md`

**Packages**, installed in `web/` in this task only:

```
npm install react react-dom @xyflow/react react-diff-view
npm install -D vite @vitejs/plugin-react typescript @types/react @types/react-dom @types/node tailwindcss @tailwindcss/vite vitest jsdom @testing-library/react @testing-library/dom @playwright/test
```

Each at its current release; `package.json` keeps npm's caret ranges and `web/package-lock.json` is committed. The design's list (design §7.1) gains two development packages: `@testing-library/dom`, which `@testing-library/react` requires as a peer, and `@types/node`, for the config files and the tests that read files. After the install, read `node_modules/react-diff-view/package.json` `peerDependencies`. If they do not include the installed React major, uninstall `react-diff-view` and record in the commit message that Task 8 builds the fallback renderer (design §7.1).

**Interfaces:**
- `package.json`: `"private": true`, `"type": "module"`, `"engines": {"node": ">=24 <25"}`. Scripts:
  - `dev`: `vite`
  - `build`: `vite build`
  - `preview`: `vite preview`
  - `typecheck`: `tsc --noEmit`
  - `test`: `vitest run`
  - `e2e`: `playwright test`
- `vite.config.ts`:
  - `base: './'`
  - plugins `react()`, `tailwindcss()`, `cspMeta()`
  - `test: {environment: 'jsdom', setupFiles: ['src/test/setup.ts'], include: ['src/**/*.test.{ts,tsx}', 'plugins/**/*.test.ts']}`
- `web/plugins/csp.ts`:
  - `export const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'"`
  - `export function cspMeta(): Plugin` with `apply: 'build'`, injecting `<meta http-equiv="Content-Security-Policy" content="<CSP>">` through `transformIndexHtml`.
- `tsconfig.json`: `strict`, `resolveJsonModule`, `jsx: react-jsx`, `noEmit`; it covers `src`, `plugins`, `e2e`, `vite.config.ts` and `playwright.config.ts`.
- `styles.css`: `@import "tailwindcss";`. `main.tsx` imports `@xyflow/react/dist/style.css` and `react-diff-view/style/index.css`, or not the latter after the fallback.
- `web/src/routes.ts`:

  ```ts
  export type Route = { name: "list" } | { name: "run"; runId: string; t: number | null } | { name: "notFound" };
  export function parseHash(hash: string): Route;      // "", "#", "#/" → list; "#/run/<id>[?t=<n>]" with id /^[A-Za-z0-9-]+$/ and n ≥ 0; else notFound
  export function runHash(runId: string, t?: number): string;   // t written with 2 decimals
  ```

- `web/src/replay/types.ts` (mirrors Task 3; exact names):

  ```ts
  export const SUPPORTED_SCHEMA = 1;
  export type GraphId = "multi" | "single";
  export type OutcomeName = "patch_written" | "declined" | "failed" | "pr_opened" | "rejected" | "refused";
  export type FailureKind = "agent" | "budget" | "infra" | "none";
  export interface Caps { cost_usd: number; tool_calls: number; wall_clock_s: number }
  export interface RunInfo { run_id: string; task_id: string; repo: string; category: "bug" | "feature" | "refactor" | "trap";
    difficulty: "easy" | "medium" | "hard"; tempting: boolean; levers: string[]; system: GraphId; graph: GraphId;
    preset: string; models: Record<string, string>; prompt_version: string; mode: "bench"; recorded_at: string;
    issue: { title: string; body: string } }
  export interface RunOutcome { outcome: OutcomeName; failure_kind: FailureKind; reason: string; resolved: boolean;
    cost_usd: number; tool_calls: number; tokens_in: number; tokens_out: number; duration_s: number;
    test_attempts: number; review_rounds: number; audit: string[] }
  interface StepBase { i: number; t: number; node: string; visit: number; cost_usd: number; tool_calls: number }
  export interface ToolCall { id: string; tool: string; label: string; args: Record<string, unknown> }
  export interface NodeStep extends StepBase { kind: "node"; from: string | null; via: string | null; message: string | null }
  export interface ModelCallStep extends StepBase { kind: "model_call"; agent: string; model: string; tokens_in: number;
    tokens_out: number; text: string | null; calls: ToolCall[] }
  export interface ToolResultStep extends StepBase { kind: "tool_result"; call: string; tool: string; error: string | null;
    result: Record<string, unknown> }
  export interface PlanValue { actionable: boolean; decline_reason: string | null; summary: string;
    files_to_inspect: string[]; steps: string[]; test_strategy: string }
  export interface PatchClaim { summary: string; files_changed: string[]; tests_added: string[]; notes: string }
  export interface SoloClaim { declined: boolean; decline_reason: string | null; summary: string; files_changed: string[] }
  export interface DiffValue { files: string[]; insertions: number; deletions: number; unified_diff: string; cut: boolean }
  export interface TestsValue { passed: boolean; exit_code: number; failed_tests: string[]; output_tail: string; duration_s: number }
  export interface ReviewComment { file: string; line: number | null; severity: "blocker" | "major" | "minor" | "nit"; issue: string }
  export interface ReviewValue { verdict: "approve" | "request_changes"; comments: ReviewComment[]; must_fix: string[] }
  export interface PlanStep extends StepBase { kind: "plan"; value: PlanValue }
  export interface ClaimStep extends StepBase { kind: "claim"; agent: "coder" | "solo"; value: PatchClaim | SoloClaim }
  export interface DiffStep extends StepBase { kind: "diff"; value: DiffValue }
  export interface TestsStep extends StepBase { kind: "tests"; value: TestsValue }
  export interface ReviewStep extends StepBase { kind: "review"; value: ReviewValue }
  export interface StopStep extends StepBase { kind: "stop"; text: string }
  export interface OutcomeStep extends StepBase { kind: "outcome" }
  export interface UnknownStep extends StepBase { kind: string; [field: string]: unknown }
  export type Step = NodeStep | ModelCallStep | ToolResultStep | PlanStep | ClaimStep | DiffStep | TestsStep
    | ReviewStep | StopStep | OutcomeStep | UnknownStep;
  export interface Replay { schema: number; run: RunInfo; caps: Caps; outcome: RunOutcome; steps: Step[] }
  export interface IndexEntry { run_id: string; file: string; caption: string; pair: string | null; task_id: string;
    issue_title: string; repo: string; category: string; system: GraphId; preset: string; outcome: OutcomeName;
    failure_kind: FailureKind; resolved: boolean; cost_usd: number; tool_calls: number; duration_s: number;
    recorded_at: string; allow?: { path: string; rule: string }[] }
  export interface ReplayIndex { schema: number; note: string; replays: IndexEntry[] }
  export interface GraphNode { id: string; kind: "start" | "llm" | "function" | "router" }
  export interface GraphEdge { from: string; to: string; route: string | null }
  export interface GraphDef { source: string; nodes: GraphNode[]; edges: GraphEdge[] }
  export interface Graphs { schema: number; graphs: Record<GraphId, GraphDef> }
  export type NodeState = "idle" | "active" | "done" | "stopped" | "end";
  export type FeedItem =
    | { kind: "separator"; step: number; t: number; node: string; visit: number; via: string | null }
    | { kind: "call"; step: number; t: number; agent: string; call: ToolCall }
    | { kind: "result"; step: number; t: number; result: ToolResultStep }
    | { kind: "wait"; step: number; t: number; seconds: number }
    | { kind: "stop"; step: number; t: number; text: string }
    | { kind: "other"; step: number; t: number; label: string };
  export interface ReplayState { t: number; stepIndex: number; activeNode: string | null; activeVisit: number;
    visits: Record<string, number>; nodeStates: Record<string, NodeState>; takenEdges: GraphEdge[];
    plans: PlanStep[]; claims: ClaimStep[]; diffs: DiffStep[]; tests: TestsStep[]; reviews: ReviewStep[];
    costUsd: number; toolCalls: number; tokensIn: number; tokensOut: number; feed: FeedItem[];
    stopped: StopStep | null; finished: boolean }
  ```

- `web/src/replay/parse.ts`:
  - `export class ReplayError extends Error`, with fixed messages `unsupported schema`, `steps out of order`, `node not in graph`, and `missing field: <JSON path>`.
  - `export function parseReplay(json: unknown, graphs: Graphs): Replay`
  - `export function parseIndex(json: unknown): ReplayIndex`
  - `export function parseGraphs(json: unknown): Graphs`

**Required behaviour**

1. `parseReplay` checks what the UI relies on: the fields above, `schema === SUPPORTED_SCHEMA`, `i` equal to the step's position, `t` non-decreasing, and every step's `node` in `graphs.graphs[run.graph]`. Unknown step kinds are kept; unknown fields are ignored.
2. No component in this task renders replay text. The shell only proves the route.
3. Before the first commit, `git check-ignore -v` over every new file under `web/` reports nothing except `node_modules/` and `dist/`.

**Tests**

- `parse.test.ts`:
  - `accepts the multi and single fixtures`
  - `rejects an unsupported schema`
  - `rejects steps out of order`
  - `rejects a node that is not in the graph`
  - `keeps unknown step kinds`
  - `ignores unknown fields`
  - `reports the path of a missing field`
- `routes.test.ts`: `parses the list, run and t forms`; `treats every other hash as not found`; `runHash round-trips`.
- `plugins/csp.test.ts`: `adds the exact policy at build time only`.

- [ ] **Step 1:** `npm install` as above; peer-range check; commit nothing yet.
- [ ] **Step 2:** Tests (RED); implement; green. `npm run typecheck`, `npm test`.
- [ ] **Step 3:** `npm run build`; `web/dist/index.html` carries the CSP meta tag, and no asset path starts with `/`. `git check-ignore` as in behaviour 3.
- [ ] **Step 4:** `AGENTS.md`:
  - Project layout: `web/`, the static replay site.
  - Commands, in `web/`: `npm ci`, `npm run dev`, `npm run typecheck`, `npm test`, `npm run build`, `npm run preview`.
  - Node 24 via `web/.nvmrc`.
  - The rule that packages change only with a lockfile commit.
- [ ] **Step 5:** Commit: `feat(web): replay site scaffold, replay types and parser`.

---

### Task 6: Player core

**Files:**
- Create: `web/src/replay/state.ts`, `web/src/replay/state.test.ts`, `web/src/replay/clock.ts`, `web/src/replay/clock.test.ts`, `web/src/replay/usePlayer.ts`

**Interfaces:**
- Consumes: Task 5's types and factories.
- Produces:

  ```ts
  // state.ts
  export const WAIT_NOTE_S = 15;
  export function stateAt(replay: Replay, graph: GraphDef, t: number): ReplayState;
  export function endTime(replay: Replay): number;                       // the outcome step's t
  export function nextStepTime(replay: Replay, t: number): number;       // end if none
  export function prevStepTime(replay: Replay, t: number): number;       // 0 if none
  export function nextVisitTime(replay: Replay, nodeId: string, t: number): number | null;
                                                                         // first node step of nodeId after t, else its first, else null
  export function nodeTicks(replay: Replay): { t: number; node: string }[];   // one per node step
  // clock.ts
  export const SPEEDS = [1, 2, 5, 10, 25, 50] as const;
  export const DEFAULT_SPEED = 10;
  export const MAX_WAIT_S = 1.5;
  export function advance(t: number, realElapsedS: number, opts: { speed: number; skipWaits: boolean;
    times: number[]; end: number }): number;
  // usePlayer.ts
  export function usePlayer(replay: Replay, graph: GraphDef, startT?: number): {
    t: number; state: ReplayState; playing: boolean; speed: number; skipWaits: boolean;
    play(): void; pause(): void; seek(t: number): void; step(direction: -1 | 1): void;
    toStart(): void; toEnd(): void; setSpeed(s: number): void; setSkipWaits(on: boolean): void };
  ```

**Required behaviour**

1. `stateAt` uses only the steps with `step.t ≤ t`; on equal times, index order decides.
   - `activeNode` and `activeVisit` come from the latest `node` step.
   - `nodeStates`: `idle` (not reached), `active` (the latest node step's node while not finished), `done` (visited), `stopped` (the node of a `stop` step), `end` (the outcome step's node once it is reached).
   - `takenEdges` come from each `node` step's `from` and `via`.
   - The running numbers come from the latest step; tokens are summed over `model_call` steps.
2. Feed items, in order:
   - a `separator` per `node` step;
   - a `call` per call of a `model_call`;
   - a `result` per `tool_result`;
   - a `wait` before any step that comes 15 s or more after the previous one;
   - a `stop`;
   - an `other` for an unknown kind, labelled with its kind.
3. `advance`: `t + realElapsedS × speed`, capped at `end`. With `skipWaits`, a gap between two consecutive step times never takes more than `MAX_WAIT_S` of real time: inside a gap longer than `MAX_WAIT_S × speed`, time runs at `gap / MAX_WAIT_S` per real second.
4. `usePlayer` drives `advance` from `requestAnimationFrame`. It stops at the end, and `play()` at the end restarts from 0. `startT` opens paused.

**Tests**

- `state.test.ts`:
  - `at time 0 only the first node is active`
  - `marks visited nodes done and counts visits`
  - `lists taken edges with their routes`
  - `keeps the latest and every plan, claim, diff, test run and review`
  - `takes the running numbers from the steps`
  - `marks the node of a stop as stopped`
  - `at the end the outcome node is the end node and finished is true`
  - `seeking backwards gives the same state as playing to that time`
  - `adds a wait item for a silence of 15 s or more`
  - `turns unknown step kinds into generic feed lines`
  - `next and previous step times and next visit time`
- `clock.test.ts`:
  - `advances by real time times speed`
  - `a long wait lasts at most 1.5 s when skipping`
  - `without skipping playback is proportional`
  - `stops at the end`
  - `usePlayer plays, pauses, seeks, steps and changes speed` (fake timers, `renderHook`)

- [ ] **Step 1:** Tests (RED); implement; green.
- [ ] **Step 2:** `npm run typecheck`, `npm test`.
- [ ] **Step 3:** Commit: `feat(web): replay player state and clock`.

---

### Task 7: Graph view

**Files:**
- Create: `web/src/graph/layout.ts`, `web/src/graph/layout.test.ts`, `web/src/graph/GraphView.tsx`, `web/src/graph/GraphView.test.tsx`

**Interfaces:**
- Consumes: `web/src/graph/graphs.json` (Task 2), Task 5's types and factories.
- Produces:
  - `export const LAYOUT: Record<GraphId, Record<string, { x: number; y: number }>>`
  - `export function GraphView(props: { graph: GraphDef; graphId: GraphId; state: ReplayState; models: Record<string, string>; onNodeClick(nodeId: string): void }): JSX.Element`
  - Custom node types `startNode`, `agentNode`, `functionNode`, `routerNode`.
  - Every node element has `data-testid="node-<id>"` and `data-state="<NodeState>"`. Every edge has `data-testid="edge-<from>-<to>"` and `data-taken="true|false"`.
  - Legend text: `dashed: not taken in this run`.

**Required behaviour**

1. Positions are placed by hand (design §7.4): the main path left to right, `report_failure` below it, and the loop edges (`fail` back to `coder` or `solo`, `changes` back to `coder`) curving above.
2. Agent nodes are larger cards showing the model name. Routers are small rounded diamonds. `START` is a dot labelled `issue`.
3. Each state shows a text label as well as a colour. From the second visit on, the node shows `×<n>`. The pulse uses Tailwind's `motion-safe:` variant only.
4. Taken edges are solid and labelled with their route; edges not taken are dashed and faded.
5. React Flow props: `fitView`, `colorMode="system"`, `nodesDraggable={false}`, `nodesConnectable={false}`, `elementsSelectable={false}`, `zoomOnScroll={false}`. Controls show zoom and fit only.

**Tests**

- `layout.test.ts`: `every node of both graphs has a position`.
- `GraphView.test.tsx`:
  - `renders both graphs with every node`
  - `shows each node state as text as well as colour`
  - `marks taken edges with their route and leaves the others dashed`
  - `shows the visit count from the second visit`
  - `calls back with the node id on click`

- [ ] **Step 1:** Tests (RED); implement; green.
- [ ] **Step 2:** `npm run typecheck`, `npm test`.
- [ ] **Step 3:** Commit: `feat(web): pipeline graph view for both graphs`.

---

### Task 8: Panels

**Files:**
- Create in `web/src/panels/`: `Feed.tsx`, `IssuePanel.tsx`, `PlanPanel.tsx`, `ClaimPanel.tsx`, `DiffPanel.tsx`, `TestsPanel.tsx`, `ReviewPanel.tsx`, `CostMeter.tsx`, `OutcomeBanner.tsx`, `Transport.tsx`, `HowToRead.tsx`, `UnifiedDiff.tsx` (only if Task 5 recorded the fallback), a `*.test.tsx` per component, `markup.test.tsx`, `safety.test.ts`

**Interfaces:**
- Consumes: Task 5's types and factories. Components take state as props and never call `stateAt` themselves.
- Produces (props):
  - `Feed({ items: FeedItem[]; follow: boolean; onFollowChange(on: boolean): void; onSeek(t: number): void })`
  - `IssuePanel({ issue: RunInfo["issue"] })`
  - `PlanPanel({ plans: PlanStep[]; graphId: GraphId })`
  - `ClaimPanel({ claims: ClaimStep[] })`
  - `DiffPanel({ diffs: DiffStep[] })`
  - `TestsPanel({ tests: TestsStep[] })`
  - `ReviewPanel({ reviews: ReviewStep[]; graphId: GraphId })`
  - `CostMeter({ state: ReplayState; caps: Caps; t: number })`
  - `OutcomeBanner({ replay: Replay; visible: boolean })`, rendered with `data-testid="outcome"`
  - `Transport({ t: number; end: number; playing: boolean; speed: number; skipWaits: boolean; ticks: { t: number; node: string }[]; onPlay(): void; onPause(): void; onSeek(t: number): void; onStep(direction: -1 | 1): void; onStart(): void; onEnd(): void; onSpeed(s: number): void; onSkipWaits(on: boolean): void })`
  - `HowToRead()`
  - In `OutcomeBanner.tsx`:
    - `export function outcomeHeadline(replay: Replay): string`
    - `export function outcomeBadge(entry: IndexEntry): string`, for the run list
- Fixed texts:
  - `IssuePanel` heading: `What the agents were given`.
  - `PlanPanel` on the single graph: `The single-agent baseline has no planner.`
  - `ReviewPanel` on the single graph: `The single-agent baseline has no reviewer.`
  - `ClaimPanel` heading: `The agent's own account. No route uses it; the diff and the test run are what the pipeline checks.`
  - `TestsPanel` note: `Visible tests only. The hidden tests ran after the run, when it was scored.`
  - `DiffPanel`: `This diff was shortened for the page; the counts are those of the full diff.` when `cut`, and `diff <k> of <n>` when there are several.
  - Feed: `answer` for `set_model_response`; a wait reads `<n> s without events`; a separator reads `<node> · visit <n>`, plus ` · via <route>` when `via` is set.
  - `outcomeHeadline`:
    - `Patch written · resolved: the hidden tests pass`
    - `Patch written · not resolved: the hidden tests fail`
    - `Declined · correct: this task is a trap`
    - `Declined · not resolved: the task could be done`
    - `Stopped by a cap · <reason>`
    - `Failed · <reason>`
    - `Infrastructure failure · <reason>`
  - `outcomeBadge`: `Patch written · resolved`, `Patch written · not resolved`, `Declined · correct`, `Declined · not resolved`, `Stopped by a cap`, `Failed`, `Infrastructure failure`.
  - `HowToRead`: the four points of design §7.6, in plain sentences.

**Required behaviour**

1. All replay text renders as React text. Long text renders in monospace with `white-space: pre-wrap`, and cut markers stay visible. Nothing becomes a link.
2. `DiffPanel` renders with `react-diff-view` (`parseDiff`, then `Diff` with `viewType="unified"` and `Hunk`), or with `UnifiedDiff` after the fallback. It shows a file list with `+` and `−` counts and the diff at `t`; with several diffs, a switch between them.
3. `CostMeter` shows cost against `caps.cost_usd`, tool calls against `caps.tool_calls`, tokens in and out, and elapsed time against `caps.wall_clock_s`. A bar changes colour past 80 % of its cap.
4. `Feed` follows the newest item until the reader scrolls away, and the `follow` button resumes. It scrolls smoothly only when `prefers-reduced-motion` is not set. A line expands to its arguments and result. Errors are marked with ✗ and the error text. Clicking a line seeks to its time.
5. `Transport` controls: play/pause, previous and next step, start, end, a speed choice of `SPEEDS`, a `skip long waits` toggle, and a scrubber over recorded time (`mm:ss / mm:ss`) with a tick per node step. Every button has an accessible name.
6. `OutcomeBanner` also shows final cost, tool calls, wall time, test attempts, review rounds and audit flags.

**Tests**

- `OutcomeBanner.test.tsx`: `headline for each outcome case` (seven cases); `badge for each outcome case` (seven cases); `shows the final numbers and audit flags`.
- `DiffPanel.test.tsx`: `renders file headers and line counts from a fixture`; `notes a cut diff`; `switches between the diffs of a loop`.
- `PlanPanel.test.tsx` and `ReviewPanel.test.tsx`: `single-agent graph shows the fixed text`.
- `ClaimPanel.test.tsx`: `labels the claim as the agent's own account`.
- `TestsPanel.test.tsx`: `shows exit code, failed tests and the visible-tests note`.
- `Feed.test.tsx`:
  - `one line per call and result under node separators`
  - `set_model_response reads answer`
  - `marks errors`
  - `expanding shows arguments and result with cut markers`
  - `stops following when scrolled away and resumes on follow`
  - `clicking a line seeks to its time`
- `CostMeter.test.tsx`: `bars against the caps change colour past 80 percent`.
- `Transport.test.tsx`: `every control has an accessible name`; `offers the six speeds`; `the scrubber seeks`.
- `markup.test.tsx`: `renders markup in replay text as plain text` (Review Focus 4). A replay whose issue body, tool argument, tool result, plan summary, claim, review comment, test output and decline reason hold `<img src=x onerror=alert(1)>`, `<script>alert(1)</script>` and `[x](javascript:alert(1))` is rendered through every panel. Expected: no `img`, no `script`, no `a` element, and the text is visible verbatim.
- `safety.test.ts`: `no dangerouslySetInnerHTML anywhere in web/src`.

- [ ] **Step 1:** Tests (RED); implement; green.
- [ ] **Step 2:** `npm run typecheck`, `npm test`.
- [ ] **Step 3:** Commit: `feat(web): replay panels`.

---

### Task 9: Pages and routing

**Files:**
- Create: `web/src/data.ts`, `web/src/data.test.ts`, `web/src/pages/RunList.tsx`, `web/src/pages/RunList.test.tsx`, `web/src/pages/RunPage.tsx`, `web/src/pages/RunPage.test.tsx`, `web/src/pages/NotFound.tsx`
- Modify: `web/src/App.tsx`

**Interfaces:**
- Consumes: Tasks 5 to 8.
- Produces:

  ```ts
  // data.ts
  export async function loadIndex(fetchFn: typeof fetch = fetch): Promise<ReplayIndex>;   // GET "replays/index.json"
  export async function loadReplay(index: ReplayIndex, runId: string, graphs: Graphs,
                                   fetchFn: typeof fetch = fetch): Promise<Replay | null>;
      // null, with no fetch, when runId is not an index entry; otherwise GET "replays/<entry.file>"
  ```

- Components: `RunList({ index: ReplayIndex })`, `RunPage({ entry: IndexEntry; replay: Replay; graphs: Graphs; startT: number | null })`, `NotFound()`.
- Fixed texts:
  - `NotFound`: `Replay not found` with the link `Back to all replays`.
  - A `ReplayError("unsupported schema")`: `This replay needs a newer viewer`.
  - System labels: `three agents` (multi) and `single agent`.
  - Tabs: `Issue`, `Plan`, `Agent summary`, `Diff`, `Tests`, `Review`.
  - Pair link: `compare`.

**Required behaviour**

1. `App` reads `location.hash` with `parseHash` and re-renders on `hashchange`. The run id from the hash is only looked up in the index; the fetched path is always the index entry's `file`.
2. `RunList` shows the index `note`, then one card per entry in index order:
   - caption, task id and issue title, the system label;
   - an outcome badge from `outcomeBadge`;
   - cost as `$0.00`, wall time as `m:ss`, tool calls, and the date;
   - the `compare` link when `pair` is set.
3. `RunPage` lays out, top to bottom:
   - a header: task id, issue title, system label, model(s), prompt version, caption, and the `compare` link;
   - `CostMeter`;
   - `GraphView` beside `Feed`;
   - `Transport`;
   - the tabs;
   - `OutcomeBanner`, visible once `state.finished`;
   - a link to `HowToRead`.
   It opens paused at `startT` when given. While paused it writes `runHash(runId, t)` with `history.replaceState`. Clicking a graph node seeks to `nextVisitTime`. Keys: Space toggles play; ← and → step; Home and End go to the start and end. Keys are ignored while focus is in a form control.
4. Narrow screens stack the sections in the same order.

**Tests**

- `data.test.ts`: `never fetches a run id that is not in the index` (Review Focus 4: `../index`, `x/../../etc`, `https:%2F%2Fevil.example`, `%2e%2e`, an id that differs only in case; the fetch spy is never called); `fetches the file named by the index entry`.
- `RunList.test.tsx`: `lists replays in index order with outcome, cost, time and tool calls`; `pairs link to each other`.
- `RunPage.test.tsx`:
  - `opens paused at t from the hash`
  - `writes t to the hash when paused`
  - `shows not found for a run that is not in the index`
  - `shows the newer-viewer message for an unsupported schema`
  - `a single-agent replay shows the no-planner and no-reviewer texts`
  - `clicking a graph node seeks to its next visit`
  - `keyboard controls play, step and jump`
  - `shows the outcome banner only at the end`

- [ ] **Step 1:** Tests (RED); implement; green.
- [ ] **Step 2:** `npm run typecheck`, `npm test`, `npm run build`. If Task 4 has landed, run `npm run dev` and play one replay of each graph end to end by hand. Otherwise this check moves to Task 10. Nothing is ever written to `web/public/replays/` except by `bench.replay build`.
- [ ] **Step 3:** Commit: `feat(web): run list and run page`.

---

### Task 10: Committed replays in the viewer, and the smoke test

**Files:**
- Create: `web/src/committed.test.ts`, `web/playwright.config.ts`, `web/e2e/smoke.spec.ts`
- Modify: `AGENTS.md`

**Interfaces:**
- `playwright.config.ts`:
  - `testDir: 'e2e'`, Chromium only, `retries: 0`, `use: { baseURL: 'http://localhost:4173/', trace: 'retain-on-failure' }`
  - `webServer: { command: 'npm run build && npm run preview -- --port 4173 --strictPort', url: 'http://localhost:4173/', reuseExistingServer: false }`
- Browser: `npx playwright install chromium` locally. CI uses `--with-deps` (Task 12).

**Required behaviour**

1. `committed.test.ts` reads `public/replays/index.json`, every replay it names, and the real `src/graph/graphs.json`.
2. The smoke test fails on any `console` error (CSP violations included) and on any request whose origin is not `http://localhost:4173`.
3. If the smoke test shows a CSP violation from a library's inline `<style>`, add that style's hash to `style-src` in `web/plugins/csp.ts` and its test, and never `'unsafe-inline'`.

**Tests**

- `committed.test.ts`:
  - `parses every committed replay against the real graphs`
  - `at the end the running numbers equal the outcome for every replay`
  - `every node and taken edge exists in its graph`
  - `index entries match their replay files`
- `e2e/smoke.spec.ts`:
  - `plays the trap replay to the end`: open `#/`, open `md-005-multi-flash-r1-20261001T062611Z` (if Task 11 changes the set, the shortest multi-agent replay that remains), choose 50×, press play. Expect within 10 s `data-testid="outcome"` to contain `Declined · correct` and `node-report_failure` to have `data-state="end"`.
  - `the build uses only relative asset paths` (reads `dist/index.html`: no `src` or `href` starts with `/`).

- [ ] **Step 1:** Tests (RED where the code falls short); fix; green. `npm run e2e` passes.
- [ ] **Step 2:** `AGENTS.md` commands: `npx playwright install chromium` (once), `npm run e2e`. Typecheck, `npm test`.
- [ ] **Step 3:** Commit: `test(web): committed replays and the smoke test`.

---

### Task 11: Captions **[OWNER REVIEW]**

Controller and owner task. Free.

**Files:**
- Modify: `web/replays.yaml` (captions only, unless the owner swaps a run as design §15 allows), `web/public/replays/` (rebuilt)

- [ ] **Step 1:** `cd web && npm run dev`. The owner plays each of the seven replays to the end, reads each panel for anything that should not be public, and confirms or rewrites its caption (one line, at most 140 characters, true of the replay).
- [ ] **Step 2 [OWNER REVIEW]:** The owner approves the exact caption texts and any swap (`sr-002` for `md-002`; `md-005` for `tc-005` on the multi-agent graph).
- [ ] **Step 3:** `uv run python -m bench.replay build`, `uv run python -m bench.replay check`, `uv run pytest tests/unit/test_replay_files.py -q`, and `npm test` and `npm run e2e` in `web/`.
- [ ] **Step 4:** Commit: `docs: replay captions confirmed by the owner`.

---

### Task 12: Pages workflow

**Files:**
- Create: `.github/workflows/pages.yaml`, `tests/unit/test_pages_workflow.py`
- Modify: `AGENTS.md`

**Interfaces:**
- The workflow is design §8.2 exactly:
  - Name: `Replay site`.
  - Triggers: `push` to `main` and `pull_request`, both on paths `web/**`, `bench/replay_check.py` and `.github/workflows/pages.yaml`; plus `workflow_dispatch`.
  - Top-level `permissions: contents: read`; `concurrency` group `pages-${{ github.ref }}` with `cancel-in-progress: false`.
  - Job `build` on `ubuntu-latest`:
    1. checkout with `persist-credentials: false`;
    2. setup-python `3.12`;
    3. `python bench/replay_check.py web/public/replays` with `env: REPLAY_REDACT: ${{ secrets.REPLAY_REDACT }}`;
    4. setup-node with `node-version-file: web/.nvmrc`, `cache: npm`, `cache-dependency-path: web/package-lock.json`;
    5. `npm ci`;
    6. `npm run typecheck && npm test && npm run build`;
    7. `npx playwright install --with-deps chromium && npm run e2e`, all in `web/`;
    8. `actions/upload-pages-artifact` with `path: web/dist`, only when `github.event_name != 'pull_request'`.
  - Job `deploy`: `if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'`; `needs: build`; `permissions: pages: write, id-token: write`; `environment: name: github-pages, url: ${{ steps.deployment.outputs.page_url }}`; one step `id: deployment` using `actions/deploy-pages`.
  - Every `uses:` is `owner/action@<40-hex SHA> # <release tag>`.

**Required behaviour**

1. The release SHAs of `actions/checkout`, `actions/setup-python`, `actions/setup-node`, `actions/upload-pages-artifact` and `actions/deploy-pages` are looked up read-only, for example `git ls-remote https://github.com/actions/checkout refs/tags/<tag>`, using each action's latest release tag. No credentials are used, and the controller confirms before this network step.
2. The workflow changes nothing in the repository and uses no cloud credential. It stays inert until Task 14.

**Tests** (`tests/unit/test_pages_workflow.py`, PyYAML; note that PyYAML reads the `on` key as `True`)

- `test_triggers_are_main_pushes_pull_requests_and_dispatch_on_the_site_paths`
- `test_only_the_deploy_job_has_pages_and_id_token` (Review Focus 5)
- `test_no_job_has_contents_write` (Review Focus 5)
- `test_pull_requests_never_deploy` (Review Focus 5; the deploy job's `if` and the upload step's `if`)
- `test_every_action_is_pinned_to_a_full_sha` (Review Focus 5)
- `test_checkout_does_not_persist_credentials`
- `test_the_leak_check_runs_before_npm_and_receives_replay_redact`
- `test_the_artifact_is_web_dist`

- [ ] **Step 1:** Tests (RED); write the workflow with the looked-up SHAs; green.
- [ ] **Step 2:** `AGENTS.md`: the workflow, what it runs, its permissions, and that it deploys only once the repository is on GitHub with Pages set to "GitHub Actions". Lint, full suite.
- [ ] **Step 3:** Commit: `ci: build, test and deploy the replay site to GitHub Pages`.

---

### Task 13: Docs close-out

Controller task. Free.

**Files:**
- Create: `docs/adr/0007-replay-first-public-demo.md` (context, decision, consequences; from design §1, §5.3 and §7)
- Modify: `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` (§19), `AGENTS.md`

- [ ] **Step 1:** Whole-branch review, with diffs excluding `':(exclude,glob)bench/tasks/*-h[0-9][0-9]/**'`. Then run, all green:
  - `uv run pytest -q` and `agents-cli lint`;
  - `uv run python -m bench.replay graphs --check` and `uv run python -m bench.replay check`;
  - in `web/`: `npm run typecheck`, `npm test`, `npm run e2e`.
- [ ] **Step 2:** Parent spec §19 gains a dated block with design §11's five amendments.
- [ ] **Step 3:** ADR 7.
- [ ] **Step 4:** `AGENTS.md`: the Status line (the replay site is built; it is not live until the repository is on GitHub with Pages enabled), and anything still missing.
- [ ] **Step 5:** Commit: `docs: week 3B replay site, spec amendments and ADR 7`.

---

### Task 14: Go live **[OWNER APPROVAL]**

Owner task. Each step is approved at the time. `OWNER` is the owner's personal account and `REPO` the repository name the owner chooses (design §15, question 1).

On GitHub Free, Pages cannot be enabled while the repository is private. Until Step 6, the workflow's build job runs and is useful, and its deploy job fails with "Pages not enabled". That failure is expected.

- [ ] **Step 1 [OWNER APPROVAL]:** Create the repository, private, without pushing:

  ```
  gh repo create OWNER/REPO --private --source . --remote origin
  ```

- [ ] **Step 2 [OWNER APPROVAL]:** Store the secret without putting it on a command line. `gh secret set REPLAY_REDACT --repo OWNER/REPO` prompts for the value: the project id and the project number, comma-separated.
- [ ] **Step 3 [OWNER APPROVAL]:** Push `main`, once the owner has merged this week's branch: `git push -u origin main`. The build job passes.
- [ ] **Step 4:** README, locally: a line saying the five held-out tasks were written sealed and never run (parent spec §19), and the replay link (`https://OWNER.github.io/REPO/`). Commit: `docs: replay site link`.
- [ ] **Step 5 [OWNER APPROVAL]:** Make the repository public: `gh repo edit OWNER/REPO --visibility public --accept-visibility-change-consequences`. On a paid plan the owner may keep it private instead, and Pages then publishes from the private repository.
- [ ] **Step 6 [OWNER APPROVAL]:** In the web UI: Settings → Pages → Build and deployment → Source: "GitHub Actions". Check that the `github-pages` environment allows deployments from `main` only.
- [ ] **Step 7 [OWNER APPROVAL]:** Push Step 4's commit. `README.md` is outside the workflow's path filter, so then start the workflow by hand: `gh workflow run pages.yaml --ref main`.
- [ ] **Step 8:** `gh run watch`; the deploy job succeeds. Open the site and play one replay to the end. The browser's network panel shows no request to another origin.

---

## Exit criteria

- `uv run pytest -q` and `agents-cli lint` pass. `bench.replay graphs --check` and `bench.replay check` pass. A second `bench.replay build` changes no file.
- In `web/`: `npm run typecheck`, `npm test` and `npm run e2e` pass.
- Seven replays and `index.json` are committed, within the size limits, with captions the owner approved. Every one passes the leak check, and its final numbers equal its `record.json`.
- The Pages workflow passes its tests. It has deployed once the owner has done Task 14, and not before.
- No held-out task was opened, converted or published. No model, GCP or GitHub call was made except the read-only SHA lookup and Task 14's approved steps. Week 3B spent $0.

## Not in this plan

Live UI mode with Approve/Reject, the FastAPI proxy, IAP and persistent sessions for approvals; authentication; any backend. Live replays (they need the live graph and `approval` and `pull_request` step kinds, added when the Week 2C live runs exist). Trace links; reviewer probe and eval runs; a side-by-side view of two runs; result charts; search; a custom domain; the README's demo GIF. Everything Week 3A owns (`app/environment/`, `deployment/`, the Makefile's cloud targets, the deploy staging) and CI for the rest of the repository (Week 3C). The held-out tasks, their run, and the decision to publish them.
