"""Local hermetic sandbox: one locked-down Docker container per run."""

import asyncio
import io
import os
import tarfile
import uuid
from pathlib import Path

from app.environment.base import DEFAULT_TIMEOUT_S, WORKDIR, ExecResult, InfraError

DEFAULT_IMAGE = "issue-to-pr-sandbox:dev"
_DAEMON_ERRORS = ("Error response from daemon", "No such container", "is not running")


async def _run(
    args: list[str], *, stdin: bytes | None = None, timeout: float
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE
        if stdin is not None
        else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise InfraError(f"docker command timed out: {' '.join(args[:3])}") from None
    return (
        proc.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


class DockerEnvironment:
    def __init__(self, container: str):
        self.env_id = container

    @classmethod
    async def start(cls, image: str | None = None) -> "DockerEnvironment":
        image = image or os.environ.get("SANDBOX_IMAGE", DEFAULT_IMAGE)
        name = f"itp-{uuid.uuid4().hex[:12]}"
        args = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--cpus",
            "2",
            "--memory",
            "2g",
            "--pids-limit",
            "256",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/workspace:rw,exec,mode=1777,size=512m",
            "--tmpfs",
            "/tmp:rw,exec,mode=1777,size=256m",
            "--user",
            "1000:1000",
            image,
            "sleep",
            "infinity",
        ]
        code, _, err = await _run(args, timeout=90)
        if code != 0:
            raise InfraError(f"docker run failed: {err.strip()}")
        env = cls(name)
        await env.exec(f"mkdir -p {WORKDIR}", cwd="/workspace")
        return env

    async def exec(
        self, command: str, *, timeout: float = DEFAULT_TIMEOUT_S, cwd: str = WORKDIR
    ) -> ExecResult:
        args = [
            "docker",
            "exec",
            "-w",
            cwd,
            self.env_id,
            "timeout",
            "-k",
            "5",
            str(int(timeout)),
            "sh",
            "-c",
            command,
        ]
        code, out, err = await _run(args, timeout=timeout + 30)
        if any(marker in err for marker in _DAEMON_ERRORS):
            raise InfraError(f"sandbox {self.env_id} unavailable: {err.strip()}")
        return ExecResult(exit_code=code, stdout=out, stderr=err, timed_out=code == 124)

    async def read_file(self, path: str) -> str:
        code, out, err = await _run(
            ["docker", "exec", self.env_id, "cat", "--", path], timeout=30
        )
        if code != 0:
            if "No such file" in err:
                raise FileNotFoundError(path)
            raise InfraError(f"read_file failed: {err.strip()}")
        return out

    async def write_file(self, path: str, content: str) -> None:
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        code, _, err = await _run(
            ["docker", "exec", "-i", self.env_id, "sh", "-c", script, "sh", path],
            stdin=content.encode(),
            timeout=30,
        )
        if code != 0:
            raise InfraError(f"write_file failed: {err.strip()}")

    async def upload_dir(self, local_dir: Path, dest: str = WORKDIR) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            tar.add(str(local_dir), arcname=".")
        code, _, err = await _run(
            [
                "docker",
                "exec",
                "-i",
                self.env_id,
                "sh",
                "-c",
                f'mkdir -p "{dest}" && tar -C "{dest}" -xf -',
            ],
            stdin=buf.getvalue(),
            timeout=120,
        )
        if code != 0:
            raise InfraError(f"upload failed: {err.strip()}")

    async def close(self) -> None:
        await _run(["docker", "rm", "-f", self.env_id], timeout=60)
