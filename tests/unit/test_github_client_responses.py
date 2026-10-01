"""Malformed 2xx responses: bad JSON is infra (retried), a wrong shape is a
GitHubError, and caller mistakes are not swallowed. No error keeps response text."""

import httpx
import pytest

from app.environment.base import InfraError
from app.github_client import (
    FileChange,
    GitHubClient,
    GitHubError,
    GitHubUnavailable,
)

REPO = "octo/demo"
BODY = b"<html>SECRET-RESPONSE-BODY not json</html>"
ATTEMPTS = 3


def make(handler):
    async def no_sleep(_s: float) -> None:
        return None

    return GitHubClient(
        "tok",
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
        max_attempts=ATTEMPTS,
    )


def not_json(request):
    return httpx.Response(200, content=BODY)


CALLS = {
    "get_issue": lambda c: c.get_issue(REPO, 1),
    "last_label_actor": lambda c: c.last_label_actor(REPO, 1, "agent-ok"),
    "default_branch": lambda c: c.default_branch(REPO),
    "branch_head": lambda c: c.branch_head(REPO, "main"),
    "open_pipeline_prs": lambda c: c.open_pipeline_prs(REPO, 1),
    "create_commit": lambda c: c.create_commit(
        REPO,
        parent_sha="a" * 40,
        base_tree_sha="b" * 40,
        changes=[FileChange("f.py", b"x")],
        message="m",
        author_name="n",
        author_email="e@x",
    ),
    "ensure_branch": lambda c: c.ensure_branch(REPO, "issue-to-pr/1-x", "a" * 40),
    "open_pull_request": lambda c: c.open_pull_request(
        REPO, head="issue-to-pr/1-x", base="main", title="t", body="b"
    ),
    "comment_once": lambda c: c.comment_once(REPO, 1, body="b", marker="m"),
}


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_a_non_json_2xx_is_unavailable_and_retried(name):
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return not_json(request)

    async with make(handler) as client:
        with pytest.raises(GitHubUnavailable) as caught:
            await CALLS[name](client)
    err = caught.value
    assert isinstance(err, InfraError) and not isinstance(err, GitHubError)
    assert len(calls) >= ATTEMPTS  # every attempt was spent
    assert "SECRET-RESPONSE-BODY" not in str(err)
    assert "was not valid JSON" in str(err)
    assert err.__context__ is None and err.__cause__ is None


async def test_non_json_followed_by_good_json_succeeds():
    answers = iter(
        [
            httpx.Response(200, content=BODY),
            httpx.Response(200, json={"default_branch": "main"}),
        ]
    )
    async with make(lambda request: next(answers)) as client:
        assert await client.default_branch(REPO) == "main"


async def test_undecodable_bytes_are_unavailable_too():
    async with make(lambda r: httpx.Response(200, content=b"\xff\xfe\x00")) as client:
        with pytest.raises(GitHubUnavailable):
            await client.default_branch(REPO)


@pytest.mark.parametrize(
    "answer",
    [
        {"unexpected": True},
        [1, 2],
        {"default_branch": 5, "x": None},
    ],
)
async def test_a_wrong_shape_is_a_github_error_without_context(answer):
    async with make(lambda r: httpx.Response(200, json=answer)) as client:
        with pytest.raises(GitHubError) as caught:
            await client.get_issue(REPO, 1)
    err = caught.value
    assert not isinstance(err, GitHubUnavailable)
    assert err.__context__ is None and err.__cause__ is None


async def test_a_wrong_shape_in_a_listing_is_a_github_error():
    async with make(lambda r: httpx.Response(200, json={"a": 1})) as client:
        with pytest.raises(GitHubError):
            await client.open_pipeline_prs(REPO, 1)


@pytest.mark.parametrize("status", [300, 304])
async def test_a_non_json_3xx_is_unavailable_too(status):
    async with make(lambda r: httpx.Response(status, content=BODY)) as client:
        with pytest.raises(GitHubUnavailable) as caught:
            await client.branch_head(REPO, "main")
    err = caught.value
    assert "was not valid JSON" in str(err)
    assert "SECRET-RESPONSE-BODY" not in str(err)
    assert err.__context__ is None and err.__cause__ is None


@pytest.mark.parametrize(
    "call, answer",
    [
        ("default_branch", {"default_branch": 5}),
        ("branch_head", {"sha": 5, "commit": {"tree": {"sha": "b" * 40}}}),
        ("branch_head", {"sha": "a" * 40, "commit": {"tree": {"sha": ["x"]}}}),
    ],
)
async def test_a_wrongly_typed_scalar_is_a_github_error(call, answer):
    async with make(lambda r: httpx.Response(200, json=answer)) as client:
        with pytest.raises(GitHubError) as caught:
            await CALLS[call](client)
    assert not isinstance(caught.value, GitHubUnavailable)
    assert "unexpected response" in str(caught.value)


@pytest.mark.parametrize("bad", [{"marker": None}, {"body": None}, {"marker": 5}])
async def test_comment_once_checks_its_arguments_before_any_request(bad):
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=[])

    args = {"body": "b", "marker": "m"} | bad
    async with make(handler) as client:
        with pytest.raises(TypeError):
            await client.comment_once(REPO, 7, **args)
    assert calls == []


async def test_bad_caller_arguments_are_not_turned_into_github_errors():
    async with make(not_json) as client:
        with pytest.raises(TypeError):
            await client.comment_once(None, 7, body="b", marker="m")
        with pytest.raises((TypeError, AttributeError)):
            await client.create_commit(
                REPO,
                parent_sha="a",
                base_tree_sha="b",
                changes=None,
                message="m",
                author_name="n",
                author_email="e",
            )
