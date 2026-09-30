"""Deterministic stand-ins for LLMs, sandboxes and tool contexts. Tests only."""

import json
import shlex
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel, PrivateAttr

from app.environment import registry
from app.environment.base import DEFAULT_TIMEOUT_S, WORKDIR, ExecResult
from app.nodes.intake import BASELINE_SHA_CMD

BASELINE_SHA = "0123456789abcdef0123456789abcdef01234567"


def call(name: str, **args: Any) -> dict:
    return {"call": name, "args": args}


def text(value: str) -> dict:
    return {"text": value}


def raises(error: Exception) -> dict:
    """A model call that fails with `error`, as an API or transport error would."""
    return {"raise": error}


def malformed_call() -> dict:
    """A model response the provider rejected as a malformed function call."""
    return {"malformed": True}


def json_out(value: BaseModel | dict) -> dict:
    data = value.model_dump() if isinstance(value, BaseModel) else value
    return text(json.dumps(data))


class FakeLlm(BaseLlm):
    """Scripted model: each model call pops the next step."""

    model: str = "fake-model"
    _steps: list[dict] = PrivateAttr(default_factory=list)
    _calls: int = PrivateAttr(default=0)

    def __init__(self, steps: list[dict], **kwargs: Any):
        super().__init__(**kwargs)
        self._steps = list(steps)

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._calls += 1
        if not self._steps:
            raise AssertionError(
                f"{self.model}: script exhausted at call {self._calls}"
            )
        step = self._steps.pop(0)
        if "raise" in step:
            raise step["raise"]
        if "malformed" in step:
            yield LlmResponse(
                error_code="MALFORMED_FUNCTION_CALL",
                error_message="Malformed function call: print(read_file(",
                finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL,
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=1000, candidates_token_count=100
                ),
            )
            return
        if "call" in step:
            part = types.Part(
                function_call=types.FunctionCall(name=step["call"], args=step["args"])
            )
        else:
            part = types.Part(text=step["text"])
        yield LlmResponse(
            content=types.Content(role="model", parts=[part]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=1000, candidates_token_count=100
            ),
        )


class FakeEnvironment:
    """In-memory sandbox. `responses` maps a command prefix to one result or a
    queue of results (the last one repeats). Unless overridden, the baseline
    commit id query answers BASELINE_SHA. `read_errors` / `write_errors` map a
    path to the OSError that reading or writing it raises."""

    def __init__(
        self,
        responses: dict[str, ExecResult | list[ExecResult]] | None = None,
        files: dict[str, str] | None = None,
        read_errors: dict[str, OSError] | None = None,
        write_errors: dict[str, OSError] | None = None,
    ):
        self.env_id = f"fake-{uuid.uuid4().hex[:8]}"
        self.files: dict[str, str] = dict(files or {})
        self.commands: list[str] = []
        self.closed = False
        self._read_errors = dict(read_errors or {})
        self._write_errors = dict(write_errors or {})
        self._responses = {
            prefix: list(v) if isinstance(v, list) else [v]
            for prefix, v in (responses or {}).items()
        }

    async def exec(
        self, command: str, *, timeout: float = DEFAULT_TIMEOUT_S, cwd: str = WORKDIR
    ) -> ExecResult:
        self.commands.append(command)
        if command.startswith("test -e "):
            path = shlex.split(command)[2]
            return ExecResult(
                exit_code=0 if path in self.files else 1, stdout="", stderr=""
            )
        for prefix, queue in self._responses.items():
            if command.startswith(prefix):
                return queue.pop(0) if len(queue) > 1 else queue[0]
        if command == BASELINE_SHA_CMD:
            return ExecResult(exit_code=0, stdout=f"{BASELINE_SHA}\n", stderr="")
        return ExecResult(exit_code=0, stdout="", stderr="")

    async def read_file(self, path: str) -> str:
        if path in self._read_errors:
            raise self._read_errors[path]
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, path: str, content: str) -> None:
        if path in self._write_errors:
            raise self._write_errors[path]
        self.files[path] = content

    async def upload_dir(self, local_dir: Path, dest: str = WORKDIR) -> None:
        for item in Path(local_dir).rglob("*"):
            if item.is_file():
                self.files[f"{dest}/{item.relative_to(local_dir).as_posix()}"] = (
                    item.read_text()
                )

    async def close(self) -> None:
        self.closed = True


def fake_tool_context(
    env: FakeEnvironment, agent_name: str = "coder", **state: Any
) -> SimpleNamespace:
    """Registers `env` and returns an object with the ToolContext attributes tools use."""
    registry.register(env)
    return SimpleNamespace(
        state={"sandbox_id": env.env_id, "protected_paths": [], **state},
        agent_name=agent_name,
        session=SimpleNamespace(id="fake-session"),
    )


def make_bench_task(root: Path, task_id: str = "t-1", category: str = "bug") -> None:
    """Create a tiny bench repo + task under root/{repos,tasks} (base adds, plant subtracts)."""
    repo = root / "repos" / "mini"
    (repo / "tests").mkdir(parents=True)
    (repo / "mini.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests" / "test_mini.py").write_text(
        "from mini import add\n\n\ndef test_add_zero():\n    assert add(0, 0) == 0\n"
    )
    task = root / "tasks" / task_id
    (task / "plant").mkdir(parents=True)
    (task / "plant" / "mini.py").write_text("def add(a, b):\n    return a - b\n")
    (task / "solution").mkdir()
    (task / "solution" / "mini.py").write_text("def add(a, b):\n    return a + b\n")
    (task / "hidden_tests").mkdir()
    (task / "hidden_tests" / "test_hidden_mini.py").write_text(
        "from mini import add\n\n\ndef test_add_hidden():\n    assert add(2, 3) == 5\n"
    )
    (task / "task.yaml").write_text(
        f"repo: mini\ntitle: add is broken\nbody: add subtracts\ncategory: {category}\n"
        "difficulty: easy\nsplit: dev\n"
    )
