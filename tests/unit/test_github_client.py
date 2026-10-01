"""GitHub client: token handling, retries, idempotent writes, redaction.

Everything runs against httpx.MockTransport and a recording fake sleep; nothing
here touches the network or a real token.
"""

import base64
import json
import logging
import os
import time
from pathlib import Path

import httpx
import pytest

from app.environment.base import InfraError
from app.github_client import (
    BRANCH_PREFIX,
    DEFAULT_TOKEN_FILE,
    TOKEN_FILE_ENV,
    FileChange,
    GitHubClient,
    GitHubConfigError,
    GitHubError,
    GitHubUnavailable,
    load_token,
)

TOKEN = "ghp_SECRETSECRETSECRET1234567890"
REPO = "octo/demo"


def make_client(handler, **kwargs):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = GitHubClient(
        TOKEN,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
        **kwargs,
    )
    return client, sleeps


def reply(status=200, body=None, headers=None):
    return httpx.Response(
        status, json=body if body is not None else {}, headers=headers
    )


# --- token ---------------------------------------------------------------


def _token_file(tmp_path: Path, text: str = TOKEN + "\n", mode: int = 0o600) -> Path:
    path = tmp_path / "token"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_defaults():
    assert TOKEN_FILE_ENV == "GITHUB_TOKEN_FILE"
    assert DEFAULT_TOKEN_FILE == "~/.config/issue-to-pr/github-token"
    assert BRANCH_PREFIX == "issue-to-pr/"


def test_load_token_reads_only_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    monkeypatch.setenv("GH_TOKEN", "from-gh-env")
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    assert load_token() == TOKEN


def test_load_token_refuses_a_group_readable_file(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path, mode=0o640)))
    with pytest.raises(GitHubConfigError) as error:
        load_token()
    assert TOKEN not in str(error.value)


def test_load_token_refuses_an_empty_file(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path, text="  \n")))
    with pytest.raises(GitHubConfigError):
        load_token()


def test_missing_token_file_is_a_config_error(monkeypatch):
    # tests/conftest.py already points GITHUB_TOKEN_FILE at a path that does not exist.
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    with pytest.raises(GitHubConfigError):
        load_token()
    with pytest.raises(GitHubConfigError):
        GitHubClient.from_token_file()


def test_load_token_leaves_os_environ_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    before = dict(os.environ)
    load_token()
    assert dict(os.environ) == before
    assert TOKEN not in os.environ.values()


