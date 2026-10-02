# ADR 7: Replay-first public demo

Date: 2026-10-02. Status: accepted.
Design: `docs/superpowers/specs/2026-10-01-week3b-replay-ui-design.md` (§1, §5.3, §7).

## Context

The project is meant to be understood in about 30 seconds by a recruiter and in about 5 minutes by a hiring manager. Benchmark tables say what happened; they do not show how the planner, coder and reviewer worked, how the deterministic test and diff nodes route the run, or how cost builds up toward the cap.

A live demo would show that, but it needs a backend, a model call per visitor, a sandbox, authentication in front of the Approve/Reject gate, and a cloud bill that grows with traffic. Parent success criterion 3 asks for the opposite: a public page that loads with no backend and costs $0 to run.

Every run already records what a viewer needs. The driver writes each ADK event to `runs/<run-id>/events.jsonl` as it happens, next to `record.json` and the scored results row. Those files also hold things that must never be public: absolute host paths, the user name, sandbox and container ids, the GCP project id, raw exception text, and the content of held-out tasks if one were ever converted.

## Decision

The public demo is a set of recorded runs replayed in a static site, not a live system.

- **Converter, allowlist first.** `bench/replay.py` builds each replay field by field from the event log, the record and the results row. Nothing is copied from an event as a whole, so a field a later ADK version adds cannot leak by default. Remaining strings are rewritten (repository and home paths, other host paths, sandbox names and container ids, the project id, `projects/…` names, `REPLAY_REDACT` literals, run artifact paths, invisible and control characters), then capped. Failure reasons are fixed public texts, never exception text.
- **Leak check as a gate.** `bench/replay_check.py`, standard library only, scans every string before cutting and every finished file, for host paths, token shapes, emails, private keys and the exact project values. A build writes nothing unless the check passes, and CI runs it again before every deploy.
- **Held-out tasks cannot reach it.** The converter refuses a held-out run or task id by its shape before opening any file, never lists the task directory, and accepts only dev bench runs with exactly one scored results row.
- **Curated, honest set.** Seven Week 2B runs listed in `web/replays.yaml`: a clean multi-agent fix, a tempting task fixed at the root, the trap declined by each system, the $1.00 cost cap reached by each system on the same task, and a multi-agent against single-agent pair. Each has a one-line caption the owner approves after watching it.
- **Static viewer.** `web/` (Vite, React, TypeScript, Tailwind, React Flow, `react-diff-view`) replays the pipeline graph, tool-call feed, plan, diff, tests, review and cost meter from a pure `stateAt(replay, t)`. The graphs come from `app/pipeline.py` and `app/baseline.py` through `bench.replay graphs`, never typed by hand. All text renders as React text, a build-time CSP allows only same-origin resources, and there are no cookies, storage, analytics or third-party requests.
- **Hosting.** GitHub Pages from this repository, built, tested and deployed by `.github/workflows/pages.yaml` with actions pinned to commit SHAs and write permissions only on the deploy job.

## Consequences

- The demo costs $0 per visitor and has no server to attack or keep running. It shows real runs, including the failures, rather than a staged one.
- A replay shows one recorded run, not the system's current behaviour. A changed prompt or model needs new runs and a rebuild; the replay schema is versioned so the site can refuse files it does not understand.
- Times come from the event log, so a model call's latency and its tool run cannot be shown separately; the "How to read this" panel says so.
- The exact project values are checked only where they are known: on the owner's machine, and in CI through the `REPLAY_REDACT` secret. Elsewhere the pattern rules still apply.
- Live mode with Approve/Reject, a FastAPI proxy, IAP and persistent approval sessions are not built. Live demo runs can be added as replays once they exist, with the live graph and approval and pull request steps.
- The site goes live only when the repository is on GitHub with Pages enabled, a step the owner approves separately. (Status, 2026-10-02: live at https://omarcevi.dev/sdlc-agent-pipeline/.)
