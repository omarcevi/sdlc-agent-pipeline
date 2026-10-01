"""Cloud sandbox: one Agent Runtime sandbox per run, behind the `Environment` protocol.

Two parts, kept apart so that each can be tested alone:

- the control plane (`SandboxControl`): check the template, sign the token, create
  and delete sandboxes. `SdkSandboxControl` runs the SDK's public synchronous calls
  in worker threads; unit tests use a fake.
- the data plane: one `httpx.AsyncClient` per sandbox, talking through the
  platform's proxy to the shim (`sandbox_image/runtime/server.py`) on `/exec`,
  `/files` and `/files/zip`; unit tests give it an `httpx.MockTransport`.

The one credential is a JWT signed for `SANDBOX_CALLER_SA`. It lives only in the
headers of the sandbox's HTTP client: never in `os.environ`, state, events, logs,
error text or the sandbox. The sandbox's full resource name (it holds the project
number) stays inside the object too; everything outside sees `env_id` only.
"""

import asyncio
import base64
import io
import logging
import math
import os
import re
import shlex
import threading
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.environment.base import (
    DEFAULT_TIMEOUT_S,
    TIMEOUT_EXIT_CODES,
    WORKDIR,
    ExecResult,
    InfraError,
    sandbox_ttl_s,
    timeout_seconds,
)

logger = logging.getLogger(__name__)

SANDBOX_PORT = "8080"
SANDBOX_OWNER = "issue-to-pr"
DEFAULT_READY_TIMEOUT_S = 240.0
READY_POLL_S = 2.0
READY_ATTEMPT_TIMEOUT_S = 15.0
CREATE_GRACE_S = 60.0
# The SDK's create waits for its operation with no deadline, and its HTTP client
# has no timeout: these bound what start() and every release path wait for.
CREATE_TIMEOUT_S = 300.0
CREATE_POLL_S = 1.0  # how often the SDK polls the create operation
DELETE_TIMEOUT_S = 60.0  # as Docker's `docker rm -f`
TEMPLATE_NOT_USABLE = "sandbox template is not usable"
LINKS_REFUSED = "upload refused: links are not supported"
__all__ = [
    "CREATE_GRACE_S",
    "CREATE_POLL_S",
    "CREATE_TIMEOUT_S",
    "DEFAULT_READY_TIMEOUT_S",
    "DELETE_TIMEOUT_S",
    "READY_ATTEMPT_TIMEOUT_S",
    "READY_POLL_S",
    "SANDBOX_OWNER",
    "SANDBOX_PORT",
    "TEMPLATE_NOT_USABLE",
    "TIMEOUT_EXIT_CODES",
    "AgentRuntimeEnvironment",
    "SandboxControl",
    "SandboxHandle",
    "SandboxSettings",
    "SdkSandboxControl",
    "sdk_control",
    "template_is_usable",
]

# The fixed configuration messages. check_environment_config() (factory.py) reuses
# READY_TIMEOUT_ERROR for its comparison with RUN_TIMEOUT_S.
MISSING_SETTING = "{name} must be set for ENVIRONMENT_BACKEND=agent_runtime"
ENGINE_FORM_ERROR = (
    "SANDBOX_ENGINE must look like projects/<p>/locations/<l>/reasoningEngines/<id>"
)
TEMPLATE_FORM_ERROR = "SANDBOX_TEMPLATE must be a template under SANDBOX_ENGINE"
CALLER_FORM_ERROR = (
    "SANDBOX_CALLER_SA must be a service account email ending in "
    ".iam.gserviceaccount.com"
)
READY_TIMEOUT_ERROR = (
    "SANDBOX_READY_TIMEOUT_S must be a positive number below RUN_TIMEOUT_S"
)
_REQUIRED = ("SANDBOX_ENGINE", "SANDBOX_TEMPLATE", "SANDBOX_CALLER_SA")
_ENGINE_FORM = re.compile(
    r"projects/([^/\s]+)/locations/([^/\s]+)/reasoningEngines/[^/\s]+"
)
_SEGMENT = re.compile(r"[^/\s]+")
_CALLER_FORM = re.compile(r"[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com")

