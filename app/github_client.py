"""GitHub REST client for live mode.

This module holds the only code that reads the GitHub token. The token comes from
a file (never the environment, never the `gh` CLI), travels only in the
`Authorization` header, and is scrubbed from log records and error text.

Writes are check-then-create: the whole check-and-create unit is retried as one,
so a retry after a lost response finds the first attempt's result instead of
creating a second pull request, comment or branch.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import posixpath
import random
import re
import stat
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.environment.base import InfraError

TOKEN_FILE_ENV = "GITHUB_TOKEN_FILE"
DEFAULT_TOKEN_FILE = "~/.config/issue-to-pr/github-token"
BRANCH_PREFIX = "issue-to-pr/"

_API_VERSION = "2022-11-28"
_MAX_PAGES = 10
_MAX_WAIT_S = 60.0
_MESSAGE_LIMIT = 200
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$")
_TOKEN_FILE_MAX_BYTES = 4096
_SECONDARY_BACKOFF_S = 60.0

logger = logging.getLogger(__name__)


# --- redaction -------------------------------------------------------------

_secrets: set[str] = set()


def _scrub(text: str) -> str:
    for secret in _secrets:
        text = text.replace(secret, "***")
    return text


class _RedactFilter(logging.Filter):
    """Replaces every registered token value with *** in a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Never raise out of a logging call: an unformattable record is replaced.
        if not _secrets:
            return True
        try:
            record.msg = _scrub(record.getMessage())
        except Exception:
            record.msg = "<unformattable log record>"
        record.args = None
        try:
            if record.exc_info:
                record.exc_text = _scrub(
                    logging.Formatter().formatException(record.exc_info)
                )
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = _scrub(record.exc_text)
            if record.stack_info:
                record.stack_info = _scrub(record.stack_info)
        except Exception:
            record.exc_info = None
            record.exc_text = "<unformattable traceback>"
        return True


logger.addFilter(_RedactFilter())


# --- errors ------------------------------------------------------------------


class GitHubError(Exception):
    """A GitHub request failed. str() is `<METHOD> <path>: <status> <message>`."""

    def __init__(
        self, status: int | None, method: str, path: str, message: str
    ) -> None:
        self.status = status
        self.method = method
        self.path = path
        self.message = message
        where = f"{method} {path}: " if method else ""
        code = f"{status} " if status is not None else ""
        super().__init__(f"{where}{code}{message}")


class GitHubConfigError(GitHubError):
    """The token is missing, unreadable or too open, or GitHub rejected it (401,
    403 without rate-limit headers, 404)."""


