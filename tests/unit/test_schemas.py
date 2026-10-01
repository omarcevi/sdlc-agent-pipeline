import pytest
from pydantic import ValidationError

from app.schemas import Diff, Plan, RunRecord, RunRequest, SoloResult, TestReport


def test_solo_result_requires_declined():
    with pytest.raises(ValidationError):
        SoloResult.model_validate({"summary": "nothing to do"})


def test_testreport_is_not_collected_by_pytest():
    assert TestReport.__test__ is False


def test_plan_defaults_allow_minimal_decline():
    plan = Plan(actionable=False, decline_reason="needs credentials", summary="n/a")
    assert plan.files_to_inspect == [] and plan.steps == []


def test_diff_empty_flag():
    assert Diff(unified_diff="", files=[], insertions=0, deletions=0).is_empty
    assert not Diff(
        unified_diff="x", files=["a.py"], insertions=1, deletions=0
    ).is_empty


def test_bench_request_needs_a_task_id():
    with pytest.raises(ValidationError):
        RunRequest(run_id="r")
    with pytest.raises(ValidationError):
        RunRequest(run_id="r", mode="bench", repo="o/n", issue_number=1)
    assert RunRequest(run_id="r", task_id="tc-001").mode == "bench"


def test_live_request_needs_repo_and_issue():
    ok = RunRequest(run_id="r", mode="live", repo="o/n", issue_number=3)
    assert ok.base_ref is None and ok.task_id is None
    for bad in (
        {"repo": "o/n"},
        {"issue_number": 3},
        {"repo": "not-a-repo", "issue_number": 3},
        {"repo": "o/n/x", "issue_number": 3},
        {"repo": "o/n", "issue_number": 0},
    ):
        with pytest.raises(ValidationError):
            RunRequest(run_id="r", mode="live", **bad)


def test_old_bench_request_json_still_parses():
    request = RunRequest.model_validate_json(
        '{"task_id": "tc-001", "run_id": "smoke-1"}'
    )
    assert (request.mode, request.task_id, request.repo) == ("bench", "tc-001", None)


def test_new_outcomes_and_record_fields():
    record = RunRecord(
        task_id="o/n#3", run_id="r", outcome="refused", failure_kind="none"
    )
    assert (record.mode, record.base_ref, record.base_sha, record.base_tree_sha) == (
        "bench",
        None,
        None,
        None,
    )
    for outcome in ("pr_opened", "rejected"):
        RunRecord(task_id="t", run_id="r", outcome=outcome, failure_kind="none")


def test_run_record_roundtrips_json():
    record = RunRecord(task_id="t", run_id="r", outcome="failed", failure_kind="budget")
    assert RunRecord.model_validate_json(record.model_dump_json()) == record


def test_test_report_fields_are_unchanged():
    """TestReport is the reviewer's and the coder's user message: its fields are part
    of the frozen prompts."""
    assert list(TestReport.model_fields) == [
        "passed",
        "exit_code",
        "failed_tests",
        "output_tail",
        "duration_s",
    ]
    assert list(TestReport(passed=True, exit_code=0).model_dump()) == [
        "passed",
        "exit_code",
        "failed_tests",
        "output_tail",
        "duration_s",
    ]
