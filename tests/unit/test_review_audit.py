import hashlib
from pathlib import Path

from google.adk.events import Event, EventActions
from google.genai import types

from bench import review_audit
from bench.probes import PatchChecks
from bench.review_audit import ReviewedDiff, audit, pair_reviews, render_report

SECRET = "SECRET_PATCH_LINE_do_not_print"


def diff_event(text: str) -> Event:
    return Event(
        author="issue_to_pr",
        actions=EventActions(
            state_delta={
                "diff": {"unified_diff": text, "files": ["a.py"]},
                "diff_text": text,
            }
        ),
    )


def answer_call(verdict: str) -> Event:
    part = types.Part(
        function_call=types.FunctionCall(
            name="set_model_response",
            args={"verdict": verdict, "comments": [], "must_fix": []},
        )
    )
    return Event(author="reviewer", content=types.Content(role="model", parts=[part]))


def review_event(verdict: str) -> Event:
    return Event(
        author="reviewer",
        actions=EventActions(state_delta={"review": {"verdict": verdict}}),
    )


def write_events(path: Path, events: list[Event]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(e.model_dump_json(by_alias=True) + "\n" for e in events))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_pairs_each_verdict_with_the_diff_it_reviewed(tmp_path):
    path = tmp_path / "events.jsonl"
    write_events(
        path,
        [
            diff_event("diff one"),
            answer_call("request_changes"),
            review_event("request_changes"),
            diff_event("diff two"),
            diff_event("diff three"),
            answer_call("approve"),
            review_event("approve"),
        ],
    )
    pairs = pair_reviews(path)
    assert [(d, r.round, r.verdict) for d, r in pairs] == [
        ("diff one", 1, "request_changes"),
        ("diff three", 2, "approve"),
    ]
    assert pairs[1][1].diff_sha256 == sha("diff three")
    assert isinstance(pairs[0][1], ReviewedDiff)


def test_a_call_without_a_state_delta_still_counts_once(tmp_path):
    path = tmp_path / "events.jsonl"
    write_events(path, [diff_event("d"), answer_call("approve")])
    assert [r.verdict for _, r in pair_reviews(path)] == ["approve"]


def row(task_id="md-001", run_id="r1", **kw):
    return {
        "task_id": task_id,
        "run_id": run_id,
        "system": "multi",
        "split": "dev",
        **kw,
    }


class FakeTask:
    def __init__(self, task_id):
        self.task_id = task_id


def fake_dev_task(monkeypatch):
    monkeypatch.setattr(review_audit, "dev_task", lambda task_id: FakeTask(task_id))


def test_identical_diffs_are_scored_once(tmp_path, monkeypatch):
    fake_dev_task(monkeypatch)
    for run in ("r1", "r2"):
        write_events(
            tmp_path / run / "events.jsonl",
            [diff_event("same diff"), answer_call("approve"), review_event("approve")],
        )
    calls = []

    def score(task, patch):
        calls.append(patch)
        return True

    summary = audit([row(run_id="r1"), row(run_id="r2")], tmp_path, score=score)
    assert len(calls) == 1
    assert summary.total.rounds == 2
    assert summary.total.distinct_diffs == 1


def test_confusion_matrix_counts(tmp_path, monkeypatch):
    fake_dev_task(monkeypatch)
    write_events(
        tmp_path / "r1" / "events.jsonl",
        [
            diff_event("bad one"),
            review_event("request_changes"),  # bad caught
            diff_event("good one"),
            review_event("request_changes"),  # good sent back
        ],
    )
    write_events(
        tmp_path / "r2" / "events.jsonl",
        [
            diff_event("bad two"),
            review_event("approve"),  # bad approved
            diff_event("good two"),
            review_event("approve"),  # good approved
        ],
    )
    summary = audit(
        [row(run_id="r1"), row(run_id="r2", task_id="md-002")],
        tmp_path,
        score=lambda task, patch: patch.startswith("good"),
    )
    t = summary.total
    assert (t.bad_caught, t.bad_approved, t.good_sent_back, t.good_approved) == (
        1,
        1,
        1,
        1,
    )
    assert t.rounds == 4 and t.distinct_diffs == 4
    assert summary.by_task["md-001"].bad_caught == 1
    assert summary.by_task["md-002"].bad_approved == 1


