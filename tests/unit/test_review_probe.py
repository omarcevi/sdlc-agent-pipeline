import json
import re

import pytest
from google.adk.workflow import Edge
from pydantic import Field

from app.driver import run_pipeline
from app.environment import registry
from app.environment.base import ExecResult, InfraError
from app.models import RoleModels
from app.nodes.verify import TEST_CMD
from app.review_probe import (
    PROBE_PATCH_PATH,
    apply_probe_patch,
    build_review_probe_workflow,
)
from app.schemas import Plan, ProbeRequest, RunRequest
from bench import review_probe
from bench.probes import list_probes
from bench.review_probe import plan_probe_runs, run_probe_spec
from tests.fakes import FakeEnvironment, FakeLlm, json_out
from tests.unit._constants import DIFF, PROBE_NOTE
from tests.unit.test_pipeline import (
    APPROVE,
    CHANGES,
    FAIL,
    PASS,
    PLAN,
    diff_responses,
    use_env,
)

NOTE = PROBE_NOTE


class RecordingLlm(FakeLlm):
    """Scripted model that keeps the text of every request it is sent."""

    seen: list[str] = Field(default_factory=list)

    def __init__(self, steps: list[dict]) -> None:
        super().__init__(steps)

    async def generate_content_async(self, llm_request, stream=False):
        parts = [str(llm_request.contents), str(llm_request.config.system_instruction)]
        self.seen.append("\n".join(parts))
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def request(run_id: str = "t-1-review-flash-rp-01-r1-s") -> ProbeRequest:
    return ProbeRequest(task_id="t-1", run_id=run_id, probe_id="rp-01")


def models(planner, reviewer, coder=None) -> RoleModels:
    return RoleModels(planner=planner, coder=coder or FakeLlm([]), reviewer=reviewer)


async def run(planner, reviewer, env, monkeypatch, coder=None):
    use_env(monkeypatch, env)
    events = []
    record = await run_pipeline(
        request(),
        workflow=build_review_probe_workflow(models(planner, reviewer, coder)),
        on_event=events.append,
    )
    return record, events


def outcomes(events) -> list[dict]:
    return [
        e.actions.state_delta["outcome"]
        for e in events
        if e.actions and e.actions.state_delta.get("outcome")
    ]


def test_probe_request_extends_run_request():
    assert issubclass(ProbeRequest, RunRequest)
    assert set(ProbeRequest.model_fields) == set(RunRequest.model_fields) | {"probe_id"}


def test_probe_workflow_edges():
    workflow = build_review_probe_workflow(models(FakeLlm([]), FakeLlm([])))
    assert workflow.name == "issue_to_pr"
    assert workflow.input_schema is ProbeRequest

    def name(node) -> str:
        if isinstance(node, str):
            return node
        return getattr(node, "name", None) or node.__name__

    def shape(edge) -> tuple:
        if isinstance(edge, Edge):
            return name(edge.from_node), name(edge.to_node), edge.route
        source, target = edge
        if isinstance(target, dict):
            return name(source), {r: name(n) for r, n in target.items()}
        return name(source), name(target)

    assert [shape(e) for e in workflow.edges] == [
        ("START", "load_probe"),
        ("load_probe", "provision_sandbox"),
        ("provision_sandbox", "planner"),
        ("planner", "route_plan"),
        (
            "route_plan",
            {"actionable": "apply_probe_patch", "declined": "report_failure"},
        ),
        ("apply_probe_patch", "collect_diff"),
        ("collect_diff", "run_tests"),
        ("run_tests", {"pass": "reviewer"}),
        # one edge with two routes: ADK refuses two routed edges to the same node
        ("run_tests", "probe_invalid", ["fail", "exhausted"]),
        ("reviewer", "record_verdict"),
    ]


@pytest.mark.parametrize("review", [APPROVE, CHANGES])
async def test_probe_run_records_the_verdict(probe_store, monkeypatch, review):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    planner, reviewer = FakeLlm([json_out(PLAN)]), FakeLlm([json_out(review)])
    coder = FakeLlm([])
    record, events = await run(planner, reviewer, env, monkeypatch, coder)
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    assert record.patch_path is None
    assert coder.calls == 0
    final = outcomes(events)[-1]
    assert final["verdict"] == review.verdict
    assert final["must_fix"] == review.must_fix
    messages = [
        e.content.parts[0].text
        for e in events
        if e.author == "issue_to_pr" and e.content and e.content.parts
    ]
    assert f"review verdict: {review.verdict}" in messages
    assert env.files[PROBE_PATCH_PATH] == DIFF
    assert any(
        c.startswith("git apply") and PROBE_PATCH_PATH in c for c in env.commands
    )
    assert env.closed


async def test_agents_never_see_the_probe_kind(probe_store, monkeypatch):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    planner, reviewer = (
        RecordingLlm([json_out(PLAN)]),
        RecordingLlm([json_out(CHANGES)]),
    )
    await run(planner, reviewer, env, monkeypatch)
    seen = "\n".join(planner.seen + reviewer.seen).lower()
    assert planner.seen and reviewer.seen
    assert NOTE not in seen
    for word in ("bad", "good", "shortcut", "probe"):
        assert not re.search(rf"\b{word}", seen), word


