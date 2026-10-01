"""scripts/stage_deploy.py: the clean tree that agents-cli uploads, the deploy
command line and the smoke check. No GCP, no network, no agents-cli process.

The seal tests spy on file access: a path under a held-out directory, a `.env` or
`~/.config` must never be opened, listed, stat-ed or copied by the stager."""

import builtins
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "stage_deploy.py"
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("stage_deploy", SCRIPT)
sd = importlib.util.module_from_spec(_spec)
sys.modules["stage_deploy"] = sd
_spec.loader.exec_module(sd)
import sandbox_infra as infra  # noqa: E402  (stage_deploy imports the same module)

ENGINE = "projects/123/locations/us-central1/reasoningEngines/456"
REPOSITORY = "us-central1-docker.pkg.dev/proj/issue-to-pr"
CALLER = "sandbox-caller@proj.iam.gserviceaccount.com"
TREE = "abcdef0123456789abcdef0123456789abcdef01"
IMAGE = f"{REPOSITORY}/sandbox:{TREE[:12]}"
TEMPLATE = f"{ENGINE}/sandboxEnvironmentTemplates/t1"
TF_OUTPUTS = {
    "agent_runtime_resource_name": ENGINE,
    "sandbox_image_repository": REPOSITORY,
    "sandbox_caller_email": CALLER,
}
HELD = "zz-h01"  # a held-out-looking directory name in the synthetic tree
DOTENV_MARKER = "SECRET_MARKER_DO_NOT_COPY"


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_tree(root: Path, *, split: str = "dev") -> Path:
    for name in sd.ROOT_FILES:
        write(root / name, name)
    write(root / ".env", DOTENV_MARKER)
    write(root / "app" / "__init__.py")
    write(root / "app" / "agent.py")
    write(root / "app" / "__pycache__" / "agent.cpython-312.pyc")
    write(root / "app" / "stray.pyc")
    write(root / "app" / ".pytest_cache" / "v" / "cache")
    write(root / "bench" / "__init__.py")
    write(root / "bench" / "run.py")
    write(root / "bench" / "notes.md")  # not a .py file: not staged
    write(root / "bench" / "__pycache__" / "run.cpython-312.pyc")
    write(root / "bench" / "review_probes" / "rp-01" / "probe.yaml")
    write(root / "bench" / "repos" / "demo" / "src" / "a.py")
    write(root / "bench" / "repos" / "demo" / "src" / "__pycache__" / "a.pyc")
    write(root / "bench" / "repos" / "demo" / ".pytest_cache" / "x")
    task = root / "bench" / "tasks" / "dm-001"
    write(task / "task.yaml", f"id: dm-001\nsplit: {split}\n")
    write(task / "plant" / "src" / "a.py")
    write(task / "plant" / "src" / "__pycache__" / "a.pyc")
    for sealed in ("solution", "hidden_tests", "shortcut"):
        write(task / sealed / "f.py")
    held = root / "bench" / "tasks" / HELD
    write(held / "task.yaml", "id: zz-h01\nsplit: heldout\n")
    write(held / "plant" / "a.py")
    write(held / "hidden_tests" / "t.py")
    write(root / "docs" / "a.md")
    write(root / "results" / "r.json")
    write(root / "tests" / "t.py")
    return root


EXPECTED = sorted(
    [
        *sd.ROOT_FILES,
        "app/__init__.py",
        "app/agent.py",
        "bench/__init__.py",
        "bench/run.py",
        "bench/repos/demo/src/a.py",
        "bench/tasks/dm-001/task.yaml",
        "bench/tasks/dm-001/plant/src/a.py",
    ]
)


def all_files(root: Path) -> list[str]:
    found = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            found.append(str(Path(base, name).relative_to(root)))
    return sorted(found)


@pytest.fixture
def tree(tmp_path):
    return make_tree(tmp_path / "repo")


