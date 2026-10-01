"""Fakes for the Agent Runtime sandbox backend (`app/environment/agent_runtime.py`).
Nothing here talks to GCP or imports `agentplatform`.

- `FakeSandboxControl`: the control plane (`SandboxControl`): template check, token,
  create, delete. Records every call; failures can be injected per call; `create`
  can be held until released, for cancellation tests; signs `SANDBOX_TOKEN`.
- `FakeShim`: the data plane, an `httpx.MockTransport` handler that answers like the
  shim (`sandbox_image/runtime/server.py`) on `/exec`, `/files` and `/files/zip`,
  with queued responses per path and every request recorded with its headers.
  `exec_responder(command, cwd)` answers `/exec` with the command unwrapped from
  the backend's `timeout -k 5 <n> sh -c '<command>'` wrapper.
- `FakeSdkClient`: the slice of `agentplatform.Client` that `SdkSandboxControl`
  calls, for the adapter's own tests.
- `FakeClock`, `fake_settings`, `start_on_fakes`: start an `AgentRuntimeEnvironment`
  on the fakes with no real waiting.
- `reset_template_checks()`: forgets which templates this process has checked. Use
  it in an autouse fixture in every module that starts the cloud backend.
"""

import asyncio
import base64
import dataclasses
import enum
import inspect
import io
import json
import shlex
import threading
import zipfile
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import httpx
from google.genai import errors as genai_errors

from app.environment import agent_runtime
from app.environment.agent_runtime import (
    AgentRuntimeEnvironment,
    SandboxHandle,
    SandboxSettings,
)
from app.environment.base import ExecResult

ENGINE = "projects/123456789012/locations/us-central1/reasoningEngines/4242"
TEMPLATE = f"{ENGINE}/sandboxEnvironmentTemplates/77"
CALLER_SA = "sandbox-caller@demo-project.iam.gserviceaccount.com"
PROJECT = "demo-project"
HOSTNAME = "sandbox-lb.example.test"
# The canary: the one sandbox credential. It may appear in request headers only.
SANDBOX_TOKEN = "canary-sandbox-jwt-5b0e1c9d7a"
CLOUD_ENV = {
    "SANDBOX_ENGINE": ENGINE,
    "SANDBOX_TEMPLATE": TEMPLATE,
    "SANDBOX_CALLER_SA": CALLER_SA,
    "GOOGLE_CLOUD_PROJECT": PROJECT,
}

ExecResponder = Callable[[str, str | None], ExecResult | Awaitable[ExecResult]]


def reset_template_checks() -> None:
    """Forget the per-process template check, so each test starts from scratch."""
    agent_runtime._TEMPLATES_CHECKED.clear()


def fake_settings(**overrides: Any) -> SandboxSettings:
    """Valid settings for the fake engine and template (TTL 1800 s, ready 240 s)."""
    return dataclasses.replace(SandboxSettings.from_env(CLOUD_ENV), **overrides)


class FakeClock:
    """An injected clock and sleep: sleeping advances the clock at once."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)  # a real suspension point, so a cancel can land here


class FakeSandboxControl:
    """The control plane. `calls` holds the method names in order; `checks`,
    `signs`, `creates` and `deletes` their arguments (a failed call is recorded
    too). `fail(method, *errors)` makes the next calls of `method` raise, one error
    per call. With `block_create=True`, `create` waits for `release_create()`
    after setting `create_started`."""

    def __init__(
        self,
        *,
        token: str = SANDBOX_TOKEN,
        hostname: str = HOSTNAME,
        block_create: bool = False,
    ) -> None:
        self.token = token
        self.hostname = hostname
        self.calls: list[str] = []
        self.checks: list[str] = []
        self.signs: list[tuple[str, int]] = []
        self.creates: list[dict[str, Any]] = []
        self.created: list[SandboxHandle] = []
        self.deletes: list[str] = []
        self.create_started = asyncio.Event()
        self._create_gate = asyncio.Event()
        if not block_create:
            self._create_gate.set()
        self._failures: dict[str, list[BaseException]] = {}

    def fail(self, method: str, *errors: BaseException) -> None:
        self._failures.setdefault(method, []).extend(errors)

    def release_create(self) -> None:
        self._create_gate.set()

    def _maybe_fail(self, method: str) -> None:
        queued = self._failures.get(method)
        if queued:
            raise queued.pop(0)

    async def check_template(self, template: str) -> None:
        self.calls.append("check_template")
        self.checks.append(template)
        self._maybe_fail("check_template")

    async def sign_token(self, service_account: str, lifetime_s: int) -> str:
        self.calls.append("sign_token")
        self.signs.append((service_account, lifetime_s))
        self._maybe_fail("sign_token")
        return self.token

    async def create(
        self, *, engine: str, template: str, ttl_s: int, display_name: str
    ) -> SandboxHandle:
        self.calls.append("create")
        self.creates.append(
            {
                "engine": engine,
                "template": template,
                "ttl_s": ttl_s,
                "display_name": display_name,
            }
        )
        self.create_started.set()
        await self._create_gate.wait()
        self._maybe_fail("create")
        number = 9000 + len(self.created) + 1
        handle = SandboxHandle(
            name=f"{engine}/sandboxEnvironments/{number}",
            hostname=self.hostname,
            routing_token=f"routing-{number}",
        )
        self.created.append(handle)
        return handle

    async def delete(self, name: str) -> None:
        self.calls.append("delete")
        self.deletes.append(name)
        self._maybe_fail("delete")


def unwrap(command: str) -> str:
    """The command inside `timeout -k 5 <n> sh -c '<command>'`; other commands
    (the readiness probe's bare `true`) are returned as they are."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    if (
        len(words) == 7
        and words[:3] == ["timeout", "-k", "5"]
        and words[4:6]
        == [
            "sh",
            "-c",
        ]
    ):
        return words[6]
    return command


