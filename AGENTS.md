# AGENTS.md

Instructions for any coding agent (Claude Code, Gemini CLI, Codex, etc.) working in this repository.

## Project

A multi-agent system built on Google ADK 2.x that turns a GitHub issue into a tested pull request. A graph `Workflow` connects Planner, Coder and Reviewer LLM agents with deterministic function nodes. Code runs in hermetic sandboxes (local Docker or Agent Runtime Sandboxes). The system is measured with a hidden-test benchmark against a single-agent baseline.

- **Design spec (source of truth):** `docs/superpowers/specs/2026-09-29-sdlc-agent-pipeline-design.md`
- **Status:** design approved, implementation not started.

Read the spec before making architectural changes. If the code and the spec disagree, stop and ask. Do not quietly pick one.

## Hard rules

1. **No AI attribution.** Never add Claude, Claude Code, or any AI tool as author, co-author or contributor. That means no `Co-Authored-By:` trailers, no "Generated with ..." lines in commits, PRs, docs, or a CONTRIBUTORS file. Commits use the repo owner's git identity only.
2. **The sandbox gets no credentials and no network.** The GitHub token is read only by the orchestrator-side nodes `fetch_issue`, `open_pr` and `report_failure`. Never pass it into prompts, session state, traces, or a sandbox.
3. **Tools go through `Environment`.** Agent tools must never call `subprocess`, `open()` or the host filesystem directly.
4. **Never trust model claims for routing.** Routing uses real `git diff` output and real test exit codes from function nodes.
5. **Benchmark integrity.**
   - Never tune prompts against `split: heldout` tasks, and never open or print their contents during development.
   - Never edit `hidden_tests/` to make a run pass.
   - Run `bench validate` after changing anything under `bench/repos/`.
6. **Keep the cost guards.** The budget plugin, sandbox TTLs and loop bounds stay on. Changing their defaults needs the owner's approval.
7. **Don't change model names unless asked.** Models are configured through env vars (`PLANNER_MODEL`, `CODER_MODEL`, `REVIEWER_MODEL`, `BASELINE_MODEL`).
8. **Never deploy, create cloud resources, or push to GitHub without explicit approval** from the owner, given in the current session.

## Workflow

This project follows the `agents-cli` lifecycle: scaffold → build → evaluate → deploy → observe.

- Requires `agents-cli` ≥ 1.7.0. Upgrade with `uv tool upgrade google-agents-cli`, then run `agents-cli update`.
- Use `uv` for everything Python (`uv sync`, `uv run ...`). Never call `pip` directly.
- Before writing agent code, study the reference recipes listed in spec §17 (`google/adk-samples` → `core/python/...`).
- Run `agents-cli infra single-project` **before** the first `agents-cli deploy`.

## Testing conventions

- **pytest checks code correctness:** graph routing (using the scripted fake `BaseLlm`), guardrails, budget math, schemas, and the environment backends. pytest must never assert on real LLM output content.
- **Agent behaviour** is measured by `agents-cli eval run` (quality metrics) and the `bench/` benchmark (the resolve rate).
- Write a failing test before implementing deterministic code.

## Commands

| Task | Command |
|---|---|
| Install deps | `uv sync` |
| Lint | `agents-cli lint` |
| Unit tests | `uv run pytest tests/unit` |
| Quick smoke run | `agents-cli run "<prompt>"` |
| Interactive UI | `agents-cli playground` |
| Quality evals | `agents-cli eval run` |
| Deploy (approval required) | `agents-cli deploy` |

Benchmark commands (`bench validate`, `bench run ...`) are specified in spec §9.1. Add them to this table once they exist.

## Keeping this file current

After a structural change (new top-level directories, new env vars, new commands, callback or plugin re-wiring), update this file in the same commit.