def test_stage_copies_exactly_the_allowed_files(tree, tmp_path):
    out = tmp_path / "out"
    staged = sd.stage(tree, out)
    assert sorted(staged) == EXPECTED
    assert all_files(out) == EXPECTED


def install_spy(monkeypatch, forbidden):
    """Make every file-access entry point raise on a path `forbidden` accepts."""
    seen: list[str] = []

    def guard(path):
        try:
            text = os.fspath(path)
        except TypeError:
            return
        if isinstance(text, bytes):
            text = os.fsdecode(text)
        if forbidden(text):
            seen.append(text)
            raise AssertionError("forbidden path accessed")

    def wrap(owner, name):
        real = getattr(owner, name)

        def spy(path, *args, **kwargs):
            guard(path)
            return real(path, *args, **kwargs)

        monkeypatch.setattr(owner, name, spy)

    for owner, name in [
        (builtins, "open"),
        (io_module(), "open"),
        (os, "scandir"),
        (os, "listdir"),
        (os, "stat"),
        (os, "lstat"),
        (os, "walk"),
        (os, "access"),
        (shutil, "copy2"),
        (shutil, "copy"),
        (shutil, "copyfile"),
        (shutil, "copytree"),
    ]:
        wrap(owner, name)

    for name in ("open", "read_text", "read_bytes", "iterdir", "stat", "lstat"):
        real = getattr(Path, name)

        def spy(self, *args, real_method=real, **kwargs):
            guard(self)
            return real_method(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, spy)
    return seen


def io_module():
    import io

    return io


def test_stage_skips_heldout_dirs_without_opening_them(tree, tmp_path, monkeypatch):
    out = tmp_path / "out"
    seen = install_spy(
        monkeypatch, lambda p: f"/{HELD}/" in p + "/" and not p.startswith(str(out))
    )
    sd.stage(tree, out)
    assert seen == []
    assert all_files(out) == EXPECTED


def test_stager_never_reads_dotenv_or_the_config_dir(tree, tmp_path, monkeypatch):
    home = tmp_path / "home"
    write(home / ".config" / "issue-to-pr" / "token", "tok")
    monkeypatch.setenv("HOME", str(home))
    out = tmp_path / "out"

    def forbidden(path: str) -> bool:
        return (
            path.endswith("/.env") or "/.config" in path or path.startswith(str(home))
        )

    seen = install_spy(monkeypatch, forbidden)
    sd.stage(tree, out)
    assert seen == []
    assert DOTENV_MARKER not in "".join(
        (out / name).read_text(encoding="utf-8") for name in all_files(out)
    )


def test_stage_refuses_a_task_yaml_that_is_not_dev(tmp_path):
    tree = make_tree(tmp_path / "repo", split="heldout")
    with pytest.raises(sd.StageRefused) as caught:
        sd.stage(tree, tmp_path / "out")
    assert str(caught.value) == "staging refused: a task outside the dev split"
    assert sd.main(["stage", "--out", str(tmp_path / "out2")], repo_root=tree) == 1


def test_stage_rebuilds_from_scratch(tree, tmp_path):
    out = tmp_path / "out"
    sd.stage(tree, out)
    write(out / "leftover.txt")
    write(out / "app" / "old.py")
    sd.stage(tree, out)
    assert all_files(out) == EXPECTED
    sd.stage(tree, out)
    assert all_files(out) == EXPECTED


def test_stage_refuses_an_out_dir_that_holds_the_project(tree):
    with pytest.raises(sd.StageRefused):
        sd.stage(tree, tree)
    with pytest.raises(sd.StageRefused):
        sd.stage(tree, tree.parent)
    assert (tree / "Dockerfile").exists()


@pytest.mark.parametrize("name", ["app", "bench", "runs", "results", "docs", "build"])
def test_stage_refuses_an_out_dir_inside_the_repo_but_outside_build(tree, name):
    write(tree / name / "keep.txt", "keep")
    with pytest.raises(sd.StageRefused):
        sd.stage(tree, tree / name)
    assert (tree / name / "keep.txt").read_text() == "keep"
    assert sd.main(["stage", "--out", name], repo_root=tree) == 1


