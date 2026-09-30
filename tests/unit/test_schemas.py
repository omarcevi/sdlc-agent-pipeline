from app.schemas import Diff, Plan, RunRecord, TestReport


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
