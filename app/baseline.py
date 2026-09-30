"""Single-agent baseline: the same deterministic harness, without planner and reviewer."""

from google.adk.models.base_llm import BaseLlm
from google.adk.workflow import FunctionNode, Workflow

from app.agents.solo import build_solo
from app.nodes.finish import deliver_patch, report_failure
from app.nodes.intake import fetch_issue, provision_sandbox
from app.nodes.routing import route_solo
from app.nodes.verify import collect_diff, run_tests
from app.pipeline import INFRA_RETRY
from app.schemas import RunRequest


def build_baseline_workflow(model: BaseLlm) -> Workflow:
    solo = build_solo(model)
    fetch = FunctionNode(func=fetch_issue)
    provision = FunctionNode(func=provision_sandbox, retry_config=INFRA_RETRY)
    diff = FunctionNode(func=collect_diff, retry_config=INFRA_RETRY)
    tests = FunctionNode(func=run_tests, retry_config=INFRA_RETRY)

    return Workflow(
        name="issue_to_pr",
        input_schema=RunRequest,
        edges=[
            ("START", fetch),
            (fetch, provision),
            (provision, solo),
            (solo, route_solo),
            (route_solo, {"done": diff, "declined": report_failure}),
            (diff, tests),
            (tests, {"pass": deliver_patch, "fail": solo, "exhausted": report_failure}),
        ],
    )
