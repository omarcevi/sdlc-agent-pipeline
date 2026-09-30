"""Role → model mapping, configured by env vars (AGENTS.md rule 7)."""

import os
from dataclasses import dataclass

from google.adk.models import Gemini
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

DEFAULT_MODEL = "gemini-3.8-flash"


def make_model(name: str) -> BaseLlm:
    """Gemini names use the native client; "provider/model" strings use LiteLLM."""
    if "/" in name:
        return LiteLlm(model=name)
    return Gemini(model=name, retry_options=types.HttpRetryOptions(attempts=3))


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
