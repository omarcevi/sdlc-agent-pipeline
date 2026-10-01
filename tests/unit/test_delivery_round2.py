"""Fix round 2: repository discovery in the extracted tree, precise title defusing,
bad responses, invisible characters, NFC/NFD duplicates."""

import tempfile

import httpx
import pytest

from app import pr_text
from app.environment.base import InfraError
from app.github_client import GitHubClient, GitHubError, GitHubUnavailable
from app.nodes import finish
from app.nodes.finish import open_pr, post_failure_comment
from tests.unit import test_delivery as td
from tests.unit.test_delivery import (
    _collect,
    _decision,
    _diff,
    _sha,
    _standard_change,
    issue_record,
    make_source,
)
from tests.unit.test_delivery_hardening import archive_with

github = td.github
runs = td.runs


async def test_a_bare_repository_layout_in_the_archive_runs_nothing(
    tmp_path, runs, github
):
    _archive, unified, files = make_source(tmp_path, _standard_change)
    marker = tmp_path / "MARKER"
    config = (
        "[core]\n\trepositoryformatversion = 0\n\tworktree = .\n"
        f'[filter "evil"]\n\tclean = touch {marker}\n\tsmudge = touch {marker}\n'
    )
    archive = archive_with(
        tmp_path,
        {
            "HEAD": b"ref: refs/heads/main\n",
            "config": config.encode(),
            "objects": None,
            "refs": None,
            ".gitattributes": b"* filter=evil\n",
        },
    )
    events = await _collect(
        open_pr(
            _decision(unified),
            issue=issue_record(),
            diff=_diff(unified, files),
            patch_sha256=_sha(unified),
            source_archive=str(archive),
        )
    )
    assert not marker.exists()
    assert events[-1].output["outcome"] == "pr_opened"
    entries = {e["path"]: e for e in github.trees[0]["tree"]}
    assert github.blobs[entries["mod.py"]["sha"]] == b"x = 1\ny = 3\n"


def test_old_git_is_refused_with_a_clear_message(monkeypatch):
    monkeypatch.setattr(finish, "_git_version", lambda: (2, 37))
    with pytest.raises(RuntimeError, match=r"git 2\.38 or newer"):
        finish._require_git()
    monkeypatch.setattr(finish, "_git_version", lambda: (2, 38))
    finish._require_git()
    assert finish._parse_git_version("git version 2.50.1 (Apple Git-155)") == (2, 50)
    assert finish._parse_git_version("git version 2.47.3") == (2, 47)
    with pytest.raises(RuntimeError, match="git version"):
        finish._parse_git_version("something else")


@pytest.mark.parametrize(
    "title",
    [
        "C# 12 support",
        "F# bindings",
        "mail owner@example.com",
        "use `#pragma once`",
        "see https://example.com/pull/3 for details",
        "item #",
    ],
)
def test_legitimate_titles_survive_unchanged(title):
    assert pr_text.pr_title(title) == f"[issue-to-pr] {title}"
    assert pr_text.commit_message(1, title) == f"Fix #1: {title}"[:72]


@pytest.mark.parametrize(
    "title",
    [
        "Fixes #3",
        "closes owner/repo#4",
        "resolves GH-9",
        "closes https://github.com/o/r/pull/5",
    ],
)
def test_real_references_are_still_defused(title):
    out = pr_text.pr_title(title)
    assert "#3" not in out and "#4" not in out and "GH-9" not in out
    assert "github.com" not in out


def test_invisible_arabic_mark_and_tag_characters_are_stripped():
    assert "؜" not in pr_text.pr_title("a؜b")
    tagged = "x" + "".join(chr(0xE0000 + c) for c in (0x41, 0x7F)) + "y"
    assert pr_text.pr_title(tagged) == "[issue-to-pr] x y"
    assert pr_text.code_span(tagged) == "`x y`"
    assert pr_text.fence(tagged) == "```\nxy\n```"
    assert "؜" not in pr_text.fence("a؜b")


async def test_outside_the_tree_with_the_files_list_omitting_it(
    tmp_path, runs, github, monkeypatch
):
    archive, _unified, _files = make_source(tmp_path, _standard_change)
    apply_root = tmp_path / "apply-root"
    apply_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(apply_root))  # ../ stays in tmp_path
    evil = (
        "diff --git a/../escaped.txt b/../escaped.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/../escaped.txt\n"
        "@@ -0,0 +1 @@\n"
        "+owned\n"
    )
    kwargs = {
        "issue": issue_record(),
        "diff": _diff(evil, ["keep.py"]),  # the list does not name the escaping path
        "patch_sha256": _sha(evil),
        "source_archive": str(archive),
    }
    with pytest.raises(RuntimeError, match="unsafe path"):  # the numstat check
        await _collect(open_pr(_decision(evil), **kwargs))
    # without our own path check, git itself refuses a path outside the tree
    monkeypatch.setattr(finish, "_check_paths", lambda paths, protected: None)
    with pytest.raises(RuntimeError, match="does not apply"):
        await _collect(open_pr(_decision(evil), **kwargs))
    assert github.requests == []
    assert not list(tmp_path.rglob("escaped.txt"))


# --- bad responses ---------------------------------------------------------------------


def _client(handler):
    async def no_sleep(_s: float) -> None:
        return None

    return GitHubClient(
        "tok", transport=httpx.MockTransport(handler), sleep=no_sleep, max_attempts=2
    )


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, json=[1, 2]),
    ],
)
async def test_a_malformed_2xx_response_is_a_github_error(answer):
    async with _client(lambda request: answer) as client:
        with pytest.raises(GitHubError) as caught:
            await client.comment_once("demo/widgets", 7, body="b", marker="m")
    assert "not json" not in str(caught.value)


async def test_a_malformed_response_does_not_change_the_failure_outcome(monkeypatch):
    monkeypatch.setattr(
        GitHubClient,
        "from_token_file",
        staticmethod(
            lambda **kw: _client(lambda request: httpx.Response(200, content=b"oops"))
        ),
    )
    posted = await post_failure_comment(
        issue_record(), run_id="Run_1", outcome="failed", reason="x"
    )
    assert posted is False
    events = [
        e
        async for e in finish.report_failure(
            None, {"kind": "agent", "reason": "r"}, issue=issue_record()
        )
    ]
    assert events[-1].output["outcome"] == "failed"
    assert events[-1].output["comment_posted"] is False


def test_infra_error_is_still_what_unavailable_is():
    assert issubclass(GitHubUnavailable, InfraError)