async def test_failing_tests_mark_the_probe_invalid(probe_store, monkeypatch):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: FAIL})
    reviewer = FakeLlm([])
    record, events = await run(FakeLlm([json_out(PLAN)]), reviewer, env, monkeypatch)
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.reason == "probe patch failed the visible tests"
    assert record.patch_path is None
    assert reviewer.calls == 0
    assert "verdict" not in outcomes(events)[-1]
    assert env.closed


async def test_a_planner_decline_has_no_verdict_and_is_excluded(
    probe_store, monkeypatch
):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    decline = Plan(actionable=False, decline_reason="needs a decision", summary="-")
    reviewer = FakeLlm([])
    record, events = await run(FakeLlm([json_out(decline)]), reviewer, env, monkeypatch)
    assert (record.outcome, record.failure_kind) == ("declined", "none")
    assert reviewer.calls == 0 and PROBE_PATCH_PATH not in env.files
    assert "verdict" not in outcomes(events)[-1]


async def test_a_patch_that_does_not_apply_is_an_infra_error(probe_store):
    env = FakeEnvironment(
        responses={"git apply": ExecResult(exit_code=1, stdout="", stderr="no")}
    )
    registry.register(env)
    with pytest.raises(InfraError, match="does not apply"):
        async for _ in apply_probe_patch(
            Plan(actionable=True, summary="s"), env.env_id, "rp-01"
        ):
            pass


async def test_run_probe_spec_returns_a_row_with_the_probe_fields(
    probe_store, monkeypatch
):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    [spec] = plan_probe_runs(list_probes(), "flash", 1, "s")
    assert spec.run_id == "t-1-review-flash-rp-01-r1-s"
    assert spec.label == "t-1/review/flash/rp-01/r1"
    row = await run_probe_spec(
        spec,
        workflow_factory=lambda preset: build_review_probe_workflow(
            models(FakeLlm([json_out(PLAN)]), FakeLlm([json_out(CHANGES)]))
        ),
    )
    assert (row["probe_id"], row["kind"], row["verdict"]) == (
        "rp-01",
        "bad",
        "request_changes",
    )
    assert row["must_fix"] == CHANGES.must_fix
    assert (row["outcome"], row["failure_kind"], row["crashed"]) == (
        "patch_written",
        "none",
        False,
    )
    assert row["variant"] == "rp-01" and row["system"] == "review"


async def test_a_crashed_probe_run_keeps_the_probe_fields(probe_store, monkeypatch):
    use_env(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )
    [spec] = plan_probe_runs(list_probes(), "flash", 1, "s")

    def broken(preset):
        raise RuntimeError("boom")

    row = await run_probe_spec(spec, workflow_factory=broken)
    assert row["crashed"] is True
    assert (row["probe_id"], row["kind"], row["verdict"]) == ("rp-01", "bad", None)


def test_the_runner_validates_before_any_run(probe_store, monkeypatch, capsys):
    ran = []

    async def fake_run(spec, **kwargs):
        ran.append(spec)
        return {}

    monkeypatch.setattr(review_probe, "run_probe_spec", fake_run)
    monkeypatch.setattr(
        review_probe, "validate_probe", lambda probe: ["patch does not apply"]
    )
    out = probe_store / "out"
    assert review_probe.main(["--out", str(out), "--quiet"]) == 2
    assert "rp-01: patch does not apply" in capsys.readouterr().err
    assert ran == [] and not out.exists()


def test_the_runner_writes_results_and_the_report(probe_store, monkeypatch, capsys):
    async def fake_run(spec, **kwargs):
        return {
            "outcome": "patch_written",
            "failure_kind": "none",
            "probe_id": spec.variant,
            "kind": "bad",
            "verdict": "request_changes" if spec.repeat == 1 else "approve",
            "must_fix": [],
            "cost_usd": 0.2,
        }

    monkeypatch.setattr(review_probe, "run_probe_spec", fake_run)
    monkeypatch.setattr(review_probe, "validate_probe", lambda probe: [])
    out, report = probe_store / "out", probe_store / "docs" / "r.md"
    code = review_probe.main(
        ["--repeats", "2", "--out", str(out), "--report", str(report), "--quiet"]
    )
    assert code == 0
    [results] = out.glob("*-review-probe-flash.json")
    rows = json.loads(results.read_text())
    assert [r["repeat"] for r in rows] == [1, 2]
    assert rows[0]["run_id"].startswith("t-1-review-flash-rp-01-r1-")
    assert "1/2 (50%)" in report.read_text()
    assert "1/2 (50%)" in capsys.readouterr().out


def test_skip_validate_runs_without_validating(probe_store, monkeypatch):
    async def fake_run(spec, **kwargs):
        return {"outcome": "patch_written", "failure_kind": "none", "kind": "bad"}

    def never(probe):
        raise AssertionError("validated")

    monkeypatch.setattr(review_probe, "run_probe_spec", fake_run)
    monkeypatch.setattr(review_probe, "validate_probe", never)
    args = ["--repeats", "1", "--out", str(probe_store / "o"), "--quiet"]
    assert review_probe.main([*args, "--skip-validate"]) == 0
