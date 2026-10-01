"""Builds the clean tree that `agents-cli deploy` uploads, deploys from it, and smoke
tests the deployed agent.

    uv run python scripts/stage_deploy.py stage [--out build/deploy]
    uv run python scripts/stage_deploy.py deploy
    uv run python scripts/stage_deploy.py smoke [--run-id cloud-smoke-1] [--task tc-001]

Deploying from the project root would upload all of `bench/` (including the sealed
held-out tasks) and copy `.env` into the deployed agent. `stage` therefore copies an
explicit allow-list into `build/deploy/`, and `deploy` runs agents-cli only from there.
Held-out directories are recognised by name and pruned before anything in them is
listed, opened or copied. Nothing here reads `.env` or `~/.config`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sandbox_infra as infra  # noqa: E402

STAGE_DIR = Path("build/deploy")
HELDOUT_DIR = re.compile(r"-h[0-9]{2}$")
ROOT_FILES = (
    "Dockerfile",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "agents-cli-manifest.yaml",
    "deployment_metadata.json",
)
OPTIONAL_ROOT_FILES = frozenset({"deployment_metadata.json"})
CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache"})
NOT_DEV = "staging refused: a task outside the dev split"
NO_TEMPLATE = "no ACTIVE template for the current sandbox image"
ONLY_FROM_STAGE = "deploy runs only from build/deploy"
SMOKE_PASS = "patch written"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class StageRefused(Exception):
    """Staging stopped (exit 1)."""


Refused = infra.Refused
UsageError = infra.UsageError
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def run_command(
    cmd: Sequence[str],
    *,
    capture: bool = True,
    cwd: Path | str | None = None,
    tee: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd``. With ``tee`` its output is shown live and still collected."""
    if not tee:
        return subprocess.run(
            list(cmd), capture_output=capture, text=True, check=False, cwd=cwd
        )
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )
    collected: dict[str, list[str]] = {"out": [], "err": []}

    def pump(stream, key: str, sink) -> None:
        for line in stream:
            collected[key].append(line)
            sink.write(line)
            sink.flush()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "out", sys.stdout)),
        threading.Thread(target=pump, args=(proc.stderr, "err", sys.stderr)),
    ]
    for thread in threads:
        thread.start()
    code = proc.wait()
    for thread in threads:
        thread.join()
    return subprocess.CompletedProcess(
        list(cmd), code, "".join(collected["out"]), "".join(collected["err"])
    )


# ---- staging ----------------------------------------------------------------


def _skip_name(name: str) -> bool:
    return (
        name in CACHE_DIRS
        or name.endswith(".pyc")
        or name == ".env"
        or name.startswith(".env.")
        or bool(HELDOUT_DIR.search(name))
    )


def _copy_tree(src: Path, dst: Path, staged: list[str], out: Path) -> None:
    """Copy ``src`` into ``dst``, pruning caches, `.env` and held-out names by name
    before descending."""
    dst.mkdir(parents=True, exist_ok=True)
    with os.scandir(src) as entries:
        listing = sorted(entries, key=lambda e: e.name)
    for entry in listing:
        if _skip_name(entry.name):
            continue
        target = dst / entry.name
        if entry.is_dir(follow_symlinks=False):
            _copy_tree(Path(entry.path), target, staged, out)
        elif entry.is_file(follow_symlinks=False):
            shutil.copy2(entry.path, target)
            staged.append(str(target.relative_to(out)))


