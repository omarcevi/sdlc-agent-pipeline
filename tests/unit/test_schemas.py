import pytest
from pydantic import ValidationError

from app.schemas import Diff, Plan, RunRecord, SoloResult, TestReport


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


def test_run_record_roundtrips_json():
    record = RunRecord(task_id="t", run_id="r", outcome="failed", failure_kind="budget")
    assert RunRecord.model_validate_json(record.model_dump_json()) == record


def test_run_record_counts_model_stalls_and_old_records_still_load():
    old = (
        '{"task_id": "t", "run_id": "r", "outcome": "failed", "failure_kind": "infra"}'
    )
    assert RunRecord.model_validate_json(old).model_stalls == 0
    record = RunRecord(
        task_id="t", run_id="r", outcome="failed", failure_kind="infra", model_stalls=2
    )
    assert RunRecord.model_validate_json(record.model_dump_json()).model_stalls == 2