_HTTP_TIMEOUT_S = 30.0  # connect, write and pool; and the whole of a file request
_UPLOAD_TIMEOUT_S = 120.0
_READ_ATTEMPTS = 3  # reading is safe to repeat: the first try and two retries
_READ_RETRY_PAUSE_S = 1.0
_BODY_CHARS = 200  # of a response body, in error text

# Templates found usable in this process: each is checked once.
_TEMPLATES_CHECKED: set[str] = set()


@dataclass(frozen=True)
class SandboxSettings:
    """The cloud backend's configuration. `location` comes from the engine's name,
    never from GOOGLE_CLOUD_LOCATION (which is `global` for Gemini)."""

    engine: str
    template: str
    caller_sa: str
    project: str
    location: str
    ttl_s: int
    ready_timeout_s: float

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SandboxSettings":
        """Read and check the settings. Every per-variable check lives here; a bad
        value raises ValueError with a fixed message (the TTL's message names the
        value, as the Docker backend's does)."""
        environ = os.environ if environ is None else environ
        for name in (*_REQUIRED, "GOOGLE_CLOUD_PROJECT"):
            if not environ.get(name, "").strip():
                raise ValueError(MISSING_SETTING.format(name=name))
        engine = environ["SANDBOX_ENGINE"]
        template = environ["SANDBOX_TEMPLATE"]
        caller_sa = environ["SANDBOX_CALLER_SA"]
        match = _ENGINE_FORM.fullmatch(engine)
        if match is None:
            raise ValueError(ENGINE_FORM_ERROR)
        prefix = f"{engine}/sandboxEnvironmentTemplates/"
        if not (
            template.startswith(prefix) and _SEGMENT.fullmatch(template[len(prefix) :])
        ):
            raise ValueError(TEMPLATE_FORM_ERROR)
        if not _CALLER_FORM.fullmatch(caller_sa):
            raise ValueError(CALLER_FORM_ERROR)
        raw = environ.get("SANDBOX_READY_TIMEOUT_S", str(DEFAULT_READY_TIMEOUT_S))
        try:
            ready_timeout_s = float(raw)
        except ValueError:
            ready_timeout_s = math.nan
        if not 0 < ready_timeout_s < math.inf:
            raise ValueError(READY_TIMEOUT_ERROR)
        return cls(
            engine=engine,
            template=template,
            caller_sa=caller_sa,
            project=environ["GOOGLE_CLOUD_PROJECT"],
            location=match.group(2),
            ttl_s=sandbox_ttl_s(environ),
            ready_timeout_s=ready_timeout_s,
        )


@dataclass(frozen=True)
class SandboxHandle:
    """One created sandbox, as the control plane returns it."""

    name: str  # full resource name; never leaves the environment object
    hostname: str  # connection_info.load_balancer_hostname
    routing_token: str = field(repr=False)


class SandboxControl(Protocol):
    """The control plane. Every failure is an `InfraError`."""

    async def check_template(self, template: str) -> None:
        """Raises InfraError(TEMPLATE_NOT_USABLE) unless the template is ACTIVE and,
        when the field is returned, has the internet off."""
        ...

    async def sign_token(self, service_account: str, lifetime_s: int) -> str: ...

    async def create(
        self, *, engine: str, template: str, ttl_s: int, display_name: str
    ) -> SandboxHandle: ...

    async def delete(self, name: str) -> None:
        """Deletes without waiting for the operation; "not found" is success."""
        ...


def template_is_usable(template: Any) -> bool:
    """A template may be used when its state is ACTIVE and its internet access, when
    the API returns that field, is not on. The SDK's template state is a plain
    string (`SandboxEnvironmentTemplate.state`); an enum's value is read the same."""
    state = getattr(template, "state", None)
    if getattr(state, "value", state) != "ACTIVE":
        return False
    egress = getattr(template, "egress_control_config", None)
    return getattr(egress, "internet_access", None) is not True


