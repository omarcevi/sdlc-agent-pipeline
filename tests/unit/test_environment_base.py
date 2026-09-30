import pytest

from app.environment import registry
from app.environment.base import InfraError, tail_lines, truncate


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
