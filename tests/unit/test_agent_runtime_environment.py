"""The Agent Runtime sandbox backend against fakes: `FakeSandboxControl` for the
control plane, `FakeShim` (an `httpx.MockTransport`) for the data plane, and a fake
clock. No GCP, no network, and `agentplatform` is never imported."""

import ast
import asyncio
import base64
import enum
import io
import json
import logging
import os
import re
import shlex
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import requests

from app.environment import agent_runtime
from app.environment.agent_runtime import (
    SANDBOX_OWNER,
    SANDBOX_PORT,
    TEMPLATE_NOT_USABLE,
    AgentRuntimeEnvironment,
    SandboxSettings,
    SdkSandboxControl,
    sdk_control,
)
from app.environment.base import ExecResult, InfraError
from tests.unit.sandbox_fakes import (
    CALLER_SA,
    CLOUD_ENV,
    ENGINE,
    HOSTNAME,
    PROJECT,
    SANDBOX_TOKEN,
    TEMPLATE,
    FakeClock,
    FakeSandboxControl,
    FakeSdkClient,
    FakeShim,
    api_error,
    fake_settings,
    reset_template_checks,
    start_on_fakes,
)

READY_PROBE = {"command": "true", "timeout": 10}
BAD_GATEWAY = httpx.Response(
    502, text="Bad Gateway: Unable to reach the sandbox environment"
)


@pytest.fixture(autouse=True)
def _fresh_template_checks():
    reset_template_checks()
    yield
    reset_template_checks()


class Rig:
    """One control plane, one shim and one clock; `start()` starts a sandbox on them."""

    def __init__(self) -> None:
        self.control = FakeSandboxControl()
        self.shim = FakeShim()
        self.clock = FakeClock()

    async def start(self, settings: SandboxSettings | None = None, control=None):
        return await start_on_fakes(
            control or self.control, self.shim, settings=settings, clock=self.clock
        )

    def bodies(self, path: str = "/exec") -> list[dict]:
        return [json.loads(r.content) for r in self.shim.requests_to(path)]


@pytest.fixture
def rig() -> Rig:
    return Rig()


# --- settings -------------------------------------------------------------------


def test_settings_take_the_location_from_the_engine():
    environ = {**CLOUD_ENV, "GOOGLE_CLOUD_LOCATION": "global"}
    settings = SandboxSettings.from_env(environ)
    assert settings == SandboxSettings(
        engine=ENGINE,
        template=TEMPLATE,
        caller_sa=CALLER_SA,
        project=PROJECT,
        location="us-central1",
        ttl_s=1800,
        ready_timeout_s=240.0,
    )
    other = SandboxSettings.from_env(
        {
            **environ,
            "SANDBOX_ENGINE": "projects/p/locations/europe-west4/reasoningEngines/9",
            "SANDBOX_TEMPLATE": (
                "projects/p/locations/europe-west4/reasoningEngines/9"
                "/sandboxEnvironmentTemplates/1"
            ),
            "SANDBOX_TTL_S": "900",
            "SANDBOX_READY_TIMEOUT_S": "60.5",
        }
    )
    assert (other.location, other.ttl_s, other.ready_timeout_s) == (
        "europe-west4",
        900,
        60.5,
    )


MISSING = "{} must be set for ENVIRONMENT_BACKEND=agent_runtime"
ENGINE_FORM = (
    "SANDBOX_ENGINE must look like projects/<p>/locations/<l>/reasoningEngines/<id>"
)
TEMPLATE_FORM = "SANDBOX_TEMPLATE must be a template under SANDBOX_ENGINE"
CALLER_FORM = (
    "SANDBOX_CALLER_SA must be a service account email ending in "
    ".iam.gserviceaccount.com"
)
READY_FORM = "SANDBOX_READY_TIMEOUT_S must be a positive number below RUN_TIMEOUT_S"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"SANDBOX_ENGINE": None}, MISSING.format("SANDBOX_ENGINE")),
        ({"SANDBOX_TEMPLATE": None}, MISSING.format("SANDBOX_TEMPLATE")),
        ({"SANDBOX_CALLER_SA": None}, MISSING.format("SANDBOX_CALLER_SA")),
        ({"GOOGLE_CLOUD_PROJECT": None}, MISSING.format("GOOGLE_CLOUD_PROJECT")),
        ({"SANDBOX_ENGINE": "  "}, MISSING.format("SANDBOX_ENGINE")),
        ({"GOOGLE_CLOUD_PROJECT": ""}, MISSING.format("GOOGLE_CLOUD_PROJECT")),
        ({"SANDBOX_ENGINE": "reasoningEngines/4242"}, ENGINE_FORM),
        ({"SANDBOX_ENGINE": f"{ENGINE}/extra"}, ENGINE_FORM),
        ({"SANDBOX_ENGINE": f" {ENGINE}"}, ENGINE_FORM),
        ({"SANDBOX_ENGINE": "projects/p/locations/l/agents/1"}, ENGINE_FORM),
        ({"SANDBOX_TEMPLATE": f"{ENGINE}/sandboxEnvironmentTemplates/"}, TEMPLATE_FORM),
        (
            {"SANDBOX_TEMPLATE": f"{ENGINE}/sandboxEnvironmentTemplates/7/x"},
            TEMPLATE_FORM,
        ),
        (
            {"SANDBOX_TEMPLATE": f"{ENGINE}9/sandboxEnvironmentTemplates/7"},
            TEMPLATE_FORM,
        ),
        ({"SANDBOX_TEMPLATE": f"{ENGINE}/templates/7"}, TEMPLATE_FORM),
        ({"SANDBOX_CALLER_SA": "sandbox-caller@gmail.com"}, CALLER_FORM),
        ({"SANDBOX_CALLER_SA": "@p.iam.gserviceaccount.com"}, CALLER_FORM),
        ({"SANDBOX_READY_TIMEOUT_S": "0"}, READY_FORM),
        ({"SANDBOX_READY_TIMEOUT_S": "-5"}, READY_FORM),
        ({"SANDBOX_READY_TIMEOUT_S": "abc"}, READY_FORM),
        ({"SANDBOX_READY_TIMEOUT_S": "nan"}, READY_FORM),
        ({"SANDBOX_READY_TIMEOUT_S": "inf"}, READY_FORM),
        ({"SANDBOX_READY_TIMEOUT_S": ""}, READY_FORM),
    ],
)
def test_settings_refuse_bad_values_with_fixed_messages(changes, message):
    environ = dict(CLOUD_ENV)
    for name, value in changes.items():
        if value is None:
            environ.pop(name)
        else:
            environ[name] = value
    with pytest.raises(ValueError) as caught:
        SandboxSettings.from_env(environ)
    assert str(caught.value) == message


