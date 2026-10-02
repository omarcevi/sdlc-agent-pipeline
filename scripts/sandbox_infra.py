"""Operator tooling for the Agent Runtime sandbox (spec 3A §5.2, §4.7, §5.3, §5.5).

Subcommands: image, template, prune-templates, sweep, delete-engine, env.
Every one is safe to run twice. Standard library plus the SDK (imported only by
``SdkPlatform``, never at import time). The script never writes ``.env`` and
holds no token: it prints names only.

Exit codes: 0 done; 1 a check failed; 2 usage or missing configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

SWEEP_GRACE_S = 60
DEFAULT_TTL_S = 1800
TERMINAL_STATES = frozenset({"STATE_DELETED", "STATE_DEPROVISIONING"})  # sandboxes
# Template states have no STATE_ prefix in the SDK (types/common.py:18170-18181).
TEMPLATE_TERMINAL_STATES = frozenset({"DELETED", "DEPROVISIONING"})
TEMPLATE_PORT = 8080
TEMPLATE_RESOURCES = {
    "limits": {"cpu": "2", "memory": "2Gi"},
    "requests": {"cpu": "2", "memory": "2Gi"},
}
# delete-engine waits for the delete as `make teardown`'s wait-clear does.
ENGINE_DELETE_POLLS = 30
ENGINE_DELETE_POLL_S = 10
TERRAFORM_ROOT = Path("deployment/terraform/single-project")
ENGINE_RE = re.compile(r"^projects/[^/]+/locations/[^/]+/reasoningEngines/[^/]+$")
ENGINE_FORM_MESSAGE = (
    "SANDBOX_ENGINE must look like projects/<p>/locations/<l>/reasoningEngines/<id>"
)
REPO_ROOT = Path(__file__).resolve().parent.parent


class Refused(Exception):
    """A check failed (exit 1)."""


class UsageError(Exception):
    """Usage or missing configuration (exit 2)."""


@dataclass(frozen=True)
class Template:
    name: str
    image_uri: str | None
    state: str
    internet_access: bool | None = None  # None: the API did not return it
    ports: list[int] | None = None  # None or empty: the API did not return them


@dataclass(frozen=True)
class Sandbox:
    name: str
    state: str
    create_time: datetime | None
    load_balancer_hostname: str | None = None
    routing_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Engine:
    name: str
    display_name: str


class Platform(Protocol):
    """The SDK behind the script; a fake in tests."""

    def list_templates(self, engine: str) -> list[Template]: ...
    def get_template(self, name: str) -> Template: ...
    def create_template(self, engine: str, config: dict[str, Any]) -> str: ...
    def delete_template(self, name: str) -> None: ...
    def list_sandboxes(self, engine: str) -> list[Sandbox]: ...
    def get_sandbox(self, name: str) -> Sandbox: ...
    def delete_sandbox(self, name: str) -> None: ...
    def get_engine(self, name: str) -> Engine: ...
    def delete_engine(self, name: str, *, force: bool) -> None: ...


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def run_command(
    cmd: Sequence[str], *, capture: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=capture, text=True, check=False)


# ---- pure helpers -----------------------------------------------------------


def image_tag(repo_root: Path, run: Runner = run_command) -> str:
    """First 12 characters of the tree id of ``sandbox_image/`` at HEAD."""
    done = run(["git", "-C", str(repo_root), "rev-parse", "HEAD:sandbox_image"])
    sha = (done.stdout or "").strip()
    if done.returncode != 0 or len(sha) < 12:
        raise UsageError("cannot read the tree id of sandbox_image/ at HEAD")
    return sha[:12]


def image_uri(repository: str, tag: str) -> str:
    return f"{repository}/sandbox:{tag}"


def current_image_uri(
    repo_root: Path, repository: str, run: Runner = run_command
) -> str:
    return image_uri(repository, image_tag(repo_root, run))


def template_config(
    image: str, display_name: str, ports: Iterable[int] = (TEMPLATE_PORT,)
) -> dict[str, Any]:
    """The exact SDK config for a hermetic template."""
    return {
        "display_name": display_name,
        "custom_container_environment": {
            "custom_container_spec": {"image_uri": image},
            "resources": json.loads(json.dumps(TEMPLATE_RESOURCES)),
            "ports": [{"port": int(p), "protocol": "TCP"} for p in ports],
        },
        "egress_control_config": {"internet_access": False},
    }


def _usable(template: Template, image: str) -> bool:
    if template.image_uri != image or not template.state.endswith("ACTIVE"):
        return False
    return not template.ports or list(template.ports) == [TEMPLATE_PORT]


def _match(templates: Iterable[Template], image: str) -> Template | None:
    return next((t for t in templates if _usable(t, image)), None)


def matching_template(templates: Iterable[Template], image: str) -> str | None:
    """Name of the first ACTIVE template of ``image`` (ports 8080 when returned)."""
    found = _match(templates, image)
    return found.name if found else None


def expired(
    sandboxes: Iterable[Sandbox],
    *,
    now: datetime,
    ttl_s: int,
    grace_s: int = SWEEP_GRACE_S,
) -> list[str]:
    limit = timedelta(seconds=ttl_s + grace_s)
    return [
        s.name
        for s in sandboxes
        if s.state not in TERMINAL_STATES
        and s.create_time is not None
        and now - s.create_time > limit
    ]


def terraform_outputs(root: Path | str, run: Runner = run_command) -> dict[str, str]:
    """``terraform -chdir=<root> output -json`` as ``{name: value}``."""
    done = run(["terraform", f"-chdir={root}", "output", "-json"])
    if done.returncode != 0:
        raise UsageError(f"terraform output failed in {root} (has infra been applied?)")
    try:
        raw = json.loads(done.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise UsageError("terraform output did not return JSON") from exc
    return {
        k: str(v["value"])
        for k, v in raw.items()
        if isinstance(v, dict) and isinstance(v.get("value"), str)
    }


def find_template(platform: Platform, engine: str, image: str) -> str | None:
    """Reuse rule shared with the cloud backend and the deploy stager."""
    found = _match(platform.list_templates(engine), image)
    if found is None:
        return None
    if found.internet_access is True:
        raise Refused("matching template allows the internet: refusing")
    return found.name


# ---- the real platform ------------------------------------------------------


def _state(raw: Any) -> str:
    """SDK sandbox states are str-enums whose str() carries the class name; template
    states are bare literals. Both come out as plain strings."""
    return str(getattr(raw, "value", raw) or "")


class SdkPlatform:
    """The Agent Platform SDK. Imported lazily; unit tests never build it."""

    def __init__(self, project: str, location: str, client: Any = None) -> None:
        if client is None:
            import agentplatform

            client = agentplatform.Client(
                project=project,
                location=location,
                http_options={"api_version": "v1beta1"},
            )
        self._client = client

    @property
    def _sandboxes(self) -> Any:
        return self._client.agent_engines.sandboxes

    @staticmethod
    def _template(raw: Any) -> Template:
        env = getattr(raw, "custom_container_environment", None)
        spec = getattr(env, "custom_container_spec", None)
        ports = [
            int(p.port)
            for p in (getattr(env, "ports", None) or [])
            if getattr(p, "port", None) is not None
        ]
        egress = getattr(raw, "egress_control_config", None)
        internet = getattr(egress, "internet_access", None)
        return Template(
            name=str(raw.name),
            image_uri=getattr(spec, "image_uri", None),
            state=_state(getattr(raw, "state", None)),
            internet_access=internet,
            ports=ports or None,
        )

    @staticmethod
    def _sandbox(raw: Any) -> Sandbox:
        info = getattr(raw, "connection_info", None)
        token = getattr(info, "routing_token", None)
        host = getattr(info, "load_balancer_hostname", None)
        return Sandbox(
            name=str(raw.name),
            state=_state(getattr(raw, "state", None)),
            create_time=getattr(raw, "create_time", None),
            load_balancer_hostname=str(host) if host else None,
            routing_token=str(token) if token else None,
        )

    def list_templates(self, engine: str) -> list[Template]:
        # The list can still return a template deleted shortly before, and a get of it
        # answers 404: it is gone, so it is left out.
        templates = []
        for stub in self._sandboxes.templates.list(name=engine):
            try:
                templates.append(self.get_template(str(stub.name)))
            except Exception as error:
                if not is_not_found(error):
                    raise
        return templates

    def get_template(self, name: str) -> Template:
        return self._template(self._sandboxes.templates.get(name=name))

    def create_template(self, engine: str, config: dict[str, Any]) -> str:
        body = dict(config)
        display_name = body.pop("display_name")
        op = self._sandboxes.templates.create(
            name=engine, display_name=display_name, config=body
        )
        return str(op.response.name)

    def delete_template(self, name: str) -> None:
        self._sandboxes.templates.delete(name=name)

    def list_sandboxes(self, engine: str) -> list[Sandbox]:
        return [self._sandbox(s) for s in self._sandboxes.list(name=engine)]

    def get_sandbox(self, name: str) -> Sandbox:
        return self._sandbox(self._sandboxes.get(name=name))

    def delete_sandbox(self, name: str) -> None:
        self._sandboxes.delete(name=name)

    def get_engine(self, name: str) -> Engine:
        res = self._client.agent_engines.get(name=name).api_resource
        return Engine(
            name=str(res.name),
            display_name=str(getattr(res, "display_name", None) or ""),
        )

    def delete_engine(self, name: str, *, force: bool) -> None:
        self._client.agent_engines.delete(name=name, force=force)


# ---- configuration ----------------------------------------------------------


@dataclass
class Context:
    repo_root: Path
    environ: Mapping[str, str]
    run: Runner
    out: Callable[[str], None]
    now: datetime
    sleep: Callable[[float], None] = time.sleep
    platform_factory: Callable[[str], Platform] | None = None
    _platform: Platform | None = None
    _outputs: dict[str, str] | None = None

    def outputs(self) -> dict[str, str]:
        if self._outputs is None:
            self._outputs = terraform_outputs(self.repo_root / TERRAFORM_ROOT, self.run)
        return self._outputs

    def output(self, name: str) -> str:
        value = self.outputs().get(name)
        if not value:
            raise UsageError(f"terraform output {name} is missing")
        return value

    def project(self) -> str:
        project = self.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not project:
            raise UsageError("GOOGLE_CLOUD_PROJECT is not set")
        return project

    def engine(self, flag: str | None) -> str:
        """``--engine`` when given (an empty one is refused: teardown passes the
        Terraform output, and an empty output must never fall back to another
        engine), else SANDBOX_ENGINE, else the Terraform output."""
        if flag is not None:
            return check_engine_name(flag)
        engine = self.environ.get("SANDBOX_ENGINE") or ""
        if not engine:
            engine = self.output("agent_runtime_resource_name")
        return check_engine_name(engine)

    def ttl_s(self) -> int:
        raw = self.environ.get("SANDBOX_TTL_S", "")
        if not raw:
            return DEFAULT_TTL_S
        try:
            ttl = int(raw)
        except ValueError:
            raise UsageError("SANDBOX_TTL_S must be a positive number") from None
        if ttl <= 0:
            raise UsageError("SANDBOX_TTL_S must be a positive number")
        return ttl

    def platform(self, engine: str) -> Platform:
        if self._platform is None:
            if self.platform_factory is not None:
                self._platform = self.platform_factory(engine)
            else:
                location = engine.split("/")[3]
                self._platform = SdkPlatform(self.project(), location)
        return self._platform


def check_engine_name(engine: str) -> str:
    if not ENGINE_RE.match(engine):
        raise UsageError(ENGINE_FORM_MESSAGE)
    return engine


def is_not_found(error: BaseException) -> bool:
    return (
        getattr(error, "code", None) == 404
        or getattr(error, "status", None) == "NOT_FOUND"
    )


def error_summary(error: BaseException) -> str:
    """The error's type, then its HTTP code and status when they are a number and
    an enum-style name: never its message, which names resources."""
    parts = [type(error).__name__]
    code = getattr(error, "code", None)
    if code is None:
        code = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        parts.append(str(int(code)))
    status = getattr(error, "status", None)
    status = getattr(status, "name", status)
    if isinstance(status, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", status):
        parts.append(status)
    return " ".join(parts)


# ---- subcommands ------------------------------------------------------------


def cmd_image(ctx: Context, args: argparse.Namespace) -> int:
    project = ctx.project()
    status = ctx.run(
        [
            "git",
            "-C",
            str(ctx.repo_root),
            "status",
            "--porcelain",
            "--",
            "sandbox_image",
        ]
    )
    if (status.stdout or "").strip():
        raise Refused("sandbox_image/ has uncommitted changes")
    uri = current_image_uri(
        ctx.repo_root, ctx.output("sandbox_image_repository"), ctx.run
    )
    described = ctx.run(
        [
            "gcloud",
            "artifacts",
            "docker",
            "images",
            "describe",
            uri,
            "--project",
            project,
        ]
    )
    if described.returncode == 0:
        ctx.out(f"image exists: {uri}")
    else:
        built = ctx.run(
            [
                "gcloud",
                "builds",
                "submit",
                str(ctx.repo_root / "sandbox_image"),
                "--tag",
                uri,
                "--project",
                project,
            ],
            capture=False,
        )
        if built.returncode != 0:
            raise Refused("gcloud builds submit failed")
    ctx.out(uri)
    return 0


def _current_image(ctx: Context) -> tuple[str, str]:
    tag = image_tag(ctx.repo_root, ctx.run)
    return tag, image_uri(ctx.output("sandbox_image_repository"), tag)


def ensure_template(ctx: Context, engine: str, *, find_only: bool) -> str:
    platform = ctx.platform(engine)
    tag, image = _current_image(ctx)
    name = find_template(platform, engine, image)
    if name is not None:
        return name
    if find_only:
        raise Refused("no active template for the current image")
    config = template_config(image, f"issue-to-pr-sandbox-{tag}")
    return platform.create_template(engine, config)


def cmd_template(ctx: Context, args: argparse.Namespace) -> int:
    engine = ctx.engine(args.engine)
    name = ensure_template(ctx, engine, find_only=args.find_only)
    ctx.out(f"SANDBOX_TEMPLATE={name}")
    return 0


def cmd_prune(ctx: Context, args: argparse.Namespace) -> int:
    engine = ctx.engine(args.engine)
    platform = ctx.platform(engine)
    image = None
    in_use = ctx.environ.get("SANDBOX_TEMPLATE", "")
    if not args.all:
        image = _current_image(ctx)[1]
        if find_template(platform, engine, image) is None:
            raise Refused(
                "no active template for the current image: run the template step first"
            )
    count = 0
    for t in platform.list_templates(engine):
        if t.state in TEMPLATE_TERMINAL_STATES:
            continue
        if not args.all and (t.image_uri == image or t.name == in_use):
            continue
        if args.dry_run:
            ctx.out(f"would delete template {t.name}")
        else:
            platform.delete_template(t.name)
            ctx.out(f"deleted template {t.name}")
        count += 1
    ctx.out(f"{'would prune' if args.dry_run else 'pruned'} {count}")
    return 0


def cmd_sweep(ctx: Context, args: argparse.Namespace) -> int:
    engine = ctx.engine(args.engine)
    platform = ctx.platform(engine)
    sandboxes = platform.list_sandboxes(engine)
    if args.all:
        doomed = {s.name for s in sandboxes if s.state not in TERMINAL_STATES}
    else:
        doomed = set(expired(sandboxes, now=ctx.now, ttl_s=ctx.ttl_s()))
    for s in sandboxes:
        if s.name not in doomed:
            ctx.out(f"kept {s.name} {s.state}")
            continue
        if args.dry_run:
            ctx.out(f"would delete {s.name} {s.state}")
        else:
            platform.delete_sandbox(s.name)
            ctx.out(f"deleted {s.name} {s.state}")
    ctx.out(f"{'would sweep' if args.dry_run else 'swept'} {len(doomed)}")
    return 0


def _get_engine(platform: Platform, name: str) -> Engine | None:
    """The engine, or None when it is not found."""
    try:
        return platform.get_engine(name)
    except Exception as exc:
        if is_not_found(exc):
            return None
        raise


def cmd_delete_engine(ctx: Context, args: argparse.Namespace) -> int:
    """Safe to rerun: an engine that is not found is done. The SDK's delete returns
    before the engine is gone, so this polls until it is (bounded), and teardown's
    next step never meets an engine that is still being deleted."""
    name = check_engine_name(args.name)
    platform = ctx.platform(name)
    engine = _get_engine(platform, name)
    if engine is None:
        if args.dry_run:  # a typo must not look like success
            ctx.out("not found (already gone, or a wrong name)")
            return 1
        ctx.out("engine already gone")
        return 0
    if engine.display_name != args.expect_display_name:
        raise Refused("engine display name does not match --expect-display-name")
    if args.dry_run:
        ctx.out(f"would delete engine {name} (force, children included)")
        return 0
    try:
        platform.delete_engine(name, force=True)
    except Exception as exc:
        if not is_not_found(exc):
            raise
        ctx.out("engine already gone")
        return 0
    for poll in range(ENGINE_DELETE_POLLS + 1):
        if _get_engine(platform, name) is None:
            ctx.out(f"deleted engine {name}")
            return 0
        if poll < ENGINE_DELETE_POLLS:
            ctx.out(
                "waiting for the engine to finish deleting "
                f"({poll + 1}/{ENGINE_DELETE_POLLS})"
            )
            ctx.sleep(ENGINE_DELETE_POLL_S)
    raise Refused(f"engine still there after {ENGINE_DELETE_POLLS} polls")


def cmd_env(ctx: Context, args: argparse.Namespace) -> int:
    engine = ctx.engine(args.engine)
    template = ensure_template(ctx, engine, find_only=True)
    caller = ctx.output("sandbox_caller_email")
    ctx.out(f"SANDBOX_ENGINE={engine}")
    ctx.out(f"SANDBOX_TEMPLATE={template}")
    ctx.out(f"SANDBOX_CALLER_SA={caller}")
    ctx.out("# ENVIRONMENT_BACKEND=agent_runtime")
    return 0


# ---- entry point ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sandbox_infra")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(
        name: str, handler: Callable[..., int], *, engine: bool = True
    ) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.set_defaults(handler=handler)
        if engine:
            p.add_argument("--engine", default=None)
        return p

    add("image", cmd_image, engine=False)
    add("template", cmd_template).add_argument("--find-only", action="store_true")
    p = add("prune-templates", cmd_prune)
    p.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p = add("sweep", cmd_sweep)
    p.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p = add("delete-engine", cmd_delete_engine, engine=False)
    p.add_argument("--name", required=True)
    p.add_argument("--expect-display-name", required=True)
    p.add_argument("--dry-run", action="store_true")
    add("env", cmd_env)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    platform: Platform | None = None,
    run: Runner = run_command,
    environ: Mapping[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
    now: datetime | None = None,
    out: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    ctx = Context(
        repo_root=repo_root,
        environ=os.environ if environ is None else environ,
        run=run,
        out=out or (lambda line: print(line, flush=True)),
        now=now or datetime.now(UTC),
        sleep=sleep,
        platform_factory=(lambda _engine: platform) if platform is not None else None,
    )
    try:
        return args.handler(ctx, args)
    except Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: command not found: {exc.filename}", file=sys.stderr)
        return 2
    except Exception as exc:  # SDK, credential and validation errors: no message text
        code = 2 if type(exc).__name__ == "DefaultCredentialsError" else 1
        print(
            f"error: {error_summary(exc)} (see the operator's own credentials/API)",
            file=sys.stderr,
        )
        return code


if __name__ == "__main__":
    sys.exit(main())
