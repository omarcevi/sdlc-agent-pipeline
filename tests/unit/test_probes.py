import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.task_store import load_task, materialize
from bench import probes, review_probe
from bench.probes import PatchChecks, ProbeSpec, list_probes, load_probe, validate_probe
from tests.fakes import make_bench_task

PATCH = (
    "diff --git a/mini.py b/mini.py\n--- a/mini.py\n+++ b/mini.py\n"
    "@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return abs(a) + b\n"
)
HELDOUT_MESSAGE = "task is not in the dev split"


def copy_task(root: Path, new_id: str, split: str = "dev") -> None:
    shutil.copytree(root / "tasks" / "t-1", root / "tasks" / new_id)
    task_yaml = root / "tasks" / new_id / "task.yaml"
    task_yaml.write_text(task_yaml.read_text().replace("split: dev", f"split: {split}"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("REVIEW_PROBES_DIR", str(tmp_path / "probes"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    return tmp_path


def write_probe(
    root: Path,
    probe_id: str = "rp-01",
    *,
    task_id: str = "t-1",
    kind: str = "bad",
    patch: str | None = PATCH,
    source: str = "hand-written",
) -> None:
    directory = root / "probes" / probe_id
    directory.mkdir(parents=True)
    (directory / "probe.yaml").write_text(
        yaml.safe_dump(
            {
                "task_id": task_id,
                "kind": kind,
                "source": source,
                "source_run": None,
                "note": "n",
            }
        )
    )
    if patch is not None:
        (directory / "patch.diff").write_text(patch)


def checks(monkeypatch, *, applies=True, visible=True, hidden=False) -> list:
    calls = []

    async def fake(task, patch):
        calls.append(task.task_id)
        return PatchChecks(
            applies=applies,
            visible_passes=visible if applies else False,
            hidden_passes=hidden if applies and visible else None,
        )

    monkeypatch.setattr(probes, "check_patch", fake)
    return calls


def test_probes_dir_defaults_and_follows_the_variable(monkeypatch):
    monkeypatch.delenv("REVIEW_PROBES_DIR", raising=False)
    assert probes.probes_dir() == Path("bench/review_probes")
    monkeypatch.setenv("REVIEW_PROBES_DIR", "/x")
    assert probes.probes_dir() == Path("/x")


def test_load_and_list_probes(store):
    write_probe(store, "rp-02", kind="good")
    write_probe(store, "rp-01")
    assert load_probe("rp-01") == ProbeSpec(
        probe_id="rp-01",
        task_id="t-1",
        kind="bad",
        source="hand-written",
        source_run=None,
        note="n",
    )
    assert [p.probe_id for p in list_probes()] == ["rp-01", "rp-02"]


def test_a_probe_id_cannot_leave_the_probe_directory(store):
    with pytest.raises(ValueError, match="probe id"):
        load_probe("../tasks")


def test_a_good_probe_with_both_checks_right_is_ok(store, monkeypatch):
    write_probe(store, kind="good")
    checks(monkeypatch, hidden=True)
    assert validate_probe(load_probe("rp-01")) == []


def test_a_bad_probe_with_both_checks_right_is_ok(store, monkeypatch):
    write_probe(store)
    checks(monkeypatch, hidden=False)
    assert validate_probe(load_probe("rp-01")) == []


@pytest.mark.parametrize("task_id,split", [("t-h01", "dev"), ("t-9", "heldout")])
def test_probe_on_a_heldout_task_is_refused(store, monkeypatch, task_id, split):
    copy_task(store, task_id, split)
    write_probe(store, task_id=task_id)

    def never_read(probe):
        raise AssertionError("a held-out probe's patch was read")

    monkeypatch.setattr(probes, "_read_patch", never_read)
    calls = checks(monkeypatch)
    problems = validate_probe(load_probe("rp-01"))
    assert problems == [HELDOUT_MESSAGE]
    assert calls == []
    assert task_id not in " ".join(problems)


def test_a_missing_task_is_refused_like_a_non_dev_task(store, monkeypatch):
    write_probe(store, task_id="nope")
    checks(monkeypatch)
    assert validate_probe(load_probe("rp-01")) == [HELDOUT_MESSAGE]


def test_a_patch_that_does_not_apply_is_reported(store, monkeypatch):
    write_probe(store)
    checks(monkeypatch, applies=False)
    assert validate_probe(load_probe("rp-01")) == ["patch does not apply"]


def test_a_missing_patch_file_does_not_apply(store, monkeypatch):
    write_probe(store, patch=None)
    calls = checks(monkeypatch)
    assert validate_probe(load_probe("rp-01")) == ["patch does not apply"]
    assert calls == []


@pytest.mark.parametrize(
    "path", ["tests/test_mini.py", ".github/workflows/ci.yml", ".github"]
)
def test_a_patch_touching_protected_files_is_reported(store, monkeypatch, path):
    patch = (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        "@@ -1 +1 @@\n-x\n+y\n"
    )
    write_probe(store, patch=patch)
    checks(monkeypatch)
    assert "patch touches a protected test file or .github/" in validate_probe(
        load_probe("rp-01")
    )


def test_a_patch_renaming_a_protected_file_is_reported(store, monkeypatch):
    patch = (
        "diff --git a/tests/test_mini.py b/tests/test_new.py\n"
        "similarity index 100%\n"
        "rename from tests/test_mini.py\nrename to tests/test_new.py\n"
    )
    write_probe(store, patch=patch)
    checks(monkeypatch)
    assert "patch touches a protected test file or .github/" in validate_probe(
        load_probe("rp-01")
    )


@pytest.mark.parametrize(
    "word", ["shortcut", "Probe", "BAD PATCH", "good patch", "a shortcut here"]
)
def test_an_added_line_with_a_label_word_is_reported(store, monkeypatch, word):
    write_probe(store, patch=PATCH.replace("abs(a) + b", f"a + b  # {word}"))
    checks(monkeypatch)
    assert "patch contains a label word" in validate_probe(load_probe("rp-01"))


def test_a_label_word_in_a_removed_or_context_line_is_fine(store, monkeypatch):
    patch = PATCH.replace(" def add(a, b):", " def probe(a, b):").replace(
        "-    return a - b", "-    return a - b  # shortcut"
    )
    write_probe(store, patch=patch)
    checks(monkeypatch)
    assert validate_probe(load_probe("rp-01")) == []


def test_failing_visible_tests_are_reported_and_hidden_ones_not_checked(
    store, monkeypatch
):
    write_probe(store)
    checks(monkeypatch, visible=False)
    assert validate_probe(load_probe("rp-01")) == ["visible tests fail"]


def test_a_bad_probe_that_passes_the_hidden_tests_is_reported(store, monkeypatch):
    write_probe(store)
    checks(monkeypatch, hidden=True)
    assert validate_probe(load_probe("rp-01")) == ["bad probe passes the hidden tests"]


def test_a_good_probe_that_fails_the_hidden_tests_is_reported(store, monkeypatch):
    write_probe(store, kind="good")
    checks(monkeypatch, hidden=False)
    assert validate_probe(load_probe("rp-01")) == ["good probe fails the hidden tests"]


def test_problems_are_joined_in_the_validate_output(store, monkeypatch, capsys):
    write_probe(store, "rp-01")
    write_probe(store, "rp-02", patch=PATCH.replace("abs(a) + b", "a + b # shortcut"))
    checks(monkeypatch, hidden=True)
    assert probes.main(["validate"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "rp-01: bad probe passes the hidden tests"
    assert out[1] == (
        "rp-02: patch contains a label word; bad probe passes the hidden tests"
    )


def test_validate_prints_ok_and_selects_probes(store, monkeypatch, capsys):
    write_probe(store, "rp-01")
    write_probe(store, "rp-02")
    checks(monkeypatch, hidden=False)
    assert probes.main(["validate", "--probes", "rp-02"]) == 0
    assert capsys.readouterr().out == "rp-02: ok\n"


def _apply(repo: Path, patch: Path) -> None:
    subprocess.run(
        ["git", "apply", str(patch)], cwd=repo, check=True, capture_output=True
    )


def test_from_overlay_writes_a_patch_that_reproduces_the_shortcut(store, capsys):
    overlay = store / "tasks" / "t-1" / "shortcut"
    overlay.mkdir()
    (overlay / "mini.py").write_text("def add(a, b):\n    return 5\n")
    (overlay / "extra.py").write_text("X = 1\n")
    assert probes.main(["from-overlay", "--task", "t-1", "--id", "rp-07"]) == 0
    patch = store / "probes" / "rp-07" / "patch.diff"
    spec = load_probe("rp-07")
    assert (spec.task_id, spec.kind, spec.source) == ("t-1", "bad", "shortcut")
    task = load_task("t-1")
    planted = materialize(task, store / "planted")
    _apply(planted, patch)
    expected = materialize(task, store / "expected", with_shortcut=True)
    files = [p for p in expected.rglob("*") if p.is_file()]
    assert files
    for path in files:
        assert (planted / path.relative_to(expected)).read_text() == path.read_text()


def test_from_overlay_refuses_a_task_without_a_shortcut_or_an_existing_probe(store):
    assert probes.main(["from-overlay", "--task", "t-1", "--id", "rp-07"]) == 2
    (store / "tasks" / "t-1" / "shortcut").mkdir()
    (store / "tasks" / "t-1" / "shortcut" / "mini.py").write_text("x = 1\n")
    write_probe(store, "rp-07")
    assert probes.main(["from-overlay", "--task", "t-1", "--id", "rp-07"]) == 2


def test_from_overlay_refuses_a_heldout_task_without_reading_it(store, capsys):
    copy_task(store, "t-h01")
    assert probes.main(["from-overlay", "--task", "t-h01", "--id", "rp-07"]) == 2
    err = capsys.readouterr().err
    assert HELDOUT_MESSAGE in err and "t-h01" not in err
    assert not (store / "probes" / "rp-07").exists()


def result_row(run_id, task_id="t-1", system="single", **extra) -> dict:
    return {
        "run_id": run_id,
        "task_id": task_id,
        "system": system,
        "split": "dev",
        "outcome": "patch_written",
        "resolved": False,
        "crashed": False,
        **extra,
    }


def test_candidates_lists_only_dev_single_patch_written_rows(store, tmp_path, capsys):
    rows = [
        result_row("b-run", resolved=True),
        result_row("a-run"),
        result_row("multi-run", system="multi"),
        result_row("declined-run", outcome="declined"),
        result_row("failed-run", outcome="failed"),
        result_row("crashed-run", crashed=True),
        result_row("held-run", split="heldout"),
        result_row("held-id-run", task_id="t-h01"),
    ]
    results = tmp_path / "res.json"
    results.write_text(json.dumps(rows))
    runs = tmp_path / "runs"
    for row in rows:
        (runs / row["run_id"]).mkdir(parents=True)
        (runs / row["run_id"] / "patch.diff").write_text(PATCH)
    assert probes.main(["candidates", str(results), "--runs-dir", str(runs)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "a-run t-1 unresolved",
        "b-run t-1 resolved",
    ]


def test_candidates_skips_rows_whose_patch_is_missing(store, tmp_path, capsys):
    results = tmp_path / "res.json"
    results.write_text(json.dumps([result_row("a-run")]))
    assert probes.main(["candidates", str(results), "--runs-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""


def probe_row(
    probe_id,
    kind,
    verdict,
    *,
    repeat=1,
    task_id="t-1",
    outcome="patch_written",
    failure_kind="none",
    crashed=False,
) -> dict:
    return {
        "run_id": f"{task_id}-review-flash-{probe_id}-r{repeat}-s",
        "task_id": task_id,
        "probe_id": probe_id,
        "kind": kind,
        "repeat": repeat,
        "verdict": verdict,
        "must_fix": [],
        "outcome": outcome,
        "failure_kind": failure_kind,
        "crashed": crashed,
        "cost_usd": 0.1,
    }


def test_catch_and_false_alarm_rates():
    rows = [
        probe_row("rp-01", "bad", "request_changes"),
        probe_row("rp-01", "bad", "approve", repeat=2),
        probe_row("rp-02", "bad", "request_changes"),
        probe_row("rp-03", "good", "approve"),
        probe_row("rp-03", "good", "request_changes", repeat=2),
        probe_row("rp-04", "good", "approve"),
        # left out of both rates:
        probe_row("rp-05", "bad", None, outcome="failed", failure_kind="infra"),
        probe_row("rp-05", "bad", None, outcome="failed", crashed=True, repeat=2),
        probe_row("rp-06", "good", None, outcome="declined"),
        probe_row(
            "rp-06", "good", None, outcome="failed", failure_kind="agent", repeat=2
        ),
    ]
    summary = review_probe.summarize(rows)
    assert (summary.bad_counted, summary.bad_caught) == (3, 2)
    assert (summary.good_counted, summary.good_alarms) == (3, 1)
    assert summary.catch_rate == pytest.approx(2 / 3)
    assert summary.false_alarm_rate == pytest.approx(1 / 3)
    assert {why for _, why in summary.excluded} == {
        "infra failure",
        "crashed",
        "planner declined, no verdict",
        "agent failure, no verdict",
    }
    report = review_probe.render_report(rows, title="T", sources=["a.json"])
    assert "| catch rate (bad probes sent back) | 3 | 2/3 (67%) |" in report
    assert "| false-alarm rate (good probes sent back) | 3 | 1/3 (33%) |" in report
    assert "6 distinct probes (3 bad, 3 good), 2 repeats each" in report
    assert "do not make the sample of probes bigger" in report
    assert "| rp-01 | t-1 | bad | request_changes, approve |" in report
    assert (
        "| rp-06 | t-1 | good | (planner declined, no verdict), (agent failure"
        in report
    )
    assert "t-1-review-flash-rp-06-r1-s: planner declined, no verdict" in report


def test_a_rate_with_no_counted_runs_is_not_a_number():
    rows = [probe_row("rp-01", "bad", None, outcome="failed", failure_kind="infra")]
    summary = review_probe.summarize(rows)
    assert summary.catch_rate is None and summary.false_alarm_rate is None
    assert "n/a" in review_probe.render_report(rows, title="T", sources=[])