@pytest.mark.parametrize("ttl", ["0", "-1", "1.5", "abc", ""])
def test_settings_refuse_a_bad_ttl_like_the_docker_backend(ttl):
    with pytest.raises(ValueError, match=r"^SANDBOX_TTL_S must be a positive whole"):
        SandboxSettings.from_env({**CLOUD_ENV, "SANDBOX_TTL_S": ttl})


def test_settings_read_the_process_environment_by_default(monkeypatch):
    for name, value in CLOUD_ENV.items():
        monkeypatch.setenv(name, value)
    assert SandboxSettings.from_env() == fake_settings()


# --- start ----------------------------------------------------------------------


async def test_start_creates_from_the_template_with_ttl_owner_and_display_name(rig):
    client = FakeSdkClient()
    control = SdkSandboxControl(fake_settings(), client=client)
    env = await rig.start(control=control)
    creates = [kw for method, kw in client.sandboxes.calls if method == "create"]
    assert creates == [
        {
            "name": ENGINE,
            "poll_interval_seconds": 1.0,
            "config": {
                "sandbox_environment_template": TEMPLATE,
                "ttl": "1800s",
                "owner": SANDBOX_OWNER,
                "display_name": env.env_id,
            },
        }
    ]
    assert SANDBOX_OWNER == "issue-to-pr"
    created = await rig.start(settings=fake_settings(ttl_s=600))
    assert rig.control.creates == [
        {
            "engine": ENGINE,
            "template": TEMPLATE,
            "ttl_s": 600,
            "display_name": created.env_id,
        }
    ]


async def test_start_signs_one_token_for_the_caller_sa_with_the_ttl_lifetime(rig):
    env = await rig.start()
    await env.exec("echo one")
    await env.write_file("/workspace/repo/a.txt", "a")
    await env.read_file("/workspace/repo/a.txt")
    assert rig.control.signs == [(CALLER_SA, 1800)]
    assert rig.control.calls == ["check_template", "sign_token", "create"]
    other = FakeSandboxControl()
    await rig.start(settings=fake_settings(ttl_s=900), control=other)
    assert other.signs == [(CALLER_SA, 900)]


async def test_template_is_checked_once_per_process(rig):
    await rig.start()
    await rig.start()
    other = FakeSandboxControl()
    await rig.start(control=other)
    assert rig.control.checks == [TEMPLATE]
    assert other.checks == []
    another = fake_settings(template=f"{ENGINE}/sandboxEnvironmentTemplates/78")
    await rig.start(settings=another, control=other)
    assert other.checks == [another.template]


async def test_unusable_template_is_an_infra_error_and_creates_nothing(rig):
    rig.control.fail("check_template", InfraError(TEMPLATE_NOT_USABLE))
    with pytest.raises(InfraError, match=r"^sandbox template is not usable$"):
        await rig.start()
    assert rig.control.calls == ["check_template"]
    assert rig.shim.requests == []
    await rig.start()  # a refused template is checked again next time
    assert rig.control.checks == [TEMPLATE, TEMPLATE]


async def test_start_makes_the_repo_directory(rig):
    env = await rig.start()
    assert rig.shim.exec_calls == [
        ("true", None),
        ("mkdir -p /workspace/repo", "/workspace"),
    ]
    mkdir = rig.bodies()[-1]
    assert mkdir["command"].startswith("timeout -k 5 ")
    assert env.env_id


# --- readiness ------------------------------------------------------------------


async def test_readiness_retries_502_until_the_shim_answers(rig):
    rig.shim.queue(
        "/exec",
        BAD_GATEWAY,
        BAD_GATEWAY,
        httpx.ConnectError("connection refused"),
        ExecResult(exit_code=1, stdout="", stderr="not yet"),
        httpx.Response(200, text="not json"),
    )
    await rig.start()
    probes = rig.shim.requests_to("/exec")[:6]
    assert [json.loads(r.content) for r in probes] == [READY_PROBE] * 6
    assert all(r.extensions["timeout"]["read"] == 15.0 for r in probes)
    assert rig.clock.sleeps == [2.0] * 5
    assert rig.shim.exec_calls[6:] == [("mkdir -p /workspace/repo", "/workspace")]
    assert rig.control.deletes == []


async def test_readiness_never_calls_healthz(rig):
    rig.shim.queue("/exec", BAD_GATEWAY, BAD_GATEWAY)
    env = await rig.start()
    await env.exec("true")
    assert rig.shim.requests_to("/healthz") == []
    assert {r.url.path for r in rig.shim.requests} == {"/exec"}


async def test_readiness_timeout_deletes_the_sandbox_once(rig):
    rig.shim.queue("/exec", BAD_GATEWAY, repeat_last=True)
    with pytest.raises(InfraError) as caught:
        await rig.start(settings=fake_settings(ready_timeout_s=10.0))
    assert str(caught.value) == "sandbox did not become ready in 10 s"
    assert len(rig.shim.requests) == 6  # at 0, 2, 4, 6, 8 and 10 s
    assert rig.clock.now == 10.0
    assert rig.control.deletes == [rig.control.created[0].name]
    assert [name for name, _ in rig.shim.exec_calls] == ["true"] * 6


async def test_readiness_default_bound_is_240_s(rig):
    rig.shim.queue("/exec", BAD_GATEWAY, repeat_last=True)
    with pytest.raises(InfraError, match=r"^sandbox did not become ready in 240 s$"):
        await rig.start()
    assert rig.clock.now == 240.0
    assert len(rig.control.deletes) == 1


# --- failure and cancellation during start ---------------------------------------


async def test_failure_after_create_deletes_the_sandbox(rig):
    rig.shim.queue("/exec", {"exit_code": 0, "stdout": "", "stderr": ""})
    rig.shim.queue("/exec", httpx.ConnectError("connection reset"))  # the mkdir
    with pytest.raises(InfraError, match="ConnectError"):
        await rig.start()
    assert rig.control.deletes == [rig.control.created[0].name]


async def test_a_failing_delete_after_a_failed_start_keeps_the_first_error(rig, caplog):
    rig.shim.queue("/exec", BAD_GATEWAY, repeat_last=True)
    rig.control.fail("delete", InfraError("delete refused"))
    with caplog.at_level(logging.WARNING, logger=agent_runtime.__name__):
        with pytest.raises(InfraError, match="did not become ready"):
            await rig.start(settings=fake_settings(ready_timeout_s=2.0))
    assert len(rig.control.deletes) == 1
    assert "delete refused" in caplog.text