class GitHubUnavailable(InfraError):
    """5xx, 429, rate limits or transport errors, after retries."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        method: str = "",
        path: str = "",
    ) -> None:
        self.status = status
        self.method = method
        self.path = path
        super().__init__(message)


class _Transient(Exception):
    """Internal: this attempt failed in a way worth retrying."""

    def __init__(self, error: GitHubUnavailable, delay: float | None = None) -> None:
        self.error = error
        self.delay = delay  # None: use the exponential backoff


def _config_error(message: str) -> GitHubConfigError:
    return GitHubConfigError(None, "", "", message)


# --- data ----------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    state: str
    author: str
    labels: tuple[str, ...]
    html_url: str


@dataclass(frozen=True)
class PullRequest:
    number: int
    html_url: str
    head: str
    base: str
    state: str
    body: str


@dataclass(frozen=True)
class Comment:
    id: int
    body: str
    author: str
    html_url: str


@dataclass(frozen=True)
class FileChange:
    path: str
    content: bytes | None  # None deletes the file
    executable: bool = False


# --- token -----------------------------------------------------------------------


def load_token() -> str:
    """Read the token from the file named by GITHUB_TOKEN_FILE.

    Reads nothing else: not GITHUB_TOKEN, not GH_TOKEN, not the gh CLI's config.
    The value is never placed in os.environ. The file is opened once and checked
    through its descriptor (a regular file, no group or other permission bits, a
    few KiB at most); no error text contains the file's contents.
    """
    path = Path(os.environ.get(TOKEN_FILE_ENV) or DEFAULT_TOKEN_FILE).expanduser()
    try:
        # O_NONBLOCK: opening a FIFO must not hang; the S_ISREG check rejects it.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise _config_error(
            f"GitHub token file {path} is not readable ({type(exc).__name__})"
        ) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _config_error(f"GitHub token file {path} is not a regular file")
        if info.st_mode & 0o077:
            raise _config_error(
                f"GitHub token file {path} must not be readable or writable by group "
                "or others (chmod 600)"
            )
        raw = os.read(fd, _TOKEN_FILE_MAX_BYTES + 1)
    except OSError as exc:
        raise _config_error(
            f"GitHub token file {path} is not readable ({type(exc).__name__})"
        ) from None
    finally:
        os.close(fd)
    if len(raw) > _TOKEN_FILE_MAX_BYTES:
        raise _config_error(f"GitHub token file {path} is too large")
    # Not decoded inside an except block: UnicodeDecodeError holds the file's bytes
    # and would stay reachable as the error's __context__.
    if not raw.isascii():
        raise _config_error(f"GitHub token file {path} is not ASCII text")
    token = raw.decode("ascii").strip()
    if not token:
        raise _config_error(f"GitHub token file {path} is empty")
    if not _TOKEN_RE.fullmatch(token):
        raise _config_error(
            f"GitHub token file {path} does not hold a token (letters, digits and "
            "underscores only)"
        )
    return token


# --- helpers ---------------------------------------------------------------------


def _check_repo(repo: str) -> str:
    if not _REPO_RE.fullmatch(repo) or any(
        part in (".", "..") for part in repo.split("/")
    ):
        raise ValueError(f"repository must be 'owner/name', got {repo!r}")
    return repo


def _check_sha(sha: str) -> str:
    if not _SHA_RE.fullmatch(sha):
        raise ValueError(f"not a commit SHA: {sha!r}")
    return sha


def _check_ref(branch: str) -> str:
    """A conservative subset of git's ref-name rules."""
    parts = branch.split("/")
    if (
        not _REF_RE.fullmatch(branch)
        or ".." in branch
        or "" in parts
        or any(part.startswith(".") or part.endswith(".lock") for part in parts)
        or branch.endswith(".")
    ):
        raise ValueError(f"not a valid branch name: {branch!r}")
    return branch


def _branch_path(branch: str) -> str:
    return quote(branch, safe="/")


def _check_change_path(path: str) -> None:
    parts = path.split("/")
    if path.startswith("/") or ".." in parts:
        raise ValueError(f"unsafe path in a commit: {path!r}")
    normal = posixpath.normpath(path).lower()
    if normal == ".github" or normal.startswith(".github/"):
        raise ValueError(f"commits may not touch .github: {path!r}")


def _pull_request(data: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=data["number"],
        html_url=data["html_url"],
        head=data["head"]["ref"],
        base=data["base"]["ref"],
        state=data["state"],
        body=data.get("body") or "",
    )


def _comment(data: dict[str, Any]) -> Comment:
    return Comment(
        id=data["id"],
        body=data.get("body") or "",
        author=(data.get("user") or {}).get("login", ""),
        html_url=data["html_url"],
    )


def _parse_float(value: str | None) -> float | None:
    """A finite float, or None: `inf` and `nan` are not usable header values."""
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_of(url: httpx.URL) -> tuple[str, str, int | None]:
    return (url.scheme, url.host, url.port or _DEFAULT_PORTS.get(url.scheme))


