import json
import shutil
from pathlib import Path

import pytest
import yaml

from app.schemas import RunRecord
from app.task_store import load_task
from bench import score_run
from bench.demo import expected_tree_sha

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is missing")


@pytest.fixture
def scored(monkeypatch):
    """Scoring patched: records the tasks it was asked about."""
    calls: list = []

    async def fake(task, record):
        calls.append((task.task_id, record.outcome))
        return True

    monkeypatch.setattr(score_run, "is_resolved", fake)
    return calls


def write_run(
    root: Path,
    run_id: str = "run-1",
    *,
    base_ref: str | None = None,
    tree: str | None = None,
    events: list[dict] | None = None,
    outcome: str = "patch_written",
    pr_url: str | None = None,
    approval_wait_s: float = 0.0,
) -> None:
    run = root / "runs" / run_id
    run.mkdir(parents=True)
    record = RunRecord(
        task_id="t-1",
        run_id=run_id,
        outcome=outcome,
        failure_kind="none",
        cost_usd=0.25,
        mode="live" if base_ref else "bench",
        base_ref=base_ref,
        base_tree_sha=tree,
        pr_url=pr_url,
        approval_wait_s=approval_wait_s,
    )
    (run / "record.json").write_text(record.model_dump_json())
    if events is not None:
        (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def issue_event(title: str, body: str) -> dict:
    return {"actions": {"state_delta": {"issue": {"title": title, "body": body}}}}


def run(root: Path, *args: str) -> int:
    return score_run.main(
        [
            "--run-id",
            "run-1",
            "--runs-dir",
            str(root / "runs"),
            "--out",
            str(root / "live"),
            *args,
        ]
    )


def row(root: Path) -> dict:
    return json.loads((root / "live" / "run-1.json").read_text())


def test_score_run_maps_a_demo_branch_to_its_task(bench, scored):
    tree = expected_tree_sha(load_task("t-1"))
    write_run(
        bench,
        base_ref="demo/t-1",
        tree=tree,
        events=[issue_event("add is broken", "add subtracts")],
    )
    assert run(bench) == 0
    assert scored == [("t-1", "patch_written")]
    result = row(bench)
    assert result["task_id"] == "t-1"
    assert result["resolved"] is True
    assert result["mode"] == "live"
    assert result["cost_usd"] == 0.25
    assert result["issue_text_differs"] is False
    assert {"pr_url", "approval_wait_s", "category", "run_id"} <= set(result)


def test_score_run_refuses_a_tree_mismatch(bench, scored, capsys):
    write_run(bench, base_ref="demo/t-1", tree="0" * 40)
    assert run(bench) == 2
    assert capsys.readouterr().err.strip() == (
        "the run's source is not this task's demo state; not scored"
    )
    assert scored == []
    assert not (bench / "live").exists()


def test_score_run_refuses_heldout_without_naming_it(bench, scored, capsys):
    heldout = bench / "tasks" / "mini-h01"
    shutil.copytree(bench / "tasks" / "t-1", heldout)
    path = heldout / "task.yaml"
    path.write_text(path.read_text().replace("split: dev", "split: heldout"))
    for task_id, base_ref in (("mini-h01", None), (None, "demo/mini-h01")):
        write_run(bench, base_ref=base_ref)
        args = ["--task", task_id] if task_id else []
        assert run(bench, *args) == 2
        err = capsys.readouterr().err
        assert err.strip() == "task is not in the dev split"
        assert "h01" not in err
        shutil.rmtree(bench / "runs")
    # A held-out split behind an ordinary-looking id gets the same message.
    shutil.copytree(bench / "tasks" / "t-1", bench / "tasks" / "sneaky")
    sneaky = bench / "tasks" / "sneaky" / "task.yaml"
    sneaky.write_text(
        yaml.safe_dump({**yaml.safe_load(sneaky.read_text()), "split": "heldout"})
    )
    write_run(bench)
    assert run(bench, "--task", "sneaky") == 2
    assert capsys.readouterr().err.strip() == "task is not in the dev split"
    assert scored == []


def test_score_run_refuses_unknown_task_and_missing_record(bench, scored, capsys):
    write_run(bench)
    assert run(bench, "--task", "nope") == 2
    assert "unknown task" in capsys.readouterr().err
    assert score_run.main(["--run-id", "gone", "--runs-dir", str(bench / "runs")]) == 2
    assert "no readable record" in capsys.readouterr().err
    assert scored == []


def test_score_run_flags_changed_issue_text(bench, scored):
    tree = expected_tree_sha(load_task("t-1"))
    write_run(
        bench,
        base_ref="demo/t-1",
        tree=tree,
        events=[issue_event("add is broken", "someone edited the body")],
    )
    assert run(bench) == 0
    assert row(bench)["issue_text_differs"] is True
    assert row(bench)["resolved"] is True


def test_score_run_ignores_line_endings_and_trailing_whitespace(bench, scored):
    tree = expected_tree_sha(load_task("t-1"))
    # GitHub stores what a web form sent: CRLF line ends, a trailing newline.
    write_run(
        bench,
        base_ref="demo/t-1",
        tree=tree,
        events=[issue_event("add is broken  ", "add subtracts \r\n\r\n")],
    )
    assert run(bench) == 0
    assert row(bench)["issue_text_differs"] is False


def test_the_text_comparison_normalises_both_sides():
    same = score_run.same_issue_text
    assert same("a\r\nb\r\n", "a\nb")  # CRLF and a missing trailing newline
    assert same("a\nb \t\n\n", "a\nb")  # trailing whitespace
    assert not same("a\nb", "a\nc")
    assert not same(" a", "a")  # leading whitespace is content
    assert not same(None, "a")


def test_score_run_scores_a_bench_run_with_task(bench, scored, capsys):
    write_run(bench)
    assert run(bench) == 2
    assert "no task" in capsys.readouterr().err
    assert run(bench, "--task", "t-1") == 0
    assert scored == [("t-1", "patch_written")]
    assert row(bench)["mode"] == "bench"
    assert row(bench)["issue_text_differs"] is None


def test_score_run_scores_a_pr_opened_run_as_a_patch(bench, scored):
    tree = expected_tree_sha(load_task("t-1"))
    write_run(
        bench,
        base_ref="demo/t-1",
        tree=tree,
        outcome="pr_opened",
        pr_url="https://x/pull/1",
    )
    assert run(bench) == 0
    assert scored == [("t-1", "patch_written")]
    assert row(bench)["outcome"] == "pr_opened"
    assert row(bench)["pr_url"] == "https://x/pull/1"


def test_score_run_reads_the_pr_url_and_the_wait_from_the_record(bench, scored):
    # The driver measures the wait and keeps both in record.json; events.jsonl has
    # no state_delta for the wait, and an old pr_url there must not win.
    tree = expected_tree_sha(load_task("t-1"))
    write_run(
        bench,
        base_ref="demo/t-1",
        tree=tree,
        outcome="pr_opened",
        pr_url="https://x/pull/2",
        approval_wait_s=12.5,
        events=[
            issue_event("add is broken", "add subtracts"),
            {"actions": {"state_delta": {"outcome": {"pr_url": "https://x/pull/9"}}}},
            {"actions": {"state_delta": {"approval_wait_s": 99.0}}},
        ],
    )
    assert run(bench) == 0
    result = row(bench)
    assert (result["pr_url"], result["approval_wait_s"]) == ("https://x/pull/2", 12.5)
    assert result["issue_text_differs"] is False