def _copy_file(src: Path, dst: Path, staged: list[str], out: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    staged.append(str(dst.relative_to(out)))


def _is_dev_task(task_yaml: Path) -> bool:
    import yaml

    data = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
    return isinstance(data, dict) and data.get("split") == "dev"


def _marker(out: Path) -> Path:
    """Written beside ``out`` (not inside it, which agents-cli would upload) once a
    stage built it; only a directory with this marker, or an empty one, is replaced."""
    return out.parent / f"{out.name}.staged-by-stage_deploy"


def _check_out(repo_root: Path, out: Path) -> None:
    repo, target = repo_root.resolve(), out.resolve()
    if target == repo or target in repo.parents:
        raise StageRefused("staging refused: the output directory holds the project")
    if repo in target.parents:
        build = repo / "build"
        if build not in target.parents:
            raise StageRefused(
                "staging refused: inside the project the output must be under build/"
            )
    if target.exists():
        if not target.is_dir():
            raise StageRefused("staging refused: the output path is not a directory")
        if any(target.iterdir()) and not _marker(target).is_file():
            raise StageRefused(
                "staging refused: the output directory is not empty and was not "
                "made by a stage"
            )


def stage(repo_root: Path, out: Path) -> list[str]:
    """Rebuild ``out`` from scratch with the allow-listed files. Returns the staged
    paths, relative to ``out``, sorted. The tree is built beside ``out`` and moved
    into place at the end, so a refusal leaves no half-built ``out``."""
    repo_root, out = Path(repo_root), Path(out)
    _check_out(repo_root, out)
    for name in ROOT_FILES:
        if name not in OPTIONAL_ROOT_FILES and not (repo_root / name).is_file():
            raise StageRefused(f"staging refused: {name} is missing")
    work = out.parent / f"{out.name}.tmp"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        staged = _build(repo_root, work)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    if out.exists():
        shutil.rmtree(out)
    os.replace(work, out)
    _marker(out).write_text("built by scripts/stage_deploy.py\n", encoding="utf-8")
    return staged


def _build(repo_root: Path, out: Path) -> list[str]:
    staged: list[str] = []
    for name in ROOT_FILES:
        if (repo_root / name).is_file():
            _copy_file(repo_root / name, out / name, staged, out)
    _copy_tree(repo_root / "app", out / "app", staged, out)
    bench = repo_root / "bench"
    with os.scandir(bench) as entries:
        modules = sorted(e.name for e in entries if e.name.endswith(".py"))
    for name in modules:
        _copy_file(bench / name, out / "bench" / name, staged, out)
    _copy_tree(bench / "repos", out / "bench" / "repos", staged, out)
    tasks = bench / "tasks"
    with os.scandir(tasks) as entries:
        names = sorted(
            e.name for e in entries if not HELDOUT_DIR.search(e.name) and e.is_dir()
        )
    for name in names:
        task_yaml = tasks / name / "task.yaml"
        if not task_yaml.is_file():
            continue
        if not _is_dev_task(task_yaml):
            raise StageRefused(NOT_DEV)
        dest = out / "bench" / "tasks" / name
        _copy_file(task_yaml, dest / "task.yaml", staged, out)
        plant = tasks / name / "plant"
        if plant.is_dir():
            _copy_tree(plant, dest / "plant", staged, out)
    return sorted(staged)


# ---- deploy -----------------------------------------------------------------


def deploy_env(*, engine: str, template: str, caller_sa: str) -> dict[str, str]:
    return {
        "ENVIRONMENT_BACKEND": "agent_runtime",
        "SANDBOX_ENGINE": engine,
        "SANDBOX_TEMPLATE": template,
        "SANDBOX_CALLER_SA": caller_sa,
        "BQ_ANALYTICS_ENABLED": "1",
        "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
    }


def deploy_args(
    *, project: str, engine: str, template: str, caller_sa: str
) -> list[str]:
    env = deploy_env(engine=engine, template=template, caller_sa=caller_sa)
    return [
        "agents-cli",
        "deploy",
        "--project",
        project,
        "--update-only",
        "--min-instances",
        "0",
        "--max-instances",
        "1",
        "--concurrency",
        "4",
        "--cpu",
        "1",
        "--memory",
        "4Gi",
        "--update-env-vars",
        ",".join(f"{k}={v}" for k, v in env.items()),
    ]


def passthrough_url(engine: str) -> str:
    location = infra.check_engine_name(engine).split("/")[3]
    return (
        f"https://{location}-aiplatform.googleapis.com/reasoningEngines/v1/{engine}/api"
    )


def run_agents_cli(
    cmd: Sequence[str], *, cwd: Path, repo_root: Path, run: Runner
) -> subprocess.CompletedProcess[str]:
    """The one place that runs agents-cli: only from build/deploy."""
    if Path(cwd).resolve() != (Path(repo_root) / STAGE_DIR).resolve():
        raise Refused(ONLY_FROM_STAGE)
    return run(cmd, capture=False, cwd=cwd)


# ---- smoke ------------------------------------------------------------------


def last_event_text(output: str) -> str:
    """Text of the last JSON event that has text in `agents-cli run --verbose` output
    ("" if none). The bench graph's final event is a content-less `output` event.

    Verbose mode prints each event as `json.dumps(indent=2)`, so an event starts at a
    line that is exactly `{`."""
    output = ANSI.sub("", output)
    decoder = json.JSONDecoder()
    last = ""
    for match in re.finditer(r"^\{$", output, re.MULTILINE):
        try:
            event, _end = decoder.raw_decode(output, match.start())
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        content = event.get("content")
        parts = content.get("parts", []) if isinstance(content, dict) else []
        text = "".join(
            p["text"]
            for p in parts
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
        if text:  # the final `output` event of a run has no content: skip it
            last = text
    return last


# ---- commands ---------------------------------------------------------------


class Context:
    def __init__(
        self,
        repo_root: Path,
        environ: Mapping[str, str],
        run: Runner,
        out: Callable[[str], None],
        platform: infra.Platform | None,
    ) -> None:
        self.repo_root, self.environ, self.run, self.out = repo_root, environ, run, out
        self._platform = platform
        self._outputs: dict[str, str] | None = None

    def output(self, name: str) -> str:
        if self._outputs is None:
            self._outputs = infra.terraform_outputs(
                self.repo_root / infra.TERRAFORM_ROOT, self.run
            )
        value = self._outputs.get(name)
        if not value:
            raise UsageError(f"terraform output {name} is missing")
        return value

    def engine(self) -> str:
        return infra.check_engine_name(self.output("agent_runtime_resource_name"))

    def project(self) -> str:
        project = self.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not project:
            raise UsageError("GOOGLE_CLOUD_PROJECT is not set")
        return project

    def platform(self, engine: str) -> infra.Platform:
        if self._platform is None:
            self._platform = infra.SdkPlatform(self.project(), engine.split("/")[3])
        return self._platform


def cmd_stage(ctx: Context, args: argparse.Namespace) -> int:
    out = Path(args.out)
    if not out.is_absolute():
        out = ctx.repo_root / out
    staged = stage(ctx.repo_root, out)
    ctx.out(f"staged {len(staged)} files into {args.out}")
    return 0


def cmd_deploy(ctx: Context, args: argparse.Namespace) -> int:
    project = ctx.project()
    engine = ctx.engine()
    caller = ctx.output("sandbox_caller_email")
    image = infra.current_image_uri(
        ctx.repo_root, ctx.output("sandbox_image_repository"), ctx.run
    )
    template = infra.find_template(ctx.platform(engine), engine, image)
    if template is None:
        raise Refused(NO_TEMPLATE)
    stage_dir = ctx.repo_root / STAGE_DIR
    staged = stage(ctx.repo_root, stage_dir)
    ctx.out(f"staged {len(staged)} files into {STAGE_DIR}")
    cmd = deploy_args(
        project=project, engine=engine, template=template, caller_sa=caller
    )
    if not (stage_dir / "agents-cli-manifest.yaml").is_file():
        raise Refused("the staged tree has no agents-cli-manifest.yaml")
    done = run_agents_cli(cmd, cwd=stage_dir, repo_root=ctx.repo_root, run=ctx.run)
    if done.returncode != 0:
        print("error: agents-cli deploy failed", file=sys.stderr)
        return 1
    metadata = stage_dir / "deployment_metadata.json"
    if metadata.is_file():
        shutil.copy2(metadata, ctx.repo_root / "deployment_metadata.json")
        ctx.out(
            "deployment_metadata.json now names the engine: do not commit this change"
        )
    return 0


def cmd_smoke(ctx: Context, args: argparse.Namespace) -> int:
    from bench.demo import is_heldout_id

    if is_heldout_id(args.task):
        raise UsageError("held-out tasks never run on the deployed agent")
    url = passthrough_url(ctx.engine())
    message = json.dumps({"task_id": args.task, "run_id": args.run_id})
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/agents_cli_eval.py",
        "run",
        message,
        "--url",
        url,
        "--mode",
        "adk",
        "--verbose",
    ]
    done = ctx.run(cmd, capture=True, cwd=ctx.repo_root, tee=True)
    text = last_event_text(done.stdout or "")
    if done.returncode == 0 and text.lstrip().startswith(SMOKE_PASS):
        ctx.out(f"smoke passed: {text.strip()[:200]}")
        return 0
    session = re.search(r"^Session: (\S+)", ANSI.sub("", done.stdout or ""), re.M)
    stderr = (done.stderr or "").strip()
    if stderr:
        print(stderr[-2000:], file=sys.stderr)
    print(
        f"smoke failed: exit code {done.returncode}; session "
        f"{session.group(1) if session else '(none)'}; last text: "
        f"{text.strip()[:200] or '(none)'}",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("stage", help="build the clean deploy tree")
    p.add_argument("--out", default=str(STAGE_DIR))
    p.set_defaults(handler=cmd_stage)
    p = sub.add_parser("deploy", help="stage, then agents-cli deploy from build/deploy")
    p.set_defaults(handler=cmd_deploy)
    p = sub.add_parser("smoke", help="run one task on the deployed agent")
    p.add_argument("--run-id", default="cloud-smoke-1")
    p.add_argument("--task", default="tc-001")
    p.set_defaults(handler=cmd_smoke)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path = REPO_ROOT,
    run: Runner = run_command,
    environ: Mapping[str, str] | None = None,
    platform: infra.Platform | None = None,
    out: Callable[[str], None] | None = None,
) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    ctx = Context(
        Path(repo_root),
        os.environ if environ is None else environ,
        run,
        out or (lambda line: print(line, flush=True)),
        platform,
    )
    try:
        return args.handler(ctx, args)
    except (Refused, StageRefused) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
