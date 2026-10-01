"""The issue-to-PR graph. Deterministic function nodes wrap three LLM agents.

The bench graph (the default) ends at `deliver_patch`. The live graph
(`live=True`) reads the issue from GitHub and continues after `deliver_patch`
through the human approval gate to `open_pr` or `report_failure`.
"""

from typing import Any

from google.adk.workflow import FunctionNode, RetryConfig, Workflow

from app.agents import build_coder, build_planner, build_reviewer
from app.environment.base import InfraError
from app.models import RoleModels
from app.nodes.finish import deliver_patch, open_pr, report_failure
from app.nodes.gate import human_gate, route_approval
from app.nodes.intake import fetch_issue, fetch_live_issue, provision_sandbox
from app.nodes.routing import route_plan, route_review
from app.nodes.verify import collect_diff, run_tests
from app.schemas import RunRequest

INFRA_RETRY = RetryConfig(
    max_attempts=3, initial_delay=0.5, max_delay=4.0, exceptions=[InfraError]
)


def build_workflow(models: RoleModels, *, live: bool = False) -> Workflow:
    planner = build_planner(models.planner)
    coder = build_coder(models.coder)
    reviewer = build_reviewer(models.reviewer)
    if live:
        fetch = FunctionNode(
            func=fetch_live_issue, name="fetch_issue", retry_config=INFRA_RETRY
        )
    else:
        fetch = FunctionNode(func=fetch_issue)
    provision = FunctionNode(func=provision_sandbox, retry_config=INFRA_RETRY)
    diff = FunctionNode(func=collect_diff, retry_config=INFRA_RETRY)
    tests = FunctionNode(func=run_tests, retry_config=INFRA_RETRY)

    edges: list[Any] = [
        ("START", fetch),
        (fetch, provision),
        (provision, planner),
        (planner, route_plan),
        (route_plan, {"actionable": coder, "declined": report_failure}),
        (coder, diff),
        (diff, tests),
        (tests, {"pass": reviewer, "fail": coder, "exhausted": report_failure}),
        (reviewer, route_review),
        (
            route_review,
            {
                "approve": deliver_patch,
                "changes": coder,
                "exhausted": report_failure,
            },
        ),
    ]
    if live:
        gate = FunctionNode(func=human_gate, rerun_on_resume=False)
        open_pr_node = FunctionNode(func=open_pr, retry_config=INFRA_RETRY)
        edges += [
            (deliver_patch, gate),
            (gate, route_approval),
            (route_approval, {"approved": open_pr_node, "rejected": report_failure}),
        ]
    return Workflow(name="issue_to_pr", input_schema=RunRequest, edges=edges)