async def _until(condition) -> None:
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


async def test_cancellation_during_create_deletes_the_sandbox(rig):
    rig.control = FakeSandboxControl(block_create=True)
    task = asyncio.create_task(rig.start())
    await rig.control.create_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()  # it waits for the create it cannot stop
    rig.control.release_create()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.control.deletes == [rig.control.created[0].name]
    assert rig.shim.requests == []


async def test_cancellation_waits_for_create_at_most_the_grace_period(
    rig, monkeypatch, caplog
):
    monkeypatch.setattr(agent_runtime, "CREATE_GRACE_S", 0.05)
    rig.control = FakeSandboxControl(block_create=True)
    task = asyncio.create_task(rig.start())
    await rig.control.create_started.wait()
    task.cancel()
    with caplog.at_level(logging.WARNING, logger=agent_runtime.__name__):
        with pytest.raises(asyncio.CancelledError):
            await task
    assert rig.control.deletes == []  # the create has not finished yet
    assert "TTL" in caplog.text
    rig.control.release_create()  # it finishes later: its sandbox is deleted then
    await _until(lambda: rig.control.deletes)
    assert rig.control.deletes == [rig.control.created[0].name]


async def test_a_second_cancellation_during_the_grace_wait_is_a_cancellation(rig):
    rig.control = FakeSandboxControl(block_create=True)
    task = asyncio.create_task(rig.start())
    await rig.control.create_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()  # waiting for the create
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.control.deletes == []
    rig.control.release_create()
    await _until(lambda: rig.control.deletes)
    assert rig.control.deletes == [rig.control.created[0].name]


async def test_a_create_that_never_finishes_is_bounded(rig, monkeypatch):
    monkeypatch.setattr(agent_runtime, "CREATE_TIMEOUT_S", 0.05)
    rig.control = FakeSandboxControl(block_create=True)
    with pytest.raises(InfraError, match=r"^sandbox create did not finish in 0.05 s$"):
        await rig.start()
    assert rig.control.deletes == []
    rig.control.release_create()  # a late create's sandbox is still deleted
    await _until(lambda: rig.control.deletes)
    assert rig.control.deletes == [rig.control.created[0].name]


async def test_an_unusable_address_deletes_the_sandbox(rig):
    rig.control = FakeSandboxControl(hostname="lb.example.test:not-a-port")
    with pytest.raises(InfraError, match="cannot connect"):
        await rig.start()
    assert rig.control.deletes == [rig.control.created[0].name]
    assert rig.shim.requests == []


async def test_a_failing_create_deletes_nothing(rig):
    rig.control.fail("create", InfraError("quota"))
    with pytest.raises(InfraError, match="quota"):
        await rig.start()
    assert rig.control.deletes == []


async def test_cancellation_during_readiness_deletes_the_sandbox(rig):
    rig.shim.queue("/exec", BAD_GATEWAY, repeat_last=True)
    task = asyncio.create_task(rig.start())
    await _until(lambda: len(rig.shim.requests) >= 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.control.deletes == [rig.control.created[0].name]


# --- exec -----------------------------------------------------------------------


async def test_exec_wraps_the_command_in_timeout_and_quotes_it(rig):
    env = await rig.start()
    command = "echo 'it''s' \"$HOME\" && ls | wc -l; exit 3"
    result = await env.exec(command, timeout=7.2, cwd="/workspace/repo/sub")
    assert rig.bodies()[-1] == {
        "command": "timeout -k 5 8 sh -c " + shlex.quote(command),
        "cwd": "/workspace/repo/sub",
        "timeout": 23,
    }
    assert rig.shim.exec_calls[-1] == (command, "/workspace/repo/sub")
    assert result == ExecResult(exit_code=0, stdout="", stderr="")


async def test_exec_defaults_to_the_repo_and_120_s(rig):
    env = await rig.start()
    await env.exec("pytest -q")
    assert rig.bodies()[-1] == {
        "command": "timeout -k 5 120 sh -c 'pytest -q'",
        "cwd": "/workspace/repo",
        "timeout": 135,
    }


@pytest.mark.parametrize(
    ("timeout", "seconds"), [(0.2, 1), (1, 1), (1.2, 2), (120.0, 120)]
)
async def test_exec_sets_the_shim_backstop_and_http_timeout(rig, timeout, seconds):
    env = await rig.start()
    await env.exec("true", timeout=timeout)
    request = rig.shim.requests[-1]
    body = json.loads(request.content)
    assert body["command"].startswith(f"timeout -k 5 {seconds} sh -c ")
    assert body["timeout"] == seconds + 15
    assert request.extensions["timeout"]["read"] == seconds + 30


@pytest.mark.parametrize(
    ("exit_code", "shim_timed_out", "timed_out"),
    [
        (0, False, False),
        (1, False, False),
        (124, False, True),
        (137, False, True),
        (-1, True, True),
    ],
)
async def test_exec_reports_timeout_exit_codes(
    rig, exit_code, shim_timed_out, timed_out
):
    env = await rig.start()
    rig.shim.queue(
        "/exec",
        {
            "exit_code": exit_code,
            "stdout": "out",
            "stderr": "err",
            "timed_out": shim_timed_out,
        },
    )
    result = await env.exec("sleep 99", timeout=1)
    assert result == ExecResult(
        exit_code=exit_code, stdout="out", stderr="err", timed_out=timed_out
    )


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, json={"detail": "boom"}),
        BAD_GATEWAY,
        httpx.Response(404, json={"detail": "Not Found"}),
        httpx.Response(422, json={"detail": []}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"exit_code": "x"}),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
    ],
)
async def test_exec_non_200_and_transport_errors_are_infra_errors(rig, answer):
    env = await rig.start()
    rig.shim.queue("/exec", answer)
    with pytest.raises(InfraError):
        await env.exec("make test")


async def test_exec_is_not_retried(rig):
    env = await rig.start()
    sent = len(rig.shim.requests)
    rig.shim.queue("/exec", BAD_GATEWAY)
    with pytest.raises(InfraError, match="502"):
        await env.exec("git commit -m x")
    rig.shim.queue("/exec", httpx.ReadTimeout("timed out"))
    with pytest.raises(InfraError, match="ReadTimeout"):
        await env.exec("git commit -m x")
    assert len(rig.shim.requests) == sent + 2


# --- read_file and write_file -----------------------------------------------------

PATH = "/workspace/repo/pkg/mod.py"


