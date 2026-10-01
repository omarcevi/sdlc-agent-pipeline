"""The reviewer-probe graph: show the reviewer a prepared patch, record its verdict.

It is the pipeline minus the coder. The planner runs so that the reviewer's plan is a
real one; the patch comes from a probe (bench/probes.py) instead of the coder, and goes
through the pipeline's own diff and test nodes. Nothing here puts the probe's kind
(bad or good) in state, messages or the sandbox.
"""

from typing import Any

from google.adk.events.event import Event
from google.adk.workflow import Edge, FunctionNode, Workflow

from app.agents import build_planner, build_reviewer
from app.environment import registry
from app.environment.base import InfraError
from app.models import RoleModels
from app.nodes.finish import report_failure
from app.nodes.intake import fetch_issue, provision_sandbox
from app.nodes.routing import route_plan
from app.nodes.verify import collect_diff, run_tests
from app.pipeline import INFRA_RETRY
from app.schemas import ProbeRequest, Review
from bench.probes import NOT_DEV, dev_task, patch_file
from bench.probes import load_probe as read_probe

PROBE_PATCH_PATH = "/workspace/probe.diff"
APPLY_CMD = f"git apply --whitespace=nowarn {PROBE_PATCH_PATH}"


def load_probe(node_input: ProbeRequest):
    """The state bench `fetch_issue` yields for the probe's task, plus the probe id.
    The probe's yaml is read here, for the task id, and nowhere in the graph."""
    spec = read_probe(node_input.probe_id)
    if dev_task(spec.task_id) is None:
        raise ValueError(NOT_DEV)
    *messages, final = fetch_issue(
        ProbeRequest(
            task_id=spec.task_id, run_id=node_input.run_id, probe_id=spec.probe_id
        )
    )
    yield from messages
    final.actions.state_delta["probe_id"] = spec.probe_id
    yield final


async def apply_probe_patch(node_input: Any, sandbox_id: str, probe_id: str):
    env = registry.get(sandbox_id)
    await env.write_file(PROBE_PATCH_PATH, patch_file(probe_id).read_text())
    result = await env.exec(APPLY_CMD)
    if result.exit_code != 0:
        # The validator applies every patch first, so this is the environment.
        raise InfraError(f"patch does not apply: {result.stderr.strip()}")
    yield Event(message="patch applied")
    yield Event(output=node_input)


def probe_invalid(node_input: Any):
    outcome = {
        "outcome": "failed",
        "failure_kind": "infra",
        "reason": "probe patch failed the visible tests",
        "patch_path": None,
    }
    yield Event(message=f"failed: {outcome['reason']}")
    yield Event(output=outcome, state={"outcome": outcome})


def record_verdict(node_input: Review):
    outcome = {
        "outcome": "patch_written",
        "failure_kind": "none",
        "reason": "",
        "patch_path": None,
        "verdict": node_input.verdict,
        "must_fix": list(node_input.must_fix),
    }
    yield Event(message=f"review verdict: {node_input.verdict}")
    yield Event(output=outcome, state={"outcome": outcome})


def build_review_probe_workflow(models: RoleModels) -> Workflow:
    planner = build_planner(models.planner)
    reviewer = build_reviewer(models.reviewer)
    load = FunctionNode(func=load_probe)
    provision = FunctionNode(func=provision_sandbox, retry_config=INFRA_RETRY)
    apply_patch = FunctionNode(func=apply_probe_patch)
    diff = FunctionNode(func=collect_diff, retry_config=INFRA_RETRY)
    tests = FunctionNode(func=run_tests, retry_config=INFRA_RETRY)
    invalid = FunctionNode(func=probe_invalid)

    return Workflow(
        name="issue_to_pr",
        input_schema=ProbeRequest,
        edges=[
            ("START", load),
            (load, provision),
            (provision, planner),
            (planner, route_plan),
            (route_plan, {"actionable": apply_patch, "declined": report_failure}),
            (apply_patch, diff),
            (diff, tests),
            (tests, {"pass": reviewer}),
            # ADK rejects two routed edges to one node, so these two routes share
            # one edge with a list of routes.
            Edge(from_node=tests, to_node=invalid, route=["fail", "exhausted"]),
            (reviewer, record_verdict),
        ],
    )
