import json
from pathlib import Path

from bench.report import load_rows, main, render_markdown, summarize


def row(task_id="t1", **kw):
    base = {
        "task_id": task_id,
        "category": "bug",
        "system": "multi",
        "preset": "flash",
        "repeat": 1,
        "run_id": f"{task_id}-r{kw.get('repeat', 1)}",
        "resolved": False,
        "outcome": "patch_written",
        "failure_kind": "none",
        "reason": "",
        "cost_usd": 0.1,
        "duration_s": 10.0,
        "audit": [],
        "infra_retries": 0,
        "crashed": False,
    }
    base.update(kw)
    return base


def test_summary_groups_by_system_and_preset():
    rows = [
        row(system="single", preset="pro"),
        row(system="multi", preset="pro"),
        row(system="multi", preset="flash"),
    ]
    got = summarize(rows)
    assert [(s.system, s.preset) for s in got] == [
        ("multi", "flash"),
        ("multi", "pro"),
        ("single", "pro"),
    ]
    assert all(s.runs == 1 for s in got)


def test_resolve_rate_mean_min_max_over_repeats():
    rows = [
        row("a", repeat=1, resolved=True),
        row("b", repeat=1, resolved=True),
        row("a", repeat=2, resolved=True),
        row("b", repeat=2, resolved=False),
        row("a", repeat=3, resolved=False),
        row("b", repeat=3, resolved=False),
    ]
    (s,) = summarize(rows)
    assert (s.runs, s.tasks, s.repeats) == (6, 2, 3)
    assert s.resolve_rate_mean == 0.5
    assert s.resolve_rate_min == 0.0
    assert s.resolve_rate_max == 1.0


def test_infra_rows_are_reported_separately():
    rows = [
        row("a", resolved=True),
        row("b", outcome="failed", failure_kind="infra"),
    ]
    (s,) = summarize(rows)
    assert s.resolve_rate_mean == 1.0  # infra row is not in the denominator
    assert s.infra_failures == 1
    assert s.runs == 2


def test_flagged_rows_are_counted_in_both_columns():
    rows = [
        row("a", resolved=True),
        row("b", resolved=True, audit=["edited a test file"]),
    ]
    (s,) = summarize(rows)
    assert s.resolve_rate_mean == 1.0
    assert s.clean_resolve_rate_mean == 0.5
    assert s.flagged_resolved == 1
    md = render_markdown([s], title="T", sources=["x.json"])
    assert "Flagged patches" in md
    assert "b-r1" in md and "edited a test file" in md


def test_legacy_and_crashed_rows_load(tmp_path: Path):
    legacy = {
        "task_id": "tc-001",
        "category": "bug",
        "resolved": True,
        "outcome": "patch_written",
        "failure_kind": "none",
        "cost_usd": 0.23,
        "duration_s": 174.6,
    }
    crashed = {
        "task_id": "tc-002",
        "category": "bug",
        "resolved": False,
        "outcome": "failed",
        "failure_kind": "infra",
        "reason": "boom",
        "crashed": True,
    }
    p = tmp_path / "r.json"
    p.write_text(json.dumps([legacy, crashed]))
    rows = load_rows([p])
    assert rows[0]["system"] == "multi" and rows[0]["preset"] == "flash"
    assert rows[0]["repeat"] == 1 and rows[0]["audit"] == []
    assert rows[1]["cost_usd"] == 0 and rows[1]["infra_retries"] == 0
    (s,) = summarize(rows)
    assert s.crashed == 1 and s.infra_failures == 0
    assert s.resolve_rate_mean == 1.0


def test_render_markdown_contains_the_caveat_and_tables():
    rows = [row("a", resolved=True), row("b", category="trap", repeat=2)]
    md = render_markdown(summarize(rows), title="My report", sources=["f.json"])
    assert "# My report" in md
    assert "2 tasks" in md and "2 repeats" in md
    assert "denominator" in md
    assert "gemini-3.8-flash" in md
    assert "| trap |" in md
    assert "50%" in md
    assert "0.100" in md and "10s" in md
    assert "f.json" in md


def test_empty_input():
    assert summarize([]) == []
    md = render_markdown([], title="Empty", sources=[])
    assert "no rows" in md.lower()


def test_cli_writes_the_file(tmp_path: Path):
    src = tmp_path / "res.json"
    src.write_text(json.dumps([row("a", resolved=True)]))
    out = tmp_path / "sub" / "out.md"
    assert main([str(src), "--out", str(out), "--title", "Hello"]) == 0
    text = out.read_text()
    assert "# Hello" in text and "res.json" in text