async def test_read_file_decodes_with_replacement(rig):
    env = await rig.start()
    rig.shim.files[PATH] = "naïve ".encode() + b"\xff\xfe end\n"
    assert await env.read_file(PATH) == "naïve �� end\n"
    request = rig.shim.requests[-1]
    assert (request.method, request.url.path) == ("GET", "/files")
    assert request.url.params["path"] == PATH


async def test_read_file_maps_404_to_file_not_found(rig):
    env = await rig.start()
    sent = len(rig.shim.requests)
    with pytest.raises(FileNotFoundError) as caught:
        await env.read_file(PATH)
    assert str(caught.value) == PATH
    assert len(rig.shim.requests) == sent + 1


@pytest.mark.parametrize("status", [400, 403])
async def test_read_file_maps_400_and_403_to_os_error(rig, status):
    env = await rig.start()
    detail = f"[Errno 21] Is a directory: '{PATH}'"
    rig.shim.queue("/files", httpx.Response(status, json={"detail": detail}))
    with pytest.raises(OSError) as caught:
        await env.read_file(PATH)
    assert type(caught.value) is OSError
    assert str(caught.value) == detail


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500, text="Internal Server Error"),
        BAD_GATEWAY,
        httpx.Response(503, text="unavailable"),
        httpx.ConnectError("connection refused"),
    ],
)
async def test_read_file_retries_5xx_twice_then_infra_error(rig, failure):
    env = await rig.start()
    sent = len(rig.shim.requests)
    rig.shim.queue("/files", failure, failure, failure)
    with pytest.raises(InfraError):
        await env.read_file(PATH)
    assert len(rig.shim.requests) == sent + 3
    rig.shim.files[PATH] = b"ok"
    rig.shim.queue("/files", failure, failure)
    assert await env.read_file(PATH) == "ok"


@pytest.mark.parametrize(
    "answer",
    [httpx.Response(401, text="no"), httpx.Response(200, json={"content_b64": "%%"})],
)
async def test_read_file_other_answers_are_infra_errors_without_retry(rig, answer):
    env = await rig.start()
    sent = len(rig.shim.requests)
    rig.shim.queue("/files", answer)
    with pytest.raises(InfraError):
        await env.read_file(PATH)
    assert len(rig.shim.requests) == sent + 1


@pytest.mark.parametrize("status", [400, 403, 404])
async def test_write_file_sends_base64_and_maps_errors(rig, status):
    env = await rig.start()
    await env.write_file(PATH, "naïve\n")
    request = rig.shim.requests[-1]
    assert (request.method, request.url.path) == ("POST", "/files")
    assert request.url.params["path"] == PATH
    assert json.loads(request.content) == {
        "content_b64": base64.b64encode("naïve\n".encode()).decode("ascii")
    }
    assert rig.shim.files[PATH] == "naïve\n".encode()

    detail = f"[Errno 20] Not a directory: '{PATH}'"
    rig.shim.queue("/files", httpx.Response(status, json={"detail": detail}))
    with pytest.raises(OSError) as caught:
        await env.write_file(PATH, "x")
    assert type(caught.value) is OSError
    assert str(caught.value) == detail
    for failure in (BAD_GATEWAY, httpx.ConnectError("refused")):
        rig.shim.queue("/files", failure)
        with pytest.raises(InfraError):
            await env.write_file(PATH, "x")


async def test_write_file_is_not_retried(rig):
    env = await rig.start()
    sent = len(rig.shim.requests)
    rig.shim.queue("/files", httpx.Response(503, text="unavailable"))
    with pytest.raises(InfraError, match="503"):
        await env.write_file(PATH, "x")
    assert len(rig.shim.requests) == sent + 1
    assert PATH not in rig.shim.files


# --- upload_dir -----------------------------------------------------------------


def _tree(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n")
    (root / "README.md").write_text("# demo\n")
    (root / "empty").mkdir()
    return root


async def test_upload_dir_posts_a_zip_of_the_tree(rig, tmp_path):
    env = await rig.start()
    await env.upload_dir(_tree(tmp_path / "repo"), "/workspace/repo")
    request = rig.shim.requests[-1]
    assert (request.method, request.url.path) == ("POST", "/files/zip")
    assert request.url.params["path"] == "/workspace/repo"
    assert request.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(request.content)) as archive:
        assert sorted(archive.namelist()) == [
            "README.md",
            "empty/",
            "pkg/",
            "pkg/mod.py",
        ]
        assert archive.read("pkg/mod.py") == b"x = 1\n"
    assert rig.shim.files["/workspace/repo/README.md"] == b"# demo\n"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(400, json={"detail": "invalid zip"}),
        httpx.Response(403, json={"detail": "escapes workspace root"}),
        BAD_GATEWAY,
        httpx.ConnectError("refused"),
    ],
)
async def test_upload_dir_failures_are_infra_errors(rig, tmp_path, failure):
    env = await rig.start()
    rig.shim.queue("/files/zip", failure)
    with pytest.raises(InfraError):
        await env.upload_dir(_tree(tmp_path / "repo"))


@pytest.mark.parametrize("kind", ["file", "dir", "dangling", "root"])
async def test_upload_dir_refuses_links(rig, tmp_path, kind):
    env = await rig.start()
    root = _tree(tmp_path / "repo")
    if kind == "file":
        (root / "pkg" / "link.py").symlink_to(root / "pkg" / "mod.py")
    elif kind == "dir":
        (root / "linked").symlink_to(tmp_path)
    elif kind == "dangling":
        (root / "gone").symlink_to(tmp_path / "missing")
    else:
        (tmp_path / "alias").symlink_to(root)
        root = tmp_path / "alias"
    sent = len(rig.shim.requests)
    with pytest.raises(InfraError) as caught:
        await env.upload_dir(root)
    assert str(caught.value) == "upload refused: links are not supported"
    assert len(rig.shim.requests) == sent


async def test_upload_dir_needs_a_directory(rig, tmp_path):
    env = await rig.start()
    with pytest.raises(InfraError):
        await env.upload_dir(tmp_path / "missing")


# --- headers, proxy_request -------------------------------------------------------


async def test_every_request_carries_the_three_headers(rig, tmp_path):
    env = await rig.start()
    await env.exec("ls")
    await env.write_file(PATH, "a")
    await env.read_file(PATH)
    await env.upload_dir(_tree(tmp_path / "repo"))
    await env.proxy_request("GET", "/healthz")
    handle = rig.control.created[0]
    assert len(rig.shim.requests) == 7
    for request in rig.shim.requests:
        assert request.url.scheme == "https"
        assert request.url.host == HOSTNAME == handle.hostname
        assert request.headers["authorization"] == f"Bearer {SANDBOX_TOKEN}"
        assert request.headers["x-sandbox-routing-token"] == handle.routing_token
        assert request.headers["x-sandbox-port"] == SANDBOX_PORT == "8080"