def test_stage_accepts_an_out_dir_under_build(tree):
    sd.stage(tree, tree / "build" / "deploy")
    assert (tree / "build" / "deploy" / "Dockerfile").is_file()
    sd.stage(tree, tree / "build" / "deploy")  # the marker lets it rebuild


def test_stage_removes_only_an_empty_or_marked_directory(tmp_path, tree):
    foreign = tmp_path / "foreign"
    write(foreign / "precious.txt", "keep")
    with pytest.raises(sd.StageRefused):
        sd.stage(tree, foreign)
    assert (foreign / "precious.txt").read_text() == "keep"
    empty = tmp_path / "empty"
    empty.mkdir()
    sd.stage(tree, empty)
    assert (empty / "Dockerfile").is_file()


def test_a_refused_task_leaves_no_half_built_tree(tmp_path):
    tree = make_tree(tmp_path / "repo", split="heldout")
    out = tmp_path / "out"
    with pytest.raises(sd.StageRefused):
        sd.stage(tree, out)
    assert not out.exists()


def test_deployment_metadata_is_copied_only_when_present(tree, tmp_path):
    (tree / "deployment_metadata.json").unlink()
    staged = sd.stage(tree, tmp_path / "out")
    assert "deployment_metadata.json" not in staged
    assert "Dockerfile" in staged


def test_a_missing_required_root_file_stops_staging(tree, tmp_path):
    (tree / "uv.lock").unlink()
    with pytest.raises(sd.StageRefused):
        sd.stage(tree, tmp_path / "out")


def test_real_tree_staging_has_no_sealed_paths(tmp_path, monkeypatch):
    out = tmp_path / "out"
    # The spy goes in before staging: a pruning regression raises at the first
    # access under a held-out directory, before anything in it is read.
    seen = install_spy(
        monkeypatch,
        lambda p: (
            bool(re.search(r"-h[0-9]{2}(/|$)", p)) and not p.startswith(str(tmp_path))
        ),
    )
    staged = sd.stage(ROOT, out)
    assert seen == []
    held = sealed = dotenv = 0
    task_dirs = 0
    for base, dirs, files in os.walk(out):
        for name in list(dirs):
            if sd.HELDOUT_DIR.search(name):
                held += 1
                dirs.remove(name)  # never descend, even into a staged one
            elif name in {"hidden_tests", "solution", "shortcut"}:
                sealed += 1
            elif name in {"__pycache__", ".pytest_cache"}:
                sealed += 1
        dotenv += sum(1 for f in files if f == ".env" or f.endswith(".pyc"))
        if Path(base).parent == out / "bench" / "tasks":
            task_dirs += 1
    assert held == 0, "staged tree holds held-out directories"
    assert sealed == 0, "staged tree holds sealed or cache directories"
    assert dotenv == 0, "staged tree holds .env or bytecode files"
    assert task_dirs > 0, "staged tree holds no dev tasks"
    assert (out / "bench" / "repos").is_dir()
    assert not (out / "bench" / "tasks" / "tc-001" / "solution").exists()
    assert staged == sorted(staged)


def test_staged_tree_has_no_dotenv(tree, tmp_path):
    out = tmp_path / "out"
    sd.stage(tree, out)
    assert not (out / ".env").exists()
    assert not any(name.endswith(".env") for name in all_files(out))


def test_deploy_env_is_exact():
    env = sd.deploy_env(engine=ENGINE, template=TEMPLATE, caller_sa=CALLER)
    assert env == {
        "ENVIRONMENT_BACKEND": "agent_runtime",
        "SANDBOX_ENGINE": ENGINE,
        "SANDBOX_TEMPLATE": TEMPLATE,
        "SANDBOX_CALLER_SA": CALLER,
        "BQ_ANALYTICS_ENABLED": "1",
        "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
    }


