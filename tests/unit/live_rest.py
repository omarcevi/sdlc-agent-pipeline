"""Whole live runs against an in-memory GitHub: a REST server for `httpx.MockTransport`
that answers intake, delivery and comments, plus the sandbox and models such a run
uses. Shared by the driver, CLI and token canary tests. Not a test module."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from google.adk.workflow import Workflow
from pydantic import PrivateAttr

from app.environment.base import ExecResult
from app.github_client import GitHubClient
from app.models import RoleModels
from app.nodes import intake
from app.nodes.verify import DIFF_CMD, NUMSTAT_CMD, TEST_CMD
from app.pipeline import build_workflow
from app.schemas import PatchResult, Plan, Review, RunRequest
from tests.fakes import FakeEnvironment, FakeLlm, json_out
from tests.unit._constants import DIFF
from tests.unit.archives import make_archive
from tests.unit.delivery_fakes import BASE_SHA, BASE_TREE, REPO, FakeGitHubRest

ISSUE = 7
ISSUE_URL = f"https://github.com/{REPO}/issues/{ISSUE}"
PULL_URL = f"https://github.com/{REPO}/pull/5"
AUTHOR = "owner"
SOURCE = {
    "mini.py": "def add(a, b):\n    return a - b\n",
    "tests/test_mini.py": "from mini import add\n",
}
PLAN = Plan(actionable=True, summary="fix add", files_to_inspect=["mini.py"])
PATCH = PatchResult(summary="fixed add")
APPROVE = Review(verdict="approve")


@dataclass(frozen=True)
class Seen:
    """One request as the transport received it."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes


class LiveGitHubRest(FakeGitHubRest):
    """FakeGitHubRest plus the reads live intake makes. Records every request with
    its headers and body. `pulls_error` makes opening the pull request answer with
    that (status, message, headers); `stall_after_pull` creates the pull request,
    then never answers; `stall_comments` never answers a comment request.
    `on_request(method, path)` is called for every request before it is answered;
    when it returns a number of seconds, the answer waits that long (so the event
    loop runs, and handles a signal the hook raised, before the run goes on)."""

    def __init__(self, archive: bytes) -> None:
        super().__init__()
        self.archive = archive
        self.seen: list[Seen] = []
        self.pulls_error: tuple[int, str, dict[str, str]] | None = None
        self.stall_after_pull = False
        self.stall_comments = False
        self.on_request: Callable[[str, str], float | None] | None = None

    def __call__(self, request: httpx.Request):
        self.seen.append(
            Seen(
                request.method,
                str(request.url),
                dict(request.headers),
                request.content,
            )
        )
        pause = None
        if self.on_request is not None:
            pause = self.on_request(request.method, request.url.path)
        answer = self._answer(request)
        if not pause:
            return answer

        async def paused() -> httpx.Response:
            await asyncio.sleep(pause)
            return answer if isinstance(answer, httpx.Response) else await answer

        return paused()

    def _answer(self, request: httpx.Request):
        path, base = request.url.path, f"/repos/{REPO}"
        if request.method == "GET":
            if path == base:
                return httpx.Response(200, json={"default_branch": "main"})
            if path == f"{base}/issues/{ISSUE}":
                return httpx.Response(200, json=self._issue())
            if path == f"{base}/issues/{ISSUE}/events":
                return httpx.Response(200, json=self._label_events())
            if path == f"{base}/pulls" and "head" not in request.url.params:
                return httpx.Response(200, json=self.pulls)
            if path == f"{base}/commits/main":
                return httpx.Response(
                    200, json={"sha": BASE_SHA, "commit": {"tree": {"sha": BASE_TREE}}}
                )
            if path == f"{base}/tarball/{BASE_SHA}":
                return httpx.Response(200, content=self.archive)
        if path == f"{base}/issues/{ISSUE}/comments" and self.stall_comments:
            return self._never()
        if path == f"{base}/pulls" and request.method == "POST":
            if self.pulls_error is not None:
                status, message, headers = self.pulls_error
                return httpx.Response(
                    status, json={"message": message}, headers=headers
                )
            if self.stall_after_pull:
                super().__call__(request)
                return self._never()
        return super().__call__(request)

    @staticmethod
    async def _never() -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    def _issue(self) -> dict:
        return {
            "number": ISSUE,
            "title": "Parser loses rows",
            "body": "the parser drops the last row",
            "state": "open",
            "user": {"login": AUTHOR},
            "labels": [{"name": "agent-ok"}],
            "html_url": ISSUE_URL,
        }

    @staticmethod
    def _label_events() -> list[dict]:
        return [
            {
                "event": "labeled",
                "label": {"name": "agent-ok"},
                "actor": {"login": AUTHOR},
                "created_at": "2026-10-01T00:00:00Z",
            }
        ]

    def count(self, method: str, suffix: str) -> int:
        return sum(
            1
            for seen in self.seen
            if seen.method == method and httpx.URL(seen.url).path.endswith(suffix)
        )

    def posted_comments(self) -> int:
        return self.count("POST", f"/issues/{ISSUE}/comments")


def source_archive(tmp_path) -> bytes:
    return make_archive(tmp_path / "served-source.tar.gz", SOURCE).read_bytes()


async def no_sleep(_seconds: float) -> None:
    return None


def serve(monkeypatch, server: LiveGitHubRest) -> None:
    """Every GitHubClient the nodes build talks to `server` (fixed test token)."""

    def factory(**kwargs):
        return GitHubClient(
            "tok",
            transport=httpx.MockTransport(server),
            sleep=no_sleep,
            max_attempts=2,
        )

    monkeypatch.setattr(GitHubClient, "from_token_file", staticmethod(factory))


def use_sandbox(monkeypatch) -> FakeEnvironment:
    env = FakeEnvironment(
        responses={
            DIFF_CMD: ExecResult(exit_code=0, stdout=DIFF, stderr=""),
            NUMSTAT_CMD: ExecResult(exit_code=0, stdout="1\t1\tmini.py\n", stderr=""),
            TEST_CMD: ExecResult(exit_code=0, stdout="2 passed", stderr=""),
        }
    )

    async def start():
        return env

    monkeypatch.setattr(intake, "start_environment", start)
    return env


class HangingLlm(FakeLlm):
    """A model whose call never returns. `called` is set once a call started."""

    _called: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__([], **kwargs)

    @property
    def called(self) -> asyncio.Event:
        return self._called

    async def generate_content_async(self, llm_request, stream=False):
        self._called.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")
        yield  # an async generator, like every model


def scripted() -> RoleModels:
    """One planner, coder and reviewer answer each: a run that reaches the gate."""
    return RoleModels(
        planner=FakeLlm([json_out(PLAN)]),
        coder=FakeLlm([json_out(PATCH)]),
        reviewer=FakeLlm([json_out(APPROVE)]),
    )


def live_request(run_id: str = "r-live") -> RunRequest:
    return RunRequest(run_id=run_id, mode="live", repo=REPO, issue_number=ISSUE)


def live_workflow(models: RoleModels | None = None) -> Workflow:
    return build_workflow(models or scripted(), live=True)
