"""The per-call model timeout: a silent Gemini call is abandoned and sent once more."""

import asyncio
import logging

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.driver import classify_failure
from app.models import (
    ModelCallStalled,
    ResponseToolGemini,
    make_model,
    model_call_timeout_s,
)
from tests.fakes import STALL, fake_gemini, gemini_reply

# Each test bounds itself, so a broken timeout fails the test instead of hanging it.
GUARD_S = 5


def _request() -> LlmRequest:
    return LlmRequest(
        model="gemini-3.8-flash",
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
    )


async def _call(model) -> list:
    async with asyncio.timeout(GUARD_S):
        return [r async for r in model.generate_content_async(_request())]


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
    responses = await _call(model)
    assert [r.content.parts[0].text for r in responses] == ["done"]
    assert len(transport.requests) == 1


async def test_a_stalled_call_is_sent_again_once(monkeypatch, caplog):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "0.2")
    model, transport = fake_gemini([STALL, gemini_reply({"text": "done"})])
    with caplog.at_level(logging.WARNING, logger="app.models"):
        responses = await _call(model)
    assert [r.content.parts[0].text for r in responses] == ["done"]
    assert responses[0].usage_metadata.prompt_token_count == 1000
    assert len(transport.requests) == 2
    assert "no reply within 0.2 s" in caplog.text


async def test_a_call_that_stalls_twice_raises_model_call_stalled(monkeypatch):
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "0.2")
    model, transport = fake_gemini([STALL, STALL, gemini_reply({"text": "late"})])
    with pytest.raises(ModelCallStalled) as stalled:
        await _call(model)
    assert str(stalled.value) == (
        "model call stalled twice: no reply from gemini-3.8-flash within 0.2 s"
    )
    assert isinstance(stalled.value.__cause__, TimeoutError)
    assert len(transport.requests) == 2  # exactly one retry


def test_make_model_gemini_models_carry_the_timeout():
    assert type(make_model("gemini-3.8-flash")) is ResponseToolGemini


def test_a_stalled_model_call_is_classified_as_infra():
    try:
        try:
            raise ModelCallStalled("model call stalled twice: no reply from m")
        except ModelCallStalled as inner:
            raise RuntimeError("node failed") from inner
    except RuntimeError as exc:
        kind, reason = classify_failure(exc, llm_agent_active=True)
    assert (kind, reason) == ("infra", "model call stalled twice: no reply from m")