async def test_from_token_file_builds_a_working_client(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return reply(body={"default_branch": "main"})

    async with GitHubClient.from_token_file(
        transport=httpx.MockTransport(handler)
    ) as client:
        assert await client.default_branch(REPO) == "main"
    assert seen == [f"Bearer {TOKEN}"]


# --- headers, redirects --------------------------------------------------


async def test_token_is_sent_as_a_header_and_never_in_urls():
    requests = []

    def handler(request):
        requests.append(request)
        return reply(body={"default_branch": "main"})

    client, _ = make_client(handler)
    async with client:
        await client.default_branch(REPO)
    (request,) = requests
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert TOKEN not in str(request.url)
    assert request.url.host == "api.github.com"


async def test_redirect_to_another_host_drops_authorization(tmp_path):
    seen = {}

    def handler(request):
        seen[request.url.host] = request.headers.get("authorization")
        if request.url.host == "api.github.com":
            return httpx.Response(
                302,
                headers={
                    "location": "https://codeload.github.com/octo/demo/legacy.tar.gz/abc"
                },
            )
        return httpx.Response(200, content=b"tarbytes")

    client, _ = make_client(handler)
    async with client:
        dest = await client.download_tarball(REPO, "abc", tmp_path / "src.tgz")
    assert dest.read_bytes() == b"tarbytes"
    assert seen["api.github.com"] == f"Bearer {TOKEN}"
    assert seen["codeload.github.com"] is None


# --- retries and rate limits ---------------------------------------------


async def test_5xx_is_retried_then_raises_unavailable():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return reply(502, {"message": "Bad Gateway"})

    client, sleeps = make_client(handler)
    async with client:
        with pytest.raises(GitHubUnavailable) as error:
            await client.default_branch(REPO)
    assert isinstance(error.value, InfraError)
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert 0.5 <= sleeps[0] < 0.75
    assert 1.0 <= sleeps[1] < 1.5
    assert "502" in str(error.value)


async def test_transport_errors_are_retried_then_raise_unavailable():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("boom")

    client, sleeps = make_client(handler)
    async with client:
        with pytest.raises(GitHubUnavailable):
            await client.default_branch(REPO)
    assert len(calls) == 3
    assert len(sleeps) == 2


async def test_a_5xx_followed_by_success_returns_the_result():
    answers = iter(
        [reply(500, {"message": "oops"}), reply(body={"default_branch": "dev"})]
    )
    client, sleeps = make_client(lambda request: next(answers))
    async with client:
        assert await client.default_branch(REPO) == "dev"
    assert len(sleeps) == 1


async def test_429_waits_for_retry_after():
    answers = iter(
        [
            reply(429, {"message": "slow down"}, {"retry-after": "7"}),
            reply(body={"default_branch": "main"}),
        ]
    )
    client, sleeps = make_client(lambda request: next(answers))
    async with client:
        assert await client.default_branch(REPO) == "main"
    assert sleeps == [7.0]


async def test_retry_after_is_capped_at_60_seconds():
    answers = iter(
        [
            reply(429, {"message": "slow"}, {"retry-after": "500"}),
            reply(body={"default_branch": "main"}),
        ]
    )
    client, sleeps = make_client(lambda request: next(answers))
    async with client:
        await client.default_branch(REPO)
    assert sleeps == [60.0]


async def test_403_with_remaining_zero_waits_for_a_near_reset():
    reset = str(int(time.time()) + 5)
    answers = iter(
        [
            reply(
                403,
                {"message": "rate limit"},
                {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset},
            ),
            reply(body={"default_branch": "main"}),
        ]
    )
    client, sleeps = make_client(lambda request: next(answers))
    async with client:
        await client.default_branch(REPO)
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 6


async def test_rate_limit_reset_far_away_raises_without_waiting():
    reset = str(int(time.time()) + 3600)
    calls = []

    def handler(request):
        calls.append(1)
        return reply(
            403,
            {"message": "rate limit"},
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset},
        )

    client, sleeps = make_client(handler)
    async with client:
        with pytest.raises(GitHubUnavailable, match="rate limited until 20"):
            await client.default_branch(REPO)
    assert sleeps == []
    assert len(calls) == 1


async def test_403_without_rate_limit_headers_is_a_config_error():
    calls = []

    def handler(request):
        calls.append(1)
        return reply(
            403, {"message": "Resource not accessible by personal access token"}
        )

    client, sleeps = make_client(handler)
    async with client:
        with pytest.raises(GitHubConfigError):
            await client.default_branch(REPO)
    assert len(calls) == 1 and sleeps == []


async def test_401_is_a_config_error():
    client, _ = make_client(lambda request: reply(401, {"message": "Bad credentials"}))
    async with client:
        with pytest.raises(GitHubConfigError):
            await client.default_branch(REPO)


async def test_404_is_a_config_error_and_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return reply(404, {"message": "Not Found"})

    client, sleeps = make_client(handler)
    async with client:
        with pytest.raises(GitHubConfigError) as error:
            await client.default_branch(REPO)
    assert len(calls) == 1 and sleeps == []
    assert str(error.value) == f"GET /repos/{REPO}: 404 Not Found"
    assert error.value.status == 404
    assert error.value.method == "GET"
    assert error.value.path == f"/repos/{REPO}"


async def test_other_4xx_is_a_plain_error_with_a_truncated_message():
    client, sleeps = make_client(lambda request: reply(422, {"message": "x" * 500}))
    async with client:
        with pytest.raises(GitHubError) as error:
            await client.default_branch(REPO)
    assert not isinstance(error.value, GitHubConfigError)
    assert len(str(error.value)) < 300
    assert sleeps == []


