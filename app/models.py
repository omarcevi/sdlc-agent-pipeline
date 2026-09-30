"""Role → model mapping, configured by env vars (AGENTS.md rule 7)."""

import os
from dataclasses import dataclass

from google.adk.models import Gemini, LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

DEFAULT_MODEL = "gemini-3.8-flash"


class ResponseToolGemini(Gemini):
    """Gemini that returns structured output through ADK's `set_model_response` tool.

    ADK's native mode (response schema plus tools in one request) loops on
    gemini-3.8-flash: in a live spike on Vertex with ADK 2.8.0, an agent with tools
    and an output schema kept calling its tool and never gave its final answer in
    13 of 18 runs. With this capability reported as False, ADK adds the
    `set_model_response` tool instead, and 9 of 9 runs finished in 2 model calls.
    """

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)


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
    def from_env(cls) -> "RoleModels":
        return cls(
            planner=make_model(os.environ.get("PLANNER_MODEL", DEFAULT_MODEL)),
            coder=make_model(os.environ.get("CODER_MODEL", DEFAULT_MODEL)),
            reviewer=make_model(os.environ.get("REVIEWER_MODEL", DEFAULT_MODEL)),
        )