async def test_proxy_request_uses_the_given_port(rig):
    env = await rig.start()
    response = await env.proxy_request(
        "POST", "/echo", port="8081", json={"a": 1}, headers={"X-Probe": "1"}
    )
    assert response.status_code == 404  # the fake shim has no /echo
    request = rig.shim.requests[-1]
    assert request.headers["x-sandbox-port"] == "8081"
    assert request.headers["x-probe"] == "1"
    assert request.headers["authorization"] == f"Bearer {SANDBOX_TOKEN}"
    assert json.loads(request.content) == {"a": 1}
    rig.shim.queue("/echo", httpx.ConnectError("refused"))
    with pytest.raises(InfraError):
        await env.proxy_request("GET", "/echo", port="8081")


# --- close ----------------------------------------------------------------------


async def test_close_deletes_once_and_is_idempotent(rig):
    env = await rig.start()
    await env.close()
    await env.close()
    assert rig.control.deletes == [rig.control.created[0].name]
    assert env._http.is_closed


async def test_close_treats_not_found_as_success(rig):
    client = FakeSdkClient()
    client.sandboxes.errors["delete"] = api_error(404, "NOT_FOUND")
    env = await rig.start(control=SdkSandboxControl(fake_settings(), client=client))
    await env.close()
    assert [m for m, _ in client.sandboxes.calls if m == "delete"] == ["delete"]


async def test_close_raises_a_failing_delete(rig):
    env = await rig.start()
    rig.control.fail("delete", InfraError("delete refused"))
    with pytest.raises(InfraError, match="delete refused"):
        await env.close()
    await env.close()  # deleted once: the release paths log the failure
    assert len(rig.control.deletes) == 1
    assert env._http.is_closed


async def test_calls_after_close_raise_infra_error(rig, tmp_path):
    env = await rig.start()
    await env.close()
    sent = len(rig.shim.requests)
    calls = [
        env.exec("ls"),
        env.read_file(PATH),
        env.write_file(PATH, "x"),
        env.upload_dir(_tree(tmp_path / "repo")),
        env.proxy_request("GET", "/"),
    ]
    for call in calls:
        with pytest.raises(InfraError, match="is closed"):
            await call
    assert len(rig.shim.requests) == sent


# --- what the object shows --------------------------------------------------------


async def test_env_id_is_short_and_hides_the_resource_name(rig):
    env = await rig.start()
    handle = rig.control.created[0]
    assert re.fullmatch(r"itp-[0-9a-f]{12}", env.env_id)
    assert rig.control.creates[0]["display_name"] == env.env_id
    assert repr(env) == f"AgentRuntimeEnvironment(env_id={env.env_id!r})"
    assert str(env) == repr(env)
    for secret in (handle.name, handle.routing_token, HOSTNAME, SANDBOX_TOKEN):
        assert secret not in env.env_id
        assert secret not in repr(env)
    assert handle.routing_token not in repr(handle)
    other = await rig.start()
    assert other.env_id != env.env_id


async def _failures(rig: Rig, env: AgentRuntimeEnvironment, tmp_path: Path) -> list:
    """Make every operation fail once, in each way it can; return the errors."""
    body = "B" * 1000
    errors: list[BaseException] = []
    rig.shim.queue(
        "/exec", httpx.Response(500, text=body), httpx.ConnectError("refused")
    )
    rig.shim.queue(
        "/files",
        *[httpx.Response(502, text=body)] * 3,
        httpx.Response(403, json={"detail": "denied"}),
        httpx.Response(500, text=body),
        httpx.ReadTimeout("timed out"),
    )
    rig.shim.queue("/files/zip", httpx.Response(400, text=body))
    operations = [
        env.exec("ls"),
        env.exec("ls"),
        env.read_file(PATH),
        env.read_file(PATH),
        env.write_file(PATH, "x"),
        env.write_file(PATH, "x"),
        env.upload_dir(_tree(tmp_path / "repo")),
    ]
    for operation in operations:
        with pytest.raises((InfraError, OSError)) as caught:
            await operation
        errors.append(caught.value)
    return errors


async def test_error_text_never_holds_headers(rig, tmp_path):
    env = await rig.start()
    errors = await _failures(rig, env, tmp_path)
    handle = rig.control.created[0]
    texts = [str(error) for error in errors]
    for text in texts:
        assert SANDBOX_TOKEN not in text
        assert handle.routing_token not in text
        assert "Bearer" not in text
        assert "X-Sandbox" not in text and "x-sandbox" not in text
        assert "B" * 201 not in text  # at most 200 characters of the body
    assert texts[0] == f"sandbox {env.env_id}: POST /exec returned HTTP 500: " + (
        "B" * 200
    )
    assert "POST /exec" in texts[1] and "ConnectError" in texts[1]
    assert f"GET /files?path={PATH}" in texts[2] and "502" in texts[2]
    assert texts[3] == "denied"
    assert f"POST /files?path={PATH}" in texts[4] and "500" in texts[4]
    assert "POST /files/zip?path=/workspace/repo" in texts[6] and "400" in texts[6]


