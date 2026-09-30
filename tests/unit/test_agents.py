import re

from google.adk.models import Gemini
from google.adk.models.lite_llm import LiteLlm

from app.agents import build_coder, build_planner, build_reviewer
from app.models import DEFAULT_MODEL, RoleModels, make_model
from app.schemas import PatchResult, Plan, Review
from tests.fakes import FakeLlm

KNOWN_STATE_KEYS = {
    "planner": set(),
    "coder": {"issue_text", "plan"},
    "reviewer": {"issue_text", "plan", "diff_text"},
}


def _agents():
    return [
        build_planner(FakeLlm([])),
        build_coder(FakeLlm([])),
        build_reviewer(FakeLlm([])),
    ]


def test_instruction_placeholders_are_known_state_keys():
    for agent in _agents():
        placeholders = set(re.findall(r"\{([^{}]*)\}", agent.instruction))
        assert placeholders <= KNOWN_STATE_KEYS[agent.name], agent.name
        assert (
            agent.instruction.count("{")
            == agent.instruction.count("}")
            == len(re.findall(r"\{[^{}]*\}", agent.instruction))
        ), f"stray braces in {agent.name} instruction"


def test_agent_contracts():
    planner, coder, reviewer = _agents()
    assert (planner.output_schema, planner.output_key) == (Plan, "plan")
    assert (coder.output_schema, coder.output_key) == (PatchResult, "patch")
    assert (reviewer.output_schema, reviewer.output_key) == (Review, "review")
    assert {t.__name__ for t in planner.tools} == {"read_file", "list_dir", "grep"}
    assert {t.__name__ for t in reviewer.tools} == {"read_file", "list_dir", "grep"}
    assert "bash" in {t.__name__ for t in coder.tools}


def test_make_model_routes_provider_strings_to_litellm():
    assert isinstance(make_model("gemini-3.8-flash"), Gemini)
    assert isinstance(make_model("anthropic/claude-sonnet"), LiteLlm)


def test_role_models_from_env(monkeypatch):
    monkeypatch.delenv("PLANNER_MODEL", raising=False)
    monkeypatch.setenv("CODER_MODEL", "gemini-3.1-pro")
    models = RoleModels.from_env()
    assert models.planner.model == DEFAULT_MODEL
    assert models.coder.model == "gemini-3.1-pro"
