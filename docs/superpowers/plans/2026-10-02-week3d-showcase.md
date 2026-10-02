# Week 3D: Showcase — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Spec approved by the owner on 2026-10-02 (commit `7ef4da3`). Execution method: subagent-driven (chosen for Week 3).

**Goal:** A README that works as the project's landing page, a whole-system architecture (generated overview image with GCP icons plus Mermaid views), decision records 1 to 6, and a demo GIF of a real issue becoming a pull request.

**Architecture:** `docs/architecture/overview.py` (the `diagrams` library) renders `overview.svg`/`.png` and stamps its own source hash into the SVG; `docs/architecture/agent_graph.py` writes the agent-graph Mermaid block into `docs/architecture.md` from `web/src/graph/graphs.json`; `tests/unit/test_architecture.py` keeps both honest and checks links. ADRs and the README are prose written from the specs and run logs. The GIF comes from one owner-run live run.

**Tech Stack:** Python 3.12 via `uv` (new dependency group `docs` with `diagrams`), Graphviz (Homebrew), Mermaid (rendered by GitHub), `ffmpeg` and `gifski` (Homebrew) for the GIF.

**Spec:** `docs/superpowers/specs/2026-10-02-week3d-showcase-design.md`; parent spec `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md` (§2, §15, §18, §19).

## How to read the tasks

The plan fixes file names, section lists, the tests and the facts that must appear; the prose and the drawing are the implementer's. Tasks 1, 2 and 3 run in parallel (disjoint files). Task 4 is the owner's live run; Tasks 5 and 6 are the controller's.

## Global Constraints

- Python 3.12 via `uv` only. Never call `pip`. Work on branch `week3d-showcase`; never commit to `main` directly.
- No code path, prompt, cap, model or workflow behaviour changes. Only docs, the `docs` dependency group, the `diagrams` Make target and the architecture tests.
- Every fact in the README, the ADRs and the diagrams must be true today and come from the repository: the specs (§19 included), `AGENTS.md`, `docs/results/*` and the code. When a source and the code disagree, the code wins and the report says so. Numbers are copied from committed reports, never recomputed.
- Benchmark numbers (from `docs/results/2026-10-01-2b-comparison.md`): single agent 36 of 45 resolved, $0.38 a run, $0.47 per resolved, median 171 s; multi-agent 34 of 45, $0.43 a run, $0.57 per resolved, median 319 s; the three multi-file features failed for both at the $1.00 cap; the reviewer approved all 28 patches it saw. Cloud parity (`docs/results/2026-10-02-3a-cloud-parity.md`): the same outcome on all three tasks.
- Nothing private in any committed file or image: no token, no `.env` value, no home-directory path (`/Users/...`), no email other than what git history already shows. The project id is public by the owner's decision; do not add the project number.
- Held-out tasks: never opened, printed or diffed; review diffs exclude `':(exclude,glob)bench/tasks/*-h[0-9][0-9]/**'`. Describe the held-out split only from the spec (5 tasks, written sealed, never run).
- Writing: plain, specific, short sentences; no marketing adjectives; numbers with their sample size. Link instead of repeating.
- Commits: plain messages, owner identity only, no AI attribution. Never bypass commit signing. `agents-cli lint` passes (codespell checks Markdown too) and `uv run pytest -q --tb=no -m "not docker"` passes before each commit.

## Review Focus

1. **The overview SVG leaks a local path.** The `diagrams` library references its icon files by absolute path; an SVG can embed `/Users/<name>/.../site-packages/diagrams/...`. Expected: the committed SVG holds no `/Users/`, `/home/` or `site-packages` string and its icons render on GitHub (embedded as data, or a PNG is used). Test in Task 1.
2. **The committed overview is stale.** Expected: the SVG carries the SHA-256 of `overview.py`; the test fails when they differ. Test in Task 1.
3. **A broken link.** Expected: every relative link and image in `README.md`, `docs/architecture.md` and `docs/adr/*.md` resolves to a file in the repository. Test in Task 1 (covers the files Tasks 2 and 3 write once merged).
4. **A Mermaid block that GitHub cannot render.** Expected: each block is checked once with the Mermaid CLI (`npx -y @mermaid-js/mermaid-cli`) in Task 1 and its output reported; blocks use only flowchart syntax.
5. **A number in the README that is not in the reports.** Expected: the Task 3 review checks every figure against its source report and lists them.