async def test_token_canary_in_the_backend(rig, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    env = await rig.start()
    results = [await env.exec("echo hi")]
    rig.shim.queue("/exec", {"exit_code": 124, "stdout": "o", "stderr": "e"})
    results.append(await env.exec("sleep 9", timeout=1))
    await env.write_file(PATH, "content")
    await env.read_file(PATH)
    errors = await _failures(rig, env, tmp_path)
    rig.control.fail("delete", InfraError("delete refused"))
    with pytest.raises(InfraError) as caught:
        await env.close()
    errors.append(caught.value)
    rig.shim.queue("/exec", BAD_GATEWAY, repeat_last=True)
    with pytest.raises(InfraError) as caught:
        await rig.start(settings=fake_settings(ready_timeout_s=2.0))
    errors.append(caught.value)

    in_headers = [r.headers["authorization"] for r in rig.shim.requests]
    assert in_headers and all(h == f"Bearer {SANDBOX_TOKEN}" for h in in_headers)
    for request in rig.shim.requests:  # headers only: never in a URL or a body
        assert SANDBOX_TOKEN not in str(request.url)
        assert SANDBOX_TOKEN.encode() not in request.content
    for error in errors:
        assert SANDBOX_TOKEN not in str(error) and SANDBOX_TOKEN not in repr(error)
    for result in results:
        assert SANDBOX_TOKEN not in result.model_dump_json()
    assert caplog.records
    for record in caplog.records:
        assert SANDBOX_TOKEN not in record.getMessage()
    assert SANDBOX_TOKEN not in caplog.text
    assert all(SANDBOX_TOKEN not in value for value in os.environ.values())
    assert SANDBOX_TOKEN not in repr(env) and SANDBOX_TOKEN not in str(env)
    assert SANDBOX_TOKEN not in repr(vars(env).get("_handle"))
    plain = [v for k, v in vars(env).items() if k != "_http"]
    assert all(SANDBOX_TOKEN not in str(value) for value in plain)


# --- SdkSandboxControl, on a fake SDK client --------------------------------------


@pytest.fixture
def sdk() -> FakeSdkClient:
    return FakeSdkClient()


@pytest.fixture
def control(sdk) -> SdkSandboxControl:
    return SdkSandboxControl(fake_settings(), client=sdk)


async def test_sdk_control_creates_from_the_template_and_maps_the_handle(sdk, control):
    handle = await control.create(
        engine=ENGINE, template=TEMPLATE, ttl_s=1800, display_name="itp-0123456789ab"
    )
    assert sdk.sandboxes.calls == [
        (
            "create",
            {
                "name": ENGINE,
                "poll_interval_seconds": 1.0,
                "config": {
                    "sandbox_environment_template": TEMPLATE,
                    "ttl": "1800s",
                    "owner": "issue-to-pr",
                    "display_name": "itp-0123456789ab",
                },
            },
        )
    ]
    assert (handle.name, handle.hostname, handle.routing_token) == (
        sdk.sandboxes.sandbox.name,
        HOSTNAME,
        "routing-31",
    )


@pytest.mark.parametrize(
    "info",
    [
        None,
        SimpleNamespace(load_balancer_hostname=None, routing_token="r"),
        SimpleNamespace(load_balancer_hostname="h", routing_token=""),
    ],
)
async def test_sdk_control_create_without_connection_info_deletes_and_raises(
    sdk, control, info
):
    sdk.sandboxes.sandbox.connection_info = info
    with pytest.raises(InfraError, match="no connection info"):
        await control.create(
            engine=ENGINE, template=TEMPLATE, ttl_s=60, display_name="itp-x"
        )
    assert sdk.sandboxes.calls[-1] == ("delete", {"name": sdk.sandboxes.sandbox.name})


async def test_sdk_control_signs_with_the_caller_sa_and_lifetime(sdk, control):
    assert await control.sign_token(CALLER_SA, 1800) == SANDBOX_TOKEN
    assert sdk.sandboxes.calls == [
        (
            "generate_access_token",
            {"service_account_email": CALLER_SA, "timeout": 1800},
        )
    ]


class TemplateState(str, enum.Enum):  # noqa: UP042 - the SDK's enum shape
    """An enum-typed state, as the SDK types a sandbox's (`SandboxState`): its str()
    carries the class prefix, its value does not. Template states are plain strings
    in the installed SDK; the check reads either."""

    ACTIVE = "ACTIVE"


@pytest.mark.parametrize(
    ("state", "egress", "usable"),
    [
        ("ACTIVE", SimpleNamespace(internet_access=False), True),
        ("ACTIVE", SimpleNamespace(internet_access=None), True),
        ("ACTIVE", None, True),
        (TemplateState.ACTIVE, None, True),
        ("ACTIVE", SimpleNamespace(internet_access=True), False),
        ("PROVISIONING", None, False),
        ("DEPROVISIONING", None, False),
        ("FAILED", None, False),
        ("DELETED", None, False),
        ("UNSPECIFIED", None, False),
        (None, None, False),
    ],
)
async def test_sdk_control_template_must_be_active_without_internet(
    sdk, control, state, egress, usable
):
    sdk.sandboxes.templates.template = SimpleNamespace(
        state=state, egress_control_config=egress
    )
    if usable:
        await control.check_template(TEMPLATE)
    else:
        with pytest.raises(InfraError, match=r"^sandbox template is not usable$"):
            await control.check_template(TEMPLATE)
    assert sdk.sandboxes.calls == [("templates.get", {"name": TEMPLATE})]


async def test_sdk_control_missing_template_is_not_usable(sdk, control):
    sdk.sandboxes.templates.error = api_error(404, "NOT_FOUND")
    with pytest.raises(InfraError, match=r"^sandbox template is not usable$"):
        await control.check_template(TEMPLATE)
    sdk.sandboxes.templates.error = api_error(503, "UNAVAILABLE")
    with pytest.raises(InfraError, match=r"template check failed.*503"):
        await control.check_template(TEMPLATE)


async def test_sdk_control_delete_treats_not_found_as_success(sdk, control):
    await control.delete("projects/p/sandboxEnvironments/1")
    sdk.sandboxes.errors["delete"] = api_error(404, "NOT_FOUND")
    await control.delete("projects/p/sandboxEnvironments/1")
    sdk.sandboxes.errors["delete"] = api_error(403, "PERMISSION_DENIED")
    with pytest.raises(InfraError, match=r"delete failed.*403"):
        await control.delete("projects/p/sandboxEnvironments/1")


@pytest.mark.parametrize("method", ["create", "generate_access_token"])
@pytest.mark.parametrize(
    "error", [api_error(429, "RESOURCE_EXHAUSTED"), RuntimeError("auth")]
)
async def test_sdk_control_errors_are_infra_errors(sdk, control, method, error):
    sdk.sandboxes.errors[method] = error
    with pytest.raises(InfraError):
        if method == "create":
            await control.create(
                engine=ENGINE, template=TEMPLATE, ttl_s=60, display_name="itp-x"
            )
        else:
            await control.sign_token(CALLER_SA, 60)


async def test_sdk_control_builds_the_client_for_the_engine_location(monkeypatch):
    built: list[dict] = []

    def client(**kwargs):
        built.append(kwargs)
        return FakeSdkClient()

    monkeypatch.setitem(sys.modules, "agentplatform", SimpleNamespace(Client=client))
    control = SdkSandboxControl(fake_settings(location="europe-west4"))
    assert built == []  # built on first use, in the worker thread
    await control.sign_token(CALLER_SA, 60)
    await control.delete("projects/p/sandboxEnvironments/1")
    assert built == [
        {
            "project": PROJECT,
            "location": "europe-west4",
            "http_options": {"api_version": "v1beta1"},
        }
    ]


def test_sdk_control_is_cached_per_settings():
    first = sdk_control(fake_settings())
    assert sdk_control(fake_settings()) is first
    assert sdk_control(fake_settings(ttl_s=60)) is not first


def test_agentplatform_is_imported_only_inside_the_sdk_adapter():
    tree = ast.parse(Path(agent_runtime.__file__).read_text())
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }

    def modules(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom):
            return [node.module or ""]
        return []

    imports = [
        node
        for node in ast.walk(tree)
        if any(name.split(".")[0] == "agentplatform" for name in modules(node))
    ]
    assert imports
    for node in imports:
        owner = node
        while owner in parents and not (
            isinstance(owner, ast.ClassDef) and owner.name == "SdkSandboxControl"
        ):
            owner = parents[owner]
        assert isinstance(owner, ast.ClassDef), ast.unparse(node)


