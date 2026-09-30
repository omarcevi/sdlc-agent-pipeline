from types import SimpleNamespace

import pytest
from google.genai import types

from app.budget import BudgetExceeded, BudgetPlugin
from app.pricing import cost_usd


def test_cost_known_model():
    assert cost_usd("gemini-3.8-flash", 1_000_000, 1_000_000) == pytest.approx(4.50)


def test_cost_prefix_and_provider_prefix():
    assert cost_usd("gemini-3.1-pro-preview", 100_000, 0) == pytest.approx(0.20)
    assert cost_usd("vertex_ai/gemini-3.8-flash", 1_000_000, 0) == pytest.approx(0.75)


def test_cost_long_context_pro_rate():
    assert cost_usd("gemini-3.1-pro", 300_000, 0) == pytest.approx(1.20)


def test_cost_long_context_applies_to_prefixed_variants():
    assert cost_usd("gemini-3.1-pro-preview", 300_000, 0) == pytest.approx(1.20)


def test_cost_pro_preview_price():
    assert cost_usd("gemini-3.1-pro-preview", 100_000, 10_000) == pytest.approx(0.32)


def test_cost_pro_preview_long_context_rate():
    assert cost_usd("gemini-3.1-pro-preview", 300_000, 0) == pytest.approx(1.20)


def test_cost_unknown_model_is_conservative():
    assert cost_usd("mystery", 1_000_000, 0) == pytest.approx(5.00)


def _ctx(agent_name="coder"):
    return SimpleNamespace(
        session=SimpleNamespace(id="s1"), state={}, agent_name=agent_name
    )


def _response(tokens_in, tokens_out, thoughts=0):
    return SimpleNamespace(
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=tokens_in,
            candidates_token_count=tokens_out,
            thoughts_token_count=thoughts,
        )
    )


async def test_cost_accumulates_and_cap_blocks_next_call():
    plugin = BudgetPlugin(max_usd=0.01, max_tool_calls=100, max_turn_tool_calls=100)
    ctx = _ctx()
    await plugin.before_model_callback(
        callback_context=ctx, llm_request=SimpleNamespace(model="gemini-3.8-flash")
    )
    await plugin.after_model_callback(
        callback_context=ctx, llm_response=_response(10_000, 1_000, thoughts=1_000)
    )
    usage = plugin.usage("s1")
    assert usage.tokens_in == 10_000 and usage.tokens_out == 2_000
    assert usage.cost_usd == pytest.approx(0.015)
    assert ctx.state["budget"]["cost_usd"] == pytest.approx(0.015)
    with pytest.raises(BudgetExceeded):
        await plugin.before_model_callback(
            callback_context=ctx, llm_request=SimpleNamespace(model="gemini-3.8-flash")
        )


async def test_run_tool_call_cap():
    plugin = BudgetPlugin(max_usd=10, max_tool_calls=2, max_turn_tool_calls=100)
    ctx = _ctx()
    tool = SimpleNamespace(name="bash")
    await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=ctx)
    await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=ctx)
    with pytest.raises(BudgetExceeded):
        await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=ctx)


async def test_turn_cap_applies_to_coder_and_resets_per_turn():
    plugin = BudgetPlugin(max_usd=10, max_tool_calls=100, max_turn_tool_calls=2)
    tool = SimpleNamespace(name="bash")
    coder = _ctx("coder")
    for _ in range(2):
        await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=coder)
    await plugin.before_agent_callback(
        agent=SimpleNamespace(name="coder"), callback_context=coder
    )
    for _ in range(2):
        await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=coder)
    with pytest.raises(BudgetExceeded):
        await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=coder)
    planner = _ctx("planner")
    await plugin.before_agent_callback(
        agent=SimpleNamespace(name="planner"), callback_context=planner
    )
    for _ in range(5):
        await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=planner)


def test_defaults_come_from_env(monkeypatch):
    monkeypatch.setenv("RUN_BUDGET_USD", "0.5")
    monkeypatch.setenv("MAX_TOOL_CALLS_PER_RUN", "9")
    monkeypatch.setenv("MAX_TOOL_CALLS_PER_TURN", "3")
    plugin = BudgetPlugin()
    assert (plugin.max_usd, plugin.max_tool_calls, plugin.max_turn_tool_calls) == (
        0.5,
        9,
        3,
    )