# --- redaction ------------------------------------------------------------


async def test_error_text_never_contains_the_token():
    def handler(request):
        return reply(422, {"message": f"echoed {TOKEN} back"})

    client, _ = make_client(handler)
    async with client:
        with pytest.raises(GitHubError) as error:
            await client.get_issue(REPO, 1)
    assert TOKEN not in str(error.value)
    assert "***" in str(error.value)


async def test_error_text_has_no_query_string():
    client, _ = make_client(lambda request: reply(422, {"message": "bad"}))
    async with client:
        with pytest.raises(GitHubError) as error:
            await client.open_pipeline_prs(REPO, 3)
    assert "?" not in str(error.value)
    assert "state=" not in str(error.value)


async def test_log_records_are_redacted(caplog):
    client, _ = make_client(lambda request: reply(body={"default_branch": "main"}))
    async with client:
        with caplog.at_level(logging.DEBUG, logger="app.github_client"):
            logging.getLogger("app.github_client").info("leak %s", TOKEN)
            logging.getLogger("app.github_client").info(f"leak {TOKEN}")
            await client.default_branch(REPO)
    assert caplog.records
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in caplog.text
    assert any("***" in record.getMessage() for record in caplog.records)
    assert any(
        "GET" in record.getMessage() and "200" in record.getMessage()
        for record in caplog.records
    )


# --- reads ----------------------------------------------------------------


async def test_get_issue_maps_fields():
    body = {
        "number": 7,
        "title": "Bug",
        "body": None,
        "state": "open",
        "user": {"login": "octo"},
        "labels": [{"name": "issue-to-pr"}, {"name": "bug"}],
        "html_url": "https://github.com/octo/demo/issues/7",
    }
    client, _ = make_client(lambda request: reply(body=body))
    async with client:
        issue = await client.get_issue(REPO, 7)
    assert issue.number == 7 and issue.body == "" and issue.author == "octo"
    assert issue.labels == ("issue-to-pr", "bug")


async def test_branch_head_returns_commit_and_tree_sha():
    def handler(request):
        assert request.url.path == f"/repos/{REPO}/commits/main"
        return reply(body={"sha": "c1", "commit": {"tree": {"sha": "t1"}}})

    client, _ = make_client(handler)
    async with client:
        assert await client.branch_head(REPO, "main") == ("c1", "t1")


async def test_tarball_over_the_cap_is_refused_and_removed(tmp_path):
    dest = tmp_path / "src.tgz"
    client, sleeps = make_client(
        lambda request: httpx.Response(200, content=b"x" * 5000)
    )
    async with client:
        with pytest.raises(GitHubError, match="archive exceeds 1000 bytes"):
            await client.download_tarball(REPO, "abc", dest, max_bytes=1000)
    assert not dest.exists()
    assert sleeps == []


async def test_open_pipeline_prs_filters_by_head_prefix():
    prs = [
        _pr(1, "issue-to-pr/12-fix-parser"),
        _pr(2, "issue-to-pr/123-other"),
        _pr(3, "feature/12-x"),
        _pr(4, "issue-to-pr/12-second"),
    ]
    client, _ = make_client(lambda request: reply(body=prs))
    async with client:
        found = await client.open_pipeline_prs(REPO, 12)
    assert [pr.number for pr in found] == [1, 4]
    assert found[0].head == "issue-to-pr/12-fix-parser" and found[0].base == "main"


async def test_last_label_actor_takes_the_latest_labeled_event():
    events = [
        {
            "event": "labeled",
            "label": {"name": "issue-to-pr"},
            "actor": {"login": "first"},
            "created_at": "2026-10-01T10:00:00Z",
        },
        {
            "event": "labeled",
            "label": {"name": "other"},
            "actor": {"login": "noise"},
            "created_at": "2026-10-01T12:00:00Z",
        },
        {
            "event": "commented",
            "actor": {"login": "chatty"},
            "created_at": "2026-10-01T13:00:00Z",
        },
        {
            "event": "labeled",
            "label": {"name": "issue-to-pr"},
            "actor": {"login": "latest"},
            "created_at": "2026-10-01T11:00:00Z",
        },
        {
            "event": "unlabeled",
            "label": {"name": "issue-to-pr"},
            "actor": {"login": "remover"},
            "created_at": "2026-10-01T14:00:00Z",
        },
    ]
    client, _ = make_client(lambda request: reply(body=events))
    async with client:
        assert await client.last_label_actor(REPO, 5, "issue-to-pr") == "latest"
        assert await client.last_label_actor(REPO, 5, "missing") is None