def _is_not_found(exc: BaseException) -> bool:
    return (
        getattr(exc, "code", None) == 404 or getattr(exc, "status", "") == "NOT_FOUND"
    )


# Control-plane messages name resources, and so the project number; this text
# becomes a run's reason, which reaches record.json and the tracked results.
_REDACTIONS = (
    (re.compile(r" for url: .*", re.DOTALL), ""),  # requests' HTTPError tail
    (re.compile(r"projects/[^\s'\"]+"), "projects/…"),
    (re.compile(r"project_number:\d+"), "project_number:…"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.iam\.gserviceaccount\.com"), "<service account>"),
    (re.compile(r"\b\d{10,}\b"), "…"),  # a project number in any other form
)


def _redacted(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _control_error(action: str, exc: BaseException) -> InfraError:
    """The SDK's error as an InfraError: the action, the error type, its code and
    status, and at most 200 characters of its message with every resource name,
    project number and service account removed."""
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    status = getattr(exc, "status", None)
    message = _redacted(str(getattr(exc, "message", None) or exc))[:_BODY_CHARS]
    detail = " ".join(str(part) for part in (code, status) if part)
    kind = f"{type(exc).__name__} {detail}".strip()
    return InfraError(f"sandbox {action} failed: {kind}: {message}")


class SdkSandboxControl:
    """`SandboxControl` over the SDK's public synchronous calls, each run with
    `asyncio.to_thread`.

    The client is `agentplatform.Client(project, location=<the engine's location>,
    http_options={"api_version": "v1beta1"})`, built on first use, inside the worker
    thread. `agentplatform` is imported only here, so Docker runs and unit tests
    never import it; tests pass `client`."""

    def __init__(self, settings: SandboxSettings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client
        self._client_lock = threading.Lock()

    def _sandboxes(self) -> Any:
        with self._client_lock:
            if self._client is None:
                import agentplatform  # only here: see the class docstring

                self._client = agentplatform.Client(
                    project=self._settings.project,
                    location=self._settings.location,
                    http_options={"api_version": "v1beta1"},
                )
        return self._client.agent_engines.sandboxes

    async def _call(self, action: str, call: Callable[[], Any]) -> Any:
        try:
            return await asyncio.to_thread(call)
        except Exception as exc:
            raise _control_error(action, exc) from exc

    async def check_template(self, template: str) -> None:
        try:
            found = await asyncio.to_thread(
                lambda: self._sandboxes().templates.get(name=template)
            )
        except Exception as exc:
            if _is_not_found(exc):
                raise InfraError(TEMPLATE_NOT_USABLE) from None
            raise _control_error("template check", exc) from exc
        if not template_is_usable(found):
            raise InfraError(TEMPLATE_NOT_USABLE)

    async def sign_token(self, service_account: str, lifetime_s: int) -> str:
        token = await self._call(
            "token signing",
            lambda: self._sandboxes().generate_access_token(
                service_account_email=service_account, timeout=lifetime_s
            ),
        )
        if not isinstance(token, str) or not token:
            raise InfraError("sandbox token signing returned no token")
        return token

    async def create(
        self, *, engine: str, template: str, ttl_s: int, display_name: str
    ) -> SandboxHandle:
        config = {
            "sandbox_environment_template": template,
            "ttl": f"{ttl_s}s",
            "owner": SANDBOX_OWNER,
            "display_name": display_name,
        }
        operation = await self._call(
            "create",
            lambda: self._sandboxes().create(
                name=engine, poll_interval_seconds=CREATE_POLL_S, config=config
            ),
        )
        sandbox = getattr(operation, "response", None)
        name = getattr(sandbox, "name", None)
        info = getattr(sandbox, "connection_info", None)
        hostname = getattr(info, "load_balancer_hostname", None)
        routing_token = getattr(info, "routing_token", None)
        if not name:
            raise InfraError("sandbox create returned no sandbox")
        if not (hostname and routing_token):
            try:
                await self.delete(str(name))
            except InfraError as exc:
                logger.warning("could not delete sandbox %s: %s", display_name, exc)
            raise InfraError("sandbox create returned no connection info")
        return SandboxHandle(str(name), str(hostname), str(routing_token))

    async def delete(self, name: str) -> None:
        """Bounded by DELETE_TIMEOUT_S: the SDK's HTTP client has no timeout, and
        every release path waits here. A delete that times out is left to the TTL
        and the sweeper (its thread ends on its own)."""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(lambda: self._sandboxes().delete(name=name)),
                DELETE_TIMEOUT_S,
            )
        except TimeoutError:
            raise InfraError("sandbox delete timed out") from None
        except Exception as exc:
            if _is_not_found(exc):
                return
            raise _control_error("delete", exc) from exc


_SDK_CONTROLS: dict[SandboxSettings, SdkSandboxControl] = {}


def sdk_control(settings: SandboxSettings) -> SdkSandboxControl:
    """One control plane, and so one SDK client, per settings for the process."""
    control = _SDK_CONTROLS.get(settings)
    if control is None:
        control = _SDK_CONTROLS[settings] = SdkSandboxControl(settings)
    return control


async def _delete_logged(control: SandboxControl, name: str, env_id: str) -> None:
    """Delete a sandbox no environment owns any more; a failure is logged."""
    try:
        await control.delete(name)
    except Exception as exc:
        logger.warning("could not delete sandbox %s: %s", env_id, exc)


_LATE_DELETES: set["asyncio.Task[None]"] = set()  # keeps the tasks referenced


def _delete_when_created(
    creating: "asyncio.Future[SandboxHandle]", control: SandboxControl, env_id: str
) -> None:
    """For a create that start() stopped waiting for: if it ever succeeds while the
    event loop runs, delete its sandbox then. Otherwise the TTL and the sweeper
    remove it. A failed create has nothing to delete (its error is retrieved here,
    so asyncio does not report it)."""

    def created(task: "asyncio.Future[SandboxHandle]") -> None:
        if task.cancelled() or task.exception() is not None:
            return
        cleanup = task.get_loop().create_task(
            _delete_logged(control, task.result().name, env_id)
        )
        _LATE_DELETES.add(cleanup)
        cleanup.add_done_callback(_LATE_DELETES.discard)

    creating.add_done_callback(created)


async def _create(
    control: SandboxControl, settings: SandboxSettings, env_id: str
) -> SandboxHandle:
    """Create the sandbox, waiting at most CREATE_TIMEOUT_S. A cancellation cannot
    stop the worker thread that runs the create, so it waits for it (shielded, at
    most CREATE_GRACE_S), deletes the sandbox it made, and only then re-raises the
    cancellation. A create still running after either wait has its sandbox deleted
    when it finishes (`_delete_when_created`)."""
    creating = asyncio.ensure_future(
        control.create(
            engine=settings.engine,
            template=settings.template,
            ttl_s=settings.ttl_s,
            display_name=env_id,
        )
    )
    try:
        return await asyncio.wait_for(asyncio.shield(creating), CREATE_TIMEOUT_S)
    except TimeoutError:
        _delete_when_created(creating, control, env_id)
        raise InfraError(
            f"sandbox create did not finish in {CREATE_TIMEOUT_S:g} s"
        ) from None
    except asyncio.CancelledError:
        await _delete_once_created(control, creating, env_id)
        raise


async def _delete_once_created(
    control: SandboxControl, creating: "asyncio.Future[SandboxHandle]", env_id: str
) -> None:
    """After a cancellation: wait for the create, then delete what it made. The
    caller re-raises its cancellation; a second cancellation raised here
    propagates as itself."""
    try:
        handle = await asyncio.wait_for(asyncio.shield(creating), CREATE_GRACE_S)
    except asyncio.CancelledError:
        _delete_when_created(creating, control, env_id)
        raise
    except TimeoutError:
        _delete_when_created(creating, control, env_id)
        logger.warning(
            "sandbox %s: create still running %g s after cancellation; deleted if "
            "it finishes while this process runs, else left to the TTL",
            env_id,
            CREATE_GRACE_S,
        )
        return
    except Exception:
        return  # the create failed: there is nothing to delete
    await _delete_logged(control, handle.name, env_id)


class AgentRuntimeEnvironment:
    """One Agent Runtime sandbox (spec §4.3, §4.4). Use `start()`.

    `exec` and `write_file` are never retried (a lost reply may mean the command
    already ran); `read_file` retries a 5xx or a transport error twice. Error text
    holds the method, the path, the status and at most 200 characters of the body,
    never a header."""

    def __init__(
        self,
        env_id: str,
        handle: SandboxHandle,
        token: str,
        control: SandboxControl,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.env_id = env_id
        self._handle = handle
        self._control = control
        self._sleep = sleep
        self._closed = False
        # The token is kept nowhere else: only in this client's default headers.
        self._http = httpx.AsyncClient(
            base_url=f"https://{handle.hostname}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Sandbox-Routing-Token": handle.routing_token,
                "X-Sandbox-Port": SANDBOX_PORT,
            },
            transport=transport,
            timeout=_HTTP_TIMEOUT_S,
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(env_id={self.env_id!r})"

    @classmethod
    async def start(
        cls,
        *,
        settings: SandboxSettings | None = None,
        control: SandboxControl | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> "AgentRuntimeEnvironment":
        """Check the template (once per process), sign the token, create the
        sandbox, wait until the shim answers, make the repository directory. A
        failure or cancellation after the create deletes the sandbox first."""
        if settings is None:
            settings = SandboxSettings.from_env()
        if control is None:
            control = sdk_control(settings)
        if settings.template not in _TEMPLATES_CHECKED:
            await control.check_template(settings.template)
            _TEMPLATES_CHECKED.add(settings.template)
        token = await control.sign_token(settings.caller_sa, settings.ttl_s)
        env_id = f"itp-{uuid.uuid4().hex[:12]}"
        handle = await _create(control, settings, env_id)
        try:
            env = cls(env_id, handle, token, control, transport=transport, sleep=sleep)
        except Exception as exc:  # the platform gave an address httpx refuses
            await _delete_logged(control, handle.name, env_id)
            raise InfraError(
                f"sandbox {env_id}: cannot connect to it: {type(exc).__name__}"
            ) from None
        try:
            await env._wait_until_ready(settings.ready_timeout_s, clock)
            # As Docker's start() does. The cwd must exist: the shim runs /exec in it.
            await env.exec(f"mkdir -p {WORKDIR}", cwd="/workspace")
        except BaseException:
            await env._discard()
            raise
        return env

    async def _wait_until_ready(
        self, limit_s: float, clock: Callable[[], float]
    ) -> None:
        """A no-op command that reaches the shim, every READY_POLL_S. Never
        /healthz: the platform answers that itself before the shim listens. Each
        attempt is bounded by READY_ATTEMPT_TIMEOUT_S or the time left (at least
        1 s), so the wait ends within about `limit_s`. On expiry the last answer
        (a status or an error type, never a header) is logged."""
        deadline = clock() + limit_s
        while True:
            left = deadline - clock()
            answer = await self._probe(max(min(READY_ATTEMPT_TIMEOUT_S, left), 1.0))
            if answer is None:
                return
            if clock() >= deadline:
                logger.warning(
                    "sandbox %s did not become ready in %g s; last answer: %s",
                    self.env_id,
                    limit_s,
                    answer,
                )
                raise InfraError(f"sandbox did not become ready in {limit_s:g} s")
            await self._sleep(min(READY_POLL_S, deadline - clock()))

    async def _probe(self, bound_s: float) -> str | None:
        """None when the shim ran the no-op; otherwise what came back instead."""
        try:
            response = await asyncio.wait_for(
                self._send(
                    "POST",
                    "/exec",
                    label="/exec",
                    json={"command": "true", "timeout": 10},
                    timeout=bound_s,
                ),
                bound_s,
            )
        except TimeoutError:
            return f"no answer in {bound_s:g} s"
        except InfraError as exc:
            return str(exc)
        if response.status_code != 200:
            return f"HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            return "HTTP 200 with an unreadable body"
        exit_code = body.get("exit_code") if isinstance(body, dict) else None
        return None if exit_code == 0 else f"HTTP 200, exit code {exit_code}"

    async def _discard(self) -> None:
        """Delete the sandbox of a failed start; a failing delete is logged, so the
        start's own error is the one raised."""
        try:
            await self.close()
        except Exception as exc:
            logger.warning("could not delete sandbox %s: %s", self.env_id, exc)

    def _ensure_open(self) -> None:
        if self._closed:
            raise InfraError(f"sandbox {self.env_id} is closed")

    async def _send(
        self,
        method: str,
        path: str,
        *,
        label: str,
        timeout: Any = httpx.USE_CLIENT_DEFAULT,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            return await self._http.request(method, path, timeout=timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise InfraError(
                f"sandbox {self.env_id}: {method} {label} failed: "
                f"{type(exc).__name__}: {str(exc)[:_BODY_CHARS]}"
            ) from None

    def _status_error(
        self, method: str, label: str, response: httpx.Response
    ) -> InfraError:
        return InfraError(
            f"sandbox {self.env_id}: {method} {label} returned HTTP "
            f"{response.status_code}: {response.text[:_BODY_CHARS]}"
        )

    def _body_error(
        self, method: str, label: str, response: httpx.Response
    ) -> InfraError:
        return InfraError(
            f"sandbox {self.env_id}: {method} {label} returned an unreadable body: "
            f"{response.text[:_BODY_CHARS]}"
        )

    @staticmethod
    def _shim_detail(response: httpx.Response) -> str | None:
        """The shim's own error text (FastAPI's JSON `detail` string), or None when
        the answer did not come from the shim: a 404 or 403 from the platform's
        proxy (a sandbox that is gone, a refused token) is no file error."""
        try:
            body = response.json()
        except ValueError:
            return None
        detail = body.get("detail") if isinstance(body, dict) else None
        return detail[:_BODY_CHARS] if isinstance(detail, str) else None

    async def exec(
        self, command: str, *, timeout: float = DEFAULT_TIMEOUT_S, cwd: str = WORKDIR
    ) -> ExecResult:
        self._ensure_open()
        seconds = timeout_seconds(timeout)
        response = await self._send(
            "POST",
            "/exec",
            label="/exec",
            json={
                # Docker's wrapper: it stops the command's children too; the
                # shim's own timeout (the backstop) kills only the shell.
                "command": f"timeout -k 5 {seconds} sh -c {shlex.quote(command)}",
                "cwd": cwd,
                "timeout": seconds + 15,
            },
            timeout=httpx.Timeout(_HTTP_TIMEOUT_S, read=seconds + 30),
        )
        if response.status_code != 200:
            raise self._status_error("POST", "/exec", response)
        try:
            result = ExecResult.model_validate(response.json())
        except ValueError:
            raise self._body_error("POST", "/exec", response) from None
        timed_out = result.timed_out or result.exit_code in TIMEOUT_EXIT_CODES
        return result.model_copy(update={"timed_out": timed_out})

    async def read_file(self, path: str) -> str:
        self._ensure_open()
        label = f"/files?path={path}"
        error: InfraError | None = None
        for attempt in range(_READ_ATTEMPTS):
            if attempt:
                await self._sleep(_READ_RETRY_PAUSE_S)
            try:
                response = await self._send(
                    "GET",
                    "/files",
                    label=label,
                    params={"path": path},
                    timeout=_HTTP_TIMEOUT_S,
                )
            except InfraError as exc:
                error = exc
                continue
            status = response.status_code
            if status == 200:
                try:
                    data = base64.b64decode(
                        response.json()["content_b64"], validate=True
                    )
                except (ValueError, KeyError, TypeError):
                    raise self._body_error("GET", label, response) from None
                return data.decode("utf-8", errors="replace")
            detail = self._shim_detail(response) if status in (400, 403, 404) else None
            if detail is not None and status == 404:
                raise FileNotFoundError(path)
            if detail is not None:
                raise OSError(detail)
            error = self._status_error("GET", label, response)
            if status < 500:
                raise error
        assert error is not None
        raise error

    async def write_file(self, path: str, content: str) -> None:
        self._ensure_open()
        label = f"/files?path={path}"
        response = await self._send(
            "POST",
            "/files",
            label=label,
            params={"path": path},
            json={"content_b64": base64.b64encode(content.encode()).decode("ascii")},
            timeout=_HTTP_TIMEOUT_S,
        )
        if response.status_code == 200:
            return
        if response.status_code in (400, 403, 404):
            detail = self._shim_detail(response)
            if detail is not None:
                raise OSError(detail)
        raise self._status_error("POST", label, response)

    async def upload_dir(self, local_dir: Path, dest: str = WORKDIR) -> None:
        self._ensure_open()
        archive = _zip_tree(Path(local_dir))
        label = f"/files/zip?path={dest}"
        response = await self._send(
            "POST",
            "/files/zip",
            label=label,
            params={"path": dest},
            content=archive,
            headers={"Content-Type": "application/zip"},
            timeout=_UPLOAD_TIMEOUT_S,
        )
        if response.status_code != 200:
            raise self._status_error("POST", label, response)

    async def close(self) -> None:
        """Delete the sandbox (without waiting for the operation) and close the
        HTTP client. Once: later calls do nothing. "Not found" is success; any
        other delete failure is raised, and the release paths log it."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._control.delete(self._handle.name)
        finally:
            await self._http.aclose()

    async def proxy_request(
        self, method: str, path: str, *, port: str = SANDBOX_PORT, **kwargs: Any
    ) -> httpx.Response:
        """One request through the platform's proxy to `port` in the sandbox, with
        the sandbox's headers, returned as it is. For tests and integration checks
        only: nothing under app/ calls it."""
        self._ensure_open()
        headers = {**(kwargs.pop("headers", None) or {}), "X-Sandbox-Port": port}
        return await self._send(method, path, label=path, headers=headers, **kwargs)


def _zip_tree(root: Path) -> bytes:
    """`root` as an in-memory zip (members relative to it, directories included).
    Links are refused before anything is read."""
    if root.is_symlink():
        raise InfraError(LINKS_REFUSED)
    if not root.is_dir():
        raise InfraError(f"upload failed: {root.name} is not a directory")

    def unreadable(error: OSError) -> None:
        raise error  # os.walk would skip the directory and upload the rest

    buffer = io.BytesIO()
    try:
        members: list[Path] = []
        for directory, dirnames, filenames in os.walk(root, onerror=unreadable):
            for name in sorted(dirnames) + sorted(filenames):
                path = Path(directory, name)
                if path.is_symlink():
                    raise InfraError(LINKS_REFUSED)
                members.append(path)
        with zipfile.ZipFile(
            buffer, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False
        ) as archive:
            for path in members:
                archive.write(path, path.relative_to(root).as_posix())
    except (OSError, ValueError) as exc:  # ValueError: a name zip cannot encode
        raise InfraError(f"upload failed: {type(exc).__name__}: {exc}") from None
    return buffer.getvalue()
