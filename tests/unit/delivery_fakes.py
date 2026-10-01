"""Shared fakes and builders for the delivery tests: an in-memory GitHub REST server
(for `httpx.MockTransport`), a source tarball plus the diff the sandbox would make, and
the node inputs `open_pr` takes. Not a test module."""

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import httpx

REPO = "demo/widgets"
BASE_SHA = "a" * 40
BASE_TREE = "b" * 40


class FakeGitHubRest:
    """Just enough of the GitHub REST API for open_pr and failure comments."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.trees: list[dict] = []
        self.commits: list[dict] = []
        self.refs: dict[str, str] = {}
        self.pulls: list[dict] = []
        self.comments: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.drop_first_pull_response = False
        self.fail_comments = False
        self.fail_pulls = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        body = json.loads(request.content) if request.content else None
        base = f"/repos/{REPO}"
        if path == "/user":
            return httpx.Response(200, json={"login": "pipeline-bot"})
        if path == f"{base}/git/blobs":
            data = base64.b64decode(body["content"])
            sha = hashlib.sha1(data).hexdigest()
            self.blobs[sha] = data
            return httpx.Response(201, json={"sha": sha})
        if path == f"{base}/git/trees":
            self.trees.append(body)
            return httpx.Response(
                201,
                json={
                    "sha": hashlib.sha1(
                        json.dumps(body, sort_keys=True).encode()
                    ).hexdigest()
                },
            )
        if path == f"{base}/git/commits":
            self.commits.append(body)
            sha = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
            return httpx.Response(201, json={"sha": sha})
        if path.startswith(f"{base}/git/ref/heads/"):
            ref = path.removeprefix(f"{base}/git/ref/heads/")
            if ref not in self.refs:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": self.refs[ref]}})
        if path == f"{base}/git/refs":
            self.refs[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return httpx.Response(201, json={})
        if path == f"{base}/pulls" and request.method == "GET":
            head = request.url.params["head"].split(":", 1)[1]
            return httpx.Response(
                200, json=[p for p in self.pulls if p["head"]["ref"] == head]
            )
        if path == f"{base}/pulls" and request.method == "POST":
            if self.fail_pulls:
                return httpx.Response(500, json={"message": "boom"})
            pull = {
                "number": 5,
                "html_url": f"https://github.com/{REPO}/pull/5",
                "head": {"ref": body["head"], "repo": {"full_name": REPO}},
                "base": {"ref": body["base"]},
                "state": "open",
                "body": body["body"],
                "title": body["title"],
            }
            self.pulls.append(pull)
            if self.drop_first_pull_response:
                self.drop_first_pull_response = False
                raise httpx.ReadTimeout("lost response", request=request)
            return httpx.Response(201, json=pull)
        if path == f"{base}/issues/7/comments":
            if self.fail_comments:
                return httpx.Response(500, json={"message": "boom"})
            if request.method == "GET":
                return httpx.Response(200, json=self.comments)
            comment = {
                "id": len(self.comments) + 1,
                "body": body["body"],
                "user": {"login": "pipeline-bot"},
                "html_url": f"https://github.com/{REPO}/issues/7#c{len(self.comments)}",
            }
            self.comments.append(comment)
            return httpx.Response(201, json=comment)
        return httpx.Response(500, json={"message": f"unexpected {path}"})

    def count(self, method: str, suffix: str) -> int:
        return sum(1 for m, p in self.requests if m == method and p.endswith(suffix))


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    ).stdout.decode()


def make_source(tmp_path: Path, change) -> tuple[Path, str, list[str]]:
    """A source tarball (one top-level directory, like GitHub's), plus the
    `git diff --binary` the sandbox would produce after `change(worktree)`."""
    base = tmp_path / "base"
    base.mkdir()
    (base / "keep.py").write_text("keep = 1\n")
    (base / "mod.py").write_text("x = 1\ny = 2\n")
    (base / "gone.py").write_text("old\n")
    (base / "blob.bin").write_bytes(bytes(range(256)))
    _git(base, "init", "-q")
    _git(base, "add", "-A")
    _git(base, "commit", "-q", "-m", "base")
    work = tmp_path / "work"
    shutil.copytree(base, work)
    change(work)
    _git(work, "add", "-A")
    unified = _git(work, "diff", "--cached", "--no-renames", "--binary", "HEAD")
    files = [
        line.split("\t", 2)[2]
        for line in _git(
            work, "diff", "--cached", "--numstat", "--no-renames", "HEAD"
        ).splitlines()
    ]
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(base / "keep.py", arcname="demo-widgets-abc/keep.py")
        tar.add(base / "mod.py", arcname="demo-widgets-abc/mod.py")
        tar.add(base / "gone.py", arcname="demo-widgets-abc/gone.py")
        tar.add(base / "blob.bin", arcname="demo-widgets-abc/blob.bin")
    return archive, unified, files


def _standard_change(work: Path) -> None:
    (work / "mod.py").write_text("x = 1\ny = 3\n")
    (work / "gone.py").unlink()
    (work / "new.py").write_text("new = 1\n")
    script = work / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    (work / "blob.bin").write_bytes(bytes(range(255, -1, -1)))


def issue_record(**over) -> dict:
    record = {
        "task_id": f"{REPO}#7",
        "run_id": "Run_1",
        "repo": REPO,
        "title": "Add\nthe thing",
        "body": "b",
        "mode": "live",
        "issue_number": 7,
        "base_ref": "main",
        "base_sha": BASE_SHA,
        "base_tree_sha": BASE_TREE,
    }
    record.update(over)
    return record


def _diff(unified: str, files: list[str]) -> dict:
    return {"unified_diff": unified, "files": files, "insertions": 4, "deletions": 2}


def _sha(unified: str) -> str:
    return hashlib.sha256(unified.encode()).hexdigest()


def _decision(unified: str, approver: str = "octocat", **over):
    fields = {"approved": True, "approver": approver, "patch_sha256": _sha(unified)}
    return SimpleNamespace(**{**fields, **over})


async def _collect(generator) -> list:
    return [event async for event in generator]


def _plain(generator) -> list:
    return list(generator)


TOP = "demo-widgets-abc"


def archive_with(tmp_path: Path, extra: dict[str, bytes | None], name="evil.tar.gz"):
    """The make_source archive plus `extra` members (None makes a directory)."""
    base = tmp_path / "base"
    archive = tmp_path / name
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(base.iterdir()):
            if path.name != ".git":
                tar.add(path, arcname=f"{TOP}/{path.name}")
        for member, data in extra.items():
            info = tarfile.TarInfo(f"{TOP}/{member}")
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return archive
