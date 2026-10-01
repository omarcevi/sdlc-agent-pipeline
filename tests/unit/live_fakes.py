"""A stand-in GitHub client for live intake tests. Not a test module."""

from pathlib import Path

from app.github_client import Issue, PullRequest

REPO = "acme/widgets"
BODY = "zebra-body-marker the parser drops the last row"
BASE_SHA = "a" * 40
TREE_SHA = "b" * 40


class FakeGitHub:
    """Stands in for GitHubClient. Any method not listed here (a comment read, a
    write) fails the test."""

    def __init__(self, archive: Path, **overrides):
        self.archive = archive
        self.calls: list[tuple] = []
        self.issue = Issue(
            number=7,
            title="Parser loses rows",
            body=BODY,
            state="open",
            author="Owner",
            labels=("bug", "agent-ok"),
            html_url=f"https://github.com/{REPO}/issues/7",
        )
        self.label_actor: str | None = "owner"
        self.prs: list[PullRequest] = []
        self.default = "main"
        self.errors: dict[str, Exception] = {}
        self.comments: list[tuple] = []
        self.__dict__.update(overrides)

    # The node builds its client with GitHubClient.from_token_file() and uses it
    # as an async context manager.
    def from_token_file(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def __getattr__(self, name):
        raise AssertionError(f"unexpected GitHub call: {name}")

    async def _do(self, name, *args):
        self.calls.append((name, *args))
        if name in self.errors:
            raise self.errors[name]

    async def default_branch(self, repo):
        await self._do("default_branch", repo)
        return self.default

    async def get_issue(self, repo, number):
        await self._do("get_issue", repo, number)
        return self.issue

    async def last_label_actor(self, repo, number, label):
        await self._do("last_label_actor", repo, number, label)
        return self.label_actor

    async def open_pipeline_prs(self, repo, number):
        await self._do("open_pipeline_prs", repo, number)
        return self.prs

    async def branch_head(self, repo, branch):
        await self._do("branch_head", repo, branch)
        return BASE_SHA, TREE_SHA

    async def comment_once(self, repo, number, *, body, marker):
        # The failure comment that `report_failure` leaves in live mode (Task 3).
        await self._do("comment_once", repo, number)
        self.comments.append((repo, number, marker))

    async def download_tarball(self, repo, sha, dest, *, max_bytes=50_000_000):
        await self._do("download_tarball", repo, sha)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.archive.read_bytes())
        return dest


class FakeClientClass:
    """Patched in for `GitHubClient` in `app.nodes.intake` and `app.nodes.finish`."""

    def __init__(self, fake: FakeGitHub):
        self.fake = fake

    def from_token_file(self, **kwargs):
        return self.fake.from_token_file()
