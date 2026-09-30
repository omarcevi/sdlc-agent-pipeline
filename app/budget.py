"""Runner-wide cost and tool-call caps.

Caps raise BudgetExceeded, which aborts the run (workflow edges cannot route
exceptions). ADK wraps plugin exceptions in RuntimeError, so the driver walks
__cause__ to classify it. The ledger lives on the plugin, keyed by session id,
because state deltas from a failing callback are not persisted.
"""

import os
from dataclasses import dataclass
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from app.pricing import cost_usd


class BudgetExceeded(RuntimeError):
    """A per-run cost or tool-call cap was hit."""


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    turn_tool_calls: int = 0
    last_model: str = ""


class BudgetPlugin(BasePlugin):
    def __init__(
        self,
        *,
        max_usd: float | None = None,
        max_tool_calls: int | None = None,
        max_turn_tool_calls: int | None = None,
        turn_limited_agents: frozenset[str] = frozenset({"coder"}),
    ) -> None:
        super().__init__(name="budget")
        self.max_usd = (
            max_usd
            if max_usd is not None
            else float(os.environ.get("RUN_BUDGET_USD", "1.00"))
        )
        self.max_tool_calls = (
            max_tool_calls
            if max_tool_calls is not None
            else int(os.environ.get("MAX_TOOL_CALLS_PER_RUN", "75"))
        )
        self.max_turn_tool_calls = (
            max_turn_tool_calls
            if max_turn_tool_calls is not None
            else int(os.environ.get("MAX_TOOL_CALLS_PER_TURN", "25"))
        )
        self.turn_limited_agents = turn_limited_agents
        self._usage: dict[str, Usage] = {}

    def usage(self, session_id: str) -> Usage:
        return self._usage.setdefault(session_id, Usage())

    async def before_agent_callback(self, *, agent: Any, callback_context: Any) -> None:
        self.usage(callback_context.session.id).turn_tool_calls = 0

    async def before_model_callback(
        self, *, callback_context: Any, llm_request: Any
    ) -> None:
        usage = self.usage(callback_context.session.id)
        usage.last_model = llm_request.model or ""
        if usage.cost_usd >= self.max_usd:
            raise BudgetExceeded(
                f"run cost ${usage.cost_usd:.3f} reached the ${self.max_usd:.2f} cap"
            )

    async def after_model_callback(
        self, *, callback_context: Any, llm_response: Any
    ) -> None:
        meta = llm_response.usage_metadata
        if meta is None:
            return None
        usage = self.usage(callback_context.session.id)
        tokens_in = meta.prompt_token_count or 0
        tokens_out = (meta.candidates_token_count or 0) + (
            meta.thoughts_token_count or 0
        )
        usage.tokens_in += tokens_in
        usage.tokens_out += tokens_out
        usage.cost_usd += cost_usd(usage.last_model, tokens_in, tokens_out)
        callback_context.state["budget"] = {
            "cost_usd": round(usage.cost_usd, 4),
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "tool_calls": usage.tool_calls,
        }
        return None

    async def before_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
    ) -> None:
        usage = self.usage(tool_context.session.id)
        usage.tool_calls += 1
        usage.turn_tool_calls += 1
        if usage.tool_calls > self.max_tool_calls:
            raise BudgetExceeded(f"run exceeded {self.max_tool_calls} tool calls")
        if (
            tool_context.agent_name in self.turn_limited_agents
            and usage.turn_tool_calls > self.max_turn_tool_calls
        ):
            raise BudgetExceeded(
                f"{tool_context.agent_name} exceeded {self.max_turn_tool_calls} tool calls in one turn"
            )
        return None
