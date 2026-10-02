# Week 3D design: the showcase (README, architecture, decision records, demo)

Date: 2026-10-02. Status: owner decisions given and the design approved in conversation on 2026-10-02; written spec awaiting owner review.
Parent spec: `2026-09-29-sdlc-agent-pipeline-design.md` (§2 audience, §15 Week 3: "README, architecture diagram, ADRs and demo GIF", §18 the ADR list). Builds on Weeks 3A (cloud), 3B (replay site) and 3C (CI and CD), all done.

## Owner decisions (2026-10-02)

1. **Architecture shows the whole system,** not only the agent graph: how the pipeline connects to each GCP service, to GitHub and to CI, and with which identity.
2. **Diagrams:** one polished overview image made as code with the official GCP icons, plus focused views in Mermaid.
3. **Demo GIF:** a real issue becoming a pull request, from one live run on a demo issue with the owner at the keyboard.
4. **README framing:** lead with the system; then state the measured result plainly (the single agent matched the three-agent pipeline on this task set) and what it teaches.

## 1. Why

- **Two readers** (parent §2): a recruiter has about 30 seconds (what it does, that it is real, a link to click); a hiring manager has about 5 minutes (architecture, decisions, honest numbers, cost).
- **Today's README** still says "under construction" and leads with benchmark tables. The repository has no architecture picture and one decision record of seven.
- **The demo** must show the project's promise, an issue turning into a reviewed pull request, not a recording of internals.

## 2. Success criteria

1. A visitor sees, above the fold: the one-line pitch, the CI badge, the demo GIF, the replay link.
2. `docs/architecture.md` shows the whole system: the overview image, and Mermaid views of one run's journey, CI/CD and identity, observability and cost, and the agent graph. Every component and identity shown exists today.
3. The overview image is generated from `docs/architecture/overview.py`; a test fails if the committed SVG does not match its source. The agent-graph view is generated from `web/src/graph/graphs.json`; a test fails if it drifts.
4. `docs/adr/0001` to `0006` exist, one page or less each, in the form of ADR 7, and the README links all seven.
5. The README's numbers match the committed reports.
6. One live run on a demo issue opened a pull request, and the GIF made from it is in the README (under 8 MB, about 30 s).
7. Spend: about $0.50 (the live run); everything else is free.

## 3. README (the landing page)

Top to bottom, short; details live in linked documents:

1. Title, one-line pitch ("A multi-agent pipeline on Google ADK 2.x that turns a GitHub issue into a tested, reviewed pull request"), the CI badge.
2. The demo GIF, then two links: **watch seven recorded runs** (the replay site) and **the pull request from the GIF** (on the demo repository).
3. **How it works:** the overview image and three sentences: planner, coder and reviewer agents in a graph whose routing comes from real diffs and test exit codes; every command in a hermetic sandbox (Docker locally, Agent Runtime in the cloud) with no credentials or network; a human approves before any pull request opens.
4. **What I measured:** the hidden-test benchmark (15 dev tasks on three repositories, three repeats, both systems under the same caps); the result stated plainly (single agent 36/45, $0.38 a run; three agents 34/45, $0.43; the same three multi-file features failed both at the $1.00 cap); what it teaches (the reviewer never changed an outcome; the extra agents cost more; the cap and context size, not the architecture, decided the hard tasks); the cloud parity result; a link to the full report.
5. **Engineering highlights,** one line each with a link: sandbox backends behind one interface; deterministic routing; the sealed held-out split; the $500 budget with a hard stop; keyless CI/CD with an approval gate; traces without message content; reviews that caught real bugs (a short list from the run logs).
6. **Run it yourself:** prerequisites and the four commands that matter (`uv sync`, `make test`, one bench task, the live CLI), linking AGENTS.md for the rest.
7. **Project documents:** decision records 1 to 7, the design spec, results, run logs.

The existing result sections move into the linked reports; nothing in them is lost.

## 4. Architecture

### 4.1 Overview image

- `docs/architecture/overview.py` builds the picture with the `diagrams` library (official GCP icons, rendered by Graphviz) and writes `docs/architecture/overview.svg` and `overview.png`.
- Clusters: **Laptop** (bench, live CLI, `make`), **GitHub** (repository, Actions workflows `ci`, `paid`, `release`, `pages`; Pages site; demo organisation with issues and pull requests), **GCP project** (Agent Runtime engine `issue-to-pr`; sandbox template and per-run sandboxes; Artifact Registry and Cloud Build; Vertex AI Gemini; Cloud Trace; logs bucket; BigQuery telemetry and the log sink; service accounts `issue-to-pr-app`, `sandbox-caller`, `ci-runner`, `deployer`; Workload Identity Federation; Billing budget, Pub/Sub topic, `budget-guard` function), **Local Docker** (sandboxes for laptop and CI runs).
- Edges carry the identity or the data: for example "WIF → ci-runner", "deployer, after approval", "sandbox-caller token", "spans (no message text)", "prompts as files", "over budget → disable billing".
- The script writes a hash of its own source into the SVG (an XML comment); `tests/unit/test_architecture.py` fails when the committed SVG's hash differs from the script's, so a changed picture source cannot ship with a stale image. Rendering needs Graphviz, so it runs on the laptop (`make diagrams`), not in CI; CI checks only the hash.
- `diagrams` goes in a new dependency group `docs` (`uv sync --group docs`); Graphviz is installed with Homebrew (`brew install graphviz`).