async def test_pagination_follows_next_links():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        page = int(request.url.params.get("page", "1"))
        headers = {}
        if page < 3:
            headers["link"] = (
                f'<https://api.github.com/repos/{REPO}/pulls?state=open&page={page + 1}>; rel="next"'
            )
        return reply(body=[_pr(page, f"issue-to-pr/9-p{page}")], headers=headers)

    client, _ = make_client(handler)
    async with client:
        found = await client.open_pipeline_prs(REPO, 9)
    assert [pr.number for pr in found] == [1, 2, 3]
    assert len(seen) == 3


async def test_pagination_stops_after_ten_pages():
    calls = []

    def handler(request):
        calls.append(1)
        return reply(
            body=[],
            headers={
                "link": f'<https://api.github.com/repos/{REPO}/pulls?page=99>; rel="next"'
            },
        )

    client, _ = make_client(handler)
    async with client:
        await client.open_pipeline_prs(REPO, 9)
    assert len(calls) == 10


async def test_pagination_refuses_a_next_link_to_another_host():
    def handler(request):
        return reply(
            body=[], headers={"link": '<https://evil.example/pulls?page=2>; rel="next"'}
        )

    client, _ = make_client(handler)
    async with client:
        with pytest.raises(GitHubError):
            await client.open_pipeline_prs(REPO, 9)


def _pr(number, head, base="main", state="open"):
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "head": {"ref": head},
        "base": {"ref": base},
        "state": state,
        "body": "body",
    }


# --- idempotent writes ----------------------------------------------------


class Api:
    """A tiny stateful fake of the endpoints the writes touch."""

    def __init__(self):
        self.refs: dict[str, str] = {}
        self.prs: list[dict] = []
        self.comments: list[dict] = []
        self.log: list[tuple[str, str]] = []
        self.fail_next_create: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.log.append((method, path))
        prefix = f"/repos/{REPO}"
        if path.startswith(f"{prefix}/git/ref/heads/"):
            name = path.removeprefix(f"{prefix}/git/ref/heads/")
            if name in self.refs:
                return reply(body={"object": {"sha": self.refs[name]}})
            return reply(404, {"message": "Not Found"})
        if method == "POST" and path == f"{prefix}/git/refs":
            data = json.loads(request.content)
            self.refs[data["ref"].removeprefix("refs/heads/")] = data["sha"]
            return reply(201, {"ref": data["ref"]})
        if path == f"{prefix}/pulls" and method == "GET":
            head = request.url.params.get("head", "")
            branch = head.split(":", 1)[1]
            return reply(body=[p for p in self.prs if p["head"]["ref"] == branch])
        if path == f"{prefix}/pulls" and method == "POST":
            data = json.loads(request.content)
            if self.fail_next_create == "lost":
                self.fail_next_create = None
                self.prs.append(_pr(41, data["head"]))
                return reply(502, {"message": "Bad Gateway"})
            if self.fail_next_create == "422":
                self.fail_next_create = None
                self.prs.append(_pr(42, data["head"]))
                return reply(
                    422,
                    {
                        "message": "Validation Failed",
                        "errors": [
                            {
                                "message": f"A pull request already exists for octo:{data['head']}."
                            }
                        ],
                    },
                )
            pr = _pr(43, data["head"], data["base"])
            pr["body"] = data["body"]
            self.prs.append(pr)
            return reply(201, pr)
        if "/issues/" in path and path.endswith("/comments") and method == "GET":
            return reply(body=self.comments)
        if "/issues/" in path and path.endswith("/comments") and method == "POST":
            data = json.loads(request.content)
            if self.fail_next_create == "lost":
                self.fail_next_create = None
                self.comments.append(_comment(8, data["body"]))
                return reply(502, {"message": "Bad Gateway"})
            comment = _comment(9, data["body"])
            self.comments.append(comment)
            return reply(201, comment)
        raise AssertionError(f"unexpected {method} {path}")


