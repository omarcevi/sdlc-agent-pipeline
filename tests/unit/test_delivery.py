"""Delivery nodes: pr_body.md, the hash, open_pr through the Git Data API, failure
comments. GitHub is an httpx.MockTransport server held in memory; git runs for real
on temporary directories; nothing touches the network."""

import hashlib
import json
import re
import tempfile
from pathlib import Path

import pytest

from app import pr_text
from app.environment.base import InfraError
from app.github_client import GitHubClient
from app.nodes.finish import (
    deliver_patch,
    open_pr,
    post_failure_comment,
    report_failure,
)
from tests.unit.delivery_fakes import (
    BASE_SHA,
    BASE_TREE,
    REPO,
    _collect,
    _decision,
    _diff,
    _plain,
    _sha,
    _standard_change,
    issue_record,
    make_source,
)

# --- deliver_patch -----------------------------------------------------------------

STATE = {
    "plan": {"summary": "plan text @someone"},
    "patch": {"summary": "coder text"},
    "test_report": {"passed": True, "exit_code": 0, "failed_tests": []},
    "review": {"verdict": "approve", "comments": [], "must_fix": []},
    "budget": {"cost_usd": 0.2, "tool_calls": 12, "models": ["gemini-3.8-flash"]},
}


def test_deliver_patch_writes_pr_body_and_hash(runs):
    diff = _diff("diff --git a/a b/a\n+x\n", ["a"])
    events = _plain(deliver_patch(None, issue_record(), diff, **STATE))
    run_dir = runs / "Run_1"
    assert (run_dir / "patch.diff").read_text() == diff["unified_diff"]
    body = (run_dir / "pr_body.md").read_text()
    assert body.startswith(pr_text.marker("Run_1", "pr"))
    assert "Proposed fix for #7" in body
    assert "`a`" in body and "gemini-3.8-flash" in body
    assert _sha(diff["unified_diff"]) in body
    assert events[-1].actions.state_delta["patch_sha256"] == _sha(diff["unified_diff"])
    message = events[0].content.parts[0].text
    assert message == f"patch written to {run_dir / 'patch.diff'}\n\n{body}"
    # the full unified diff is hashed, UTF-8
    unicode_diff = _diff("diff --git a/é b/é\n+ü\n", ["é"])
    out = _plain(deliver_patch(None, issue_record(), unicode_diff, **STATE))
    assert (
        out[-1].actions.state_delta["patch_sha256"]
        == hashlib.sha256(unicode_diff["unified_diff"].encode("utf-8")).hexdigest()
    )


def test_deliver_patch_first_line_is_unchanged(runs):
    events = _plain(deliver_patch(None, issue_record(), _diff("d\n", ["a"]), **STATE))
    first = events[0].content.parts[0].text.split("\n")[0]
    assert first == f"patch written to {runs / 'Run_1' / 'patch.diff'}"
    assert events[-1].output["outcome"] == "patch_written"
    assert events[-1].output["patch_path"] == str(runs / "Run_1" / "patch.diff")


def test_deliver_patch_works_in_the_baseline_graph(runs):
    # The baseline graph never writes plan, patch or review.
    bench_issue = {"task_id": "t-1", "run_id": "r-1", "mode": "bench", "title": "t"}
    events = _plain(
        deliver_patch(
            None,
            bench_issue,
            _diff("d\n", ["a"]),
            test_report=STATE["test_report"],
            budget=STATE["budget"],
        )
    )
    body = (runs / "r-1" / "pr_body.md").read_text()
    assert "Proposed fix for" in body and "Plan summary" not in body
    assert events[-1].output["outcome"] == "patch_written"


# --- open_pr -------------------------------------------------------------------------


async def test_open_pr_refuses_a_patch_hash_mismatch(tmp_path, runs, github):
    archive, unified, files = make_source(tmp_path, _standard_change)
    kwargs = {
        "issue": issue_record(),
        "diff": _diff(unified, files),
        "patch_sha256": _sha(unified),
        "source_archive": str(archive),
    }
    with pytest.raises(RuntimeError, match="patch hash"):
        await _collect(open_pr(_decision(unified + "\n"), **kwargs))
    with pytest.raises(RuntimeError, match="patch hash"):
        await _collect(
            open_pr(_decision(unified), **{**kwargs, "patch_sha256": "0" * 64})
        )
    tampered = {**kwargs["diff"], "unified_diff": unified + "# tampered\n"}
    with pytest.raises(RuntimeError, match="patch hash"):
        await _collect(open_pr(_decision(unified), **{**kwargs, "diff": tampered}))
    assert github.requests == []


async def test_open_pr_refuses_github_paths(tmp_path, runs, github):
    def change(work: Path) -> None:
        (work / ".github").mkdir()
        (work / ".github" / "ci.yml").write_text("on: push\n")

    archive, unified, files = make_source(tmp_path, change)
    with pytest.raises(RuntimeError, match=r"\.github"):
        await _collect(
            open_pr(
                _decision(unified),
                issue=issue_record(),
                diff=_diff(unified, files),
                patch_sha256=_sha(unified),
                source_archive=str(archive),
            )
        )
    assert github.requests == []


