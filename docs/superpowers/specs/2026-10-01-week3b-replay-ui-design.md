# Week 3B design: replay UI

Date: 2026-10-01. Status: Approved by the owner on 2026-10-01; built in Week 3B. Later changes are recorded in the parent spec's §19 (2026-10-02, Week 3B).
Parent spec: `2026-09-29-sdlc-agent-pipeline-design.md` (§2 criterion 3, §5 graph, §10 observability, §11 web UI, §15 cut line, §19 amendments). Where this document is more specific, it wins for Week 3B once approved; the parent spec's §19 then records the amendments listed in §11.
Plan: `docs/superpowers/plans/2026-10-01-week3b-replay-ui.md` (written after this design is approved).
Sibling: `2026-10-01-week3a-cloud-design.md` (Week 3A, written separately). The two do not overlap: 3B adds no cloud resource and one GitHub Actions workflow of its own (§8).

## Owner decisions (2026-10-01)

These are decisions, not options. The rest of this document is written for them.

1. **Converter.** `bench/replay.py` turns a recorded run (`runs/<run-id>/events.jsonl` and `record.json`) into one replay JSON file: an ordered list of steps (node started, model call, tool call and result, diff, test run, review verdict, outcome) with time offsets and running cost. Before anything is published it scrubs host paths, container ids and the GCP project id, and caps long outputs. A test fails if a replay contains `/Users/`, the project id, or anything token-shaped.
2. **Curated set.** Six to eight existing Week 2B runs: a clean multi-agent fix, a tempting task fixed at the root (`md-002` or `sr-002`), the trap declined (`md-005` or `tc-005`), a run that hit the $1.00 cost cap (`md-004`, `sr-003` or `sr-004`), and multi-agent against single-agent on the same task. Live demo runs (Week 2C, not yet run) are added later. Each run gets a one-line caption. Held-out tasks never appear; the converter refuses a held-out task id anyway, without opening anything under `bench/tasks/*-hNN`.
3. **Site.** `web/`: Vite, React, TypeScript, Tailwind, React Flow and a diff-view component. A run list (outcome, cost, time) and a run page: the pipeline graph lighting up as nodes run, play, pause, speed and a scrubber, a live tool-call feed, the diff, the test output, the reviewer's verdict and a cost meter. A static build that reads replay JSON; no backend; $0. It renders both the multi-agent graph and the single-agent baseline graph.
4. **Hosting.** GitHub Pages from this repository, through a small GitHub Actions workflow. Making the repository public is a separate step the owner approves at the time. The five held-out tasks stay sealed and unrun; by the owner's decision in parent §19, they are published as they are if the repository goes public first.
5. **Tests.** Converter unit tests in pytest; component tests in Vitest; one Playwright smoke test that loads a replay, plays it to the end and checks the outcome panel.
6. **Not in 3B.** Live mode with Approve/Reject (the parent §15 cut line's live UI mode; replay stays), authentication, any backend.

Settled by this design, within those decisions: the replay is built from an allowlist of fields, never by copying events (§5.3); the leak check is a standard-library script that CI can run without installing the project (§5.5); the graph's nodes and edges are exported from `app/pipeline.py` and `app/baseline.py`, never typed by hand (§5.6); the curated set is seven runs (§6.2); the diff component is `react-diff-view` (§7.1); the site uses hash routes and relative asset paths, so it works under any repository name (§7.8).

## 1. Why

- **Parent success criterion 3:** "The public replay page loads without any backend and costs $0 to run." It does not exist yet.
- **The recruiter's 30 seconds** (parent §2) need something to click. The benchmark tables say what happened; a replay shows how: the planner reading the code, the coder's edits, the real test run, the reviewer's verdict, and the cost climbing.
- **The honest cases matter as much as the wins.** The Week 2B comparison (`docs/results/2026-10-01-2b-comparison.md`) found that the single agent resolved 36 of 45 runs and the multi-agent pipeline 34 of 45, and that the $1.00 cap binds on the multi-file features for both systems. The curated set shows a cap stop and a single-agent run next to the multi-agent one, not only clean fixes.
- **Every run already records what a replay needs.** The driver writes each event to `events.jsonl` as it happens (`app/driver.py`, `_EventLog`). Nothing new has to be recorded; the parent spec's `record` flag (§11) is not needed.

## 2. Success criteria

1. `uv run python -m bench.replay build` turns the seven curated runs (§6.2) into seven replay files and `index.json` under `web/public/replays/`. Building twice from the same inputs gives byte-identical files.
2. Every replay's final cost and tool-call count equal its `record.json`, and its outcome and `resolved` equal its results row; the converter refuses to write a replay where they differ.
3. The leak check passes on every committed replay. pytest proves that the converter rewrites or refuses each planted pattern: host paths, the repository's own path, sandbox names, container ids, a project id, `projects/...` resource names, and each token shape (§5.5). It also proves that a held-out run id and a held-out task id are refused with a fixed message and that no file under a `*-hNN` task directory is touched.
4. The site renders the multi-agent graph and the single-agent graph from `graphs.json`, which a test keeps equal to the edges the code builds. For every committed replay, playing to the end marks exactly the nodes the run visited and shows the cost, tool calls and outcome of its `record.json`.
5. The production build makes no request to another origin and logs no console error (the Playwright smoke test fails on either), and it works from any subpath.
6. The Pages workflow builds, tests and deploys on a push to `main` once the repository is on GitHub with Pages enabled. Only its deploy job has `pages: write` and `id-token: write`; nothing has `contents: write`.
7. Week 3B spends $0: no model call, no cloud resource, no paid service.
8. Bench mode, the graphs, prompts, caps, scoring and all Week 2 results are unchanged.

## 3. What a recorded run holds

Read from `runs/md-001-multi-flash-r1-20261001T062611Z/`, `runs/md-001-single-flash-r1-20261001T084838Z/`, the trap run `runs/md-005-multi-flash-r1-20261001T062611Z/`, the cap run `runs/sr-003-multi-flash-r1-20261001T062611Z/` and a wall-clock run `runs/tc-001-multi-flash-r2-20261001T062611Z/`, then checked over all 91 Week 2B run directories (the 90 scored runs, plus one attempt that failed on infra and was rerun).

**`events.jsonl`:** one ADK `Event` per line (`event.model_dump_json(exclude_none=True)`), in order.

| Field | What it holds |
|---|---|
| `author` | `issue_to_pr` (the workflow, for function nodes), or the agent: `planner`, `coder`, `reviewer`, `solo` |
| `node_info.path` | `issue_to_pr@1/<node>@<visit>`, for example `issue_to_pr@1/coder@2` on the coder's second visit |
| `node_info.output_for` | present on a node's output event |
| `content.parts[]` | `text` (a function node's status line, such as `tests: passed (exit 0)`, or an agent's answer as JSON), `function_call` (`id`, `name`, `args`, plus an opaque `thought_signature`), `function_response` (`id`, `name`, `response`) |
| `actions.state_delta` | `issue`, `issue_text`, `sandbox_id`, `protected_paths`, `baseline_sha`, `plan`, `patch` (coder), `solo`, `diff` (with `unified_diff`), `diff_text` (cut to 20,000 characters for prompts), `test_report`, `review`, `failure`, `outcome`, and `budget` (`cost_usd`, `tokens_in`, `tokens_out`, `tool_calls`, running totals) on every model-response event |
| `actions.route` | the route a node took: `actionable` or `declined` (`route_plan`), `pass`, `fail` or `exhausted` (`run_tests`), `approve`, `changes` or `exhausted` (`route_review`), `done` or `declined` (`route_solo`) |
| `usage_metadata` | per model call: `prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, cache counts |
| `model_version` | for example `gemini-3.8-flash` |
| `error_code`, `error_message` | on the last event of a run stopped by an exception, for example `RuntimeError`, "Error in plugin 'budget' during 'before_model_callback' callback: run cost $1.008 reached the $1.00 cap" |
| `timestamp` | Unix seconds, float |

Facts the format relies on:

- **When a time is stamped.** ADK creates the model-response event before it calls the model (`google/adk/flows/llm_flows/base_llm_flow.py`, ADK 2.8.0), so a `function_call` event carries the time the call **started**. The matching `function_response` event is stamped when the tool **finished**. The gap between them is the model's reply plus the tool run; the log cannot separate the two.
- **Running numbers agree with the record.** In all 91 Week 2B runs, the number of `function_response` parts equals `record.json`'s `tool_calls` (`set_model_response` included), and the last `budget.cost_usd` equals `record.json`'s `cost_usd` within $0.0002, cap stops included.
- **A run can end without an outcome event.** A cap stop ends on an event with `error_code`; the outcome exists only in `record.json`. A wall-clock stop ends on an ordinary event: in `tc-001-multi-flash-r2-…` the log stops at 788 s and the record says 3,000 s. The player must show that silence honestly (§7.3).
- **No run on disk ever looped.** All 90 Week 2B rows, and every `record.json` under `runs/`, have `test_attempts` 0 and `review_rounds` 0. The loop edges exist in both graphs but no curated replay takes them.
- **What is sensitive.** No run contains `/Users/`, `/home/`, a `projects/…` resource name, `googleapis.com` or a token shape. Every run names its sandbox (`itp-` and 12 hex digits) in a status line and in `sandbox_id`. One infra error event (a rerun, not curated) carries provider text with a URL. Thought signatures are long opaque strings. Email-shaped matches were all `+@pytest.mark` false positives; the sandbox's git identity is `pipeline@localhost`.
- **Size.** Event logs are 16–506 KB (median 158 KB); without thought signatures, 14–296 KB.

**`record.json`:** `RunRecord` (`app/schemas.py`): `task_id`, `run_id`, `outcome`, `failure_kind`, `reason`, `patch_path`, `test_attempts`, `review_rounds`, `tokens_in`, `tokens_out`, `cost_usd`, `tool_calls`, `duration_s` and, in newer records, `mode`, `model_stalls` and live fields. It does not hold `resolved`, `system`, `preset` or the caps.

**The results row** (`results/<stamp>-<system>-<preset>.json`, tracked in git): adds `resolved` (the hidden-test score), `system`, `preset`, `split`, `category`, `difficulty`, `repo`, `repeat`, `audit`, `infra_retries`, `crashed` and, from Week 2C on, `prompt_version` (rows without it are `2a`, `app/prompts.py::LEGACY_PROMPT_VERSION`). `runs/` is git-ignored, so the converter runs on the owner's machine and only the replays are committed.

## 4. Replay format

### 4.1 Files

| File | Written by | Holds |
|---|---|---|
| `web/public/replays/<run-id>.json` | `bench.replay build` | one replay (§4.2) |
| `web/public/replays/index.json` | `bench.replay build` | the run list (§4.6) |
| `web/src/graph/graphs.json` | `bench.replay graphs` | both graphs' nodes and edges (§5.6); bundled into the site |
| `web/replays.yaml` | the owner, by hand | the curated list and captions (§6.1); not served |

JSON is written with `indent=2`, keys in a fixed order, UTF-8, so a reviewer can read what will be published in a normal diff. Pages serves it compressed.

### 4.2 Top level

```json
{
  "schema": 1,
  "run": {
    "run_id": "md-001-multi-flash-r1-20261001T062611Z",
    "task_id": "md-001",
    "repo": "mdlite",
    "category": "bug",
    "difficulty": "medium",
    "tempting": false,
    "levers": ["distant_symptom"],
    "system": "multi",
    "graph": "multi",
    "preset": "flash",
    "models": {"planner": "gemini-3.8-flash", "coder": "gemini-3.8-flash", "reviewer": "gemini-3.8-flash"},
    "prompt_version": "2a",
    "mode": "bench",
    "recorded_at": "2026-10-01T06:26:11Z",
    "issue": {"title": "Angle brackets stay in the output for some autolinks", "body": "Autolinks like `<https://example.com>` work, but ..."}
  },
  "caps": {"cost_usd": 1.0, "tool_calls": 75, "wall_clock_s": 3000},
  "outcome": {
    "outcome": "patch_written",
    "failure_kind": "none",
    "reason": "",
    "resolved": true,
    "cost_usd": 0.6595,
    "tool_calls": 70,
    "tokens_in": 795061,
    "tokens_out": 16855,
    "duration_s": 365.37,
    "test_attempts": 0,
    "review_rounds": 0,
    "audit": []
  },
  "steps": []
}
```

| Field | Type | Source |
|---|---|---|
| `schema` | int | `1`; a breaking change bumps it (§4.7) |
| `run.run_id`, `run.task_id` | string | manifest entry, checked against `record.json` and the results row |
| `run.repo`, `category`, `difficulty`, `tempting`, `levers` | string, string, string, bool, string[] | the task's `task.yaml`, through `bench.probes.dev_task` (§5.2) |
| `run.system`, `run.preset` | `"multi"` \| `"single"`, `"flash"` \| `"pro"` \| `"mixed"` | results row |
| `run.graph` | `"multi"` \| `"single"` | `system`; names a graph in `graphs.json` |
| `run.models` | {agent: model} | `model_version` of each agent's model calls |
| `run.prompt_version` | string | results row, default `"2a"` |
| `run.mode` | `"bench"` | `record.json`; other modes are refused in 3B |
| `run.recorded_at` | ISO 8601 UTC, seconds | the first event's timestamp |
| `run.issue` | {title, body} | the `issue` state delta: what the agents saw |
| `caps` | {cost_usd, tool_calls, wall_clock_s} | the manifest (§6.1); `record.json` does not hold the caps |
| `outcome` | object | `record.json`, plus `resolved` and `audit` from the results row; `reason` is the public reason (§4.5) |
| `steps` | Step[] | §4.3 |

### 4.3 Steps

Every step has these fields:

| Field | Type | Meaning |
|---|---|---|
| `i` | int | index in `steps`, from 0 |
| `t` | float | seconds since the first event, two decimals, never less than the previous step's |
| `kind` | string | one of the kinds below |
| `node` | string | the graph node this step belongs to (a node id in `graphs.json`) |
| `visit` | int | which visit of that node, from `node_info.path` (`coder@2` is 2) |
| `cost_usd` | float | running cost: the latest `budget.cost_usd` seen, 0 before the first |
| `tool_calls` | int | running tool calls: `function_response` parts seen so far, `set_model_response` included |

The kinds, with an example of each. Examples are from `md-001-multi-flash-r1-20261001T062611Z`, except `stop`, from `sr-003-multi-flash-r1-20261001T062611Z`. Indices are illustrative; long strings are shortened here with `...`.

**`node`**: a node visit starts. `from` is the previous node (null for the first), `via` the route that previous node took (null if it took none), `message` the first line of the node's status text (null for agents and routers).

```json
{"i": 1, "t": 1.25, "kind": "node", "node": "provision_sandbox", "visit": 1, "cost_usd": 0.0, "tool_calls": 0,
 "from": "fetch_issue", "via": null, "message": "sandbox <sandbox> ready"}
```

**`model_call`**: one model response. Call ids are renumbered `c1`, `c2`, ... in order of appearance. `label` is a one-line summary built as `bench/progress.py` builds its progress lines. `tokens_out` counts candidates and thoughts, as `BudgetPlugin` does. `text` is the response's non-thought text, usually null.

```json
{"i": 3, "t": 1.26, "kind": "model_call", "node": "planner", "visit": 1, "cost_usd": 0.0008, "tool_calls": 0,
 "agent": "planner", "model": "gemini-3.8-flash", "tokens_in": 914, "tokens_out": 28, "text": null,
 "calls": [{"id": "c1", "tool": "list_dir", "label": "list_dir .", "args": {"path": "."}}]}
```

**`tool_result`**: one tool response. `error` holds the response's `error` value (a guardrail block or a tool error), else null. `result` is the response without `error`. There is no `tool_result` for `set_model_response`: its value arrives in the `plan`, `claim` or `review` step at the same time.

```json
{"i": 92, "t": 265.8, "kind": "tool_result", "node": "coder", "visit": 1, "cost_usd": 0.4228, "tool_calls": 44,
 "call": "c44", "tool": "edit_file", "error": null, "result": {"ok": true, "path": "mdlite/escape.py"}}
```

**`plan`**: the planner's answer (`Plan`, from the `plan` state delta).

```json
{"i": 52, "t": 120.2, "kind": "plan", "node": "planner", "visit": 1, "cost_usd": 0.2713, "tool_calls": 24,
 "value": {"actionable": true, "decline_reason": null,
           "summary": "Fix URL scheme regex in `mdlite/escape.py` to allow `+`, `.`, and `-` ...",
           "files_to_inspect": ["mdlite/escape.py", "mdlite/inline.py", "tests/test_inline.py"],
           "steps": ["In `mdlite/escape.py`, update `_SCHEME` regex ...", "..."],
           "test_strategy": "Add test cases to `tests/test_inline.py` ..."}}
```

**`claim`**: the coder's `PatchResult` (`patch` state delta) or the single agent's `SoloResult` (`solo` state delta). The UI labels it as the agent's own account, which no route uses.

```json
{"i": 110, "t": 304.52, "kind": "claim", "node": "coder", "visit": 1, "cost_usd": 0.5246, "tool_calls": 52,
 "agent": "coder",
 "value": {"summary": "Updated `_SCHEME` in `mdlite/escape.py` to match `+`, `.`, and `-` ...",
           "files_changed": ["mdlite/escape.py"], "tests_added": ["tests/test_scheme_autolinks.py"], "notes": "..."}}
```

**`diff`**: the real `git diff`, from the `diff` state delta's `unified_diff` (never the shortened `diff_text`). `cut` is true when the size cap shortened it; `insertions` and `deletions` stay the true counts.

```json
{"i": 112, "t": 304.64, "kind": "diff", "node": "collect_diff", "visit": 1, "cost_usd": 0.5246, "tool_calls": 52,
 "value": {"files": ["mdlite/escape.py", "tests/test_scheme_autolinks.py"], "insertions": 39, "deletions": 1,
           "unified_diff": "diff --git a/mdlite/escape.py b/mdlite/escape.py\n...", "cut": false}}
```

**`tests`**: the real test run (`TestReport`, from the `test_report` state delta). Visible tests only.

```json
{"i": 114, "t": 305.82, "kind": "tests", "node": "run_tests", "visit": 1, "cost_usd": 0.5246, "tool_calls": 52,
 "value": {"passed": true, "exit_code": 0, "failed_tests": [], "duration_s": 1.18,
           "output_tail": "........ [ 59%]\n........ [100%]\n122 passed in 0.81s"}}
```

**`review`**: the reviewer's verdict (`Review`, from the `review` state delta).

```json
{"i": 151, "t": 365.23, "kind": "review", "node": "reviewer", "visit": 1, "cost_usd": 0.6595, "tool_calls": 70,
 "value": {"verdict": "approve", "comments": [], "must_fix": []}}
```

**`stop`**: an event with `error_code`; the run was aborted there. `text` is the public reason (§4.5), never the event's `error_message`.

```json
{"i": 90, "t": 541.85, "kind": "stop", "node": "coder", "visit": 1, "cost_usd": 1.0084, "tool_calls": 44,
 "text": "run cost $1.008 reached the $1.00 cap"}
```

In this run the outcome step follows at 541.85 as well: the last event is 0.07 s later than `record.json`'s `duration_s` (541.78). Event times are wall-clock timestamps and `duration_s` comes from the driver's monotonic clock, so the two can differ by a fraction of a second; the outcome step takes the later of the two.

**`outcome`**: always the last step, at `max(last t, record.duration_s)`. It has no fields of its own; the UI reads the top-level `outcome`.

```json
{"i": 153, "t": 365.37, "kind": "outcome", "node": "deliver_patch", "visit": 1, "cost_usd": 0.6595, "tool_calls": 70}
```

### 4.4 How events become steps

In event order:

1. `t` is the event's timestamp minus the first event's, rounded to 0.01 s and raised to the previous step's `t` if it is lower.
2. When `node_info.path` names a different node visit from the previous event, emit a `node` step. `from` is the previous visit's node; `via` is the last `actions.route` seen in that visit; `message` is the first line of this event's text when the author is `issue_to_pr` (the workflow).
3. A model-response event (an agent author with `usage_metadata` or function calls) becomes a `model_call` step. Thought parts and `thought_signature` are dropped.
4. Each `function_response` part becomes a `tool_result` step, except `set_model_response`'s.
5. State deltas: `plan` → `plan`; `patch` → `claim` (agent `coder`); `solo` → `claim` (agent `solo`); `diff` → `diff`; `test_report` → `tests`; `review` → `review`; `issue` → `run.issue`; `budget` → the running cost; `outcome` → remembered for the reason (§4.5). Every other key (`sandbox_id`, `protected_paths`, `baseline_sha`, `issue_text`, `failure`, `test_attempts`, `review_rounds`, `diff_text`) is ignored.
6. An event with `error_code` becomes a `stop` step.
7. After the last event, emit the `outcome` step.

The converter refuses to write the replay when any of these fails:

- the final running cost differs from `record.json`'s `cost_usd` by more than $0.0002, or the running tool calls differ from its `tool_calls`;
- a node is not in the run's graph in `graphs.json`;
- a `tool_result` has no earlier call with its id;
- `record.json` and the results row disagree on `task_id`, `run_id` or `outcome`.

### 4.5 Public reasons

The outcome's `reason` and a `stop` step's `text`:

| Case | Text |
|---|---|
| `patch_written` | empty |
| `declined` | the planner's or the single agent's `decline_reason`, scrubbed and cut at 1,000 characters |
| `failed`, `budget` | `record.json`'s reason as it is. These are built from numbers by `BudgetPlugin` or the driver ("run cost $1.008 reached the $1.00 cap", "run exceeded 3000 s wall clock") |
| `failed`, `agent`, outcome written by the graph's `report_failure` | its reason as it is. These are fixed sentences built from counts ("tests still failing after 3 fix attempts", "reviewer still requested changes after 2 rounds") |
| `failed`, `agent`, outcome from the driver | `app.driver.PUBLIC_AGENT`. The driver's reason may quote a provider's message |
| `failed`, `infra`, or a crashed row | `app.driver.PUBLIC_INFRA` or `PUBLIC_CRASH`. These reasons may hold exception text, host paths or project ids |

Whether `report_failure` wrote the outcome is read from the events: its output event carries the `outcome` state delta. The curated set has no `agent`, `infra` or crashed run.

### 4.6 `index.json`

```json
{
  "schema": 1,
  "note": "Seven of the 90 Week 2B dev-split runs (Gemini 3.8 Flash), chosen to show each way a run can end.",
  "replays": [
    {
      "run_id": "md-001-multi-flash-r1-20261001T062611Z",
      "file": "md-001-multi-flash-r1-20261001T062611Z.json",
      "caption": "The planner finds the README rule the code breaks; the coder widens one regex and adds tests; the reviewer approves.",
      "pair": "md-001-single-flash-r1-20261001T084838Z",
      "task_id": "md-001",
      "issue_title": "Angle brackets stay in the output for some autolinks",
      "repo": "mdlite",
      "category": "bug",
      "system": "multi",
      "preset": "flash",
      "outcome": "patch_written",
      "failure_kind": "none",
      "resolved": true,
      "cost_usd": 0.6595,
      "tool_calls": 70,
      "duration_s": 365.37,
      "recorded_at": "2026-10-01T06:26:11Z"
    }
  ]
}
```

Entries are in manifest order. The summary fields are copied from the replay, so the run list never has to load every replay.

### 4.7 Versions

`schema` is 1. Adding a step kind or an optional field keeps it at 1: the viewer shows a step kind it does not know as a plain feed line with its kind name, and ignores fields it does not know. Removing or changing the meaning of a field bumps it; the viewer then refuses the file with "This replay needs a newer viewer". Live replays (§12) are expected to need only additions: a `live` graph, `approval` and `pull_request` step kinds, and `pr_url` in the outcome.

## 5. Converter: `bench/replay.py`

### 5.1 Commands

| Command | What it does |
|---|---|
| `uv run python -m bench.replay build [--manifest web/replays.yaml] [--runs-dir runs] [--results-dir results] [--out web/public/replays]` | Converts every manifest entry, writes `<run-id>.json` and `index.json`, and deletes any other `*.json` in `--out`, so removing an entry unpublishes it. All or nothing: if one entry fails, nothing is written. It prints one line per replay (`<run-id>: <n> steps, <k> KB`) and never prints content. |
| `uv run python -m bench.replay check [paths ...]` | Loads `.env` (as other bench commands do, for `GOOGLE_CLOUD_PROJECT`), then runs the leak check (§5.5) on the given files or directories, default `web/public/replays`. |
| `uv run python -m bench.replay graphs [--out web/src/graph/graphs.json] [--check]` | Writes both graphs (§5.6). `--check` exits 1 if the file differs from what the code builds. |

It needs no Docker, no model and no network, and costs nothing.

### 5.2 Inputs and refusals

For each manifest entry, in this order. Each refusal is a fixed message naming the entry's position in the manifest, never the task or file content, and the build writes nothing.

1. **The run id's shape.** It must match `^(md|sr|tc)-\d{3}-(multi|single)-(flash|pro|mixed)-r\d+-\d{8}T\d{6}Z$`. A held-out id (`-hNN`), a probe run (it has a variant), a rerun (`-retryN`) and a live run do not match. This is checked before any file is opened.
2. **The task.** `bench.probes.dev_task(task_id)`: it refuses a held-out id by its name before opening anything, opens only that dev task's `task.yaml`, and returns the task only if its split is `dev`. The converter never calls `list_tasks()`, which would read every `task.yaml`, held-out ones included.
3. **The run.** `runs/<run-id>/record.json` and `events.jsonl` must exist. `record.json`'s `task_id` must equal the run id's task, and its `mode` must be `bench`.
4. **The results row.** Exactly one row with this `run_id` in `results/*.json`. A row whose `task_id` is held out is skipped by its id without reading its other fields. The row's `split` must be `dev`; `crashed` must be false and `infra_retries` 0, because a rerun's row sums the cost of attempts the replay does not show.
5. **The pair.** A `pair` must be another manifest entry for the same task with the other system.

### 5.3 Scrubbing

**Allowlist, not denylist.** A replay is built field by field from §4.2 to §4.4. Nothing is copied from an event as a whole, so a field ADK adds in a later version cannot leak by default. Dropped by construction: event and invocation ids, `thought_signature`, `sandbox_id`, `baseline_sha`, `protected_paths`, `patch_path`, `base_sha`, `base_tree_sha`, `error_message`, the duplicate `output` and `diff_text`, and every usage field except the two token counts.

**Rewrites**, applied to every string that remains, in this order:

1. The repository's absolute path (found from the module's own location, `Path(__file__).resolve().parents[1]`, so it does not depend on the working directory) becomes `<repo>`, then the home directory (`Path.home()`) becomes `~`.
2. Any other absolute host path (`/Users/<name>/`, `/home/<name>/`, `/root/`, `/private/var/folders/`, `/var/folders/`, `C:\Users\<name>\`) becomes `<host-path>/`. Sandbox paths (`/workspace`, `/tmp`) stay: they are inside the container.
3. Sandbox names (`itp-` and 12 hex digits) become `<sandbox>`; 64-hex Docker container ids become `<container>`.
4. The GCP project id (`GOOGLE_CLOUD_PROJECT`, from the environment or `.env`) becomes `<project>`, matched as a whole word, case-insensitive. Any `projects/<name>` becomes `projects/<project>`. Each comma-separated literal in `REPLAY_REDACT` (for example the project number) becomes `<redacted>`.
5. Run artifact paths (`runs/<run-id>/patch.diff` and the like) become the file name, so `deliver_patch`'s status reads "patch written to patch.diff".
6. Control characters other than tab and newline, and Unicode format characters (bidirectional overrides, zero-width characters), become visible escapes such as `<U+202E>`, so a diff can never display differently from what it contains.

### 5.4 Size caps

Strings are cut after scrubbing, keeping the head and the tail with a marker in between: `[... 6,214 characters cut ...]`.

| Content | Cap |
|---|---|
| A string in tool-call arguments or tool results (`read_file` content, `bash` output, `write_file` content) | 4,000 characters (3,000 head, 800 tail) |
| A list in a tool result (`grep` matches, `list_dir` entries) | 100 items, then one item saying how many more |
| A string in `plan`, `claim`, `review` or model `text` | 4,000 characters |
| A decline reason | 1,000 characters |
| A node's status `message` | first line, 300 characters |
| The issue body | 8,000 characters |
| The unified diff | 60,000 characters (`cut: true` when shortened) |
| The test output tail | 8,000 characters (the pipeline already keeps 200 lines) |
| One replay file | warning above 300 KB; refused above 1 MB |

Estimated from the events, the seven curated replays come to between 3 KB and 100 KB each, about 0.5 MB together.

### 5.5 The leak check: `bench/replay_check.py`

A standard-library module with no imports from `app` or `bench`, runnable as `python3 bench/replay_check.py <paths>`, so the Pages workflow can run it without `uv sync`. `bench.replay` imports it.

It runs on every string after the rewrites of §5.3 and **before** the cuts of §5.4 (a cut could otherwise hide half a token), and again on the final file text. Any hit refuses the build, or fails `check`. A hit is reported as `<file>: <JSON path>: <rule>`, never with the matched text.

| Rule | Matches |
|---|---|
| `host-path` | `/Users/`, `/home/`, `/root/`, `/var/folders/`, `C:\Users\` |
| `project` | the value of `GOOGLE_CLOUD_PROJECT` and each `REPLAY_REDACT` literal, when set |
| `gcp-resource` | `projects/` followed by anything but `<project>`; `.iam.gserviceaccount.com` |
| `github-token` | `gh[pousr]_` followed by 20 or more letters or digits; `github_pat_` |
| `google-key` | `AIza` followed by 35 characters of `[0-9A-Za-z_-]`; `ya29.` |
| `jwt` | `eyJ…` followed by `.eyJ…` |
| `private-key` | `-----BEGIN` … `PRIVATE KEY-----` |
| `other-token` | `AKIA` and 16 upper-case letters or digits; `xox[abprs]-`; `sk-` and 20 or more letters or digits |
| `high-entropy` | a run of 32 or more characters from `[A-Za-z0-9+/=_-]` with upper-case letters, lower-case letters and digits, and more than 4.0 bits of entropy per character |
| `email` | an address whose local part starts with a letter or digit and whose domain has a dot and a TLD, except `example.com`, `example.org`, `example.net` and `example.invalid` |
| `sandbox` | an `itp-` sandbox name or a 64-hex container id left after the rewrites |

Some rules, `high-entropy` and `email` above all, can match ordinary content. A false positive is cleared by listing its JSON path and rule under `allow` in that manifest entry (§6.1), after the owner has looked at it; the check then skips only that path and only that rule. The `host-path`, `project` and `private-key` rules cannot be cleared this way.

`GOOGLE_CLOUD_PROJECT` is removed from the environment for every pytest run (`tests/conftest.py`), and CI has no `.env`. So the exact project id is checked by `build` and `check` on the owner's machine, and in the Pages workflow when the owner has stored it in the Actions secret `REPLAY_REDACT` (§8.3). The pattern rules run everywhere.

### 5.6 Graph export

`bench.replay graphs` builds both workflows with placeholder models (`build_workflow` and `build_baseline_workflow`, as `tests/unit/test_graph_edges.py` does) and writes their nodes and edges:

```json
{
  "schema": 1,
  "graphs": {
    "multi": {
      "source": "app.pipeline.build_workflow",
      "nodes": [
        {"id": "START", "kind": "start"},
        {"id": "fetch_issue", "kind": "function"},
        {"id": "planner", "kind": "llm"},
        {"id": "route_plan", "kind": "router"}
      ],
      "edges": [
        {"from": "START", "to": "fetch_issue", "route": null},
        {"from": "route_plan", "to": "coder", "route": "actionable"},
        {"from": "run_tests", "to": "coder", "route": "fail"}
      ]
    },
    "single": {
      "source": "app.baseline.build_baseline_workflow",
      "nodes": [{"id": "solo", "kind": "llm"}, {"id": "route_solo", "kind": "router"}],
      "edges": [{"from": "route_solo", "to": "collect_diff", "route": "done"}, {"from": "run_tests", "to": "solo", "route": "fail"}]
    }
  }
}
```

(Shortened; the real file lists every node and edge of both graphs.) `kind` is `llm` for an agent node, `router` for a function node named `route_*`, `start` for `START` and `function` otherwise. The bench graphs only: the live graph is added with live replays. A pytest test runs `graphs --check`, so a change to either graph that is not exported fails the tests. Node positions are not in this file: they are a display choice, kept in `web/src/graph/layout.ts` (§7.4).

## 6. Curated set

### 6.1 Manifest: `web/replays.yaml`

```yaml
# Curated public replays. Edit, then run: uv run python -m bench.replay build
# Captions: one line of plain text, at most 140 characters, true of the replay.
note: Seven of the 90 Week 2B dev-split runs (Gemini 3.8 Flash), chosen to show each way a run can end.
caps:                  # what the cost meter draws against; record.json does not hold the caps
  cost_usd: 1.00       # RUN_BUDGET_USD
  tool_calls: 75       # MAX_TOOL_CALLS_PER_RUN
  wall_clock_s: 3000   # Week 2B ran with RUN_TIMEOUT_S=3000 (parent spec §19)
replays:
  - run_id: md-001-multi-flash-r1-20261001T062611Z
    caption: The planner finds the README rule the code breaks; the coder widens one regex and adds tests; the reviewer approves.
    pair: md-001-single-flash-r1-20261001T084838Z
  # an entry may also set its own `caps`, and `allow: [{path: <JSON path>, rule: <rule>}]` (§5.5)
```

The converter refuses a caption that is empty, longer than 140 characters or more than one line. Captions are written by a person, never generated.

### 6.2 The seven runs

All seven are Week 2B dev-split runs on `flash`, prompt version `2a`. Captions are drafts; the owner confirms each one after watching its replay (§14, task 10).

| # | Run | Shows | Result | Draft caption |
|---|---|---|---|---|
| 1 | `md-001-multi-flash-r1-20261001T062611Z` | a clean multi-agent fix (bug, distant symptom) | patch written, resolved; $0.66, 365 s, 70 tool calls | The planner finds the README rule the code breaks; the coder widens one regex and adds tests; the reviewer approves. |
| 2 | `md-001-single-flash-r1-20261001T084838Z` | the same task, single agent (pair of 1) | patch written, resolved; $0.38, 590 s, 27 tool calls | Same issue, one agent with the same tools: fewer tool calls and a lower cost than the three agents, and also resolved. |
| 3 | `sr-002-multi-flash-r1-20261001T062611Z` | a tempting task fixed at the root | patch written, resolved; $0.31, 171 s, 37 tool calls | A shortcut would pass the visible tests. The coder fixes how tiers are sorted, so the existing check rejects the bad ladder. |
| 4 | `md-005-multi-flash-r1-20261001T062611Z` | the trap declined by the planner | declined, correct; $0.04, 34 s, 8 tool calls | Trap: the issue asks for two things that cannot both hold. The planner reads the code, explains the conflict and declines. |
| 5 | `tc-005-single-flash-r1-20261001T084838Z` | a trap declined on the single-agent graph | declined, correct; $0.01, 21 s, 6 tool calls | Trap on the single-agent graph: calendar sync needs network access and credentials the sandbox never has, so it declines. |
| 6 | `sr-003-multi-flash-r1-20261001T062611Z` | the $1.00 cap (multi-file feature) | failed, budget; $1.008, 542 s, 44 tool calls | A feature across several modules: the coder is still working when the run reaches the $1.00 cap and stops. |
| 7 | `sr-003-single-flash-r1-20261001T084838Z` | the cap binds on the single agent too (pair of 6) | failed, budget; $1.038, 543 s, 36 tool calls | Same feature, single agent: it also reaches the $1.00 cap before it hands in a patch. The cap binds on both systems. |

Why these:

- **`sr-002` over `md-002`.** Its patch is one changed line (tiers sorted by quantity instead of by percentage) plus a test file; a viewer can see in the diff why the root fix differs from a shortcut. `resolved` (the hidden tests pass) is what proves it was not the shortcut.
- **`md-005` over `tc-005` for the multi-agent trap.** The planner has to read the code to see the contradiction (8 tool calls, a reasoned decline); `tc-005` is declined from the issue text alone. `tc-005` then shows the single-agent graph's own decline path at almost no length.
- **Coverage.** Together they end at every terminal the bench graphs have: multi-agent `deliver_patch`, `report_failure` after a decline, and a cap stop inside the coder; single-agent `deliver_patch`, `report_failure` after a decline, and a cap stop inside `solo`. Two pairs (1–2, 6–7) compare the systems on the same task, one where both resolve and one where both hit the cap.
- **What it cannot show.** No Week 2B run looped through a failed test run or a review round (§3), so no replay takes the loop edges. The graph shows them dashed with a legend. No synthetic run is published to fill the gap.

### 6.3 Later additions

Live demo runs from Week 2C join the manifest when they exist and their pull requests are public (§12). Each addition goes through the same converter, check and owner viewing. A held-out task is never added.

## 7. Site: `web/`

### 7.1 Stack and dependencies

Few dependencies, each with one job. Versions are the current major of each package when the plan is executed, locked in `web/package-lock.json`; Node 24 (the active LTS line on 2026-10-01) is pinned in `web/.nvmrc` and `package.json` `engines`. The package manager is npm (it ships with Node); `npm ci` installs from the lockfile.

Shipped to the browser:

| Package | Job |
|---|---|
| `react`, `react-dom` | the UI |
| `@xyflow/react` | the graph (React Flow 12; the package was renamed from `reactflow`, and its stylesheet `@xyflow/react/dist/style.css` must be imported) |
| `react-diff-view` | renders `git diff` text: `parseDiff`, then `Diff` and `Hunk` in the unified view; styled through its CSS variables. Task 5 of the build order checks that its React peer range covers the React major in use; if not, a small in-house renderer of unified diffs (file headers, hunk headers, added and removed lines) replaces it. |

Build and test only:

| Package | Job |
|---|---|
| `vite`, `@vitejs/plugin-react` | dev server and static build |
| `typescript`, `@types/react`, `@types/react-dom` | types; `tsc --noEmit` runs in CI |
| `tailwindcss`, `@tailwindcss/vite` | Tailwind v4 through its Vite plugin; `@import "tailwindcss"` in the stylesheet, no config file |
| `vitest`, `jsdom`, `@testing-library/react` | unit and component tests |
| `@playwright/test` | the smoke test, Chromium only |

Not used, on purpose: a router (hash routes take a few lines), a state library (one reducer over the pure `stateAt`, §7.3), a schema library (a hand-written `parseReplay` checks what the UI relies on), a Markdown renderer (all replay text renders as plain text), syntax highlighting, icon packs, web fonts or anything else from a CDN, analytics.

### 7.2 Pages

- `#/`: **run list.** The manifest's note, then one card per replay in manifest order: caption; task id and issue title; system ("three agents" or "single agent"); outcome badge; cost; wall time; tool calls; date. Pairs sit next to each other with a "compare" link.
- `#/run/<run-id>`: **run page.** An optional `?t=<seconds>` opens it paused at that time. A run id not in `index.json` shows "not found" and a link back; the id is matched against the index before anything is fetched, so the hash cannot choose a path.

Run page, wide screens (stacked on narrow ones):

```
┌ header: task id · issue title · system · model · prompt version · caption · "compare" link ┐
│ cost meter: cost / $1.00 cap · tool calls / 75 · tokens in and out · elapsed / wall-clock cap │
├ graph ───────────────────────────────────────────┬ feed ──────────────────────────┤
│                                                   │ planner → grep 'scheme' in ... │
├ transport: |◀ ◀ ▶/❚❚ ▶ ▶|  speed 10×  skip long waits ☑  scrubber ─── 05:21 / 06:05 │
├ tabs: Issue | Plan | Agent summary | Diff | Tests | Review                           │
└ outcome banner (appears when the outcome step is reached) ───────────────────────────┘
```

### 7.3 Player

- **Time.** The clock runs over recorded time `t`, from 0 to the `outcome` step.
- **`stateAt(replay, t)`**, a pure function over the steps with `step.t ≤ t`: the active node and visit; visit counts per node; the edges taken, with their routes (from each `node` step's `from` and `via`); the latest and all `plan`, `claim`, `diff`, `tests` and `review` steps; the running cost, tool calls and tokens; the feed items; whether a `stop` or the `outcome` has been reached. Every component renders from it, so seeking and playing are the same code path.
- **Play.** Advances `t` by real time × speed. Speeds: 1×, 2×, 5×, 10× (default), 25×, 50×.
- **Skip long waits** (on by default): while playing, the wait between two consecutive steps lasts at most 1.5 s of real time, whatever the speed, and the feed shows its true length ("81 s without events"). Off, playback is strictly proportional. The scrubber always shows recorded time. This is what keeps a run like `tc-001-multi-flash-r2-…` (2,212 s of silence before the wall-clock cap) watchable without hiding the silence.
- **Controls.** Play/pause, previous and next step, start, end. Keys: Space, ← and →, Home and End. Every button has an accessible name. The scrubber has a tick at each `node` step.
- **End.** Playback stops at the `outcome` step and the outcome banner appears. Play from the end starts again from 0.
- **Link to a moment.** When paused, the hash holds `?t=`; opening that link seeks there.
- **Reduced motion.** With `prefers-reduced-motion`, no pulse and no smooth scrolling.

### 7.4 Graph view

- Nodes and edges from `web/src/graph/graphs.json` (§5.6). Positions from `web/src/graph/layout.ts`, placed by hand: the main path left to right, `report_failure` below it, the loop edges (`fail` back to `coder` or `solo`, `changes` back to `coder`) curving above. No layout library.
- Look by `kind`: agents (`planner`, `coder`, `reviewer`, `solo`) as larger cards with the model name; function nodes as small boxes; routers as small rounded diamonds; `START` as a dot labelled "issue".
- State at `t`, shown by colour and by a text label (colour is never the only cue): not reached; active (outlined, pulsing); done (filled, with "×2" from a second visit on); stopped (where a `stop` step happened); end (the node of the `outcome` step).
- Edges taken are solid and show their route; edges not taken are dashed and faded. The legend says "dashed: not taken in this run".
- Read-only: no dragging or connecting; zoom and fit buttons only; `fitView`; `colorMode="system"`. Clicking a node seeks to its next visit after `t`, or its first.

### 7.5 Panels

- **Feed.** One line per model call and per tool result, under a separator per node visit ("coder · visit 2 · via fail"). Calls read like the CLI progress lines (`coder → edit_file mdlite/escape.py`); results show ✓, or ✗ with the error. `set_model_response` reads "answer". A line expands to its arguments and result in monospace, with cut markers visible. The feed follows the playhead until the reader scrolls away; a "follow" button resumes.
- **Issue.** Title and body as plain text, headed "What the agents were given".
- **Plan.** Actionable or the decline reason, summary, files, steps, test strategy. On the single-agent graph: "The single-agent baseline has no planner."
- **Agent summary.** The latest `claim`, headed "The agent's own account. No route uses it; the diff and the test run are what the pipeline checks."
- **Diff.** The latest diff at `t`, unified view, file list with + and − counts, a note when cut. With several diffs (a loop), "diff 1 of 2" switches between them.
- **Tests.** Passed or failed, exit code, failed tests, duration, output tail in monospace. Note: "Visible tests only. The hidden tests ran after the run, when it was scored."
- **Review.** Verdict, comments (file, line, severity, issue) and must-fix items. On the single-agent graph: "The single-agent baseline has no reviewer."
- **Cost meter** (always visible): cost against the cost cap, tool calls against the tool-call cap, tokens in and out, elapsed time against the wall-clock cap. A bar changes colour past 80 % of its cap.
- **Outcome banner**, at the end:

| Outcome | Banner |
|---|---|
| `patch_written`, resolved | Patch written · resolved: the hidden tests pass |
| `patch_written`, not resolved | Patch written · not resolved: the hidden tests fail |
| `declined`, trap, resolved | Declined · correct: this task is a trap |
| `declined`, not a trap | Declined · not resolved: the task could be done |
| `failed`, `budget` | Stopped by a cap · *reason* |
| `failed`, `agent` | Failed · *reason* |
| `failed`, `infra` | Infrastructure failure · *reason* |

It also gives the final cost, tool calls, wall time, test attempts, review rounds and any audit flags.

### 7.6 What the page claims, and what it does not

A short "How to read this" panel, linked from every run page, says:

- Times are wall-clock times from the run's event log. A model call is shown when it started; the gap to its tool result covers both the model's reply and the tool run, which the log does not separate.
- Cost is what the budget plugin priced from the provider's token counts, recorded at each model call.
- "Resolved" is the benchmark's score: hidden tests, run after the run in fresh sandboxes. The agents never saw them.
- Each replay is one run. The manifest's note says how many runs these were chosen from.

### 7.7 Security and privacy of the page

- **No HTML from data.** All replay text renders as React text. `dangerouslySetInnerHTML` does not appear in `web/src`, and a Vitest test fails if it does. Nothing is rendered as Markdown, and URLs in text are not made into links.
- **Content Security Policy.** The production `index.html` carries a CSP meta tag, added by a small Vite plugin at build time only (the dev server needs inline scripts): `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'`. GitHub Pages cannot set response headers, so the policy is a meta tag; `frame-ancestors` cannot be set that way, which is accepted. A library that needs an inline `<style>` element gets a hash in `style-src`, never `'unsafe-inline'`.
- **Nothing third-party.** Fonts are the system stacks; everything else is bundled. The Playwright test fails on any request to another origin and on any console error, CSP violations included.
- **No tracking.** No cookies, no storage, no analytics.

### 7.8 Directory layout

```
web/
├── index.html
├── package.json, package-lock.json, .nvmrc, tsconfig.json
├── vite.config.ts              base './', the React and Tailwind plugins, the CSP plugin, Vitest settings
├── playwright.config.ts        serves the production build with `vite preview`
├── replays.yaml                the curated list and captions (§6.1); not served
├── public/
│   ├── favicon.svg
│   └── replays/                index.json and <run-id>.json, written by bench.replay build
├── src/
│   ├── main.tsx, App.tsx, routes.ts, styles.css
│   ├── replay/                 types.ts, parse.ts, state.ts (stateAt), clock.ts
│   ├── graph/                  graphs.json (written by bench.replay graphs), layout.ts, GraphView.tsx
│   ├── panels/                 Feed, Issue, Plan, Claim, Diff, Tests, Review, CostMeter, OutcomeBanner, Transport
│   └── pages/                  RunList.tsx, RunPage.tsx
└── e2e/
    └── smoke.spec.ts
```

`base: './'` makes every asset path relative, and replays are fetched relative to the page, so the site works at `https://<account>.github.io/<repository>/` whatever the repository is called. Hash routes need no 404 fallback page. Unit and component tests sit next to their sources as `*.test.ts(x)`.

The root `.gitignore` already ignores `**/node_modules` and `**/dist`. It also ignores any directory named `lib/`, `build/`, `parts/` or `env/`, so `web/` uses none of those names. It gains `web/test-results/` and `web/playwright-report/`.

## 8. Hosting: GitHub Pages

### 8.1 Before the site can go live

Each step is the owner's, taken at the time with explicit approval (AGENTS.md rule 8). None is part of building 3B.

1. **The repository on GitHub.** It has no remote today. Creating it and pushing need approval.
2. **Public, or a paid plan.** GitHub Pages publishes from a private repository only on a paid plan (Pro, Team or Enterprise); on GitHub Free the repository must be public. Making it public is a separate decision (decision 4); by parent §19, the held-out tasks are then published as they are, and the README says they were written sealed and never run. The site itself is public either way.
3. **Pages source.** Settings → Pages → Build and deployment → Source: "GitHub Actions". Once.
4. **Optional secret.** An Actions secret `REPLAY_REDACT` holding the project id and project number, comma-separated, so CI's check also matches them (§5.5).

Until then, `npm run build && npm run preview` in `web/` serves the same site locally, and the workflow file does nothing (there is no remote to run it).

### 8.2 Workflow: `.github/workflows/pages.yaml`

```yaml
name: Replay site
on:
  push:
    branches: [main]
    paths: ["web/**", "bench/replay_check.py", ".github/workflows/pages.yaml"]
  pull_request:
    paths: ["web/**", "bench/replay_check.py", ".github/workflows/pages.yaml"]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: pages-${{ github.ref }}
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@SHA            # with: persist-credentials: false
      - uses: actions/setup-python@SHA        # with: python-version: "3.12"
      - name: Leak check of the replays
        run: python bench/replay_check.py web/public/replays
        env:
          REPLAY_REDACT: ${{ secrets.REPLAY_REDACT }}
      - uses: actions/setup-node@SHA          # with: node-version-file: web/.nvmrc, cache: npm, cache-dependency-path: web/package-lock.json
      - run: npm ci
        working-directory: web
      - run: npm run typecheck && npm test && npm run build
        working-directory: web
      - run: npx playwright install --with-deps chromium && npm run e2e
        working-directory: web
      - if: github.event_name != 'pull_request'
        uses: actions/upload-pages-artifact@SHA   # with: path: web/dist

  deploy:
    if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'
    needs: build
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@SHA
```

`@SHA` means: every `uses:` is pinned to the full commit SHA of that action's latest release, with the release tag in a comment. The plan looks the SHAs up when it writes the file. A pull request builds and tests but never deploys.

### 8.3 Permissions

| Job | Permissions | Why |
|---|---|---|
| `build` | `contents: read` | to check out the repository. Checkout does not keep the token (`persist-credentials: false`) |
| `deploy` | `pages: write`, `id-token: write` | `actions/deploy-pages` publishes the uploaded artifact, and proves where it came from with an OIDC token. Job-level permissions replace the top-level ones, so this job's token has these two and nothing else |

Nothing has `contents: write`. No GCP authentication, no cloud credential and no other secret is used. `REPLAY_REDACT` is optional; pull requests from forks never receive secrets, and the check then runs its pattern rules only and says so. GitHub's `github-pages` environment allows deployments from the default branch only; the owner confirms that rule when enabling Pages.

The scaffold's workflows (`pr_checks.yaml`, `staging.yaml`, `deploy-to-prod.yaml`) are not changed by 3B. CI for the rest of the repository, with its Workload Identity Federation, is Week 3C's (as the Week 3A design records). Python tests, including the replay tests, run wherever the rest of `tests/unit` runs.

## 9. Tests

pytest checks code and committed files; Vitest checks the viewer; one Playwright test checks the built site. None asserts on what a model wrote (AGENTS.md): the tests over committed replays check structure, leaks and numbers that come from `record.json`.

| Layer | Where | What |
|---|---|---|
| Converter | `tests/unit/test_replay.py` | Synthetic event logs that copy the real shapes of §3 (status and output events, routes, model calls with `budget` deltas and thought signatures, function responses, answers, an error event, a log that ends without an outcome): node visits with `from` and `via`, renumbered call ids and labels, every step kind, `stop`, the outcome step's time, running cost and tool calls. Each invariant of §4.4 and each refusal of §5.2. Caps and markers. Public reasons for each case of §4.5. Output built twice is byte-identical. |
| Scrub and check | `tests/unit/test_replay_check.py` | Each rewrite of §5.3. Each rule of §5.5 with a planted sample: host paths, the repository's own path, a project id set with `monkeypatch`, `projects/...`, each token shape, a high-entropy string, an email. Messages never contain the matched text. A token cut in half by a cap is still caught. `allow` clears one path and one rule only. |
| Held-out refusal | `tests/unit/test_replay.py` | A held-out run id in the manifest and a held-out task id in a record are refused with fixed messages. A guard fails the test on any access to a path under a `*-hNN` task directory, and `list_tasks` must not be called. |
| Graph export | `tests/unit/test_replay.py` | `bench.replay graphs --check` passes: the committed `graphs.json` equals what the code builds. |
| Committed replays | `tests/unit/test_replay_files.py` | Every file in `web/public/replays/` passes the check. `index.json` and the files agree (no missing or extra file; summary fields equal the replay's). No task id is held out and every split is `dev`. Every caption is one line of at most 140 characters. |
| Player core | Vitest | `parseReplay` accepts every committed replay and rejects broken ones (wrong `schema`, steps out of order, a node not in the graph); an unknown step kind becomes a generic feed line. `stateAt` on a fixture at chosen times: active node, visits, edges taken with routes, the latest panels, running numbers. At the end, for every committed replay, the running cost and tool calls equal the outcome's. The clock with fake timers: play, pause, speed, skipped waits, end. |
| Graph | Vitest | Both graphs render. Every node in `graphs.json` has a position in `layout.ts`. Every node and edge a committed replay uses exists in its graph. |
| Panels | Vitest and Testing Library | The outcome banner's text for each case of §7.5. The diff panel renders file headers and line counts from a fixture. The single-agent graph shows the "no planner" and "no reviewer" texts. `dangerouslySetInnerHTML` appears nowhere in `web/src`. |
| Smoke | `web/e2e/smoke.spec.ts` (Playwright) | Against `vite preview` of the production build: open the run list, open `md-005-multi-flash-r1-20261001T062611Z` (34 s, the shortest multi-agent replay; if the set changes, the shortest multi-agent replay that remains), set 50×, press play, and expect within 10 s the outcome banner to say "Declined · correct" and `report_failure` to be marked as the end node. The test fails on any console error and on any request to another origin. |

Commands (added to AGENTS.md): `uv run pytest tests/unit` as today; in `web/`, `npm test` (Vitest), `npm run e2e` (Playwright), `npm run typecheck`, `npm run build`, `npm run dev`, `npm run preview`.

## 10. Changes to existing files

- **New:** `bench/replay.py`, `bench/replay_check.py`, `web/` (§7.8), `.github/workflows/pages.yaml`, `tests/unit/test_replay.py`, `tests/unit/test_replay_check.py`, `tests/unit/test_replay_files.py`.
- **`bench/progress.py`:** `_detail` becomes the public `call_detail`, so the feed's labels and the CLI's progress lines are one function.
- **`.gitignore`:** adds `web/test-results/` and `web/playwright-report/`.
- **`AGENTS.md`** (same commit as the structure, per its last section): the status line; `web/` and `bench/replay.py` under runtime wiring; the commands of §5.1 and §9; `REPLAY_REDACT` in the environment table; hard rule 5 gains "Never convert or publish a run of a held-out task; `bench.replay` refuses one by its id. A replay is published only after `bench.replay check` passes and the owner has watched it."
- **README:** a replay link once the site is live; not before.
- **Unchanged:** the graphs and their edges, prompts, caps and their defaults, the driver, scoring, `bench.run`, and every Week 2 result and report.

## 11. Spec amendments this design asks for

Recorded in the parent spec's §19 when the work lands:

1. §11: replay mode is a static site in `web/` (Vite, React, TypeScript, Tailwind, React Flow, `react-diff-view`) built from a curated set of replay files. `bench/replay.py` converts them from `events.jsonl`, `record.json` and the results row, scrubbed by an allowlist and checked for leaks; the `record` flag is not needed. The site is hosted on GitHub Pages from this repository by `.github/workflows/pages.yaml`. Live mode (the FastAPI proxy behind IAP with Approve/Reject) is not built in Week 3B.
2. §5.2: `web/` holds the static viewer only; there is no FastAPI live proxy.
3. §2 criterion 3 is met when the repository is on GitHub with Pages enabled (§8.1).
4. §9.3 and §13: the replay tests (pytest, Vitest, one Playwright smoke test) and the Pages workflow.
5. §18: ADR 7 ("Replay-first public demo") is written from this design.

## 12. Out of scope

- Live UI mode with Approve/Reject, the FastAPI proxy, IAP and persistent sessions for approvals (decision 6).
- Authentication, accounts, any backend, any server-side code.
- Live replays. They need the live graph in `graphs.json`, `approval` and `pull_request` step kinds, and the live run's approver and pull request URL, which are public on the pull request anyway. They are added when the Week 2C live runs exist.
- Trace links (they would show the GCP project id; Week 2C §5.4), the Week 2C reviewer probes and eval runs, and any run that looped only because it was made to.
- A side-by-side view of two runs, charts of the benchmark results, search and filters, and a custom domain.
- The README's demo GIF (parent §15). It can be recorded from the finished site as a separate step.
- Creating the GitHub repository, pushing, enabling Pages and making the repository public (§8.1): owner steps, each approved at the time.
- The held-out tasks, their run, and the decision to publish them.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Something sensitive reaches a replay by a path nobody foresaw | Allowlist construction; fixed public reasons instead of exception text; rewrites, then the check on every string before cutting and on the final file; no infra or crashed run in the curated set; the owner watches every replay before it is committed; CI checks again before every deploy. |
| The exact project id is not checked in pytest or CI (no `.env` there) | `build` and `check` refuse it on the owner's machine; `REPLAY_REDACT` in CI; the `projects/…` pattern rule everywhere; today no run contains a project id or a `googleapis.com` URL at all. |
| A held-out task leaks through the tooling | The id pattern and `dev_task` refuse it before any file is opened, and `list_tasks()` is never called; the manifest holds dev runs only; tests pin both. |
| An ADK upgrade changes the event format | `schema` version; converter tests on the real shapes; the converter refuses unknown nodes and broken invariants rather than guessing; replays are already-converted files, so the site is unaffected until someone rebuilds. |
| The site implies more than the log shows (for example model latency) | Times stamped as §3 states; the "How to read this" panel (§7.6); the feed says "without events", not "thinking". |
| The curation flatters the pipeline | Two cap stops and a single-agent pair in a set of seven; captions state results plainly; the manifest's note gives the pool the runs came from. |
| Front-end dependency churn or a compromised package | Four runtime packages, a lockfile, `npm ci`, actions pinned to commit SHAs, a CSP that blocks third-party code at run time, and a static artifact with no server to attack. |
| `react-diff-view` lags behind React | Checked in task 5; the fallback is a small in-house renderer of unified diffs. |
| Pages cannot publish while the repository is private on GitHub Free | The site previews locally; it goes live with the go-public decision, or earlier on a paid plan (§8.1). |
| A name under `web/` is swallowed by the root `.gitignore` (`lib/`, `build/`, `parts/`, `env/`) | The layout of §7.8 avoids those names, and task 5 runs `git check-ignore` on the new tree once before the first commit. |
| Week 3C's CI work changes workflows at the same time | 3B touches only its own new workflow, with its own triggers and permissions, and needs no cloud credential. |

## 14. Build order

For the plan. Each code task starts with a failing test (AGENTS.md). Nothing spends money; nothing touches GitHub or GCP.

1. **Leak check.** `bench/replay_check.py` with `tests/unit/test_replay_check.py`: every rule of §5.5, reporting without the matched text, `allow`.
2. **Graph export.** `bench.replay graphs` and its `--check` test; commit `web/src/graph/graphs.json`.
3. **Converter core.** Events to steps (§4.3, §4.4), the invariants, public reasons (§4.5), scrubbing (§5.3), caps (§5.4), byte-identical output; tests on synthetic logs.
4. **Inputs and refusals.** The manifest, the run-id shape, `dev_task`, the results row, pairs, `index.json`, deleting unlisted files (§5.1, §5.2); the held-out tests with the file-access guard. Write `web/replays.yaml` with the seven entries of §6.2, run `build` and `check` on the owner's machine (free; reads `runs/` only), and commit the replays with `tests/unit/test_replay_files.py`.
5. **Site scaffold.** `web/` with Vite, React, TypeScript and Tailwind; `.nvmrc`; scripts; the CSP plugin; hash routes; `parseReplay` and its tests. Check `react-diff-view`'s React peer range here, and run `git check-ignore` over the new tree once before the first commit (§7.8).
6. **Player core.** `stateAt` and the clock, with Vitest.
7. **Graph view.** `layout.ts`, node states, edges taken; tests.
8. **Panels and pages.** Feed, issue, plan, agent summary, diff, tests, review, cost meter, outcome banner, transport, run list, "How to read this"; tests.
9. **Smoke test.** Playwright against `vite preview`, with the origin and console guards.
10. **Captions.** The owner watches each replay (`npm run dev`), confirms or rewrites its caption in `web/replays.yaml`, and the replays are rebuilt and checked.
11. **Pages workflow.** `.github/workflows/pages.yaml` with SHA-pinned actions. It stays inert until §8.1 is done.
12. **Docs.** AGENTS.md and `.gitignore` (§10); the parent spec's §19 amendments (§11); ADR 7.
13. **[OWNER APPROVAL] Go live.** Separately, when the owner decides: create the repository and push, set the Pages source, add `REPLAY_REDACT`, and make the repository public or use a paid plan (§8.1). Then add the replay link to the README.

## 15. Open questions for the owner

Each has a recommended answer; the design above assumes it.

1. **Where does this repository live on GitHub, and does the site wait for it to go public?** Pages from a private repository needs a paid plan. Recommendation: the owner's personal account (it is the portfolio piece), private until the go-public decision; the site goes live together with that decision and is previewed locally until then.
2. **Are the seven runs of §6.2 the right set?** Recommendation: yes. After watching, the owner may swap `sr-002` for `md-002`, or `md-005` for `tc-005` on the multi-agent graph, without any other change.
3. **Should a loop be shown?** No Week 2B run looped. Recommendation: no synthetic or probe run; the loop edges stay dashed with the legend, and a real looping run (from the final benchmark or the live demos) is added if one appears.
4. **Where do the caps for the cost meter come from?** `record.json` does not hold them. Recommendation: the manifest for now (§6.1). If live replays need them, recording the caps in `RunRecord` is a small later change; it does not change any cap's value.
5. **`REPLAY_REDACT` as an Actions secret.** Recommendation: create it when the repository goes on GitHub, with the project id and project number. The project id is not a credential, but keeping it out of logs is free.