def _comment(id_, body):
    return {
        "id": id_,
        "body": body,
        "user": {"login": "bot"},
        "html_url": f"https://github.com/c/{id_}",
    }


async def test_ensure_branch_is_idempotent():
    api = Api()
    client, _ = make_client(api)
    async with client:
        await client.ensure_branch(REPO, "issue-to-pr/7-fix", "abc")
        await client.ensure_branch(REPO, "issue-to-pr/7-fix", "abc")
    creates = [entry for entry in api.log if entry[0] == "POST"]
    assert len(creates) == 1
    assert api.refs == {"issue-to-pr/7-fix": "abc"}


async def test_ensure_branch_refuses_another_sha():
    api = Api()
    api.refs["issue-to-pr/7-fix"] = "abc"
    client, _ = make_client(api)
    async with client:
        with pytest.raises(GitHubError):
            await client.ensure_branch(REPO, "issue-to-pr/7-fix", "def")
    assert api.refs["issue-to-pr/7-fix"] == "abc"
    assert all(method == "GET" for method, _ in api.log)


async def test_ensure_branch_refuses_branches_outside_the_prefix():
    calls = []
    client, _ = make_client(lambda request: calls.append(request) or reply())
    async with client:
        with pytest.raises(ValueError):
            await client.ensure_branch(REPO, "main", "abc")
    assert calls == []


async def test_open_pull_request_creates_then_returns_the_existing_pr():
    api = Api()
    client, _ = make_client(api)
    async with client:
        first = await client.open_pull_request(
            REPO, head="issue-to-pr/7-fix", base="main", title="t", body="b"
        )
        second = await client.open_pull_request(
            REPO, head="issue-to-pr/7-fix", base="main", title="t", body="b"
        )
    assert first.number == second.number == 43
    assert len(api.prs) == 1


async def test_open_pull_request_returns_the_existing_pr():
    api = Api()
    api.prs.append(_pr(5, "issue-to-pr/7-fix"))
    client, _ = make_client(api)
    async with client:
        pr = await client.open_pull_request(
            REPO, head="issue-to-pr/7-fix", base="main", title="t", body="b"
        )
    assert pr.number == 5
    assert ("POST", f"/repos/{REPO}/pulls") not in api.log


async def test_open_pull_request_returns_the_existing_pr_after_a_422():
    api = Api()
    api.fail_next_create = "422"
    client, _ = make_client(api)
    async with client:
        pr = await client.open_pull_request(
            REPO, head="issue-to-pr/7-fix", base="main", title="t", body="b"
        )
    assert pr.number == 42
    assert len(api.prs) == 1


async def test_open_pull_request_retry_after_a_lost_response_finds_the_first_pr():
    api = Api()
    api.fail_next_create = "lost"
    client, sleeps = make_client(api)
    async with client:
        pr = await client.open_pull_request(
            REPO, head="issue-to-pr/7-fix", base="main", title="t", body="b"
        )
    assert pr.number == 41
    assert len(api.prs) == 1
    assert len(sleeps) == 1


async def test_comment_once_skips_when_the_marker_exists():
    api = Api()
    api.comments.append(_comment(3, "hello <!-- issue-to-pr:run-1 -->"))
    client, _ = make_client(api)
    async with client:
        comment = await client.comment_once(
            REPO,
            7,
            body="new <!-- issue-to-pr:run-1 -->",
            marker="<!-- issue-to-pr:run-1 -->",
        )
    assert comment.id == 3 and comment.author == "bot"
    assert not any(method == "POST" for method, _ in api.log)