def test_audit_skips_heldout_rows_without_opening_them(tmp_path, monkeypatch):
    fake_dev_task(monkeypatch)
    opened = []
    monkeypatch.setattr(review_audit, "pair_reviews", lambda p: opened.append(p) or [])
    for run in ("h1", "d1"):
        write_events(tmp_path / run / "events.jsonl", [])
    rows = [
        row(task_id="md-001-h01", run_id="h1", split="heldout"),
        row(run_id="s1", system="single"),
        row(run_id="d1"),
    ]
    summary = audit(rows, tmp_path, score=lambda t, p: True)
    assert [p.parent.name for p in opened] == ["d1"]
    assert summary.skipped_not_dev == 1
    assert summary.skipped_not_multi == 1
    assert "md-001-h01" not in render_report(summary)


def test_report_never_contains_patch_text(tmp_path, monkeypatch):
    fake_dev_task(monkeypatch)
    write_events(
        tmp_path / "r1" / "events.jsonl",
        [diff_event(f"+{SECRET}"), review_event("approve")],
    )
    summary = audit([row()], tmp_path, score=lambda t, p: False)
    report = render_report(summary)
    assert SECRET not in report
    assert "bad approved" in report
    assert "verdict, not its reasons" in report
    assert "excluded by system or split" not in report


def test_the_full_diff_is_scored_not_the_truncated_text(tmp_path):
    full = "full diff " * 5
    path = tmp_path / "events.jsonl"
    write_events(
        path,
        [
            Event(
                author="issue_to_pr",
                actions=EventActions(
                    state_delta={
                        "diff": {"unified_diff": full},
                        "diff_text": "truncated...",
                    }
                ),
            ),
            review_event("approve"),
        ],
    )
    ((text, reviewed),) = pair_reviews(path)
    assert text == full
    assert reviewed.diff_sha256 == sha(full)


def test_a_diff_the_audit_cannot_reproduce_is_unscored(tmp_path, monkeypatch):
    fake_dev_task(monkeypatch)
    write_events(
        tmp_path / "r1" / "events.jsonl",
        [diff_event("d"), review_event("approve")],
    )

    async def fake_check(task, patch, **kw):
        return PatchChecks(True, False, None)

    monkeypatch.setattr(review_audit, "check_patch", fake_check)
    summary = audit([row()], tmp_path)
    t = summary.total
    assert t.unscored == 1 and t.rounds == 1
    assert t.bad_approved == t.bad_caught == 0
    assert "unscored" in render_report(summary)


def test_hidden_tests_alone_decide_good_and_bad(tmp_path, monkeypatch):
    fake_dev_task(monkeypatch)
    write_events(
        tmp_path / "r1" / "events.jsonl",
        [diff_event("d"), review_event("approve")],
    )

    async def fake_check(task, patch, **kw):
        return PatchChecks(True, True, True)

    monkeypatch.setattr(review_audit, "check_patch", fake_check)
    assert audit([row()], tmp_path).total.good_approved == 1


def test_audit_fills_run_and_task_ids(tmp_path, monkeypatch):
    fake_dev_task(monkeypatch)
    write_events(
        tmp_path / "r7" / "events.jsonl", [diff_event("d"), review_event("approve")]
    )
    seen = []
    real = review_audit.pair_reviews

    def spy(path):
        out = real(path)
        seen.extend(out)
        return out

    monkeypatch.setattr(review_audit, "pair_reviews", spy)
    audit([row(run_id="r7")], tmp_path, score=lambda t, p: True)
    assert seen[0][1].run_id == "" and seen[0][1].task_id == ""
    # The ids are set on the record the audit works with.
    captured = []
    monkeypatch.setattr(
        review_audit, "replace", lambda f, **kw: captured.append(kw) or f
    )
    audit([row(run_id="r7")], tmp_path, score=lambda t, p: True)
    assert captured == [{"run_id": "r7", "task_id": "md-001"}]


def test_a_dev_split_row_with_a_heldout_id_is_skipped_without_opening(
    tmp_path, monkeypatch
):
    fake_dev_task(monkeypatch)
    write_events(tmp_path / "h1" / "events.jsonl", [])
    opened = []
    monkeypatch.setattr(review_audit, "pair_reviews", lambda p: opened.append(p) or [])
    summary = audit([row(task_id="md-001-h02", run_id="h1")], tmp_path)
    assert opened == [] and summary.skipped_not_dev == 1


def test_a_row_without_an_event_log_is_counted(tmp_path, monkeypatch):
    fake_dev_task(monkeypatch)
    summary = audit([row(run_id="missing")], tmp_path, score=lambda t, p: True)
    assert summary.skipped_no_events == 1 and summary.total.rounds == 0