async def test_a_whole_sandbox_life_never_imports_agentplatform(
    rig, tmp_path, monkeypatch
):
    monkeypatch.setitem(sys.modules, "agentplatform", None)  # importing it fails
    env = await rig.start(control=SdkSandboxControl(fake_settings(), FakeSdkClient()))
    await env.exec("ls")
    await env.write_file(PATH, "x")
    await env.read_file(PATH)
    await env.upload_dir(_tree(tmp_path / "repo"))
    await env.close()
    other = await rig.start()
    await other.close()


# --- fix round 1: bounded deletes, redacted errors, edges -------------------------


@pytest.fixture
def hanging_delete(sdk, monkeypatch) -> threading.Event:
    """The SDK's delete blocks its thread (a stalled connection) until the returned
    event is set. Each test sets it before it ends: the event loop's teardown joins
    the worker thread."""
    monkeypatch.setattr(agent_runtime, "DELETE_TIMEOUT_S", 0.05)
    gate = sdk.sandboxes.delete_gate = threading.Event()
    yield gate
    gate.set()


async def test_sdk_control_delete_is_bounded(hanging_delete, control):
    try:
        with pytest.raises(InfraError, match=r"^sandbox delete timed out$"):
            await control.delete(f"{ENGINE}/sandboxEnvironments/1")
    finally:
        hanging_delete.set()


async def test_close_is_bounded_when_the_delete_hangs(rig, hanging_delete, control):
    try:
        env = await rig.start(control=control)
        with pytest.raises(InfraError, match=r"^sandbox delete timed out$"):
            await env.close()
        assert env._http.is_closed
        await env.close()  # once only
    finally:
        hanging_delete.set()


async def test_a_hanging_delete_after_a_failed_start_is_bounded(
    rig, hanging_delete, control, caplog
):
    rig.shim.queue("/exec", BAD_GATEWAY, repeat_last=True)
    try:
        with caplog.at_level(logging.WARNING, logger=agent_runtime.__name__):
            with pytest.raises(InfraError, match="did not become ready"):
                await rig.start(
                    settings=fake_settings(ready_timeout_s=2.0), control=control
                )
        assert "sandbox delete timed out" in caplog.text
    finally:
        hanging_delete.set()


REALISTIC_ERRORS = [
    api_error(
        403,
        "PERMISSION_DENIED",
        "Permission 'aiplatform.sandboxEnvironments.delete' denied on resource "
        "'//aiplatform.googleapis.com/projects/123456789012/locations/us-central1/"
        "reasoningEngines/4242/sandboxEnvironments/9001' (or it may not exist).",
    ),
    api_error(
        429,
        "RESOURCE_EXHAUSTED",
        "Quota exceeded for quota metric 'Sandbox create requests' and limit "
        "'per minute' of service 'aiplatform.googleapis.com' for consumer "
        "'project_number:123456789012'.",
    ),
    api_error(
        400,
        "FAILED_PRECONDITION",
        f"Template {TEMPLATE} is not ready; caller {CALLER_SA} may not use it.",
    ),
]


def _http_error(status: int) -> requests.HTTPError:
    """What `generate_access_token` raises: requests' `raise_for_status()`."""
    response = requests.Response()
    response.status_code = status
    response.reason = "Forbidden"
    response.url = (
        "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
        f"{CALLER_SA}:signJwt"
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        return exc
    raise AssertionError("no error raised")


@pytest.mark.parametrize("error", [*REALISTIC_ERRORS, _http_error(403)])
async def test_control_errors_carry_no_project_number_or_service_account(
    sdk, control, error
):
    sdk.sandboxes.errors["create"] = error
    sdk.sandboxes.errors["generate_access_token"] = error
    with pytest.raises(InfraError) as created:
        await control.create(
            engine=ENGINE, template=TEMPLATE, ttl_s=60, display_name="itp-x"
        )
    with pytest.raises(InfraError) as signed:
        await control.sign_token(CALLER_SA, 60)
    for caught, action in ((created, "create"), (signed, "token signing")):
        text = str(caught.value)
        assert text.startswith(f"sandbox {action} failed: {type(error).__name__} ")
        assert str(getattr(error, "code", 403)) in text
        # redact() keeps the path after the project segment (the engine and
        # template ids of these fakes are short; real ones are long numbers)
        for secret in ("123456789012", "demo-project", "sandbox-caller", "for url"):
            assert secret not in text
        assert "projects/" not in text.replace("projects/<project>", "")
        assert caught.value.__cause__ is None
        if getattr(error, "status", None):
            assert error.status in text


@pytest.mark.parametrize("status", [401, 403, 502])
async def test_readiness_expiry_logs_the_last_answer(rig, caplog, status):
    rig.shim.queue("/exec", httpx.Response(status, text="denied"), repeat_last=True)
    with caplog.at_level(logging.WARNING, logger=agent_runtime.__name__):
        with pytest.raises(InfraError) as caught:
            await rig.start(settings=fake_settings(ready_timeout_s=4.0))
    assert str(caught.value) == "sandbox did not become ready in 4 s"
    assert f"HTTP {status}" in caplog.text
    assert SANDBOX_TOKEN not in caplog.text


async def test_readiness_attempts_are_bounded_by_the_time_left(rig, monkeypatch):
    bounds: list[float] = []
    real_wait_for = asyncio.wait_for

    async def recording_wait_for(awaitable, timeout):
        bounds.append(timeout)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(agent_runtime.asyncio, "wait_for", recording_wait_for)
    rig.shim.queue("/exec", BAD_GATEWAY, repeat_last=True)
    with pytest.raises(InfraError):
        await rig.start(settings=fake_settings(ready_timeout_s=20.0))
    attempts = [b for b in bounds if b <= agent_runtime.READY_ATTEMPT_TIMEOUT_S]
    assert len(attempts) == len(rig.shim.requests) == 11  # at 0, 2, ..., 20 s
    assert attempts[0] == 15.0  # READY_ATTEMPT_TIMEOUT_S while time is left
    assert attempts[-2:] == [2.0, 1.0]  # then what is left, at least 1 s
    assert rig.clock.now == 20.0


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404, text="<html>Not Found</html>"),
        httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND"}}),
        httpx.Response(403, text="Forbidden"),
        httpx.Response(400, json={"detail": ["not", "a", "string"]}),
    ],
)
async def test_a_proxy_answer_is_not_a_file_error(rig, answer):
    env = await rig.start()
    sent = len(rig.shim.requests)
    rig.shim.queue("/files", answer, answer)
    with pytest.raises(InfraError):
        await env.read_file(PATH)
    with pytest.raises(InfraError):
        await env.write_file(PATH, "x")
    assert len(rig.shim.requests) == sent + 2  # neither is retried