def test_deploy_args_use_update_only_and_the_sizes():
    args = sd.deploy_args(
        project="proj", engine=ENGINE, template=TEMPLATE, caller_sa=CALLER
    )
    env = sd.deploy_env(engine=ENGINE, template=TEMPLATE, caller_sa=CALLER)
    assert args == [
        "agents-cli",
        "deploy",
        "--project",
        "proj",
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


class FakeRun:
    """Answers git, terraform and smoke commands; records agents-cli with its cwd."""

    def __init__(self, *, smoke_stdout="", smoke_code=0, smoke_stderr=""):
        self.calls: list[tuple[list[str], str | None]] = []
        self.uv_calls: list[tuple[list[str], str | None, bool]] = []
        self.smoke_stdout = smoke_stdout
        self.smoke_code = smoke_code
        self.smoke_stderr = smoke_stderr

    def __call__(self, cmd, *, capture=True, cwd=None, tee=False):
        cmd = list(cmd)
        self.calls.append((cmd, None if cwd is None else str(cwd)))
        joined = " ".join(cmd)
        if "rev-parse HEAD:sandbox_image" in joined:
            return done(TREE + "\n")
        if cmd[0] == "terraform":
            payload = {k: {"value": v} for k, v in TF_OUTPUTS.items()}
            return done(json.dumps(payload))
        if cmd[0] == "agents-cli":
            if cwd is not None:
                # what agents-cli writes when it finishes
                write(Path(cwd) / "deployment_metadata.json", '{"deployed": true}')
            return done()
        if cmd[0] == "uv":
            self.uv_calls.append((cmd, None if cwd is None else str(cwd), tee))
            return subprocess.CompletedProcess(
                cmd, self.smoke_code, stdout=self.smoke_stdout, stderr=self.smoke_stderr
            )
        raise AssertionError(f"unexpected command: {joined}")

    def agents_cli(self):
        return [c for c in self.calls if c[0][0] == "agents-cli"]


def done(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


class FakePlatform:
    def __init__(self, templates=()):
        self.templates = list(templates)

    def list_templates(self, engine):
        return list(self.templates)


def active_template(image=IMAGE, internet=False):
    return infra.Template(
        name=TEMPLATE,
        image_uri=image,
        state="STATE_ACTIVE",
        internet_access=internet,
        ports=[8080],
    )


@pytest.fixture
def deploy_repo(tree):
    return tree


def run_deploy(repo, run, platform, environ=None):
    return sd.main(
        ["deploy"],
        repo_root=repo,
        run=run,
        platform=platform,
        environ={"GOOGLE_CLOUD_PROJECT": "proj"} if environ is None else environ,
        out=lambda line: None,
    )


def test_deploy_never_runs_from_the_project_root(deploy_repo):
    run = FakeRun()
    assert run_deploy(deploy_repo, run, FakePlatform([active_template()])) == 0
    ((_cmd, cwd),) = run.agents_cli()
    assert cwd is not None
    assert Path(cwd).resolve() == (deploy_repo / "build" / "deploy").resolve()
    assert Path(cwd).resolve() != deploy_repo.resolve()
    # the helper itself refuses any other directory
    with pytest.raises(sd.Refused) as caught:
        sd.run_agents_cli(
            ["agents-cli", "deploy"], cwd=deploy_repo, repo_root=deploy_repo, run=run
        )
    assert str(caught.value) == "deploy runs only from build/deploy"
    assert len(run.agents_cli()) == 1


def test_deploy_stages_then_passes_the_flags(deploy_repo):
    run = FakeRun()
    assert run_deploy(deploy_repo, run, FakePlatform([active_template()])) == 0
    ((cmd, _cwd),) = run.agents_cli()
    assert cmd == sd.deploy_args(
        project="proj", engine=ENGINE, template=TEMPLATE, caller_sa=CALLER
    )
    assert (deploy_repo / "build" / "deploy" / "Dockerfile").exists()
    assert not (deploy_repo / "build" / "deploy" / ".env").exists()


def test_deploy_copies_metadata_back(deploy_repo):
    run = FakeRun()
    assert run_deploy(deploy_repo, run, FakePlatform([active_template()])) == 0
    assert (
        deploy_repo / "deployment_metadata.json"
    ).read_text() == '{"deployed": true}'


def test_deploy_refuses_without_a_template(deploy_repo, capsys):
    run = FakeRun()
    other = active_template(image=f"{REPOSITORY}/sandbox:other")
    assert run_deploy(deploy_repo, run, FakePlatform([other])) == 1
    assert "no ACTIVE template for the current sandbox image" in capsys.readouterr().err
    assert run.agents_cli() == []


def test_deploy_needs_the_project(deploy_repo):
    run = FakeRun()
    assert run_deploy(deploy_repo, run, FakePlatform([active_template()]), {}) == 2
    assert run.agents_cli() == []


def test_deploy_checks_the_engine_name_form(deploy_repo, monkeypatch):
    run = FakeRun()
    monkeypatch.setitem(TF_OUTPUTS, "agent_runtime_resource_name", "456")
    assert run_deploy(deploy_repo, run, FakePlatform([active_template()])) == 2
    assert run.agents_cli() == []


def test_passthrough_url_takes_the_location_from_the_engine():
    assert sd.passthrough_url(ENGINE) == (
        "https://us-central1-aiplatform.googleapis.com/reasoningEngines/v1/"
        f"{ENGINE}/api"
    )
    europe = "projects/1/locations/europe-west4/reasoningEngines/9"
    assert sd.passthrough_url(europe).startswith(
        "https://europe-west4-aiplatform.googleapis.com/"
    )


def verbose_output(*texts, final_output=True):
    """What `agents-cli run --verbose` prints: human text, then each event as
    indented JSON. In the real bench graph the last event is the `deliver_patch` node's
    `output` event, which carries no `content`."""
    chunks = []
    for text in texts:
        event = {"author": "deliver_patch", "content": {"parts": [{"text": text}]}}
        chunks.append(
            f"[deliver_patch]: {text}\n\n" + json.dumps(event, indent=2) + "\n"
        )
    if final_output:
        last = {
            "author": "issue_to_pr",
            "output": {"patch": "p.diff"},
            "node_info": {"path": "issue_to_pr@1/deliver_patch@1"},
        }
        chunks.append("\n" + json.dumps(last, indent=2) + "\n")
    return "".join(chunks) + "\nSession: abc-123\n"


def run_smoke(repo, stdout, *extra, code=0):
    run = FakeRun(smoke_stdout=stdout)
    status = sd.main(
        ["smoke", *extra],
        repo_root=repo,
        run=run,
        environ={},
        out=lambda line: None,
    )
    return status, run


def test_smoke_passes_only_on_patch_written(tree):
    status, run = run_smoke(tree, verbose_output("working", "patch written to p.diff"))
    assert status == 0
    ((cmd, _cwd),) = [c for c in run.calls if c[0][0] == "uv"]
    assert cmd[:4] == ["uv", "run", "python", "scripts/agents_cli_eval.py"]
    assert cmd[4] == "run"
    assert json.loads(cmd[5]) == {"task_id": "tc-001", "run_id": "cloud-smoke-1"}
    assert cmd[6:] == [
        "--url",
        sd.passthrough_url(ENGINE),
        "--mode",
        "adk",
        "--verbose",
    ]
    # the last event that carries text decides, not an earlier one
    assert run_smoke(tree, verbose_output("patch written", "failed: budget"))[0] == 1
    assert run_smoke(tree, verbose_output("no patch"))[0] == 1


def test_smoke_skips_the_contentless_final_output_event(tree):
    ok = verbose_output("working", "patch written to p.diff", final_output=True)
    assert run_smoke(tree, ok)[0] == 0
    assert sd.last_event_text(ok) == "patch written to p.diff"
    failed = verbose_output("patch written", "report_failure: declined")
    assert run_smoke(tree, failed)[0] == 1


def test_smoke_fails_when_no_event_has_text(tree):
    assert run_smoke(tree, verbose_output())[0] == 1
    assert run_smoke(tree, "")[0] == 1


def test_smoke_failure_prints_the_code_stderr_and_session(tree, capsys):
    run = FakeRun(smoke_stdout=verbose_output(), smoke_code=3, smoke_stderr="HTTP 403")
    status = sd.main(
        ["smoke"], repo_root=tree, run=run, environ={}, out=lambda line: None
    )
    err = capsys.readouterr().err
    assert status == 1
    assert "HTTP 403" in err and "exit code 3" in err and "abc-123" in err


def test_smoke_streams_and_runs_from_the_project_root(tree):
    _status, run = run_smoke(tree, verbose_output("patch written"))
    ((_cmd, cwd, tee),) = [(c, w, t) for c, w, t in run.uv_calls]
    assert tee is True
    assert Path(cwd).resolve() == tree.resolve()


def test_smoke_options_and_heldout_refusal(tree):
    status, run = run_smoke(
        tree, verbose_output("patch written"), "--task", "md-001", "--run-id", "r9"
    )
    assert status == 0
    payload = next(c for c, _ in run.calls if c[0] == "uv")[5]
    assert json.loads(payload) == {"task_id": "md-001", "run_id": "r9"}
    status, run = run_smoke(tree, verbose_output("patch written"), "--task", "sr-h02")
    assert status == 2
    assert [c for c, _ in run.calls if c[0] == "uv"] == []


def test_dockerfile_copies_bench():
    lines = [
        line.strip()
        for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "COPY ./bench ./bench" in lines
    assert lines.index("COPY ./bench ./bench") == lines.index("COPY ./app ./app") + 1


def test_stage_never_removes_a_preexisting_sibling(tree, tmp_path):
    out = tmp_path / "mine"
    for name in ("mine.tmp", "mine.staging", ".mine.staging-x"):
        write(tmp_path / name / "precious.txt", "keep")
    sd.stage(tree, out)
    for name in ("mine.tmp", "mine.staging", ".mine.staging-x"):
        assert (tmp_path / name / "precious.txt").read_text() == "keep"
    assert all_files(out) == EXPECTED
    leftovers = [
        p.name for p in tmp_path.iterdir() if p.name.startswith(".mine.staging-")
    ]
    assert leftovers == [".mine.staging-x"]  # its own work dir is gone


def test_a_refused_unmarked_directory_says_how_to_fix_it(tree):
    out = tree / "build" / "deploy"
    write(out / "old.txt")
    with pytest.raises(sd.StageRefused) as caught:
        sd.stage(tree, out)
    assert "remove it by hand" in str(caught.value)


def test_tee_survives_a_flood_on_stderr_and_bad_bytes():
    code = (
        "import sys\n"
        "sys.stderr.write('e' * 400000)\n"
        "sys.stdout.buffer.write(b'\\xff\\xfe ok\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('done\\n')\n"
    )
    done = sd.run_command([sys.executable, "-c", code], tee=True)
    assert done.returncode == 0
    assert len(done.stderr) > 400000 and done.stderr.endswith("done\n")
    assert "ok" in done.stdout


def test_tee_keeps_collecting_when_the_terminal_pipe_breaks(monkeypatch):
    class Broken:
        def write(self, _text):
            raise BrokenPipeError

        def flush(self):
            raise BrokenPipeError

    monkeypatch.setattr(sys, "stdout", Broken())
    code = "import sys\nfor _ in range(200):\n    print('x' * 3000)\nprint('END')\n"
    done = sd.run_command([sys.executable, "-c", code], tee=True)
    assert done.returncode == 0
    assert done.stdout.endswith("END\n")
