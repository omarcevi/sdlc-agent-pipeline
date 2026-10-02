"""The dispatch guard for paid CI runs (scripts/ci_guard.py), run as a subprocess."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "ci_guard.py"
MAIN = "refs/heads/main"


@pytest.fixture
def tasks_dir(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    for name in ("tc-001", "tc-002", "sr-001", "zz-xyz", "notes"):
        (d / name).mkdir()
    for name in ("tc-h01", "sr-h02"):
        (d / name).mkdir()
    yield d
    for p in d.iterdir():
        p.chmod(0o755)


def run(args, tasks_dir, output=None):
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_OUTPUT"}
    if output is not None:
        env["GITHUB_OUTPUT"] = str(output)
    return subprocess.run(
        [sys.executable, str(GUARD), "--tasks-dir", str(tasks_dir), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def bench(tasks_dir, *args, ref=MAIN, output=None):
    return run(["--kind", "bench", "--ref", ref, *args], tasks_dir, output)


def assert_refused(r, message):
    assert r.returncode == 2, r.stderr
    assert r.stdout == ""
    assert r.stderr == f"error: {message}\n"


def test_main_only(tasks_dir):
    assert_refused(
        bench(tasks_dir, ref="refs/heads/feature"),
        "paid runs start from main only",
    )
    assert_refused(
        bench(tasks_dir, ref="refs/heads/main2"), "paid runs start from main only"
    )
    assert_refused(
        bench(tasks_dir, ref="refs/tags/v1"), "paid runs start from main only"
    )
    assert bench(tasks_dir).returncode == 0


def test_smoke_is_fixed_and_three_runs(tasks_dir, tmp_path):
    out = tmp_path / "out"
    r = run(["--kind", "smoke", "--ref", MAIN], tasks_dir, out)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "worst case: $3.00 for 3 runs\n"
    lines = out.read_text().splitlines()
    assert "tasks=tc-001,tc-003,sr-001" in lines
    assert "system=multi" in lines
    assert "preset=flash" in lines
    assert "repeats=1" in lines
    assert "runs=3" in lines


def test_eval_is_five_runs(tasks_dir):
    r = run(["--kind", "eval", "--ref", MAIN], tasks_dir)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "worst case: $5.00 for 5 runs\n"


def test_smoke_and_eval_reject_bench_flags(tasks_dir):
    for kind in ("smoke", "eval"):
        for flag in (
            ["--tasks", "tc-001"],
            ["--system", "multi"],
            ["--preset", "flash"],
            ["--repeats", "1"],
        ):
            r = run(["--kind", kind, "--ref", MAIN, *flag], tasks_dir)
            assert_refused(r, f"{kind} takes no task, system, preset or repeats")


def test_bench_defaults_and_whole_dev_split(tasks_dir, tmp_path):
    # Held-out directories must not be counted nor opened.
    for name in ("tc-h01", "sr-h02"):
        (tasks_dir / name).chmod(0o000)
    out = tmp_path / "out"
    r = bench(tasks_dir, output=out)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "worst case: $3.00 for 3 runs\n"
    lines = out.read_text().splitlines()
    assert "tasks=sr-001,tc-001,tc-002" in lines
    assert "system=multi" in lines
    assert "preset=flash" in lines
    assert "repeats=1" in lines
    assert "runs=3" in lines


@pytest.mark.parametrize(
    "tasks",
    [
        " tc-h01",
        "TC-H01",
        "tc-h01,tc-001",
        "tc-001, tc-h01 ",
        "tc-H01 ",
        "../bench/tasks/tc-h01",
    ],
)
def test_held_out_ids_are_refused_in_any_form(tasks_dir, tasks):
    assert_refused(bench(tasks_dir, "--tasks", tasks), "held-out tasks never run in CI")


@pytest.mark.parametrize(
    ("tasks", "message"),
    [
        ("tc-001,,tc-002", "bad task id"),
        ("../bench/tasks/tc-001", "bad task id"),
        ("tc-001,", "bad task id"),
        ("tc-1", "bad task id"),
        ("tc-001,tc-001", "task listed twice"),
        ("tc-001, tc-001", "task listed twice"),
        ("zz-999", "unknown task"),
    ],
)
def test_bad_and_duplicate_and_unknown_ids(tasks_dir, tasks, message):
    assert_refused(bench(tasks_dir, "--tasks", tasks), message)


def test_listed_tasks_are_used(tasks_dir, tmp_path):
    out = tmp_path / "out"
    r = bench(tasks_dir, "--tasks", " tc-002 , tc-001", "--repeats", "2", output=out)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "worst case: $4.00 for 4 runs\n"
    assert "tasks=tc-002,tc-001" in out.read_text().splitlines()


def test_single_with_mixed_and_repeats_range(tasks_dir):
    assert_refused(
        bench(tasks_dir, "--system", "single", "--preset", "mixed"),
        "single does not accept mixed",
    )
    assert bench(tasks_dir, "--system", "multi", "--preset", "mixed").returncode == 0
    for bad in ("0", "6", "x", "-1", "1.5"):
        assert_refused(bench(tasks_dir, "--repeats", bad), "repeats must be 1 to 5")
    for good in ("1", "5"):
        assert bench(tasks_dir, "--repeats", good).returncode == 0


def test_over_25_needs_accept(tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(16):
        (big / f"tc-{i + 1:03d}").mkdir()
    r = bench(big, "--repeats", "2")
    assert_refused(r, "worst case $32.00 is over $25; pass accept_over_25 to run it")
    r = bench(big, "--repeats", "2", "--accept-over-25")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "worst case: $32.00 for 32 runs\n"


def test_budget_usd_scales_cost(tasks_dir):
    r = bench(tasks_dir, "--budget-usd", "2.50")
    assert r.stdout == "worst case: $7.50 for 3 runs\n"


def test_github_output_lines(tasks_dir, tmp_path):
    out = tmp_path / "out"
    out.write_text("earlier=1\n")
    r = bench(tasks_dir, "--tasks", "tc-001,tc-002", output=out)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "worst case: $2.00 for 2 runs\n"
    lines = out.read_text().splitlines()
    assert lines[0] == "earlier=1"
    assert lines[1:] == [
        "tasks=tc-001,tc-002",
        "system=multi",
        "preset=flash",
        "repeats=1",
        "runs=2",
    ]


def test_refusal_prints_one_error_line_and_exits_2(tasks_dir, tmp_path):
    out = tmp_path / "out"
    r = bench(tasks_dir, "--tasks", "zz-999", ref="refs/heads/x", output=out)
    assert r.returncode == 2
    assert r.stdout == ""
    assert len(r.stderr.splitlines()) == 1
    assert r.stderr.startswith("error: ")
    assert not out.exists()
    # Argument errors are refusals too, not argparse usage dumps.
    r = run(["--kind", "nope", "--ref", MAIN], tasks_dir)
    assert r.returncode == 2
    assert r.stdout == ""
    assert len(r.stderr.splitlines()) == 1
    assert r.stderr.startswith("error: ")


def test_non_ascii_digits_are_not_dev_tasks(tasks_dir, tmp_path):
    (tasks_dir / "tc-\u0660\u0660\u0663").mkdir()
    out = tmp_path / "out"
    r = bench(tasks_dir, output=out)
    assert r.returncode == 0, r.stderr
    assert "tasks=sr-001,tc-001,tc-002" in out.read_text().splitlines()
    assert_refused(bench(tasks_dir, "--tasks", "tc-\u0660\u0660\u0663"), "bad task id")
    assert_refused(bench(tasks_dir, "--tasks", "tc-h\u0660\u0661"), "bad task id")


@pytest.mark.parametrize("budget", ["1e30", "nan", "inf", "-1", "0", "abc", "1e999999"])
def test_bad_budgets_are_one_error_line(tasks_dir, budget):
    assert_refused(
        bench(tasks_dir, "--budget-usd", budget), "budget-usd must be a number"
    )


def test_abbreviated_flags_are_refused(tasks_dir):
    r = bench(tasks_dir, "--repeat", "1")
    assert r.returncode == 2
    assert r.stdout == ""
    assert len(r.stderr.splitlines()) == 1
    assert r.stderr.startswith("error: ")


def test_empty_dev_split_is_refused(tmp_path):
    empty = tmp_path / "empty"
    (empty / "tc-h01").mkdir(parents=True)
    assert_refused(bench(empty), "no dev tasks found")
