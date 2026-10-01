"""The per-call model timeout: a silent Gemini call is abandoned and sent once more."""

import asyncio
import logging
from types import SimpleNamespace

import aiohttp
import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app import models
from app.driver import classify_failure
from app.models import (
    RUN_STALLS,
    STALLED_TWICE,
    ModelCallStalled,
    ResponseToolGemini,
    StallCount,
    make_model,
    model_call_timeout_s,
)
from tests.fakes import STALL, GeminiTransport, fake_gemini, gemini_client, gemini_reply

# Each test bounds itself, so a broken timeout fails the test instead of hanging it.
GUARD_S = 5


def _request() -> LlmRequest:
    return LlmRequest(
        model="gemini-3.8-flash",
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
    )


async def _call(model, stalls: StallCount | None = None) -> list:
    """One model call, inside a run whose stalls go to `stalls` (None: no run)."""
    token = RUN_STALLS.set(stalls)
    try:
        async with asyncio.timeout(GUARD_S):
            return [r async for r in model.generate_content_async(_request())]
    finally:
        RUN_STALLS.reset(token)


def test_default_model_call_timeout_is_eight_minutes(monkeypatch):
    # Twice the longest reply in completed runs (238 s), rounded up to a minute.
    monkeypatch.delenv("MODEL_CALL_TIMEOUT_S", raising=False)
    assert model_call_timeout_s() == 480.0


@pytest.mark.parametrize("value", ["0", "-1", "abc", "", "nan", "inf"])
def test_model_call_timeout_must_be_a_positive_number(monkeypatch, value):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", value)
    with pytest.raises(ValueError, match="MODEL_CALL_TIMEOUT_S"):
        model_call_timeout_s()


async def test_a_prompt_reply_is_one_request(monkeypatch):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "0.2")
    model, transport = fake_gemini([gemini_reply({"text": "done"})])
    stalls = StallCount()
    responses = await _call(model, stalls)
    assert [r.content.parts[0].text for r in responses] == ["done"]
    assert len(transport.requests) == 1
    assert stalls.value == 0


async def test_a_stalled_call_is_sent_again_once(monkeypatch, caplog):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "0.2")
    model, transport = fake_gemini([STALL, gemini_reply({"text": "done"})])
    stalls = StallCount()
    with caplog.at_level(logging.WARNING, logger="app.models"):
        responses = await _call(model, stalls)
    assert [r.content.parts[0].text for r in responses] == ["done"]
    assert responses[0].usage_metadata.prompt_token_count == 1000
    assert len(transport.requests) == 2
    assert "no reply within 0.2 s" in caplog.text
    assert stalls.value == 1


async def test_a_call_that_stalls_twice_raises_model_call_stalled(monkeypatch):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "0.2")
    model, transport = fake_gemini([STALL, STALL, gemini_reply({"text": "late"})])
    stalls = StallCount()
    with pytest.raises(ModelCallStalled) as stalled:
        await _call(model, stalls)
    assert str(stalled.value) == (
        "model call stalled twice: no reply from gemini-3.8-flash within 0.2 s"
    )
    assert str(stalled.value).startswith(STALLED_TWICE)
    assert isinstance(stalled.value.__cause__, TimeoutError)
    assert len(transport.requests) == 2  # exactly one retry
    assert stalls.value == 2


async def test_a_stall_outside_a_driver_run_is_retried_without_counting(monkeypatch):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "0.2")
    model, transport = fake_gemini([STALL, gemini_reply({"text": "done"})])
    responses = await _call(model)  # no run, so no count (agents-cli)
    assert len(responses) == 1 and len(transport.requests) == 2


@pytest.mark.parametrize(
    "error",
    [TimeoutError("read timed out"), aiohttp.ServerTimeoutError("read timed out")],
)
async def test_a_transport_timeout_is_raised_not_retried(monkeypatch, error):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "2")
    model, transport = fake_gemini([error, gemini_reply({"text": "unused"})])
    stalls = StallCount()
    with pytest.raises(type(error)) as raised:
        await _call(model, stalls)
    assert raised.value is error
    assert len(transport.requests) == 1
    assert stalls.value == 0


async def test_cancelling_a_wrapped_call_propagates(monkeypatch):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "2")
    model, transport = fake_gemini([STALL, gemini_reply({"text": "unused"})])
    stalls = StallCount()
    task = asyncio.create_task(_call(model, stalls))
    async with asyncio.timeout(GUARD_S):
        while not transport.requests:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(transport.requests) == 1
    assert stalls.value == 0


async def test_make_model_gemini_models_use_the_configured_timeout(monkeypatch):
    used: list[float] = []

    def spy(delay: float):
        used.append(delay)
        return asyncio.timeout(delay)

    monkeypatch.setattr(models, "asyncio", SimpleNamespace(timeout=spy))
    model = make_model("gemini-3.8-flash")
    assert type(model) is ResponseToolGemini

    model.client = gemini_client(GeminiTransport([gemini_reply({"text": "done"})]))
    await _call(model)
    assert used == [480.0]  # the default

    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "0.2")
    transport = GeminiTransport([STALL, gemini_reply({"text": "done"})])
    model = make_model("gemini-3.8-flash")
    model.client = gemini_client(transport)
    await _call(model)
    assert used == [480.0, 0.2, 0.2]
    assert len(transport.requests) == 2  # the 0.2 s deadline really fired


def test_a_stalled_model_call_is_classified_as_infra():
    try:
        try:
            raise ModelCallStalled("model call stalled twice: no reply from m")
        except ModelCallStalled as inner:
            raise RuntimeError("node failed") from inner
    except RuntimeError as exc:
        kind, reason = classify_failure(exc, llm_agent_active=True)
    assert (kind, reason) == ("infra", "model call stalled twice: no reply from m")