async def test_open_pr_refuses_protected_paths(tmp_path, runs, github):
    archive, unified, files = make_source(
        tmp_path, lambda work: (work / "keep.py").write_text("keep = 2\n")
    )
    with pytest.raises(RuntimeError, match="protected"):
        await _collect(
            open_pr(
                _decision(unified),
                issue=issue_record(),
                diff=_diff(unified, files),
                patch_sha256=_sha(unified),
                source_archive=str(archive),
                protected_paths=["keep.py"],
            )
        )
    assert github.requests == []


async def test_open_pr_builds_the_commit_from_the_applied_patch(tmp_path, runs, github):
    archive, unified, files = make_source(tmp_path, _standard_change)
    events = await _collect(
        open_pr(
            _decision(unified),
            issue=issue_record(),
            diff=_diff(unified, files),
            patch_sha256=_sha(unified),
            source_archive=str(archive),
            protected_paths=["keep.py"],
            **STATE,
        )
    )
    tree = github.trees[0]
    assert tree["base_tree"] == BASE_TREE
    entries = {e["path"]: e for e in tree["tree"]}
    assert set(entries) == {"mod.py", "gone.py", "new.py", "run.sh", "blob.bin"}
    assert github.blobs[entries["mod.py"]["sha"]] == b"x = 1\ny = 3\n"
    assert github.blobs[entries["new.py"]["sha"]] == b"new = 1\n"
    assert entries["new.py"]["mode"] == "100644"
    assert entries["run.sh"]["mode"] == "100755"
    assert entries["gone.py"]["sha"] is None  # deleted
    assert github.blobs[entries["blob.bin"]["sha"]] == bytes(range(255, -1, -1))
    commit = github.commits[0]
    assert commit["parents"] == [BASE_SHA]
    assert commit["author"] == {
        "name": "issue-to-pr pipeline",
        "email": "issue-to-pr@example.invalid",
    }
    assert commit["message"] == "Fix #7: Add the thing"
    branch = "issue-to-pr/7-run-1"
    assert github.refs == {
        branch: hashlib.sha1(json.dumps(commit, sort_keys=True).encode()).hexdigest()
    }
    pull = github.pulls[0]
    assert pull["head"]["ref"] == branch and pull["base"]["ref"] == "main"
    assert pull["title"] == "[issue-to-pr] Add the thing"
    assert "approved by @octocat" in pull["body"]
    assert pull["body"].count("Proposed fix for #7") == 1
    outcome = events[-1].output
    assert outcome == {
        "outcome": "pr_opened",
        "failure_kind": "none",
        "reason": "",
        "patch_path": str(runs / "Run_1" / "patch.diff"),
        "pr_url": f"https://github.com/{REPO}/pull/5",
    }
    assert (
        events[0].content.parts[0].text == f"pull request opened: {outcome['pr_url']}"
    )


async def test_open_pr_refuses_a_patch_that_writes_outside_the_tree(
    tmp_path, runs, github, monkeypatch
):
    archive, _unified, _files = make_source(tmp_path, _standard_change)
    apply_root = tmp_path / "apply-root"
    apply_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(apply_root))
    evil = (
        "diff --git a/../../escaped.txt b/../../escaped.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/../../escaped.txt\n"
        "@@ -0,0 +1 @@\n"
        "+owned\n"
    )
    with pytest.raises(RuntimeError, match="unsafe path"):
        await _collect(
            open_pr(
                _decision(evil),
                issue=issue_record(),
                diff=_diff(evil, ["../../escaped.txt"]),
                patch_sha256=_sha(evil),
                source_archive=str(archive),
            )
        )
    assert github.requests == []
    assert not list(
        tmp_path.rglob("escaped.txt")
    )  # ../../ from the repo is apply_root, which outlives the call


async def test_open_pr_refuses_a_patch_that_does_not_apply(tmp_path, runs, github):
    archive, unified, files = make_source(tmp_path, _standard_change)
    broken = unified.replace("-y = 2", "-y = 99")
    with pytest.raises(RuntimeError, match="does not apply"):
        await _collect(
            open_pr(
                _decision(broken),
                issue=issue_record(),
                diff=_diff(broken, files),
                patch_sha256=_sha(broken),
                source_archive=str(archive),
            )
        )
    assert github.requests == []


async def test_open_pr_retry_after_a_lost_response_opens_one_pr(tmp_path, runs, github):
    archive, unified, files = make_source(tmp_path, _standard_change)
    kwargs = {
        "issue": issue_record(),
        "diff": _diff(unified, files),
        "patch_sha256": _sha(unified),
        "source_archive": str(archive),
    }
    kwargs["patch_created_at"] = "2026-10-01T12:00:00Z"
    github.drop_first_pull_response = True  # the PR is created, the answer is lost
    first = await _collect(open_pr(_decision(unified), **kwargs))
    second = await _collect(open_pr(_decision(unified), **kwargs))
    assert first[-1].output["pr_url"] == second[-1].output["pr_url"]
    assert len(github.pulls) == 1
    assert github.count("POST", "/pulls") == 1