---

## File Structure

```
docs/architecture.md                    NEW  the views and the overview image (Task 1)
docs/architecture/overview.py           NEW  diagrams-as-code overview (Task 1)
docs/architecture/overview.svg, .png    NEW  rendered (Task 1)
docs/architecture/agent_graph.py        NEW  writes the agent-graph Mermaid block (Task 1)
tests/unit/test_architecture.py         NEW  hash, graph block, links (Task 1)
pyproject.toml                          MOD  dependency group `docs` (Task 1)
Makefile                                MOD  `diagrams` target (Task 1)
docs/adr/0001-graph-workflow.md         NEW  (Task 2)
docs/adr/0002-deterministic-routing.md  NEW  (Task 2)
docs/adr/0003-hermetic-sandbox.md       NEW  (Task 2)
docs/adr/0004-one-sandbox-image.md      NEW  (Task 2)
docs/adr/0005-hidden-test-benchmark.md  NEW  (Task 2)
docs/adr/0006-runner-wide-budget.md     NEW  (Task 2)
README.md                               MOD  rewritten (Task 3)
docs/media/demo.gif                     NEW  (Task 5)
docs/results/2026-10-01-2c-live-log.md  MOD  the live run (Task 4)
AGENTS.md, parent spec §19              MOD  (Task 6)
```

---

### Task 1: Architecture

**Files:** as listed above for Task 1.

**Interfaces:** Produces `docs/architecture.md` (the README links it and embeds `docs/architecture/overview.svg`), `make diagrams`, `uv run --group docs python docs/architecture/overview.py`, `uv run python docs/architecture/agent_graph.py [--check]`.

**Required content**