def _ok(command: str, cwd: str | None) -> ExecResult:
    return ExecResult(exit_code=0, stdout="", stderr="")


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


class FakeShim:
    """The shim behind the platform's proxy, as an `httpx.MockTransport` handler.

    `requests` holds every request (with its headers and body). `exec_calls` holds
    `(command, cwd)` per `/exec` request, the command unwrapped; `cwd` is None when
    the request has none (the readiness probe).

    Default answers: `/exec` asks `exec_responder(command, cwd)` (sync or async;
    default: exit 0, no output); `GET /files` reads `files` (404 when missing);
    `POST /files` writes it; `POST /files/zip` extracts into it under `path`; any
    other path is a 404.

    `queue(path, *items)` answers the next requests to `path` with `items` in order,
    then the defaults again (with `repeat_last=True` the last item repeats). An
    item is an `httpx.Response`, an exception (raised, as a transport error is), an
    `ExecResult`, or a dict (sent as a 200 JSON body).
    """

    def __init__(
        self,
        exec_responder: ExecResponder | None = None,
        files: dict[str, bytes] | None = None,
    ) -> None:
        self.exec_responder = exec_responder or _ok
        self.files: dict[str, bytes] = dict(files or {})
        self.requests: list[httpx.Request] = []
        self.exec_calls: list[tuple[str, str | None]] = []
        self._queues: dict[str, list[Any]] = {}
        self._repeat: set[str] = set()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def queue(self, path: str, *items: Any, repeat_last: bool = False) -> None:
        self._queues.setdefault(path, []).extend(items)
        if repeat_last:
            self._repeat.add(path)

    def requests_to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        exec_call = None
        if path == "/exec":
            body = json.loads(request.content)
            exec_call = (unwrap(body["command"]), body.get("cwd"))
            self.exec_calls.append(exec_call)
        queued = self._queues.get(path)
        if queued:
            repeat = len(queued) == 1 and path in self._repeat
            return self._answer(queued[0] if repeat else queued.pop(0))
        if exec_call is not None:
            result = self.exec_responder(*exec_call)
            if inspect.isawaitable(result):
                result = await result
            return _json(200, result.model_dump())
        return self._default(request)

    @staticmethod
    def _answer(item: Any) -> httpx.Response:
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, httpx.Response):  # a fresh copy: a response is single-use
            return httpx.Response(
                item.status_code, headers=item.headers, content=item.content
            )
        if isinstance(item, ExecResult):
            return _json(200, item.model_dump())
        return _json(200, item)

    def _default(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        target = request.url.params.get("path", "")
        if path == "/files" and request.method == "GET":
            if target not in self.files:
                detail = f"[Errno 2] No such file or directory: '{target}'"
                return _json(404, {"detail": detail})
            encoded = base64.b64encode(self.files[target]).decode("ascii")
            return _json(200, {"content_b64": encoded})
        if path == "/files" and request.method == "POST":
            body = json.loads(request.content)
            self.files[target] = base64.b64decode(body["content_b64"])
            return _json(200, {"status": "ok"})
        if path == "/files/zip" and request.method == "POST":
            with zipfile.ZipFile(io.BytesIO(request.content)) as archive:
                for member in archive.infolist():
                    if not member.is_dir():
                        name = f"{target.rstrip('/')}/{member.filename}"
                        self.files[name] = archive.read(member)
            return _json(200, {"status": "ok"})
        return _json(404, {"detail": "Not Found"})


async def start_on_fakes(
    control: FakeSandboxControl | None = None,
    shim: FakeShim | None = None,
    *,
    settings: SandboxSettings | None = None,
    clock: FakeClock | None = None,
) -> AgentRuntimeEnvironment:
    """`AgentRuntimeEnvironment.start()` on the fakes, with no real waiting."""
    clock = clock or FakeClock()
    return await AgentRuntimeEnvironment.start(
        settings=settings or fake_settings(),
        control=control or FakeSandboxControl(),
        transport=(shim or FakeShim()).transport(),
        sleep=clock.sleep,
        clock=clock.clock,
    )


# --- the SDK client, for SdkSandboxControl's own tests -------------------------


def api_error(
    code: int, status: str, message: str | None = None
) -> genai_errors.APIError:
    """The error the SDK raises for an API failure (404 NOT_FOUND, 503 ...)."""
    text = status.lower() if message is None else message
    body = {"error": {"code": code, "message": text, "status": status}}
    if code >= 500:
        return genai_errors.ServerError(code, body)
    return genai_errors.ClientError(code, body)


class SdkSandboxState(str, enum.Enum):  # noqa: UP042 - as the SDK's
    """Mirrors the SDK's `SandboxState` (agentplatform/_genai/types/common.py:358):
    a str enum whose str() carries the class prefix."""

    STATE_PROVISIONING = "STATE_PROVISIONING"
    STATE_RUNNING = "STATE_RUNNING"


# The objects below have the SDK's field names and nesting (installed SDK,
# agentplatform/_genai/types/common.py): SandboxEnvironmentTemplate (:18137; `state`
# is a plain str Literal, :18170; `egress_control_config.internet_access`, :17999),
# AgentEngineSandboxOperation (:17388; `done`, `response`), SandboxEnvironment
# (:17284; `name`, `state`, `connection_info`) and SandboxEnvironmentConnectionInfo
# (:17243; `load_balancer_hostname`, `routing_token`).


class FakeSdkTemplates:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._calls = calls
        self.error: Exception | None = None
        self.template = SimpleNamespace(
            name=TEMPLATE,
            state="ACTIVE",
            custom_container_environment=SimpleNamespace(
                custom_container_spec=SimpleNamespace(image_uri="registry/sandbox:1"),
                ports=[SimpleNamespace(port=8080, protocol="TCP")],
            ),
            egress_control_config=SimpleNamespace(internet_access=False),
        )

    def get(self, *, name: str) -> Any:
        self._calls.append(("templates.get", {"name": name}))
        if self.error is not None:
            raise self.error
        return self.template


class FakeSdkSandboxes:
    """`client.agent_engines.sandboxes`: `calls` holds `(method, kwargs)` in order;
    `errors[method]` is raised by every call of `method`. When `delete_gate` is set
    to a `threading.Event`, `delete` blocks its worker thread until the event is
    set (at most 10 s), as a stalled connection would."""

    def __init__(self, token: str = SANDBOX_TOKEN) -> None:
        self.token = token
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.errors: dict[str, Exception] = {}
        self.delete_gate: threading.Event | None = None
        self.templates = FakeSdkTemplates(self.calls)
        self.sandbox = SimpleNamespace(
            name=f"{ENGINE}/sandboxEnvironments/31",
            state=SdkSandboxState.STATE_RUNNING,
            connection_info=SimpleNamespace(
                load_balancer_hostname=HOSTNAME,
                load_balancer_ip=None,
                routing_token="routing-31",
            ),
        )

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))
        if method in self.errors:
            raise self.errors[method]

    def create(
        self,
        *,
        name: str,
        poll_interval_seconds: float = 0.1,
        config: dict[str, Any],
    ) -> Any:
        self._record(
            "create",
            name=name,
            poll_interval_seconds=poll_interval_seconds,
            config=config,
        )
        return SimpleNamespace(
            name=f"{ENGINE}/operations/1", done=True, error=None, response=self.sandbox
        )

    def delete(self, *, name: str) -> Any:
        self._record("delete", name=name)
        if self.delete_gate is not None:
            self.delete_gate.wait(10)
        return SimpleNamespace(name=f"{ENGINE}/operations/2", done=False)

    def generate_access_token(
        self, service_account_email: str, timeout: int = 3600
    ) -> str:
        self._record(
            "generate_access_token",
            service_account_email=service_account_email,
            timeout=timeout,
        )
        return self.token


class FakeSdkClient:
    def __init__(self, token: str = SANDBOX_TOKEN) -> None:
        self.sandboxes = FakeSdkSandboxes(token)
        self.agent_engines = SimpleNamespace(sandboxes=self.sandboxes)
