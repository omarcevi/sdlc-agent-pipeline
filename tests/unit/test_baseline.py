import re
from pathlib import Path

import pytest

from app.agents.solo import build_solo
from app.baseline import build_baseline_workflow
from app.driver import run_pipeline
from app.nodes.routing import route_solo
from app.nodes.verify import TEST_CMD
from app.schemas import RunRequest, SoloResult
from app.tools import CODER_TOOLS
from tests.fakes import (
    FakeEnvironment,
    FakeLlm,
    call,
    empty_response,
    json_out,
    make_bench_task,
)
from tests.unit.test_pipeline import DIFF, FAIL, PASS, diff_responses, use_env

SOLO = SoloResult(declined=False, summary="fixed add", files_changed=["mini.py"])


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    return tmp_path


async def run(solo):
    return await run_pipeline(
        RunRequest(task_id="t-1", run_id="r-1"),
        workflow=build_baseline_workflow(solo),
    )


async def test_baseline_happy_path_writes_patch(bench, monkeypatch):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    solo = FakeLlm([json_out(SOLO)])
    record = await run(solo)
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    assert Path(record.patch_path).read_text() == DIFF
    assert solo.calls == 1 and env.closed


async def test_baseline_declines_a_trap(bench, monkeypatch):
    use_env(monkeypatch, FakeEnvironment())
    solo = FakeLlm(
        [
            json_out(
                SoloResult(declined=True, decline_reason="needs OAuth", summary="n/a")
            )
        ]
    )
    record = await run(solo)
    assert (record.outcome, record.reason) == ("declined", "needs OAuth")


async def test_baseline_test_loop_exhausts_after_three_returns(bench, monkeypatch):
    use_env(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: FAIL})
    )
    solo = FakeLlm([json_out(SOLO)] * 4)
    record = await run(solo)
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.test_attempts == 4 and solo.calls == 4


async def test_baseline_empty_diff_goes_back_to_the_agent(bench, monkeypatch):
    responses = {
        **diff_responses(diffs=("", DIFF), numstats=("", "1\t1\tmini.py\n")),
        TEST_CMD: PASS,
    }
    use_env(monkeypatch, FakeEnvironment(responses=responses))
    solo = FakeLlm([json_out(SOLO)] * 2)
    record = await run(solo)
    assert record.outcome == "patch_written"
    assert solo.calls == 2 and record.test_attempts == 1


async def test_baseline_shares_the_run_budget(bench, monkeypatch):
    monkeypatch.setenv("RUN_BUDGET_USD", "0.001")
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    solo = FakeLlm([call("list_dir", path="."), json_out(SOLO)])
    record = await run(solo)
    assert (record.outcome, record.failure_kind) == ("failed", "budget")
    assert env.closed


async def test_solo_is_exempt_from_the_per_turn_cap(bench, monkeypatch):
    monkeypatch.setenv("MAX_TOOL_CALLS_PER_TURN", "2")
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    solo = FakeLlm([call("list_dir", path=".")] * 4 + [json_out(SOLO)])
    record = await run(solo)
    assert record.outcome == "patch_written", record.reason
    assert record.tool_calls == 4


def test_solo_instruction_reads_only_issue_text():
    instruction = build_solo(FakeLlm([])).instruction
    assert isinstance(instruction, str)
    assert set(re.findall(r"\{([^{}]*)\}", instruction)) == {"issue_text"}
    assert instruction.count("{") == instruction.count("}") == 1


def test_solo_contract():
    agent = build_solo(FakeLlm([]))
    assert agent.name == "solo"
    assert (agent.output_schema, agent.output_key) == (SoloResult, "solo")
    assert list(agent.tools) == list(CODER_TOOLS)
    assert agent.generate_content_config.temperature == 0


def test_baseline_workflow_shape():
    wf = build_baseline_workflow(FakeLlm([]))
    assert wf.name == "issue_to_pr"
    assert wf.input_schema is RunRequest


@pytest.mark.parametrize("declined", [True, False])
def test_route_solo(declined):
    result = SoloResult(declined=declined, decline_reason="why", summary="s")
    event = route_solo(result)
    assert event.actions.route == ("declined" if declined else "done")
    if declined:
        assert event.actions.state_delta["failure"] == {
            "kind": "declined",
            "reason": "why",
        }


async def test_empty_solo_answer_is_an_agent_failure(bench, monkeypatch):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    record = await run(FakeLlm([empty_response()]))
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.reason == "model returned no structured answer (solo)"
    assert record.tokens_in > 0 and record.cost_usd > 0 and env.closed
