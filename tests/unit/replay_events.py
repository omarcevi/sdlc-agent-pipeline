"""Synthetic event logs for the replay converter, made of real ADK `Event` objects.

Each event is written as the driver writes it (`model_dump_json(exclude_none=True)`,
one per line), in the shapes of design section 3:

- a function node writes a status line, then an output event carrying its state
  delta and route;
- a router writes one output event with its route and no text;
- an agent writes model-response events (function calls, usage, the running
  `budget`) and function-response events; its answer is a `set_model_response`
  call, that call's response, and an output event with the state delta.

`Log` keeps the running totals as `BudgetPlugin` does, so `record()` matches the
log unless a test says otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from google.adk.events import Event, EventActions
from google.adk.events.event import NodeInfo
from google.genai import types

from app.schemas import RunRecord

WORKFLOW = "issue_to_pr"
MODEL = "gemini-3.8-flash"
T0 = 1790835971.0  # 2026-10-01T06:26:11Z
TASK_ID = "md-001"
MULTI_RUN_ID = "md-001-multi-flash-r1-20261001T062611Z"
SINGLE_RUN_ID = "md-001-single-flash-r1-20261001T062611Z"
SANDBOX = "itp-0123456789ab"

ISSUE = {
    "task_id": TASK_ID,
    "run_id": MULTI_RUN_ID,
    "repo": "mini",
    "title": "add is broken",
    "body": "add subtracts instead of adding",
    "mode": "bench",
}
PLAN = {
    "actionable": True,
    "summary": "Make add return the sum.",
    "files_to_inspect": ["mini.py", "tests/test_mini.py"],
    "steps": ["In mini.py, return a + b."],
    "test_strategy": "Run the existing tests.",
}
PATCH = {
    "summary": "add returns a + b.",
    "files_changed": ["mini.py"],
    "tests_added": [],
    "notes": "",
}
SOLO = {
    "declined": False,
    "summary": "add returns a + b.",
    "files_changed": ["mini.py"],
}
UNIFIED_DIFF = (
    "diff --git a/mini.py b/mini.py\n"
    "--- a/mini.py\n"
    "+++ b/mini.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n"
    "-    return a - b\n"
    "+    return a + b\n"
)
DIFF = {
    "unified_diff": UNIFIED_DIFF,
    "files": ["mini.py"],
    "insertions": 1,
    "deletions": 1,
}
TEST_REPORT = {
    "passed": True,
    "exit_code": 0,
    "failed_tests": [],
    "output_tail": "1 passed in 0.01s",
    "duration_s": 1.18,
}
FAILED_TESTS = {
    "passed": False,
    "exit_code": 1,
    "failed_tests": ["tests/test_mini.py::test_add_zero"],
    "output_tail": "1 failed in 0.02s",
    "duration_s": 1.2,
}
REVIEW = {"verdict": "approve", "comments": [], "must_fix": []}
CAP_REASON = "run cost $1.008 reached the $1.00 cap"
CAP_ERROR = (
    "Error in plugin 'budget' during 'before_model_callback' callback: " + CAP_REASON
)


class Log:
    """An event log under construction. Times are seconds after `T0`."""

    def __init__(self, run_id: str = MULTI_RUN_ID, *, task_id: str = TASK_ID) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.events: list[Event] = []
        self.t = 0.0
        self.cost = 0.0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tool_calls = 0
        self._call_ids = 0

    # --- primitives -------------------------------------------------------------

    def event(
        self,
        node: str,
        *,
        visit: int = 1,
        author: str = WORKFLOW,
        at: float | None = None,
        output: bool = False,
        as_message: bool = False,
        **fields,
    ) -> Event:
        self.t = self.t + 0.25 if at is None else at
        path = f"{WORKFLOW}@1/{node}@{visit}"
        info = NodeInfo(
            path=path,
            output_for=[path] if output else None,
            message_as_output=True if as_message else None,
        )
        event = Event(
            id=f"ev-{len(self.events)}",
            invocation_id="inv-0001",
            author=author,
            node_info=info,
            timestamp=T0 + self.t,
            **fields,
        )
        self.events.append(event)
        return event

    def status(self, node: str, text: str, *, visit: int = 1, at=None) -> Event:
        content = types.Content(role="user", parts=[types.Part(text=text)])
        return self.event(node, visit=visit, at=at, content=content)

    def output(
        self,
        node: str,
        *,
        visit: int = 1,
        state: dict | None = None,
        route: str | None = None,
        at=None,
    ) -> Event:
        actions = EventActions(state_delta=state or {}, route=route)
        return self.event(node, visit=visit, at=at, output=True, actions=actions)

    def node(
        self,
        node: str,
        text: str,
        *,
        visit: int = 1,
        state: dict | None = None,
        route: str | None = None,
        at=None,
    ) -> None:
        """A function node: its status line, then its output event at the same time."""
        self.status(node, text, visit=visit, at=at)
        self.output(node, visit=visit, state=state, route=route, at=self.t)

    def router(
        self, node: str, route: str, *, visit: int = 1, state=None, at=None
    ) -> None:
        self.output(node, visit=visit, state=state, route=route, at=at)

    def model_call(
        self,
        agent: str,
        *calls: tuple[str, dict],
        visit: int = 1,
        node: str | None = None,
        cost: float = 0.001,
        tokens: tuple[int, int, int] = (1000, 20, 30),
        text: str | None = None,
        thought: str | None = None,
        model: str = MODEL,
        at=None,
    ) -> list[str]:
        """One model response; returns the ADK call ids it holds."""
        prompt, candidates, thoughts = tokens
        self.cost += cost
        self.tokens_in += prompt
        self.tokens_out += candidates + thoughts
        parts = []
        if thought is not None:
            parts.append(types.Part(text=thought, thought=True))
        if text is not None:
            parts.append(types.Part(text=text))
        ids = []
        for name, args in calls:
            self._call_ids += 1
            call_id = f"adk-call-{self._call_ids:04d}"
            ids.append(call_id)
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(id=call_id, name=name, args=args),
                    thought_signature=b"opaque-signature",
                )
            )
        budget = {
            "cost_usd": round(self.cost, 4),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tool_calls": self.tool_calls,
            "models": [model],
        }
        self.event(
            node or agent,
            visit=visit,
            author=agent,
            at=at,
            model_version=model,
            content=types.Content(role="model", parts=parts),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=prompt,
                candidates_token_count=candidates,
                thoughts_token_count=thoughts,
                total_token_count=prompt + candidates + thoughts,
            ),
            actions=EventActions(state_delta={"budget": budget}),
        )
        return ids

    def tool_result(
        self,
        agent: str,
        call_id: str,
        name: str,
        response: dict,
        *,
        visit: int = 1,
        node: str | None = None,
        at=None,
        actions: EventActions | None = None,
    ) -> None:
        self.tool_calls += 1
        part = types.Part(
            function_response=types.FunctionResponse(
                id=call_id, name=name, response=response
            )
        )
        self.event(
            node or agent,
            visit=visit,
            author=agent,
            at=at,
            content=types.Content(role="user", parts=[part]),
            actions=actions or EventActions(),
        )

    def tool(
        self, agent: str, name: str, args: dict, response: dict, *, visit: int = 1
    ) -> None:
        """One model call with one tool call, then its response."""
        (call_id,) = self.model_call(agent, (name, args), visit=visit)
        self.tool_result(agent, call_id, name, response, visit=visit)

    def answer(
        self, agent: str, key: str, value: dict, *, visit: int = 1, cost: float = 0.002
    ) -> None:
        """The agent's structured answer, as ADK records `set_model_response`."""
        (call_id,) = self.model_call(
            agent, ("set_model_response", value), visit=visit, cost=cost
        )
        self.tool_result(
            agent,
            call_id,
            "set_model_response",
            value,
            visit=visit,
            actions=EventActions(set_model_response=value),
        )
        self.event(
            agent,
            visit=visit,
            author=agent,
            at=self.t,
            output=True,
            as_message=True,
            content=types.Content(
                role="model", parts=[types.Part(text=json.dumps(value))]
            ),
            actions=EventActions(state_delta={key: value}),
        )

    def error(
        self,
        node: str,
        *,
        visit: int = 1,
        code: str = "RuntimeError",
        message: str = CAP_ERROR,
        at=None,
    ) -> None:
        self.event(
            node,
            visit=visit,
            author=node,
            at=at,
            error_code=code,
            error_message=message,
        )

    # --- whole stretches ----------------------------------------------------------

    def start(self, *, issue: dict | None = None) -> None:
        """fetch_issue and provision_sandbox, as in every bench run."""
        issue = {**ISSUE, "run_id": self.run_id, **(issue or {})}
        self.node(
            "fetch_issue",
            f"issue: {issue['title']}",
            at=0.0,
            state={
                "issue": issue,
                "issue_text": f"<issue>\nTitle: {issue['title']}\n\n{issue['body']}\n</issue>",
                "test_attempts": 0,
                "review_rounds": 0,
                "failure": None,
                "outcome": None,
            },
        )
        self.node(
            "provision_sandbox",
            f"sandbox {SANDBOX} ready",
            at=1.25,
            state={
                "sandbox_id": SANDBOX,
                "baseline_sha": "f" * 40,
                "protected_paths": ["tests/test_mini.py"],
            },
        )

    def verify(
        self,
        *,
        visit: int = 1,
        diff: dict | None = None,
        test_report: dict | None = None,
        route: str = "pass",
    ) -> None:
        """collect_diff, then run_tests with its route."""
        diff = diff or DIFF
        report = test_report or TEST_REPORT
        self.node(
            "collect_diff",
            f"diff: {len(diff['files'])} files, +{diff['insertions']} -{diff['deletions']}",
            visit=visit,
            state={"diff": diff, "diff_text": diff["unified_diff"][:20000]},
        )
        passed = "passed" if report["passed"] else "failed"
        self.node(
            "run_tests",
            f"tests: {passed} (exit {report['exit_code']})",
            visit=visit,
            state={"test_report": report},
            route=route,
        )

    def deliver(self) -> None:
        path = f"runs/{self.run_id}/patch.diff"
        self.node(
            "deliver_patch",
            f"patch written to {path}\n\n## Summary\nadd returns a + b.",
            state={
                "outcome": {
                    "outcome": "patch_written",
                    "failure_kind": "none",
                    "reason": "",
                    "patch_path": path,
                }
            },
        )

    def report_failure(self, outcome: str, failure_kind: str, reason: str) -> None:
        self.node(
            "report_failure",
            f"{outcome}: {reason}",
            state={
                "outcome": {
                    "outcome": outcome,
                    "failure_kind": failure_kind,
                    "reason": reason,
                    "patch_path": None,
                }
            },
        )

    # --- what the driver and the matrix write -------------------------------------

    def record(self, **overrides) -> RunRecord:
        fields = {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "outcome": "patch_written",
            "failure_kind": "none",
            "reason": "",
            "patch_path": f"runs/{self.run_id}/patch.diff",
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost, 4),
            "tool_calls": self.tool_calls,
            "duration_s": round(self.t, 2),
            "mode": "bench",
        }
        return RunRecord(**{**fields, **overrides})

    def row(self, record: RunRecord, *, system: str = "multi", **overrides) -> dict:
        row = {
            **record.model_dump(),
            "resolved": record.outcome == "patch_written",
            "audit": [],
            "category": "bug",
            "repo": "mini",
            "difficulty": "easy",
            "split": "dev",
            "system": system,
            "preset": "flash",
            "repeat": 1,
            "infra_retries": 0,
            "crashed": False,
        }
        return {**row, **overrides}

    def lines(self, edit: Callable[[int, dict], None] | None = None) -> list[str]:
        out = []
        for index, event in enumerate(self.events):
            line = event.model_dump_json(exclude_none=True)
            if edit is not None:
                data = json.loads(line)
                edit(index, data)
                line = json.dumps(data)
            out.append(line)
        return out

    def write(
        self, directory: Path, edit: Callable[[int, dict], None] | None = None
    ) -> Path:
        run_dir = directory / "runs" / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "events.jsonl"
        path.write_text("".join(line + "\n" for line in self.lines(edit)))
        return path


