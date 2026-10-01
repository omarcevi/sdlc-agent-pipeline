"""The bench graph and the baseline graph keep their edges exactly (global constraint)."""

from google.adk.workflow import Edge

from app.baseline import build_baseline_workflow
from app.models import RoleModels
from app.pipeline import build_workflow
from tests.fakes import FakeLlm


def _name(node) -> str:
    if isinstance(node, str):
        return node
    return getattr(node, "name", None) or node.__name__


def shape(edge) -> tuple:
    if isinstance(edge, Edge):
        return _name(edge.from_node), _name(edge.to_node), edge.route
    source, target = edge
    if isinstance(target, dict):
        return _name(source), {route: _name(node) for route, node in target.items()}
    return _name(source), _name(target)


def test_bench_graph_edges_are_pinned():
    models = RoleModels(planner=FakeLlm([]), coder=FakeLlm([]), reviewer=FakeLlm([]))
    assert [shape(e) for e in build_workflow(models).edges] == [
        ("START", "fetch_issue"),
        ("fetch_issue", "provision_sandbox"),
        ("provision_sandbox", "planner"),
        ("planner", "route_plan"),
        ("route_plan", {"actionable": "coder", "declined": "report_failure"}),
        ("coder", "collect_diff"),
        ("collect_diff", "run_tests"),
        (
            "run_tests",
            {"pass": "reviewer", "fail": "coder", "exhausted": "report_failure"},
        ),
        ("reviewer", "route_review"),
        (
            "route_review",
            {
                "approve": "deliver_patch",
                "changes": "coder",
                "exhausted": "report_failure",
            },
        ),
    ]


def test_baseline_graph_edges_are_pinned():
    assert [shape(e) for e in build_baseline_workflow(FakeLlm([])).edges] == [
        ("START", "fetch_issue"),
        ("fetch_issue", "provision_sandbox"),
        ("provision_sandbox", "solo"),
        ("solo", "route_solo"),
        ("route_solo", {"done": "collect_diff", "declined": "report_failure"}),
        ("collect_diff", "run_tests"),
        (
            "run_tests",
            {"pass": "deliver_patch", "fail": "solo", "exhausted": "report_failure"},
        ),
    ]
