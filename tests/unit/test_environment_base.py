import pytest

from app.environment import docker, registry
from app.environment.base import InfraError, tail_lines, truncate
from app.environment.docker import DockerEnvironment


def test_truncate_keeps_short_text():
    assert truncate("abc", cap=10) == "abc"


def test_truncate_keeps_head_and_tail_with_marker():
    out = truncate("a" * 50 + "b" * 50, cap=20)
    assert out.startswith("a" * 10) and out.endswith("b" * 10)
    assert "80 chars truncated" in out


def test_tail_lines():
    assert tail_lines("1\n2\n3\n4", 2) == "3\n4"


async def test_registry_lifecycle():
    class Env:
        env_id = "e1"
        closed = False

        async def close(self):
            self.closed = True

    env = Env()
    assert registry.register(env) == "e1"
    assert registry.get("e1") is env
    await registry.release("e1")
    assert env.closed
    with pytest.raises(InfraError):
        registry.get("e1")
    await registry.release("e1")  # releasing twice is a no-op


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5", ""])
async def test_invalid_sandbox_ttl_is_rejected_before_docker_runs(value, monkeypatch):
    async def no_docker(*args, **kwargs):
        raise AssertionError("docker must not be invoked")

    monkeypatch.setattr(docker, "_run", no_docker)
    monkeypatch.setenv("SANDBOX_TTL_S", value)
    with pytest.raises(InfraError, match="SANDBOX_TTL_S"):
        await DockerEnvironment.start()


@pytest.mark.parametrize(("value", "expected"), [(None, "1800"), ("90", "90")])
async def test_sandbox_sleeps_for_its_ttl_not_forever(value, expected, monkeypatch):
    calls = []

    async def fake_run(args, **kwargs):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(docker, "_run", fake_run)
    if value is None:
        monkeypatch.delenv("SANDBOX_TTL_S", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_TTL_S", value)
    await DockerEnvironment.start()
    assert calls[0][:2] == ["docker", "run"] and "--rm" in calls[0]
    assert calls[0][-2:] == ["sleep", expected]


@pytest.mark.parametrize(
    ("timeout", "expected"), [(0.2, "1"), (1, "1"), (1.2, "2"), (120.0, "120")]
)
async def test_exec_timeout_is_rounded_up_to_at_least_one_second(
    timeout, expected, monkeypatch
):
    calls = []

    async def fake_run(args, **kwargs):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(docker, "_run", fake_run)
    await DockerEnvironment("c1").exec("true", timeout=timeout)
    command = calls[0]
    assert command[command.index("timeout") : command.index("sh")] == [
        "timeout",
        "-k",
        "5",
        expected,
    ]


@pytest.mark.parametrize(
    ("code", "timed_out"), [(0, False), (1, False), (124, True), (137, True)]
)
async def test_exec_reports_timeout_exit_codes(code, timed_out, monkeypatch):
    async def fake_run(args, **kwargs):
        return code, "", ""

    monkeypatch.setattr(docker, "_run", fake_run)
    result = await DockerEnvironment("c1").exec("true")
    assert (result.exit_code, result.timed_out) == (code, timed_out)


@pytest.mark.parametrize(
    "stderr",
    [
        "Error response from daemon: No such container: c1\n",
        "Error response from daemon: container abc is not running\n",
        "Error: No such container: c1\n",
    ],
)
async def test_daemon_errors_are_infra_errors(stderr, monkeypatch):
    async def fake_run(args, **kwargs):
        return 1, "", stderr

    monkeypatch.setattr(docker, "_run", fake_run)
    env = DockerEnvironment("c1")
    with pytest.raises(InfraError):
        await env.exec("true")
    with pytest.raises(InfraError):
        await env.read_file("/workspace/repo/a.py")
    with pytest.raises(InfraError):
        await env.write_file("/workspace/repo/a.py", "x")


@pytest.mark.parametrize(
    "stderr",
    [
        "the service is not running\n",
        "grep: Error response from daemon\n",
        "cat: x: No such container\n",
    ],
)
async def test_agent_stderr_that_mentions_daemon_words_is_not_an_infra_error(
    stderr, monkeypatch
):
    async def fake_run(args, **kwargs):
        return 1, "", stderr

    monkeypatch.setattr(docker, "_run", fake_run)
    result = await DockerEnvironment("c1").exec("svc status")
    assert (result.exit_code, result.stderr) == (1, stderr)
