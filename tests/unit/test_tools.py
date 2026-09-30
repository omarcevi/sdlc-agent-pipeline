import pytest

from app.environment.base import WORKDIR, ExecResult
from app.tools import CODER_TOOLS, READ_ONLY_TOOLS
from app.tools._common import PathError, resolve_repo_path
from app.tools.files import edit_file, read_file, write_file
from app.tools.shell import bash
from tests.fakes import FakeEnvironment, fake_tool_context


def test_resolve_repo_path_accepts_relative_and_repo_absolute():
    assert resolve_repo_path("pkg/a.py") == f"{WORKDIR}/pkg/a.py"
    assert resolve_repo_path(f"{WORKDIR}/pkg/a.py") == f"{WORKDIR}/pkg/a.py"
    assert resolve_repo_path(".") == WORKDIR


@pytest.mark.parametrize(
    "bad", ["../x", "/etc/passwd", f"{WORKDIR}/../x", "pkg/../../x"]
)
def test_resolve_repo_path_rejects_escapes(bad):
    with pytest.raises(PathError):
        resolve_repo_path(bad)


async def test_paths_outside_repo_are_refused():
    env = FakeEnvironment(files={"/etc/passwd": "root"})
    ctx = fake_tool_context(env)
    result = await read_file("../../etc/passwd", ctx)
    assert "error" in result
    result = await write_file("/tmp/evil.py", "x", ctx)
    assert "error" in result and "/tmp/evil.py" not in env.files


async def test_read_file_returns_content_and_reports_missing():
    env = FakeEnvironment(files={f"{WORKDIR}/a.py": "print(1)\n"})
    ctx = fake_tool_context(env)
    assert (await read_file("a.py", ctx))["content"] == "print(1)\n"
    assert "error" in await read_file("missing.py", ctx)


async def test_edit_file_requires_unique_match():
    env = FakeEnvironment(files={f"{WORKDIR}/a.py": "x = 1\nx = 1\ny = 2\n"})
    ctx = fake_tool_context(env)
    assert "2 times" in (await edit_file("a.py", "x = 1", "x = 3", ctx))["error"]
    assert "not found" in (await edit_file("a.py", "z = 9", "z = 1", ctx))["error"]
    assert "error" in await edit_file("a.py", "", "q", ctx)
    assert (await edit_file("a.py", "y = 2", "y = 5", ctx)) == {
        "ok": True,
        "path": "a.py",
    }
    assert env.files[f"{WORKDIR}/a.py"].endswith("y = 5\n")


async def test_write_file_only_creates_new_files():
    env = FakeEnvironment(files={f"{WORKDIR}/a.py": "old"})
    ctx = fake_tool_context(env)
    assert "already exists" in (await write_file("a.py", "new", ctx))["error"]
    assert (await write_file("tests/test_new.py", "def test(): pass\n", ctx))["ok"]
    assert env.files[f"{WORKDIR}/tests/test_new.py"] == "def test(): pass\n"


async def test_bash_truncates_huge_output():
    env = FakeEnvironment(
        responses={"yes": ExecResult(exit_code=0, stdout="y\n" * 500_000, stderr="")}
    )
    ctx = fake_tool_context(env)
    result = await bash("yes | head -n 500000", ctx)
    assert result["exit_code"] == 0
    assert len(result["stdout"]) < 10_200
    assert "chars truncated" in result["stdout"]


def test_tool_sets():
    assert {t.__name__ for t in READ_ONLY_TOOLS} == {"read_file", "list_dir", "grep"}
    assert {t.__name__ for t in CODER_TOOLS} == {
        "read_file",
        "list_dir",
        "grep",
        "edit_file",
        "write_file",
        "bash",
    }
