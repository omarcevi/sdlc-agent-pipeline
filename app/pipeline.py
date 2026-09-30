"""The issue-to-PR graph. Deterministic function nodes wrap three LLM agents."""

from google.adk.workflow import FunctionNode, RetryConfig, Workflow

from app.agents import build_coder, build_planner, build_reviewer
from app.environment.base import InfraError
from app.models import RoleModels
from app.nodes.finish import deliver_patch, report_failure
from app.nodes.intake import fetch_issue, provision_sandbox
from app.nodes.routing import route_plan, route_review
from app.nodes.verify import collect_diff, run_tests
from app.schemas import RunRequest

INFRA_RETRY = RetryConfig(
    max_attempts=3, initial_delay=0.5, max_delay=4.0, exceptions=[InfraError]
)


def build_workflow(models: RoleModels) -> Workflow:
    planner = build_planner(models.planner)
    coder = build_coder(models.coder)
    reviewer = build_reviewer(models.reviewer)
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
        ],
    )
