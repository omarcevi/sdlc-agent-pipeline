import json
from pathlib import Path

import pytest

from app.models import STALLED_TWICE
from bench import report
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


# ---- fix wave B: the report states its sample and buckets every run ----


def _buckets(s):
    return (
        s.resolved
        + s.unresolved
        + s.agent_failures
        + s.budget_failures
        + s.infra_failures
        + s.crashed
    )


def _section(md: str, header: str) -> str:
    return md.split(header)[1].split("\n## ")[0]


def test_buckets_sum_to_runs_with_every_bucket_used():
    rows = [
        row("a", resolved=True),
        row("b"),  # wrong patch, failure_kind none
        row("c", outcome="failed", failure_kind="agent"),
        row(
            "d",
            outcome="failed",
            failure_kind="budget",
            reason="run exceeded 75 tool calls",
        ),
        row("e", outcome="failed", failure_kind="infra"),
        row("f", outcome="failed", failure_kind="infra", crashed=True),
    ]
    (s,) = summarize(rows)
    assert (s.resolved, s.unresolved, s.agent_failures) == (1, 1, 1)
    assert (s.budget_failures, s.infra_failures, s.crashed) == (1, 1, 1)
    assert _buckets(s) == s.runs == 6
    assert s.counted == 4


def test_unresolved_none_kind_row_is_listed():
    rows = [row("a", resolved=True), row("b", reason="patch did not fix it")]
    md = render_markdown(summarize(rows), title="T", sources=[])
    section = _section(md, "## Runs that did not resolve")
    assert "b-r1" in section and "unresolved" in section
    assert "patch did not fix it" in section
    assert "a-r1" not in section


def test_no_unresolved_runs_says_none():
    md = render_markdown(summarize([row("a", resolved=True)]), title="T", sources=[])
    assert "None." in _section(md, "## Runs that did not resolve")


def test_reason_is_one_line_cut_to_160_and_dash_when_empty():
    long = "x" * 300 + "\nsecond line"
    rows = [row("a", reason=long), row("b", reason="")]
    md = render_markdown(summarize(rows), title="T", sources=[])
    section = _section(md, "## Runs that did not resolve")
    assert "x" * 160 in section and "x" * 161 not in section
    assert "b-r1 [unresolved] -" in section


def test_all_infra_or_crashed_renders_na_not_zero():
    rows = [
        row("a", outcome="failed", failure_kind="infra"),
        row("b", outcome="failed", failure_kind="infra", crashed=True),
    ]
    (s,) = summarize(rows)
    assert s.counted == 0
    assert s.resolve_rate_mean is None and s.clean_resolve_rate_mean is None
    assert s.resolve_rate_min is None and s.resolve_rate_max is None
    md = render_markdown([s], title="T", sources=[])
    results = _section(md, "## Results")
    assert "n/a" in results and "0%" not in results


def test_one_repeat_has_no_range_and_three_repeats_do():
    one = [row("a", resolved=True), row("b")]
    three = [
        row(t, repeat=r, system="single", resolved=(t == "a" or r == 1))
        for r in (1, 2, 3)
        for t in ("a", "b")
    ]
    md = render_markdown(summarize(one + three), title="T", sources=[])
    lines = _section(md, "## Results").splitlines()
    multi = next(x for x in lines if x.startswith("| multi"))
    single = next(x for x in lines if x.startswith("| single"))
    rate_cell = multi.strip("|").split("|")[7].strip()  # resolve rate column
    assert rate_cell == "50%"
    assert "67% (50%-100%)" in single


def test_range_needs_two_repeats_with_counted_runs():
    rows = [
        row("a", repeat=1, resolved=True),
        row("a", repeat=2, outcome="failed", failure_kind="infra"),
    ]
    (s,) = summarize(rows)
    assert s.repeats == 2 and s.rate_repeats == 1
    md = render_markdown([s], title="T", sources=[])
    assert "100% (" not in md


def test_mixed_repeats_caveat_is_not_a_single_maximum():
    rows = [row(t, repeat=r, resolved=True) for r in (1, 2, 3) for t in ("a", "b")]
    rows += [row(t, system="single", resolved=True) for t in ("a", "b")]
    got = summarize(rows)
    assert sorted(s.repeats for s in got) == [1, 3]
    md = render_markdown(got, title="T", sources=[])
    caveat = md.split("## Results")[0]
    assert "3 repeats" not in caveat
    assert "differ" in caveat
    cells = [
        [c.strip() for c in x.strip("|").split("|")]
        for x in _section(md, "## Results").splitlines()
        if x.startswith("| multi") or x.startswith("| single")
    ]
    assert sorted(c[3] for c in cells) == ["1", "3"]  # repeats column


