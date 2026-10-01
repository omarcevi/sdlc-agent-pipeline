"""The state that live intake produces is what delivery reads: one test that runs the
real `fetch_live_issue`, then feeds its state to the real `open_pr`."""

import hashlib

from app.nodes import intake
from app.nodes.finish import open_pr
from app.schemas import RunRequest
from tests.unit import test_delivery as td
from tests.unit._constants import DIFF
from tests.unit.test_live_intake import (
    BASE_SHA,
    TREE_SHA,
    FakeClientClass,
    FakeGitHub,
    make_archive,
)

github = td.github  # the REST fake that open_pr talks to
runs = td.runs


async def test_the_state_live_intake_produces_feeds_open_pr(
    tmp_path, runs, github, monkeypatch
):
    monkeypatch.setenv("LIVE_REPOS", td.REPO)
    monkeypatch.setenv("LIVE_ALLOWED_USERS", "owner")
    archive = make_archive(
        tmp_path / "src.tar.gz",
        {
            "mini.py": "def add(a, b):\n    return a - b\n",
            "tests/test_mini.py": "from mini import add\n",
        },
    )
    fake = FakeGitHub(archive)
    fake.issue = type(fake.issue)(
        **{
            **fake.issue.__dict__,
            "html_url": f"https://github.com/{td.REPO}/issues/7",
        }
    )
    monkeypatch.setattr(intake, "GitHubClient", FakeClientClass(fake))

    request = RunRequest(run_id="Run_1", mode="live", repo=td.REPO, issue_number=7)
    events = [e async for e in intake.fetch_live_issue(request)]
    state = events[-1].actions.state_delta

    # The keys and meanings delivery reads, as intake wrote them.
    issue = state["issue"]
    assert issue["repo"] == td.REPO and issue["issue_number"] == 7
    assert issue["run_id"] == "Run_1" and issue["mode"] == "live"
    assert issue["base_ref"] == "main"
    assert (issue["base_sha"], issue["base_tree_sha"]) == (BASE_SHA, TREE_SHA)

    diff = {"unified_diff": DIFF, "files": ["mini.py"], "insertions": 1, "deletions": 1}
    digest = hashlib.sha256(DIFF.encode()).hexdigest()
    out = [
        e
        async for e in open_pr(
            td._decision(DIFF),
            issue=issue,
            diff=diff,
            patch_sha256=digest,
            source_archive=state["source_archive"],
            protected_paths=["tests/test_mini.py"],
        )
    ]
    assert out[-1].output["outcome"] == "pr_opened"
    assert github.commits[0]["parents"] == [BASE_SHA]
    assert github.trees[0]["base_tree"] == TREE_SHA
    pull = github.pulls[0]
    assert pull["base"]["ref"] == "main"
    assert pull["head"]["ref"] == "issue-to-pr/7-run-1"
    assert pull["title"].endswith(issue["title"])