# --- client ------------------------------------------------------------------------


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = "https://api.github.com",
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not _TOKEN_RE.fullmatch(token or ""):
            raise _config_error(
                "GitHub token is empty or has characters other than letters, digits "
                "and underscores"
            )
        base = httpx.URL(base_url)
        # https only, so the token never travels in cleartext; httpx.MockTransport
        # never touches the network, so only it may be used with another base URL.
        if base.scheme != "https" and not isinstance(transport, httpx.MockTransport):
            raise ValueError("base_url must use https")
        _secrets.add(token)
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._origin = _origin_of(base)
        self._me: str | None = None
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
            },
        )

    @classmethod
    def from_token_file(cls, **kwargs: Any) -> GitHubClient:
        return cls(load_token(), **kwargs)

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- transport ----------------------------------------------------------------

    async def _attempts(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """Run `operation`, retrying while it raises _Transient."""
        for attempt in range(1, self._max_attempts + 1):
            transient: _Transient | None = None
            try:
                return await operation()
            except _Transient as caught:
                transient = caught
            # Raised outside the except block so the error keeps no __context__
            # (and so no reference to a request carrying the Authorization header).
            if attempt == self._max_attempts:
                raise transient.error
            delay = transient.delay
            if delay is None:
                base = 0.5 * 2 ** (attempt - 1)
                delay = base + random.uniform(0, base / 2)
            await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _message(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            body = None
        message = ""
        if isinstance(body, dict):
            message = str(body.get("message") or "")
            if response.status_code == 422:
                for item in body.get("errors") or []:
                    detail = item.get("message") if isinstance(item, dict) else item
                    if detail:
                        message += f" {detail}"
        message = message.strip() or response.reason_phrase or "error"
        return _scrub(message)[:_MESSAGE_LIMIT]

    def _rate_limit_delay(
        self, response: httpx.Response, method: str, path: str, secondary: bool
    ) -> float | None:
        """Seconds to wait, or raise GitHubUnavailable when the wait is too long."""
        retry_after = _parse_float(response.headers.get("retry-after"))
        if retry_after is not None:
            if retry_after > _MAX_WAIT_S:
                raise GitHubUnavailable(
                    f"rate limited: retry after {int(retry_after)} s",
                    status=response.status_code,
                    method=method,
                    path=path,
                )
            retry_after = max(retry_after, 0.0)
            return max(retry_after, _SECONDARY_BACKOFF_S) if secondary else retry_after
        if secondary:
            return _SECONDARY_BACKOFF_S
        reset = _parse_float(response.headers.get("x-ratelimit-reset"))
        if reset is not None:
            wait = reset - time.time()
            if wait <= _MAX_WAIT_S:
                return max(wait, 0.0)
            until = datetime.fromtimestamp(reset, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            raise GitHubUnavailable(
                f"rate limited until {until}",
                status=response.status_code,
                method=method,
                path=path,
            )
        return None  # no hint: plain backoff

    async def _send(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        allow_404: bool = False,
    ) -> httpx.Response:
        """One attempt. Returns the response (unread when `stream`) or raises."""
        request = self._http.build_request(method, url, json=json, params=params)
        path = request.url.path
        failure: str | None = None
        try:
            response = await self._http.send(request, stream=stream)
        except httpx.RequestError as exc:
            failure = type(exc).__name__
        if failure is not None:
            # Built outside the except block: no __context__ keeps the request.
            logger.warning("%s %s transport error %s", method, path, failure)
            raise _Transient(
                GitHubUnavailable(
                    f"{method} {path}: transport error {failure}",
                    method=method,
                    path=path,
                )
            )
        status = response.status_code
        logger.info("%s %s %s", method, path, status)
        if status < 400 or (status == 404 and allow_404):
            return response
        read_failure: str | None = None
        try:
            if stream:
                await response.aread()
        except httpx.RequestError as exc:
            read_failure = type(exc).__name__
        finally:
            await response.aclose()
        if read_failure is not None:
            raise _Transient(
                GitHubUnavailable(
                    f"{method} {path}: transport error {read_failure}",
                    method=method,
                    path=path,
                )
            )
        message = self._message(response)
        secondary = status in (403, 429) and "secondary rate limit" in message.lower()
        rate_limited = (
            secondary
            or status == 429
            or (
                status == 403
                and (
                    "retry-after" in response.headers
                    or response.headers.get("x-ratelimit-remaining") == "0"
                )
            )
        )
        if rate_limited:
            delay = self._rate_limit_delay(response, method, path, secondary)
            raise _Transient(
                GitHubUnavailable(
                    f"{method} {path}: {status} {message}",
                    status=status,
                    method=method,
                    path=path,
                ),
                delay,
            )
        if status >= 500:
            raise _Transient(
                GitHubUnavailable(
                    f"{method} {path}: {status} {message}",
                    status=status,
                    method=method,
                    path=path,
                )
            )
        if status in (401, 403, 404):
            raise GitHubConfigError(status, method, path, message)
        raise GitHubError(status, method, path, message)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> httpx.Response:
        return await self._attempts(
            lambda: self._send(
                method, url, json=json, params=params, allow_404=allow_404
            )
        )

    def _same_origin(self, url: str) -> bool:
        parsed = httpx.URL(url)
        return _origin_of(parsed) == self._origin

    async def _paginate(
        self, path: str, params: dict[str, Any] | None = None, *, retry: bool = True
    ) -> list[Any]:
        """All items of a listing. Raises when more than _MAX_PAGES pages exist.

        `retry=False` sends each page once and lets _Transient escape, for callers
        that retry a larger unit themselves.
        """
        items: list[Any] = []
        url, query = path, params
        for _ in range(_MAX_PAGES):
            if retry:
                response = await self._request("GET", url, params=query)
            else:
                response = await self._send("GET", url, params=query)
            items.extend(response.json())
            next_link = response.links.get("next")
            if not next_link:
                return items
            url, query = next_link["url"], None
            if not self._same_origin(url):
                raise GitHubError(
                    None, "GET", path, "pagination link leaves the API origin"
                )
        raise GitHubError(
            None, "GET", path, f"more than {_MAX_PAGES} pages; refusing a partial list"
        )

    # -- reads --------------------------------------------------------------------

    async def get_issue(self, repo: str, number: int) -> Issue:
        data = (
            await self._request("GET", f"/repos/{_check_repo(repo)}/issues/{number}")
        ).json()
        return Issue(
            number=data["number"],
            title=data.get("title") or "",
            body=data.get("body") or "",
            state=data["state"],
            author=(data.get("user") or {}).get("login", ""),
            labels=tuple(label["name"] for label in data.get("labels") or []),
            html_url=data["html_url"],
        )

    async def last_label_actor(self, repo: str, number: int, label: str) -> str | None:
        events = await self._paginate(
            f"/repos/{_check_repo(repo)}/issues/{number}/events", {"per_page": 100}
        )
        best: tuple[str, int, str] | None = None
        for index, event in enumerate(events):
            if (
                event.get("event") != "labeled"
                or (event.get("label") or {}).get("name") != label
            ):
                continue
            key = (
                event.get("created_at") or "",
                index,
                (event.get("actor") or {}).get("login", ""),
            )
            if best is None or key[:2] > best[:2]:
                best = key
        return (best[2] or None) if best else None

    async def default_branch(self, repo: str) -> str:
        data = (await self._request("GET", f"/repos/{_check_repo(repo)}")).json()
        return data["default_branch"]

    async def branch_head(self, repo: str, branch: str) -> tuple[str, str]:
        path = f"/repos/{_check_repo(repo)}/commits/{_branch_path(_check_ref(branch))}"
        data = (await self._request("GET", path)).json()
        return data["sha"], data["commit"]["tree"]["sha"]

    async def download_tarball(
        self, repo: str, sha: str, dest: Path, *, max_bytes: int = 50_000_000
    ) -> Path:
        path = f"/repos/{_check_repo(repo)}/tarball/{_check_sha(sha)}"
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        async def attempt() -> Path:
            response = await self._send("GET", path, stream=True)
            try:
                declared = _parse_float(response.headers.get("content-length"))
                if declared is not None and declared > max_bytes:
                    raise GitHubError(
                        response.status_code,
                        "GET",
                        path,
                        f"archive exceeds {max_bytes} bytes",
                    )
                size = 0
                failure: str | None = None
                try:
                    with dest.open("wb") as out:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise GitHubError(
                                    response.status_code,
                                    "GET",
                                    path,
                                    f"archive exceeds {max_bytes} bytes",
                                )
                            out.write(chunk)
                except httpx.RequestError as exc:
                    failure = type(exc).__name__
                if failure is not None:
                    raise _Transient(
                        GitHubUnavailable(
                            f"GET {path}: transport error {failure}",
                            method="GET",
                            path=path,
                        )
                    )
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            finally:
                await response.aclose()
            return dest

        return await self._attempts(attempt)

    async def open_pipeline_prs(
        self, repo: str, issue_number: int
    ) -> list[PullRequest]:
        prefix = f"{BRANCH_PREFIX}{issue_number}-"
        items = await self._paginate(
            f"/repos/{_check_repo(repo)}/pulls", {"state": "open", "per_page": 100}
        )
        # Only branches of this repository: a fork PR cannot block runs.
        same_repo = [
            item
            for item in items
            if ((item["head"].get("repo") or {}).get("full_name") or "").lower()
            == repo.lower()
        ]
        return [
            pr for pr in map(_pull_request, same_repo) if pr.head.startswith(prefix)
        ]

    # -- idempotent writes -------------------------------------------------------------

    async def create_commit(
        self,
        repo: str,
        *,
        parent_sha: str,
        base_tree_sha: str,
        changes: list[FileChange],
        message: str,
        author_name: str,
        author_email: str,
        date: str | None = None,
    ) -> str:
        """`date` (ISO 8601, e.g. 2026-10-01T12:00:00Z) fixes the author and committer
        dates, so repeating the call with the same inputs yields the same commit."""
        _check_repo(repo)
        for change in changes:
            _check_change_path(change.path)
        entries: list[dict[str, Any]] = []
        for change in changes:
            mode = "100755" if change.executable else "100644"
            if change.content is None:
                sha = None
            else:
                blob = await self._request(
                    "POST",
                    f"/repos/{repo}/git/blobs",
                    json={
                        "content": base64.b64encode(change.content).decode("ascii"),
                        "encoding": "base64",
                    },
                )
                sha = blob.json()["sha"]
            entries.append(
                {"path": change.path, "mode": mode, "type": "blob", "sha": sha}
            )
        tree = await self._request(
            "POST",
            f"/repos/{repo}/git/trees",
            json={"base_tree": base_tree_sha, "tree": entries},
        )
        author: dict[str, str] = {"name": author_name, "email": author_email}
        commit_body: dict[str, Any] = {
            "message": message,
            "tree": tree.json()["sha"],
            "parents": [parent_sha],
            "author": author,
        }
        if date is not None:
            author["date"] = date
            commit_body["committer"] = {**author}
        commit = await self._request(
            "POST", f"/repos/{repo}/git/commits", json=commit_body
        )
        return commit.json()["sha"]

    async def ensure_branch(self, repo: str, branch: str, sha: str) -> None:
        if not branch.startswith(BRANCH_PREFIX):
            raise ValueError(
                f"branch must start with {BRANCH_PREFIX!r}, got {branch!r}"
            )
        _check_repo(repo)
        _check_ref(branch)
        ref_path = f"/repos/{repo}/git/ref/heads/{_branch_path(branch)}"

        async def check() -> bool:
            """True when the branch exists at `sha`; False when absent."""
            response = await self._send("GET", ref_path, allow_404=True)
            if response.status_code == 404:
                return False
            existing = response.json()["object"]["sha"]
            if existing != sha:
                raise GitHubError(
                    None,
                    "GET",
                    ref_path,
                    f"branch {branch} exists at another commit; not moving it",
                )
            return True

        async def unit() -> None:
            if await check():
                return
            try:
                await self._send(
                    "POST",
                    f"/repos/{repo}/git/refs",
                    json={"ref": f"refs/heads/{branch}", "sha": sha},
                )
            except GitHubError as exc:
                if exc.status != 422 or not await check():
                    raise

        await self._attempts(unit)

    async def open_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequest:
        owner = _check_repo(repo).split("/")[0]
        path = f"/repos/{repo}/pulls"

        async def existing() -> PullRequest | None:
            response = await self._send(
                "GET", path, params={"state": "open", "head": f"{owner}:{head}"}
            )
            items = response.json()
            return _pull_request(items[0]) if items else None

        async def unit() -> PullRequest:
            if found := await existing():
                return found
            try:
                response = await self._send(
                    "POST",
                    path,
                    json={"title": title, "head": head, "base": base, "body": body},
                )
            except GitHubError as exc:
                if (
                    exc.status == 422
                    and "pull request already exists" in exc.message.lower()
                ):
                    if found := await existing():
                        return found
                raise
            return _pull_request(response.json())

        return await self._attempts(unit)

    async def _whoami(self) -> str:
        if self._me is None:
            response = await self._send("GET", "/user")
            self._me = response.json()["login"]
        return self._me

    async def comment_once(
        self, repo: str, number: int, *, body: str, marker: str
    ) -> Comment:
        """Post `body` unless the token's own user already posted `marker`.

        Comments by anyone else never count: anyone can post a marker on a public
        issue, and that must not suppress the pipeline's comment.
        """
        path = f"/repos/{_check_repo(repo)}/issues/{number}/comments"

        async def unit() -> Comment:
            me = await self._whoami()
            comments = await self._paginate(path, {"per_page": 100}, retry=False)
            for item in comments:
                author = (item.get("user") or {}).get("login", "")
                if author == me and marker in (item.get("body") or ""):
                    return _comment(item)
            created = await self._send("POST", path, json={"body": body})
            return _comment(created.json())

        return await self._attempts(unit)
