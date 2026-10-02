"""The graph export for the replay site: structure only, no model involved."""

import json

import pytest

import bench.replay as replay
from app.baseline import build_baseline_workflow
from app.models import RoleModels
from app.pipeline import build_workflow
from tests.fakes import FakeLlm
from tests.unit.test_graph_edges import shape


def _expected_edges(workflow) -> list[dict]:
    """The pinned edge shapes of test_graph_edges, one entry per route."""
    out = []
    for edge in workflow.edges:
        source, target, *rest = shape(edge)
        if isinstance(target, dict):
            out += [{"from": source, "to": t, "route": r} for r, t in target.items()]
        else:
            out.append(
                {"from": source, "to": target, "route": rest[0] if rest else None}
            )
    return out


def test_export_matches_the_pinned_edges():
    models = RoleModels(planner=FakeLlm([]), coder=FakeLlm([]), reviewer=FakeLlm([]))
    exported = replay.export_graphs()
    assert exported["schema"] == replay.SCHEMA == 1
    multi, single = exported["graphs"]["multi"], exported["graphs"]["single"]
    assert multi["edges"] == _expected_edges(build_workflow(models))
    assert single["edges"] == _expected_edges(build_baseline_workflow(FakeLlm([])))
    assert multi["source"] == "app.pipeline.build_workflow"
    assert single["source"] == "app.baseline.build_baseline_workflow"


def test_node_kinds():
    graphs = replay.export_graphs()["graphs"]
    for graph in graphs.values():
        ids = [n["id"] for n in graph["nodes"]]
        assert ids[0] == "START"
        seen: list[str] = []
        for edge in graph["edges"]:
            for end in (edge["from"], edge["to"]):
                if end not in seen:
                    seen.append(end)
        assert ids == seen
    kinds = {
        name: {n["id"]: n["kind"] for n in g["nodes"]} for name, g in graphs.items()
    }
    assert kinds["multi"]["START"] == "start"
    for llm in ("planner", "coder", "reviewer"):
        assert kinds["multi"][llm] == "llm"
    assert kinds["single"]["solo"] == "llm"
    for router in ("route_plan", "route_review"):
        assert kinds["multi"][router] == "router"
    assert kinds["single"]["route_solo"] == "router"
    for fn in ("fetch_issue", "run_tests", "report_failure", "deliver_patch"):
        assert kinds["multi"][fn] == "function"


def test_committed_graphs_match_the_code():
    assert replay.GRAPHS_PATH.read_text(encoding="utf-8") == replay.render_graphs()
    assert replay.main(["graphs", "--check"]) == 0


def test_check_fails_on_a_stale_file(tmp_path, capsys):
    stale = tmp_path / "graphs.json"
    stale.write_text(json.dumps({"schema": 1, "graphs": {}}) + "\n", encoding="utf-8")
    assert replay.main(["graphs", "--check", "--out", str(stale)]) == 1
    assert (
        "graphs.json differs from the code; run: uv run python -m bench.replay graphs"
        in capsys.readouterr().err
    )
    assert replay.main(["graphs", "--check", "--out", str(tmp_path / "none.json")]) == 1


def test_graphs_writes_a_byte_stable_file(tmp_path):
    out = tmp_path / "graphs.json"
    assert replay.main(["graphs", "--out", str(out)]) == 0
    first = out.read_bytes()
    assert replay.main(["graphs", "--out", str(out)]) == 0
    assert out.read_bytes() == first
    assert first.endswith(b"\n") and first.startswith(b'{\n  "schema"')


def test_export_never_calls_a_model(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a model was requested")

    monkeypatch.setattr("app.models.make_model", boom)
    monkeypatch.setattr(RoleModels, "from_env", boom)
    assert set(replay.export_graphs()["graphs"]) == {"multi", "single"}


async def test_the_placeholder_refuses_to_be_called():
    with pytest.raises(RuntimeError):
        async for _ in replay._NoModel(model="placeholder").generate_content_async(
            None
        ):
            pass
