"""Role → model mapping, configured by env vars (AGENTS.md rule 7)."""

import asyncio
import logging
import math
import os
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from dataclasses import dataclass

from google.adk.models import Gemini, LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

DEFAULT_MODEL = "gemini-3.8-flash"
# Twice the longest reply in completed runs up to 2026-10-01 (238 s, a flash
# reviewer's final answer), rounded up to a whole minute.
DEFAULT_MODEL_CALL_TIMEOUT_S = 480.0
# How a ModelCallStalled reason starts; bench/report.py lists those runs.
STALLED_TWICE = "model call stalled twice"

logger = logging.getLogger(__name__)


class ModelCallStalled(Exception):
    """A model call gave no reply within MODEL_CALL_TIMEOUT_S, and neither did its
    one retry. A provider fault: the driver records it as an infra failure."""


@dataclass
class StallCount:
    """Model calls in one run that got no reply within MODEL_CALL_TIMEOUT_S."""

    value: int = 0


# The current run's stall count. The driver sets a fresh one for each run, and the
# tasks the run starts inherit it. Outside a driver run (agents-cli) it is None and
# a stall is only logged.
RUN_STALLS: ContextVar[StallCount | None] = ContextVar("run_stalls", default=None)


def model_call_timeout_s() -> float:
    """How long one model call may go without a reply, from MODEL_CALL_TIMEOUT_S
    (default 480 s). Anything that is not a positive number is a configuration
    error. The driver also checks that it stays below RUN_TIMEOUT_S."""
    raw = os.environ.get("MODEL_CALL_TIMEOUT_S", f"{DEFAULT_MODEL_CALL_TIMEOUT_S:g}")
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not value > 0 or math.isinf(value):
        raise ValueError(f"MODEL_CALL_TIMEOUT_S must be a positive number, got {raw!r}")
    return value


class ResponseToolGemini(Gemini):
    """Gemini that returns structured output through ADK's `set_model_response` tool,
    and abandons a call that stays silent.

    ADK's native mode (response schema plus tools in one request) loops on
    gemini-3.8-flash: in a live spike on Vertex with ADK 2.8.0, an agent with tools
    and an output schema kept calling its tool and never gave its final answer in
    13 of 18 runs. With this capability reported as False, ADK adds the
    `set_model_response` tool instead, and 9 of 9 runs finished in 2 model calls.

    A provider can accept a request and never answer it (seen on flash and pro,
    for 20 minutes and more). A call with no reply within MODEL_CALL_TIMEOUT_S is
    cancelled and sent again once; a second silence raises ModelCallStalled.
    google-genai's own `HttpOptions.timeout` does not do this: on the aiohttp
    transport it raises a bare TimeoutError that the SDK's retry does not cover.
    """

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            # Chunks are passed on as they arrive, so a retry could repeat them.
            # Bench runs do not stream.
            async for response in super().generate_content_async(llm_request, True):
                yield response
            return
        timeout_s = model_call_timeout_s()
        for attempt in (1, 2):
            # The deadline covers the whole call, the SDK's own retries included.
            # Responses are yielded after it, so the caller's handling of them
            # (tool calls, callbacks) never runs under it.
            deadline = asyncio.timeout(timeout_s)
            try:
                async with deadline:
                    call = super().generate_content_async(llm_request, False)
                    responses = [response async for response in call]
            except TimeoutError as exc:
                if not deadline.expired():
                    raise  # a transport timeout, not this deadline
                if (stalls := RUN_STALLS.get()) is not None:
                    stalls.value += 1
                if attempt == 2:
                    raise ModelCallStalled(
                        f"{STALLED_TWICE}: no reply from {self.model} "
                        f"within {timeout_s:g} s"
                    ) from exc
                logger.warning(
                    "model call to %s: no reply within %g s; sending it again",
                    self.model,
                    timeout_s,
                )
                continue
            for response in responses:
                yield response
            return


def make_model(name: str) -> BaseLlm:
    """Gemini names use the native client; "provider/model" strings use LiteLLM."""
    if "/" in name:
        return LiteLlm(model=name)
    return ResponseToolGemini(
        model=name, retry_options=types.HttpRetryOptions(attempts=3)
    )


@dataclass(frozen=True)
class RoleModels:
    planner: BaseLlm
    coder: BaseLlm
    reviewer: BaseLlm

    @classmethod
    def from_names(cls, planner: str, coder: str, reviewer: str) -> "RoleModels":
        return cls(
            planner=make_model(planner),
            coder=make_model(coder),
            reviewer=make_model(reviewer),
        )

    @classmethod
    def from_env(cls) -> "RoleModels":
        return cls.from_names(
            planner=os.environ.get("PLANNER_MODEL", DEFAULT_MODEL),
            coder=os.environ.get("CODER_MODEL", DEFAULT_MODEL),
            reviewer=os.environ.get("REVIEWER_MODEL", DEFAULT_MODEL),
        )