def test_same_sample_caveat_states_the_numbers():
    rows = [row(t, repeat=r) for r in (1, 2, 3) for t in "abcde"]
    md = render_markdown(summarize(rows), title="T", sources=[])
    caveat = md.split("## Results")[0]
    assert "5 tasks" in caveat and "3 repeats" in caveat
    assert "20 points" in caveat
    assert "do not make the sample of tasks bigger" in caveat
    assert "noise" not in caveat


def test_cost_columns_and_na_when_nothing_resolved():
    rows = [
        row("a", resolved=True, cost_usd=0.2),
        row("b", cost_usd=0.4),
        row("c", outcome="failed", failure_kind="infra", cost_usd=0.6),
    ]
    (s,) = summarize(rows)
    assert s.cost_total == pytest.approx(1.2)
    assert s.cost_per_run_mean == pytest.approx(0.4)
    assert s.cost_per_resolved == pytest.approx(1.2)
    none = summarize([row("a", cost_usd=0.5)])[0]
    assert none.cost_per_resolved is None
    md = render_markdown([s, none], title="T", sources=[])
    assert "$/resolved" in md and "total $" in md and "1.200" in md
    assert "n/a" in md
    assert "reruns after infra failures" in md
    assert "crashed runs may record less" in md


def test_budget_failures_grouped_by_reason():
    over = "run exceeded 75 tool calls"
    turn = "coder exceeded 25 tool calls in one turn"
    rows = [
        row("a", outcome="failed", failure_kind="budget", reason=over),
        row("b", outcome="failed", failure_kind="budget", reason=over),
        row("c", outcome="failed", failure_kind="budget", reason=turn),
    ]
    (s,) = summarize(rows)
    assert s.budget_reasons == {over: 2, turn: 1}
    md = render_markdown([s], title="T", sources=[])
    section = _section(md, "## Budget failures by cap")
    assert f"| multi (flash) | {over} | 2 |" in section
    assert f"| multi (flash) | {turn} | 1 |" in section


def test_no_budget_failures_says_none():
    md = render_markdown(summarize([row("a")]), title="T", sources=[])
    assert "None." in _section(md, "## Budget failures by cap")


STALLED = f"{STALLED_TWICE}: no reply from gemini-3.8-flash within 480 s"


def test_the_stall_prefix_matches_the_models_reason():
    assert report.STALLED_TWICE == STALLED_TWICE


def test_stalled_twice_runs_are_listed_per_configuration():
    def stalled(task_id, run_id, **kw):
        return row(
            task_id,
            run_id=run_id,
            outcome="failed",
            failure_kind="infra",
            reason=STALLED,
            infra_retries=2,
            **kw,
        )

    other_infra = row(
        "d", outcome="failed", failure_kind="infra", reason="ServerError: 503"
    )
    rows = [
        row("a", resolved=True),
        stalled("b", "b-retry2"),
        stalled("c", "c-retry2"),
        other_infra,
        row("a", preset="pro", resolved=True),
        stalled("a", "a-single-retry2", system="single"),
    ]
    multi, multi_pro, single = summarize(rows)
    assert multi.stalled_twice == [("b-retry2", 2), ("c-retry2", 2)]
    assert multi.infra_failures == 3 and multi.resolve_rate_mean == 1.0
    assert multi_pro.stalled_twice == []
    assert single.stalled_twice == [("a-single-retry2", 2)]

    md = render_markdown(summarize(rows), title="T", sources=[])
    section = _section(md, "## Model calls that stalled twice")
    assert "| multi (flash) | 2 |" in section
    assert "| multi (pro) | 0 |" in section
    assert "| single (flash) | 1 |" in section
    assert "- b-retry2 (multi (flash); infra reruns: 2)" in section
    assert "- a-single-retry2 (single (flash); infra reruns: 2)" in section
    assert "d-r1" not in section  # another infra failure is not a stall


def test_no_stalled_runs_says_none():
    md = render_markdown(summarize([row("a")]), title="T", sources=[])
    assert "None." in _section(md, "## Model calls that stalled twice")