async def test_open_pr_rerun_after_the_branch_exists_but_no_pr_opens_exactly_one(
    tmp_path, runs, github
):
    archive, unified, files = make_source(tmp_path, _standard_change)
    kwargs = {
        "issue": issue_record(),
        "diff": _diff(unified, files),
        "patch_sha256": _sha(unified),
        "source_archive": str(archive),
        "patch_created_at": "2026-10-01T12:00:00Z",
    }
    github.fail_pulls = True  # branch and commit land, the pull request does not
    with pytest.raises(InfraError):
        await _collect(open_pr(_decision(unified), **kwargs))
    assert len(github.refs) == 1 and github.pulls == []
    github.fail_pulls = False
    events = await _collect(open_pr(_decision(unified), **kwargs))  # same commit
    assert len(github.pulls) == 1 and len(github.refs) == 1
    assert events[-1].output["outcome"] == "pr_opened"
    commit = github.commits[0]
    assert (
        commit["author"]["date"]
        == commit["committer"]["date"]
        == kwargs["patch_created_at"]
    )
    assert github.commits[0] == github.commits[-1]


def test_deliver_patch_records_when_the_patch_was_written(runs):
    events = _plain(deliver_patch(None, issue_record(), _diff("d\n", ["a"]), **STATE))
    stamp = events[-1].actions.state_delta["patch_created_at"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp)


async def test_open_pr_refuses_an_approval_that_is_not_one(tmp_path, runs, github):
    archive, unified, files = make_source(tmp_path, _standard_change)
    for approved in (False, None):
        with pytest.raises(RuntimeError, match="not approved"):
            await _collect(
                open_pr(
                    _decision(unified, approved=approved),
                    issue=issue_record(),
                    diff=_diff(unified, files),
                    patch_sha256=_sha(unified),
                    source_archive=str(archive),
                )
            )
    assert github.requests == []


# --- failure comments --------------------------------------------------------------------


def _failure_issue():
    return issue_record()


async def test_report_failure_in_bench_mode_makes_no_github_call(monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError("bench mode must not build a GitHub client")

    monkeypatch.setattr(GitHubClient, "from_token_file", staticmethod(forbidden))
    bench_issue = {"task_id": "t-1", "run_id": "r-1", "mode": "bench"}
    for kind, outcome, failure_kind in (
        ("declined", "declined", "none"),
        ("agent", "failed", "agent"),
    ):
        events = [
            e
            async for e in report_failure(
                None, {"kind": kind, "reason": "why"}, issue=bench_issue
            )
        ]
        result = events[-1].output
        assert (result["outcome"], result["failure_kind"]) == (outcome, failure_kind)
        assert "comment_posted" not in result
    # the old call shape (no issue in state) still works
    events = [e async for e in report_failure(None, {"kind": "agent", "reason": "r"})]
    assert events[-1].output["outcome"] == "failed"


async def test_live_failure_comment_is_posted_once_per_run(github):
    for kind, outcome, failure_kind in (
        ("declined", "declined", "none"),
        ("agent", "failed", "agent"),
        ("rejected", "rejected", "none"),
    ):
        github.comments.clear()
        events = [
            e
            async for e in report_failure(
                None,
                {"kind": kind, "reason": "@x Fixes #1 because"},
                issue=_failure_issue(),
                plan={"summary": "s"},
                test_report=None,
                budget={"models": ["m"]},
            )
        ]
        result = events[-1].output
        assert (result["outcome"], result["failure_kind"]) == (outcome, failure_kind)
        assert result["comment_posted"] is True
        assert len(github.comments) == 1
        assert pr_text.marker("Run_1", "failure") in github.comments[0]["body"]
    # a second call for the same run adds nothing
    again = await post_failure_comment(
        _failure_issue(), run_id="Run_1", outcome="failed", reason="again"
    )
    assert again is True and len(github.comments) == 1


async def test_comment_failure_does_not_change_the_outcome(github, caplog):
    github.fail_comments = True
    events = [
        e
        async for e in report_failure(
            None, {"kind": "agent", "reason": "tests"}, issue=_failure_issue()
        )
    ]
    result = events[-1].output
    assert result["outcome"] == "failed" and result["failure_kind"] == "agent"
    assert result["comment_posted"] is False
    assert (
        await post_failure_comment(
            _failure_issue(), run_id="Run_1", outcome="failed", reason="x"
        )
        is False
    )


async def test_missing_token_is_a_logged_failed_comment(runs):
    # conftest points GITHUB_TOKEN_FILE at a file that does not exist
    posted = await post_failure_comment(
        _failure_issue(), run_id="Run_1", outcome="failed", reason="x"
    )
    assert posted is False
