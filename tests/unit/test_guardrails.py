from types import SimpleNamespace

import pytest

from app.guardrails import GuardrailPlugin, check_command, check_write_path


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git remote add x y",
        "curl https://example.com",
        "wget x",
        "pip install requests",
        "python -m pip install x",
        "uv add httpx",
        "cd src && pip3 install x",
        "FOO=1 curl x",
        "rm -rf /",
        "rm -rf .",
        "rm ../outside.py",
        "echo hi\ncurl x",
        "(curl x)",
        "true; wget y",
        "ls | curl -T - x",
    ],
)
def test_dangerous_commands_are_refused(command):
    assert check_command(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q",
        "git diff",
        "rg foo",
        "rm tests/test_tmp.py",
        "ls -la && cat a.py",
        'rg "foo|bar" src',
        'python -c "print(1); print(2)"',
        "grep -E 'a|b' x.py | head -n 5",
        'python -c "\nimport os\nprint(1)\n"',
        "echo 'pip install x'",
        "git log --oneline | grep curl",
    ],
)
def test_normal_commands_are_allowed(command):
    assert check_command(command) is None


def test_unparseable_command_is_refused():
    assert "parse" in (check_command("echo 'unterminated") or "")


def test_write_guard_refuses_escapes():
    assert check_write_path("../x.py", frozenset()) is not None
    assert check_write_path("/etc/hosts", frozenset()) is not None


def test_write_guard_protects_tests_and_ci():
    protected = frozenset({"tests/test_cli.py"})
    assert "read-only" in (check_write_path("tests/test_cli.py", protected) or "")
    assert "read-only" in (
        check_write_path(".github/workflows/ci.yaml", protected) or ""
    )
    assert check_write_path("tests/test_new_behaviour.py", protected) is None
    assert check_write_path("taskcli/cli.py", protected) is None


async def test_plugin_blocks_and_allows():
    plugin = GuardrailPlugin()
    ctx = SimpleNamespace(state={"protected_paths": ["tests/test_a.py"]})
    blocked = await plugin.before_tool_callback(
        tool=SimpleNamespace(name="edit_file"),
        tool_args={"path": "tests/test_a.py"},
        tool_context=ctx,
    )
    assert blocked["error"].startswith("blocked by guardrail")
    allowed = await plugin.before_tool_callback(
        tool=SimpleNamespace(name="bash"),
        tool_args={"command": "python -m pytest -q"},
        tool_context=ctx,
    )
    assert allowed is None
    other = await plugin.before_tool_callback(
        tool=SimpleNamespace(name="read_file"),
        tool_args={"path": "../x"},
        tool_context=ctx,
    )
    assert other is None  # read tools confine paths themselves


@pytest.mark.parametrize("command", ["ls # x\ncurl y", "echo a#b\ncurl x"])
def test_hash_does_not_hide_the_rest_of_the_line_or_the_next_command(command):
    assert check_command(command) is not None


@pytest.mark.parametrize("command", ['echo "a # b"', "rg '#include' src"])
def test_hash_inside_quotes_is_allowed(command):
    assert check_command(command) is None