def test_duplicate_rows_are_an_error(tmp_path: Path):
    with pytest.raises(ValueError, match=r"multi.*flash.*a"):
        summarize([row("a", resolved=True), row("a")])
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    one.write_text(json.dumps([row("a", resolved=True)]))
    two.write_text(json.dumps([row("a")]))
    out = tmp_path / "out.md"
    assert main([str(one), str(two), "--out", str(out)]) == 2
    assert not out.exists()


def test_category_header_is_explicit():
    md = render_markdown(summarize([row("a")]), title="T", sources=[])
    section = _section(md, "## By category")
    assert "resolved / counted" in section
    assert "repeats pooled" in section
    assert "infra and crashed runs excluded" in section


def test_realistic_four_configuration_table_is_well_formed(capsys):
    tasks = ["tc-001", "tc-002", "tc-003", "tc-004", "tc-005"]
    cats = dict(zip(tasks, ["bug", "bug", "feature", "trap", "trap"], strict=True))
    rows = []
    for system in ("multi", "single"):
        for r in (1, 2, 3):
            for t in tasks:
                rows.append(
                    row(
                        t,
                        category=cats[t],
                        system=system,
                        preset="flash",
                        repeat=r,
                        run_id=f"{system}-flash-{t}-r{r}",
                        resolved=(t != "tc-005" and (r + len(t)) % 2 == 0),
                        cost_usd=0.2,
                    )
                )
        for t in tasks:
            rows.append(
                row(
                    t,
                    category=cats[t],
                    system=system,
                    preset="pro",
                    run_id=f"{system}-pro-{t}-r1",
                    resolved=t == "tc-001",
                    cost_usd=0.9,
                )
            )
    rows[3].update(
        outcome="failed",
        failure_kind="budget",
        reason="run exceeded 75 tool calls | with pipe",
    )
    rows[4].update(outcome="failed", failure_kind="infra")
    rows[5].update(outcome="failed", failure_kind="infra", crashed=True)
    md = render_markdown(summarize(rows), title="T", sources=["a.json"])
    for header in ("## Results", "## By category", "## Budget failures by cap"):
        table = [x for x in _section(md, header).splitlines() if x.startswith("|")]
        assert len(table) >= 3
        widths = {
            len(x.replace("\\|", "").strip().strip("|").split("|")) for x in table
        }
        assert len(widths) == 1, (header, widths)
    with capsys.disabled():
        print("\n=====RENDERED=====\n" + md + "=====END=====")


def _table(md: str, header: str) -> list[str]:
    return [ln for ln in _section(md, header).splitlines() if ln.startswith("|")]


def _col(lines: list[str], i: int) -> list[str]:
    return [ln.split("|")[i].strip() for ln in lines[2:]]


def test_by_repo_and_by_difficulty_tables():
    rows = [
        row("a", repo="r1", difficulty="hard", resolved=True),
        row("b", repo="r1", difficulty="easy", resolved=False),
        row("c", repo="r2", difficulty="medium", resolved=True),
        row("d", repo="r2", difficulty="easy", resolved=True),
        row("e", repo="r2", difficulty="easy", failure_kind="infra"),
    ]
    md = render_markdown(summarize(rows), title="T", sources=[])
    assert md.index("## By category") < md.index("## By repo")
    assert md.index("## By repo") < md.index("## By difficulty")
    for header in ("## By repo", "## By difficulty"):
        section = _section(md, header)
        assert "resolved / counted" in section
        assert "repeats pooled" in section
        assert "infra and crashed runs excluded" in section
    repo = _table(md, "## By repo")
    assert _col(repo, 1) == ["r1", "r2"]
    assert _col(repo, 2) == ["1 / 2", "2 / 2"]
    diff = _table(md, "## By difficulty")
    assert _col(diff, 1) == ["easy", "medium", "hard"]
    assert _col(diff, 2) == ["1 / 2", "1 / 1", "1 / 1"]


def test_rows_without_repo_or_difficulty_load_as_unknown(tmp_path: Path):
    old = {
        "task_id": "tc-001",
        "category": "bug",
        "resolved": True,
        "outcome": "patch_written",
        "failure_kind": "none",
    }
    p = tmp_path / "r.json"
    p.write_text(json.dumps([old]))
    (loaded,) = load_rows([p])
    assert (loaded["repo"], loaded["difficulty"], loaded["split"]) == ("unknown",) * 3
    (s,) = summarize([loaded])
    assert s.by_repo == {"unknown": (1, 1)}
    assert s.by_difficulty == {"unknown": (1, 1)}
    md = render_markdown([s], title="T", sources=[])
    assert _col(_table(md, "## By difficulty"), 1) == ["unknown"]