def multi_run(
    *,
    run_id: str = MULTI_RUN_ID,
    plan: dict | None = None,
    patch: dict | None = None,
    diff: dict | None = None,
    test_report: dict | None = None,
    review: dict | None = None,
) -> Log:
    """A clean multi-agent run: plan, one edit, diff, tests pass, approve, deliver."""
    log = Log(run_id)
    log.start()
    log.tool("planner", "list_dir", {"path": "."}, {"entries": ["mini.py", "tests"]})
    log.answer("planner", "plan", plan or PLAN)
    log.router("route_plan", "actionable")
    log.tool(
        "coder",
        "edit_file",
        {"path": "mini.py", "old": "a - b", "new": "a + b"},
        {"ok": True, "path": "mini.py"},
    )
    log.answer("coder", "patch", patch or PATCH)
    log.verify(diff=diff, test_report=test_report)
    log.answer("reviewer", "review", review or REVIEW)
    log.router("route_review", "approve")
    log.deliver()
    return log


def single_run(*, run_id: str = SINGLE_RUN_ID, solo: dict | None = None) -> Log:
    """A clean single-agent run: one edit, the answer, diff, tests pass, deliver."""
    log = Log(run_id)
    log.start()
    log.tool(
        "solo",
        "edit_file",
        {"path": "mini.py", "old": "a - b", "new": "a + b"},
        {"ok": True, "path": "mini.py"},
    )
    log.answer("solo", "solo", solo or SOLO)
    log.router("route_solo", "done")
    log.verify()
    log.deliver()
    return log


def cap_run(*, stop_at: float = 541.85) -> Log:
    """A multi-agent run stopped by the cost cap while the coder works."""
    log = Log(MULTI_RUN_ID)
    log.start()
    log.answer("planner", "plan", PLAN)
    log.router("route_plan", "actionable")
    log.tool("coder", "read_file", {"path": "mini.py"}, {"content": "def add(a, b):"})
    (call_id,) = log.model_call("coder", ("bash", {"command": "pytest -q"}), cost=1.0)
    log.tool_result("coder", call_id, "bash", {"exit_code": 0, "stdout": "ok"})
    log.error("coder", at=stop_at)
    return log
