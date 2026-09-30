import pytest

from app.environment.base import WORKDIR, ExecResult
from app.tools import CODER_TOOLS, READ_ONLY_TOOLS
from app.tools._common import PathError, resolve_repo_path
from app.tools.files import LIST_LIMIT, edit_file, grep, list_dir, read_file, write_file
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


async def test_read_file_reports_a_directory_as_a_tool_error():
    env = FakeEnvironment(read_errors={f"{WORKDIR}/pkg": IsADirectoryError("pkg")})
    ctx = fake_tool_context(env)
    result = await read_file("pkg", ctx)
    assert "cannot read pkg" in result["error"]


async def test_edit_file_reports_read_and_write_failures_as_tool_errors():
    env = FakeEnvironment(
        files={f"{WORKDIR}/a.py": "x = 1\n"},
        read_errors={f"{WORKDIR}/pkg": IsADirectoryError("pkg")},
        write_errors={f"{WORKDIR}/a.py": OSError("Permission denied")},
    )
    ctx = fake_tool_context(env)
    assert "cannot read pkg" in (await edit_file("pkg", "x", "y", ctx))["error"]
    result = await edit_file("a.py", "x = 1", "x = 2", ctx)
    assert "cannot write a.py" in result["error"]
    assert "Permission denied" in result["error"]
    assert env.files[f"{WORKDIR}/a.py"] == "x = 1\n"


async def test_write_file_reports_failure_as_a_tool_error():
    env = FakeEnvironment(
        write_errors={f"{WORKDIR}/mini.py/new.py": NotADirectoryError("mini.py")}
    )
    ctx = fake_tool_context(env)
    result = await write_file("mini.py/new.py", "x = 1\n", ctx)
    assert "cannot write mini.py/new.py" in result["error"]


@pytest.mark.parametrize("path", [".", ""])
async def test_list_dir_lists_repo_relative_entries(path):
    listing = f"{WORKDIR}/a.py\n{WORKDIR}/pkg/b.py\n"
    env = FakeEnvironment(
        responses={"test -d": ExecResult(exit_code=0, stdout=listing, stderr="")}
    )
    ctx = fake_tool_context(env)
    assert await list_dir(path, ctx) == {"entries": ["a.py", "pkg/b.py"]}
    assert env.commands[0].startswith(f"test -d {WORKDIR} ")


async def test_list_dir_reports_missing_or_non_directory_paths():
    env = FakeEnvironment(
        responses={"test -d": ExecResult(exit_code=3, stdout="", stderr="")}
    )
    ctx = fake_tool_context(env)
    result = await list_dir("nope", ctx)
    assert "nope" in result["error"] and "entries" not in result
    assert "error" in await list_dir("../outside", ctx)


async def test_list_dir_flags_truncation_at_the_entry_limit():
    lines = "".join(f"{WORKDIR}/f{i:04}.py\n" for i in range(LIST_LIMIT + 1))
    env = FakeEnvironment(
        responses={"test -d": ExecResult(exit_code=0, stdout=lines, stderr="")}
    )
    ctx = fake_tool_context(env)
    result = await list_dir(".", ctx)
    assert len(result["entries"]) == LIST_LIMIT and result["truncated"] is True
    assert result["entries"][-1] == f"f{LIST_LIMIT - 1:04}.py"


async def test_list_dir_at_exactly_the_limit_is_not_truncated():
    lines = "".join(f"{WORKDIR}/f{i:04}.py\n" for i in range(LIST_LIMIT))
    env = FakeEnvironment(
        responses={"test -d": ExecResult(exit_code=0, stdout=lines, stderr="")}
    )
    result = await list_dir(".", fake_tool_context(env))
    assert len(result["entries"]) == LIST_LIMIT and "truncated" not in result


async def test_empty_path_is_never_an_exception():
    env = FakeEnvironment(read_errors={WORKDIR: IsADirectoryError(WORKDIR)})
    ctx = fake_tool_context(env)
    assert "error" in await read_file("", ctx)
    assert "error" in await edit_file("", "x", "y", ctx)
    assert "matches" in await grep("x", "", ctx)


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