- `overview.py`: clusters and nodes exactly as spec §4.1 lists (Laptop; GitHub with the four workflows, Pages and the demo organisation; GCP project with the engine, sandbox template and sandboxes, Artifact Registry and Cloud Build, Vertex AI Gemini, Cloud Trace, logs bucket, BigQuery and the log sink, the four service accounts, Workload Identity Federation, Billing budget, Pub/Sub, `budget-guard`; Local Docker), edges labelled with identity or data as spec §4.1 says. It writes `overview.svg` and `overview.png` next to itself, embeds icons so the SVG renders on GitHub without local files, and writes `<!-- source-sha256: <hex> -->` into the SVG.
- `agent_graph.py`: reads `web/src/graph/graphs.json` and writes the multi-agent graph (and the single-agent baseline graph) as Mermaid flowcharts between `<!-- agent-graph:start -->` and `<!-- agent-graph:end -->` in `docs/architecture.md`; `--check` exits 1 when the file differs from what it would write.
- `docs/architecture.md`: the overview image, then the four views of spec §4.2 (one run's journey; CI/CD and identity; observability and cost; the agent graph), each with two or three sentences. Identities and service names exactly as they exist (`issue-to-pr-app`, `sandbox-caller`, `ci-runner`, `deployer`, `github-actions`/`github-oidc`, `budget-guard`, `issue-to-pr-budget`, `issue_to_pr_telemetry`).
- `pyproject.toml`: `[dependency-groups] docs = ["diagrams>=0.24"]` (or the current release).
- `Makefile`: `diagrams:` runs `uv run --group docs python docs/architecture/overview.py` and `uv run python docs/architecture/agent_graph.py`; listed under the free targets in `help`.

**Tests** (`tests/unit/test_architecture.py`): `test_overview_svg_matches_its_source` (hash), `test_overview_svg_holds_no_local_paths` (Review Focus 1), `test_agent_graph_block_is_current` (runs the `--check` logic), `test_relative_links_resolve` (README, `docs/architecture.md`, `docs/adr/*.md`; skip `http(s)://` and `#anchor` links; Review Focus 3).

- [ ] **Step 1:** The controller installs Graphviz (`brew install graphviz`) before dispatch. Tests (RED).
- [ ] **Step 2:** Write the scripts and the page; `make diagrams`; GREEN. Check every Mermaid block once with `npx -y @mermaid-js/mermaid-cli` (render to a temporary file) and report the result (Review Focus 4).
- [ ] **Step 3:** Commit: `docs: whole-system architecture with generated diagrams`.

### Task 2: Decision records 1 to 6

**Files:** the six `docs/adr/000N-*.md` above.

Each follows `docs/adr/0007-replay-first-public-demo.md`: `# ADR N: <title>`, `Date: 2026-10-02. Status: accepted.`, a line naming its sources, then Context, Decision, Consequences; one page or less; links to the evidence (spec sections, run logs, results). Content from parent spec §18 and the sections it implies:

1. Graph workflow over autonomous multi-agent chat (§5; why a graph: bounded loops, testable routing, replayable runs).
2. Deterministic routing: function nodes route on real `git diff` output and test exit codes, never on what a model says (AGENTS.md hard rule 4; the review audit).
3. Hermetic, credential-free sandbox; git on the orchestrator side (§7; the 3A cloud tests: no token from the metadata server, no header reaches the container).
4. One sandbox image for local Docker and Agent Runtime (§7.3; the 3A parity result).
5. Hidden-test benchmark with a sealed held-out split and trap tasks (§9; the seal procedure; the owner's decision to drop the held-out run for now, §19 2026-10-01).
6. Runner-wide budget plugin (§6; equal caps for both systems; the first comparison's per-turn cap finding; the 75 → 100 tool-call change of 2026-10-02 with its data).

- [ ] **Step 1:** Write; `agents-cli lint` (codespell). **Step 2:** Commit: `docs: decision records 1 to 6`.

### Task 3: README

**Files:** `README.md`.

Sections exactly as spec §3 lists, in that order, with the numbers from the Global Constraints. The demo GIF line is `![Demo: an issue becomes a pull request](docs/media/demo.gif)` (the file arrives in Task 5); until then the README keeps that line and the link test skips this one path. The overview image is `docs/architecture/overview.svg`. Link all seven ADRs by their file names above. The replay site is `https://omarcevi.dev/sdlc-agent-pipeline/`. "Run it yourself" lists `uv sync`, `make test`, `uv run python -m bench.run --tasks tc-001`, and the live CLI line from AGENTS.md, and links AGENTS.md. Keep the CI badge.

- [ ] **Step 1:** Write; `agents-cli lint`. **Step 2:** List, in the report, every number in the README with the file and line it comes from (Review Focus 5). **Step 3:** Commit: `docs: README as the project's front page`.

### Task 4: The live run **[OWNER, about $0.50]**

- [ ] **Step 1:** Latency gate (three tool calls under 15 s each).
- [ ] **Step 2:** The owner starts a screen recording, then runs `uv run python -m app.live --repo issue-to-pr-demo/mdlite --issue 1 --approver omarcevi` in a terminal from the main checkout, reads the patch, types `approve`, and records the pull request page after it opens. A decline or cap is recorded as the outcome; the owner decides whether to try `stockroom#1`.
- [ ] **Step 3:** The controller adds the run to `docs/results/2026-10-01-2c-live-log.md`: run id, outcome, pull request link, cost, duration.

### Task 5: The GIF

- [ ] **Step 1:** `brew install gifski` (ffmpeg is installed). From the owner's recording: cut the waiting, speed up the agent phase, keep the approval readable; about 30 s; `docs/media/demo.gif` under 8 MB.
- [ ] **Step 2:** The owner checks the GIF for anything private.
- [ ] **Step 3:** README: the pull request link next to the GIF; the link test passes with the GIF present. Commit: `docs: demo GIF of an issue becoming a pull request`.

### Task 6: Close-out

- [ ] **Step 1:** Final whole-branch review (one fix wave if needed); merge `week3d-showcase` into `main`; push.
- [ ] **Step 2:** Parent spec §19 gains a dated Week 3D block; `AGENTS.md` Status and Commands (`make diagrams`, the architecture rule). Commit `docs: week 3D close-out`; push.

## Exit criteria

- The README shows the pitch, CI badge, GIF and replay link above the fold, and its numbers match the reports.
- `docs/architecture.md` has the overview image and the four views; the tests for the SVG hash, local paths, the graph block and links pass.
- ADRs 1 to 7 exist and are linked.
- The live run's pull request exists and the GIF shows it; spend about $0.50.