async def test_upload_dir_refuses_an_unreadable_directory(rig, tmp_path):
    env = await rig.start()
    root = _tree(tmp_path / "repo")
    locked = root / "pkg"
    locked.chmod(0)
    try:
        sent = len(rig.shim.requests)
        with pytest.raises(InfraError, match="upload failed"):
            await env.upload_dir(root)
        assert len(rig.shim.requests) == sent
    finally:
        locked.chmod(0o755)


async def test_upload_dir_turns_a_name_it_cannot_encode_into_an_infra_error(
    rig, tmp_path, monkeypatch
):
    env = await rig.start()

    def write(self, filename, arcname=None, *args, **kwargs):
        raise UnicodeEncodeError("utf-8", "\udcff", 0, 1, "surrogates not allowed")

    monkeypatch.setattr(zipfile.ZipFile, "write", write)
    with pytest.raises(InfraError, match="upload failed"):
        await env.upload_dir(_tree(tmp_path / "repo"))


# --- final fix wave: one redact() on every cloud error path -----------------------

LEAKY_PROJECT = "my-demo-proj"
# Each body is short, so its redacted form fits in the 200 characters an error keeps.
LEAKY_BODIES = [
    "denied on projects/123456789012/locations/us-central1/reasoningEngines/4242 "
    "port 8080",
    "caller sandbox-caller@my-demo-proj.iam.gserviceaccount.com port 8080",
    "agent service-123456789012@gcp-sa-aiplatform.iam.gserviceaccount.com port 8080",
    "compute 123456789012-compute@developer.gserviceaccount.com port 8080",
    "build 987654321@cloudservices.gserviceaccount.com port 8080",
    "bucket gs://My-Demo-Proj_cloudbuild in my-demo-proj port 8080",
    "consumer project_number:123456789012 port 8080",
]


def _assert_redacted(error: BaseException) -> None:
    text = str(error)
    for secret in ("123456789012", "987654321", "@", "gserviceaccount"):
        assert secret not in text, secret
    assert LEAKY_PROJECT not in text.lower()
    assert "8080" in text  # a port is not a project number
    assert re.search(r"<(project|number|service-account)>", text)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_redact_removes_project_paths_numbers_service_accounts_and_the_id(
    monkeypatch,
):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", LEAKY_PROJECT)
    redact = agent_runtime.redact
    assert redact("x projects/123456789012/locations/l y") == (
        "x projects/<project>/locations/l y"
    )
    assert redact("number 123456789 and 12345678, HTTP 403") == (
        "number <number> and 12345678, HTTP 403"
    )
    for email in (
        "a@p.iam.gserviceaccount.com",
        "1-compute@developer.gserviceaccount.com",
        "2@cloudservices.gserviceaccount.com",
        "service-3@gcp-sa-aiplatform.iam.gserviceaccount.com",
    ):
        assert redact(f"by {email}.") == "by <service-account>."
    assert redact("MY-DEMO-PROJ, my-demo-proj_cloudbuild, my-demo-project") == (
        "<project>, <project>_cloudbuild, my-demo-project"
    )
    assert redact("403 Forbidden for url: https://x/projects/1") == "403 Forbidden"


def test_redact_leaves_names_alone_without_a_project_setting(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    assert agent_runtime.redact("my-demo-proj on port 8080") == (
        "my-demo-proj on port 8080"
    )


@pytest.mark.parametrize("body", LEAKY_BODIES)
async def test_data_plane_errors_carry_provider_text_only_redacted(
    rig, monkeypatch, body
):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", LEAKY_PROJECT)
    env = await rig.start()
    caught: list[InfraError] = []
    answers = {
        "status": httpx.Response(403, text=body),
        "unreadable body": httpx.Response(200, text=body),
        "transport": httpx.ConnectError(body),
    }
    for kind, answer in answers.items():
        rig.shim.queue("/exec", answer)
        with pytest.raises(InfraError) as raised:
            await env.exec("true")
        caught.append(raised.value)
        if kind == "status":
            assert "HTTP 403" in str(raised.value)
        if kind == "transport":
            assert "ConnectError" in str(raised.value)
    rig.shim.queue("/files", httpx.Response(403, text=body))
    with pytest.raises(InfraError) as raised:
        await env.read_file(PATH)
    assert "HTTP 403" in str(raised.value)
    caught.append(raised.value)
    rig.shim.queue("/files", httpx.Response(200, text=body))
    with pytest.raises(InfraError) as raised:
        await env.read_file(PATH)
    caught.append(raised.value)
    rig.shim.queue("/files", *[httpx.ConnectError(body)] * 3)
    with pytest.raises(InfraError) as raised:
        await env.read_file(PATH)
    caught.append(raised.value)
    for error in caught:
        _assert_redacted(error)


@pytest.mark.parametrize("body", LEAKY_BODIES)
async def test_control_plane_errors_carry_provider_text_only_redacted(
    sdk, control, monkeypatch, body
):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", LEAKY_PROJECT)
    error = api_error(403, "PERMISSION_DENIED", body)
    for method in ("create", "generate_access_token", "delete"):
        sdk.sandboxes.errors[method] = error
    sdk.sandboxes.templates.error = error
    calls = {
        "create": lambda: control.create(
            engine=ENGINE, template=TEMPLATE, ttl_s=60, display_name="itp-x"
        ),
        "token signing": lambda: control.sign_token(CALLER_SA, 60),
        "template check": lambda: control.check_template(TEMPLATE),
        "delete": lambda: control.delete(f"{ENGINE}/sandboxEnvironments/1"),
    }
    for action, call in calls.items():
        with pytest.raises(InfraError) as raised:
            await call()
        text = str(raised.value)
        assert text.startswith(
            f"sandbox {action} failed: ClientError 403 PERMISSION_DENIED: "
        )
        _assert_redacted(raised.value)