### 4.2 Focused views (`docs/architecture.md`)

Mermaid, rendered by GitHub:

1. **One run's journey:** issue → intake → planner → sandbox provision → coder ⇄ tools → diff and tests (real exit codes) → reviewer → human gate → pull request, marking where each step runs (laptop or Agent Runtime) and what crosses the sandbox boundary (commands in, output out; nothing else).
2. **CI/CD and identity:** the four workflows, their triggers, the guard, WIF, `ci-runner` and `deployer`, the `production` approval.
3. **Observability and cost:** spans to Cloud Trace with message references only, prompt files in the logs bucket, the analytics plugin and log sink into BigQuery, the per-run caps, the budget, the hard stop.
4. **The agent graph:** generated from `web/src/graph/graphs.json` by `docs/architecture/agent_graph.py` between marker comments in `docs/architecture.md`; `tests/unit/test_architecture.py` fails when the committed block differs from what the script writes (as `bench.replay graphs --check` does for the replay site).

## 5. Decision records

`docs/adr/0001` to `0006`, the form of ADR 7 (title, date, status, context, decision, consequences), each one page or less, written from the parent spec and the run logs with evidence links:

1. Graph workflow over autonomous multi-agent chat.
2. Deterministic function nodes route on real diffs and test exit codes, never on model claims.
3. Hermetic, credential-free sandbox; git runs on the orchestrator side.
4. One sandbox image for local Docker and Agent Runtime.
5. Hidden-test benchmark with a sealed held-out split and trap tasks (including the owner's decision to drop the held-out run for now).
6. A runner-wide budget plugin, for safety and for a fair comparison between the two systems.

## 6. Demo GIF

- **The run:** `uv run python -m app.live --repo issue-to-pr-demo/mdlite --issue 1 --approver omarcevi`, on local Docker sandboxes, from the owner's terminal. The owner reads the patch at the prompt and types `approve`; the pull request opens on the demo repository. The run is logged in the Week 2C live log. If the run ends without a pull request (a decline, a cap), that is recorded honestly and the owner decides whether to try `stockroom#1`.
- **Recording:** the owner screen-records the terminal and, after the run, the pull request page (macOS screen recording). The controller speeds the waiting parts up, trims it to about 30 seconds, and converts it with `ffmpeg` and `gifski` (Homebrew) to `docs/media/demo.gif` under 8 MB. Nothing private may be visible: the recording shows no token, no `.env`, no home path beyond what the terminal prompt shows; the owner checks the final GIF before it is committed.

## 7. Changes to files

- **New:** `docs/architecture.md`, `docs/architecture/overview.py`, `overview.svg`, `overview.png`, `agent_graph.py`; `docs/adr/0001` to `0006`; `docs/media/demo.gif`; `tests/unit/test_architecture.py`.
- **Modified:** `README.md` (rewritten), `pyproject.toml` (dependency group `docs`), `Makefile` (`diagrams` target), `AGENTS.md` (commands, the diagram rule), `docs/results/2026-10-01-2c-live-log.md` (the live run).
- **Unchanged:** code paths, prompts, caps, workflows' behaviour.

## 8. Tests

- `tests/unit/test_architecture.py`: the overview SVG carries the hash of `overview.py`; the agent-graph block in `docs/architecture.md` equals the generated one; every relative link in `README.md` and `docs/architecture.md` points at a file that exists; the README names each of ADR 1 to 7.
- The numbers in the README are copied from the committed reports; the plan's review checks them against the reports.

## 9. Out of scope

- Re-running the benchmark or changing any result.
- A video, a landing page outside GitHub, or a custom design system.
- The Medium article itself (its draft lives in the owner's notes and draws on these documents).
- The held-out run.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Graphviz output differs between machines | The image is rendered only by `make diagrams`; CI checks the source hash, not the pixels. |
| The live run declines or hits a cap | It is a real outcome: recorded honestly; the owner decides whether to try `stockroom#1`. |
| The GIF shows something private | The owner reviews the final GIF before it is committed; the terminal shows no token or `.env`. |
| The architecture picture drifts as the system changes | The hash test, and the agent graph generated from the same file the replay site uses. |
| Numbers in the README drift from the reports | Copied from the committed reports and checked in review. |

## 11. Build order

1. **[free]** `overview.py`, `agent_graph.py`, `docs/architecture.md`, the `docs` dependency group, `make diagrams`, `tests/unit/test_architecture.py`.
2. **[free]** ADRs 1 to 6.
3. **[free]** The README rewrite, with a placeholder line for the GIF until it exists.
4. **[OWNER, about $0.50]** The live run and its recording.
5. **[free]** The GIF, the README's final links, the 2C live log; push.
6. Close-out: parent §19, AGENTS.md status; push.