async def test_comment_once_posts_when_absent_and_survives_a_lost_response():
    api = Api()
    client, _ = make_client(api)
    async with client:
        posted = await client.comment_once(REPO, 7, body="a MARK", marker="MARK")
    assert posted.id == 9
    api = Api()
    api.fail_next_create = "lost"
    client, _ = make_client(api)
    async with client:
        found = await client.comment_once(REPO, 7, body="a MARK", marker="MARK")
    assert found.id == 8
    assert len(api.comments) == 1


# --- create_commit ---------------------------------------------------------


async def test_create_commit_builds_blobs_tree_and_commit():
    posts: list[tuple[str, dict]] = []

    def handler(request):
        data = json.loads(request.content)
        posts.append((request.url.path, data))
        path = request.url.path
        if path.endswith("/git/blobs"):
            return reply(
                201,
                {"sha": f"blob{len([p for p in posts if p[0].endswith('/blobs')])}"},
            )
        if path.endswith("/git/trees"):
            return reply(201, {"sha": "newtree"})
        if path.endswith("/git/commits"):
            return reply(201, {"sha": "newcommit"})
        raise AssertionError(path)

    binary = bytes([0, 255, 1, 128])
    changes = [
        FileChange("src/new.py", b"print('hi')\n"),
        FileChange("src/old.py", "café".encode()),
        FileChange("gone.py", None),
        FileChange("bin/run.sh", b"#!/bin/sh\n", executable=True),
        FileChange("img.png", binary),
    ]
    client, _ = make_client(handler)
    async with client:
        sha = await client.create_commit(
            REPO,
            parent_sha="parent1",
            base_tree_sha="basetree",
            changes=changes,
            message="msg",
            author_name="issue-to-pr pipeline",
            author_email="issue-to-pr@example.invalid",
        )
    assert sha == "newcommit"
    blobs = [data for path, data in posts if path.endswith("/blobs")]
    assert len(blobs) == 4  # the deletion has no blob
    assert all(blob["encoding"] == "base64" for blob in blobs)
    assert base64.b64decode(blobs[3]["content"]) == binary
    (tree,) = [data for path, data in posts if path.endswith("/trees")]
    assert tree["base_tree"] == "basetree"
    entries = {entry["path"]: entry for entry in tree["tree"]}
    assert entries["gone.py"]["sha"] is None
    assert entries["gone.py"]["mode"] == "100644"
    assert entries["bin/run.sh"]["mode"] == "100755"
    assert entries["src/new.py"]["mode"] == "100644"
    assert entries["src/new.py"]["type"] == "blob"
    assert entries["src/new.py"]["sha"] == "blob1"
    (commit,) = [data for path, data in posts if path.endswith("/commits")]
    assert commit["tree"] == "newtree"
    assert commit["parents"] == ["parent1"]
    assert commit["message"] == "msg"
    assert commit["author"] == {
        "name": "issue-to-pr pipeline",
        "email": "issue-to-pr@example.invalid",
    }


@pytest.mark.parametrize(
    "path",
    [
        ".github",
        ".github/workflows/ci.yml",
        "./.github/x",
        ".GitHub/a",
        "a/../.github/x",
    ],
)
async def test_create_commit_refuses_github_paths(path):
    calls = []
    client, _ = make_client(lambda request: calls.append(request) or reply())
    async with client:
        with pytest.raises(ValueError):
            await client.create_commit(
                REPO,
                parent_sha="p",
                base_tree_sha="t",
                changes=[FileChange("ok.py", b"x"), FileChange(path, b"x")],
                message="m",
                author_name="n",
                author_email="e@example.invalid",
            )
    assert calls == []


async def test_create_commit_allows_a_file_that_merely_starts_with_github():
    def handler(request):
        return reply(201, {"sha": "s"})

    client, _ = make_client(handler)
    async with client:
        await client.create_commit(
            REPO,
            parent_sha="p",
            base_tree_sha="t",
            changes=[FileChange(".github_notes.md", b"x")],
            message="m",
            author_name="n",
            author_email="e@example.invalid",
        )
