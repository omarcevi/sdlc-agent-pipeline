# Spike S2/S3: Workflow under agents-cli eval, and tools + structured output on Gemini

- **Date:** 2026-09-30
- **Setup:** ADK 2.8.0, agents-cli 1.7.0, `gemini-3.8-flash` on Vertex (`global`), throwaway worktree with a toy three-node `Workflow` (function node → `LlmAgent` with one tool and an `output_schema` → function node).
- **Cost:** a few cents of Gemini calls.

## Questions

- **S3:** Can an `LlmAgent` workflow node use tools and return a schema-validated object in the same turn on Gemini?
- **S2:** Does `agents-cli eval run` accept a graph `Workflow` whose function nodes emit output-only events?

## S3 result: native mode loops; the `set_model_response` fallback works

ADK has two ways to combine tools with an `output_schema`:

| Mode | How it works | Result on `gemini-3.8-flash` |
|---|---|---|
| Native (`capabilities.output_schema_and_tools = True`, the default for Gemini on Vertex) | Sends the response schema together with the tools | **13 of 18 runs never finished.** The agent called its tool again and again until an 8-call cap stopped it. It could see its own earlier tool calls and results; it just kept calling. |
| Fallback (`output_schema_and_tools = False`) | ADK adds a `set_model_response` tool; the model calls it with the final object | **9 of 9 runs finished in exactly 2 model calls** with a valid object (`{"word_count": 5, "first_word": "the"}`). |

Temperature made no difference in native mode (it looped at the default and at 0).

How it showed up: `agents-cli eval run` appeared to hang for more than 10 minutes. The CLI has no per-case timeout, and over `/run_sse` the agent made 32 tool calls in 60 seconds without ending the turn. An earlier single `agents-cli run` had happened to finish, which hid the problem.

**Decision:** every Gemini agent in the pipeline gets structured output through `set_model_response`. `app/models.py` returns a `Gemini` subclass that declares `output_schema_and_tools=False`. The extra tool call counts toward the tool-call caps. The per-turn and per-run tool-call caps would have stopped a loop like this in the real pipeline, which is what they are for.

## S2 result: `agents-cli eval run` works with a Workflow

With the fallback model, `agents-cli eval run --dataset ... --metrics agent_turn_count` completed: inference, trace written, custom code metric graded.

What the trace contains:

- Only events that carry content. Output-only events (`Event(output=...)`) are silently left out; they do not cause the `Malformed agent event: missing content` error that the long-horizon-harness recipe warns about.
- A function node's `Event(message=...)` appears as a text event authored by the workflow.
- The final response (`responses`) is the last text event, here the last function node's message.

Two caveats:

1. `GET /apps/app/app-info` returns `400 Root agent is not an LlmAgent`, so traces omit `agent_data.agents` and the CLI warns that "grading will degrade". Custom metrics over `agent_data.turns` work; built-in multi-agent metrics may be weaker.
2. The eval CLI has no timeout per case, so a looping agent hangs the run. Keep the tool-call caps on.

**Decision for Week 2:** use `agents-cli eval run` as is. Function nodes keep emitting a short `message` event, and the terminal node's message should be the text the judge metrics need (for example the PR description). Router nodes emit no content today, so they do not appear in traces; add a message to them only if a trajectory metric needs it.

## Other observations

- The first `agents-cli run` after `uv sync` exceeded the CLI's 30-second server start wait (cold import). The second run was fine.
- Function-node parameters, routed edges and state behaved as the planning probes predicted.
