"""Replay-site tooling.

uv run python -m bench.replay graphs [--out PATH] [--check]
uv run python -m bench.replay build [--manifest web/replays.yaml] [--runs-dir runs]
                                    [--results-dir results] [--out web/public/replays]
uv run python -m bench.replay check [paths ...]

`graphs` exports the multi-agent and single-agent graphs to the JSON file the replay
site draws. It needs no model, no sandbox and no network.

`convert_run` turns one recorded run (`events.jsonl`, `record.json` and its results
row) into one replay (design sections 4 and 5). The replay is built field by field
from an allowlist: nothing is copied from an event as a whole.

`build` converts every run the manifest lists (design 5.2 and 6.1), all or nothing,
and writes the replays and `index.json`; `check` runs the leak check
(`bench/replay_check.py`). Both load `.env` for the exact values of the leak check.
A held-out run is refused by its id before any file is opened, and the task list is
never read.

All `app` imports sit at the top of the module, as in `bench.run`, so nothing under
`app` looks at the environment after a `.env` file has been loaded.
"""

import argparse
import json
import math
import re
import sys
import tempfile
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import yaml
from dotenv import load_dotenv
from google.adk.agents import BaseAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import BaseModel, ValidationError

from app.approval import _escaped
from app.baseline import build_baseline_workflow
from app.driver import PUBLIC_AGENT, PUBLIC_CRASH, PUBLIC_INFRA
from app.models import RoleModels
from app.pipeline import build_workflow
from app.prompts import LEGACY_PROMPT_VERSION
from app.schemas import (
    Diff,
    PatchResult,
    Plan,
    Review,
    RunRecord,
    SoloResult,
    TestReport,
)
from app.task_store import TaskSpec
from bench import replay_check
from bench.probes import _HELDOUT_ID, dev_task
from bench.progress import PIPELINE_AUTHOR, call_detail
from bench.replay_check import (
    EXACT_RULES_OFF,
    RULES,
    UNCLEARABLE,
    Hit,
    ReplayFileError,
    check_paths,
    exact_pattern,
    project_value,
    redact_values,
    scan_value,
)

SCHEMA = 1
GRAPHS_PATH = Path("web/src/graph/graphs.json")

GRAPH_SOURCES = {
    "multi": "app.pipeline.build_workflow",
    "single": "app.baseline.build_baseline_workflow",
}

STALE_GRAPHS = (
    "graphs.json differs from the code; run: uv run python -m bench.replay graphs"
)


# --- graphs ---------------------------------------------------------------------


class _NoModel(BaseLlm):
    """A placeholder for the graph builders. Building a graph needs a model object, not a reply."""

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        raise RuntimeError("the graph export never calls a model")
        yield  # pragma: no cover  (makes this an async generator, like the real method)


def _node_name(node) -> str:
    if isinstance(node, str):
        return node
    return getattr(node, "name", None) or node.__name__


def _node_kind(node) -> str:
    name = _node_name(node)
    if name == "START":
        return "start"
    if isinstance(node, BaseAgent):
        return "llm"
    if name.startswith("route_"):
        return "router"
    return "function"


def _graph_def(source: str, workflow) -> dict:
    """Nodes in order of first appearance in the edges, edges in the code's order."""
    nodes: dict[str, str] = {"START": "start"}
    edges: list[dict] = []

    def add(node) -> str:
        name = _node_name(node)
        nodes.setdefault(name, _node_kind(node))
        return name

    for edge in workflow.edges:
        source_node, target = edge
        origin = add(source_node)
        if isinstance(target, dict):
            for route, node in target.items():
                edges.append({"from": origin, "to": add(node), "route": route})
        else:
            edges.append({"from": origin, "to": add(target), "route": None})
    return {
        "source": source,
        "nodes": [{"id": name, "kind": kind} for name, kind in nodes.items()],
        "edges": edges,
    }


def export_graphs() -> dict:
    placeholder = _NoModel(model="placeholder")
    models = RoleModels(planner=placeholder, coder=placeholder, reviewer=placeholder)
    return {
        "schema": SCHEMA,
        "graphs": {
            "multi": _graph_def(GRAPH_SOURCES["multi"], build_workflow(models)),
            "single": _graph_def(
                GRAPH_SOURCES["single"], build_baseline_workflow(placeholder)
            ),
        },
    }


def render_graphs() -> str:
    return json.dumps(export_graphs(), indent=2, ensure_ascii=False) + "\n"


def _graphs_command(args: argparse.Namespace) -> int:
    out = Path(args.out)
    text = render_graphs()
    if args.check:
        try:
            current = out.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = None
        if current != text:
            print(STALE_GRAPHS, file=sys.stderr)
            return 1
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text.encode("utf-8"))
    return 0


# --- converter ------------------------------------------------------------------

# What callers pass as `repo_root`: this checkout, wherever the command runs from.
REPO_ROOT = Path(__file__).resolve().parents[1]

# Fixed refusal messages. Task 4 prefixes them with "entry <n>: ".
TOTALS_DIFFER = "running totals do not match the record"
UNKNOWN_NODE = "node not in the graph"
RESULT_WITHOUT_CALL = "tool result without a call"
RECORD_ROW_DIFFER = "record and results row disagree"
LEAK_FOUND = "leak check failed"
LOG_UNREADABLE = "event log cannot be read"
NOT_FINITE = "a number is not finite"

SET_MODEL_RESPONSE = "set_model_response"
ANSWER_LABEL = "answer"
COST_TOLERANCE_USD = 0.0002


class ReplayRefused(Exception):
    """The replay must not be written. `str()` is one fixed message, never content;
    `hits` holds the leak-check hits for "leak check failed", else nothing."""

    def __init__(self, message: str, hits: Sequence[Hit] = ()) -> None:
        super().__init__(message)
        self.hits: list[Hit] = list(hits)


@dataclass(frozen=True)
class RunInputs:
    run_id: str
    events_path: Path
    record: RunRecord
    row: dict  # the results row
    task: TaskSpec
    caps: dict  # {"cost_usd": float, "tool_calls": int, "wall_clock_s": int}
    allow: tuple[tuple[str, str], ...] = ()  # (JSON path, rule) the leak check skips


# --- rewrites (design 5.3) --------------------------------------------------------

_HOST_PATH = re.compile(
    r"(?:[/\\]{1,2}private)?[/\\]{1,2}var[/\\]{1,2}folders[/\\]{1,2}"
    r"|[/\\]{1,2}root[/\\]{1,2}"
    r"|[/\\]{1,2}(?:Users|home)[/\\]{1,2}(?:[^/\\\s\"'<>]+[/\\]{0,2})?"
    r"|[A-Za-z]:[/\\]{1,2}(?i:users)[/\\]{1,2}(?:[^/\\\s\"'<>]+[/\\]{0,2})?"
)
_SANDBOX_NAME = re.compile(r"itp-[0-9a-fA-F]{12,}")
_CONTAINER_ID = re.compile(r"(?<![A-Za-z0-9])[0-9a-f]{64}(?![A-Za-z0-9])")
_PROJECTS = re.compile(r"projects/(?!<project>)[^/\s\"'<>\\]*")
# runs/<run-id>/<file>; a run id carries a UTC stamp, which code in a diff rarely does.
_RUN_ARTIFACT = re.compile(
    r"(?<![A-Za-z0-9._-])runs/[A-Za-z0-9._-]*\d{8}T\d{6}Z[A-Za-z0-9._-]*/"
    r"([A-Za-z0-9._-]+)"
)
_PATH_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/~"
)


_ASCII_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _escape_invisible(text: str) -> str:
    """Rewrite 6: every character `app.approval` escapes for the approver (Cc, Cf,
    Zl, Zp, Cs and the default-ignorables), except tab and newline, as `<U+XXXX>`."""
    if text.isascii():
        return _ASCII_CONTROL.sub(lambda m: f"<U+{ord(m.group()):04X}>", text)
    return "".join(
        f"<U+{ord(c):04X}>" if c not in "\t\n" and _escaped(c) else c for c in text
    )


def _run_artifacts(text: str) -> str:
    """Rewrite 5: `[<path>/]runs/<run-id>/<file>` becomes `<file>`.

    The core match starts at `runs/`; the path before it is taken by a scan to the
    left that stops at the previous match, so the whole pass stays linear.
    """
    if "runs/" not in text:
        return text
    out: list[str] = []
    done = 0
    for match in _RUN_ARTIFACT.finditer(text):
        start = match.start()
        if start > done and text[start - 1] == "/":
            start -= 1
            while start > done and text[start - 1] in _PATH_CHARS:
                start -= 1
            for placeholder in ("<repo>", "<host-path>"):
                if text.endswith(placeholder, done, start):
                    start -= len(placeholder)
                    break
        out.append(text[done:start])
        out.append(match.group(1))
        done = match.end()
    out.append(text[done:])
    return "".join(out)


def _path_pattern(path: Path) -> re.Pattern[str] | None:
    """The path as a whole name: `/Users/a` never matches inside `/Users/ab`."""
    text = str(path)
    if len(path.parts) < 2:  # "/" or a relative name would match far too much
        return None
    return re.compile(re.escape(text) + r"(?![A-Za-z0-9._-])")


class _Rewriter:
    """Rewrites 1 to 6 of design 5.3, compiled once per replay."""

    def __init__(
        self,
        *,
        project: str | None,
        redact: Sequence[str],
        repo_root: Path,
        home: Path,
    ) -> None:
        self._paths = [
            (pattern, replacement)
            for pattern, replacement in (
                (_path_pattern(repo_root), "<repo>"),
                (_path_pattern(home), "~"),
            )
            if pattern is not None
        ]
        self._project = exact_pattern(project) if project else None
        self._redact = [exact_pattern(value) for value in redact if value]

    def __call__(self, text: str) -> str:
        for pattern, replacement in self._paths:
            text = pattern.sub(replacement, text)
        text = _HOST_PATH.sub("<host-path>/", text)
        text = _SANDBOX_NAME.sub("<sandbox>", text)
        text = _CONTAINER_ID.sub("<container>", text)
        if self._project is not None:
            text = self._project.sub("<project>", text)
        text = _PROJECTS.sub("projects/<project>", text)
        for pattern in self._redact:
            text = pattern.sub("<redacted>", text)
        return _escape_invisible(_run_artifacts(text))


def scrub_text(
    text: str,
    *,
    project: str | None,
    redact: Sequence[str],
    repo_root: Path,
    home: Path,
) -> str:
    """Rewrites 1 to 6 of design 5.3, in order."""
    return _Rewriter(project=project, redact=redact, repo_root=repo_root, home=home)(
        text
    )


# --- caps (design 5.4) --------------------------------------------------------------


@dataclass(frozen=True)
class _Cut:
    """A rewritten string, cut to its cap only when the replay is finished."""

    text: str
    limit: int
    head: int
    tail: int


@dataclass(frozen=True)
class _CutList:
    items: list


@dataclass(frozen=True)
class _CutDict:
    """An object in a tool value: its keys are `_Cut` names, its entries are capped."""

    entries: tuple


@dataclass(frozen=True)
class _CutDiff:
    text: str


# (limit, head, tail) per kind of content
TOOL_CAP = (4000, 3000, 800)
NAME_CAP = (300, 200, 60)  # tool, agent and model names, and keys in tool values
ANSWER_CAP = (4000, 3000, 800)
DECLINE_CAP = (1000, 800, 150)
MESSAGE_CAP = (300, 200, 60)
ISSUE_BODY_CAP = (8000, 6000, 1500)
TEST_TAIL_CAP = (8000, 1000, 6500)
DIFF_CAP = 60_000
LIST_CAP = 100


def _marker(removed: int) -> str:
    return f"\n[... {removed:,} characters cut ...]\n"


def cut_text(text: str, limit: int, *, head: int, tail: int) -> str:
    """`text` if it fits in `limit`; else its first `head` and last `tail` characters
    around the cut marker."""
    if len(text) <= limit:
        return text
    rest = text[len(text) - tail :] if tail > 0 else ""
    return text[:head] + _marker(len(text) - head - tail) + rest


_DIFF_CHUNK = re.compile(r"^(?:diff --git |@@ )", re.MULTILINE)


def _cut_diff(text: str, limit: int) -> str:
    """The diff's head, ending after its last complete hunk, then the marker.

    A file header whose hunks were all cut goes too. When not even the first hunk
    fits, the head ends at a line instead.
    """
    if len(text) <= limit:
        return text
    room = limit - len(_marker(len(text)))
    starts = [m.start() for m in _DIFF_CHUNK.finditer(text) if m.start() > 0]
    ends = [s for s in starts if s <= room]
    end = ends[-1] if ends else 0
    if end:
        previous = max([0, *(s for s in starts if s < end)])
        if text.startswith("diff --git ", previous) and text.startswith("@@ ", end):
            end = previous
    if end == 0:
        end = text.rfind("\n", 0, room) + 1 or room
    return text[:end] + _marker(len(text) - end)


def _materialize(value, *, cut: bool):
    """The replay as written (`cut=True`), or with every string whole (`cut=False`)."""
    if isinstance(value, _Cut):
        if not cut:
            return value.text
        return cut_text(value.text, value.limit, head=value.head, tail=value.tail)
    if isinstance(value, _CutDiff):
        return _cut_diff(value.text, DIFF_CAP) if cut else value.text
    if isinstance(value, _CutList):
        items = value.items
        if cut and len(items) > LIST_CAP:
            more = f"[... {len(items) - LIST_CAP:,} more items ...]"
            return [_materialize(item, cut=cut) for item in items[:LIST_CAP]] + [more]
        return [_materialize(item, cut=cut) for item in items]
    if isinstance(value, _CutDict):
        entries = value.entries
        kept = entries[:LIST_CAP] if cut else entries
        out = {
            _materialize(key, cut=cut): _materialize(item, cut=cut)
            for key, item in kept
        }
        if len(kept) < len(entries):
            out[f"[... {len(entries) - LIST_CAP:,} more entries ...]"] = None
        return out
    if isinstance(value, dict):
        return {
            _materialize(key, cut=cut): _materialize(item, cut=cut)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_materialize(item, cut=cut) for item in value]
    return value


# --- events to steps (design 4.4) ---------------------------------------------------

_VISIT = re.compile(r"(.+)@(\d+)", re.ASCII)


def _obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def _seq(value) -> list:
    return value if isinstance(value, list) else []


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _count(value) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _read_events(path: Path) -> list[dict]:
    """One dict per line. Lines are split on "\\n" only: a JSON string may hold U+2028."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ReplayRefused(LOG_UNREADABLE) from None
    events = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            raise ReplayRefused(LOG_UNREADABLE) from None
        if not isinstance(event, dict):
            raise ReplayRefused(LOG_UNREADABLE)
        if event.get("partial") is True:
            continue
        events.append(event)
    if not events:
        raise ReplayRefused(LOG_UNREADABLE)
    return events


def _position(event: dict) -> tuple[str, int] | None:
    """(node, visit) from `issue_to_pr@1/<node>@<visit>`; None for the workflow itself."""
    path = _obj(event.get("node_info")).get("path")
    if not isinstance(path, str):
        return None
    segments = path.split("/")
    if len(segments) < 2 or not segments[1]:
        return None
    match = _VISIT.fullmatch(segments[1])
    if match is None:
        return segments[1], 1
    return match.group(1), int(match.group(2))


def _texts(parts: list[dict]) -> str:
    return "".join(
        part["text"]
        for part in parts
        if isinstance(part.get("text"), str) and part.get("thought") is not True
    )


_Model = TypeVar("_Model", bound=BaseModel)


def _validated(model: type[_Model], value) -> _Model:
    """The state delta as its schema model; a bad one refuses without its content."""
    try:
        return model.model_validate(value)
    except ValidationError:
        raise ReplayRefused(LOG_UNREADABLE) from None


class _Steps:
    """Walks the events in order and builds the steps, with their running numbers."""

    def __init__(self, nodes: set[str], scrub: _Rewriter) -> None:
        self.nodes = nodes
        self.scrub = scrub
        self.steps: list[dict] = []
        self.t0: float | None = None
        self.t = 0.0
        self.cost = 0.0
        self.tool_calls = 0
        self.node: str | None = None
        self.visit = 0
        self.route: _Cut | None = None
        self.call_ids: dict[str, str] = {}
        self.next_call = 1
        self.models: dict[_Cut, _Cut] = {}
        self.issue: dict | None = None
        self.decline_reason: str | None = None
        self.outcome_from_report_failure = False
        self.stops: list[dict] = []
        self.recorded_at: str | None = None

    # strings, wrapped with their caps
    def text(self, value: str, cap: tuple[int, int, int]) -> _Cut:
        return _Cut(self.scrub(value), *cap)

    def answer(self, value: str) -> _Cut:
        return self.text(value, ANSWER_CAP)

    def name(self, value: str) -> _Cut:
        return self.text(value, NAME_CAP)

    def answers(self, values: list[str]) -> _CutList:
        return _CutList([self.answer(v) for v in values])

    def decline(self, value: str | None) -> _Cut | None:
        return None if value is None else self.text(value, DECLINE_CAP)

    def tool_value(self, value):
        """A tool's arguments or result: JSON values, every string rewritten."""
        if isinstance(value, str):
            return self.text(value, TOOL_CAP)
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, dict):
            return _CutDict(
                tuple(
                    (self.name(str(key)), self.tool_value(item))
                    for key, item in value.items()
                )
            )
        if isinstance(value, list):
            return _CutList([self.tool_value(item) for item in value])
        return self.text(str(value), TOOL_CAP)

    # steps
    def step(self, kind: str, **fields) -> dict:
        if self.node is None:
            raise ReplayRefused(UNKNOWN_NODE)
        step = {
            "i": len(self.steps),
            "t": self.t,
            "kind": kind,
            "node": self.node,
            "visit": self.visit,
            "cost_usd": self.cost,
            "tool_calls": self.tool_calls,
            **fields,
        }
        self.steps.append(step)
        return step

    def add(self, event: dict) -> None:
        stamp = _number(event.get("timestamp"))
        if stamp is None:
            raise ReplayRefused(LOG_UNREADABLE)
        if self.t0 is None:
            self.t0 = stamp
            self.recorded_at = datetime.fromtimestamp(stamp, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        self.t = max(self.t, round(stamp - self.t0, 2))
        parts = [_obj(p) for p in _seq(_obj(event.get("content")).get("parts"))]
        position = _position(event)
        if position is not None and position != (self.node, self.visit):
            self.enter(position, event, parts)
        actions = _obj(event.get("actions"))
        delta = _obj(actions.get("state_delta"))
        cost = _number(_obj(delta.get("budget")).get("cost_usd"))
        if cost is not None:
            self.cost = round(cost, 4)
        author = event.get("author")
        if self.is_model_response(author, event, parts):
            self.model_call(author, event, parts)
        for part in parts:
            response = part.get("function_response")
            if isinstance(response, dict):
                self.tool_result(response)
        self.answers_in(delta)
        route = actions.get("route")
        if isinstance(route, str):
            self.route = self.text(route, MESSAGE_CAP)
        if event.get("error_code") is not None:
            self.stops.append(self.step("stop", text=""))

    def enter(self, position: tuple[str, int], event: dict, parts: list[dict]) -> None:
        node, visit = position
        if node not in self.nodes:
            raise ReplayRefused(UNKNOWN_NODE)
        message = None
        if event.get("author") == PIPELINE_AUTHOR:
            status = _texts(parts).strip()
            if status:
                message = self.text(status.split("\n", 1)[0], MESSAGE_CAP)
        previous, via = self.node, self.route
        self.node, self.visit, self.route = node, visit, None
        self.step("node", **{"from": previous, "via": via, "message": message})

    @staticmethod
    def is_model_response(author, event: dict, parts: list[dict]) -> bool:
        if not isinstance(author, str) or author in (PIPELINE_AUTHOR, "user"):
            return False
        has_call = any(isinstance(p.get("function_call"), dict) for p in parts)
        return has_call or isinstance(event.get("usage_metadata"), dict)

    def model_call(self, author: str, event: dict, parts: list[dict]) -> None:
        calls = []
        for part in parts:
            call = part.get("function_call")
            if not isinstance(call, dict):
                continue
            number = f"c{self.next_call}"
            self.next_call += 1
            if isinstance(call.get("id"), str):
                self.call_ids[call["id"]] = number
            name = call.get("name") if isinstance(call.get("name"), str) else ""
            tool = self.scrub(name or "?")
            args = self.tool_value(_obj(call.get("args")))
            if name == SET_MODEL_RESPONSE:
                label = ANSWER_LABEL
            else:
                plain = _materialize(args, cut=False)
                label = f"{tool} {call_detail(tool, plain)}".rstrip()
            calls.append(
                {
                    "id": number,
                    "tool": _Cut(tool, *NAME_CAP),
                    "label": _Cut(label, *TOOL_CAP),
                    "args": args,
                }
            )
        usage = _obj(event.get("usage_metadata"))
        text = _texts(parts)
        model = event.get("model_version")
        model = self.name(model) if isinstance(model, str) else self.name("")
        agent = self.name(author)
        if model.text:
            self.models.setdefault(agent, model)
        self.step(
            "model_call",
            agent=agent,
            model=model,
            tokens_in=_count(usage.get("prompt_token_count")),
            tokens_out=_count(usage.get("candidates_token_count"))
            + _count(usage.get("thoughts_token_count")),
            text=self.answer(text) if text.strip() else None,
            calls=calls,
        )

    def tool_result(self, response: dict) -> None:
        self.tool_calls += 1
        name = response.get("name") if isinstance(response.get("name"), str) else ""
        if name == SET_MODEL_RESPONSE:
            return
        original = response.get("id")
        number = self.call_ids.get(original) if isinstance(original, str) else None
        if number is None:
            raise ReplayRefused(RESULT_WITHOUT_CALL)
        payload = response.get("response")
        if not isinstance(payload, dict):
            payload = {} if payload is None else {"result": payload}
        error = payload.get("error")
        if error is not None and not isinstance(error, str):
            error = json.dumps(error, ensure_ascii=False, sort_keys=True)
        self.step(
            "tool_result",
            call=number,
            tool=self.name(name or "?"),
            error=None if error is None else self.text(error, TOOL_CAP),
            result=self.tool_value({k: v for k, v in payload.items() if k != "error"}),
        )

    def answers_in(self, delta: dict) -> None:
        """State deltas that become steps, in a fixed order; the rest is ignored."""
        if delta.get("plan") is not None:
            plan = _validated(Plan, delta["plan"])
            self.decline_reason = plan.decline_reason
            self.step("plan", value=self.plan_value(plan))
        if delta.get("patch") is not None:
            patch = _validated(PatchResult, delta["patch"])
            self.step("claim", agent="coder", value=self.patch_value(patch))
        if delta.get("solo") is not None:
            solo = _validated(SoloResult, delta["solo"])
            self.decline_reason = solo.decline_reason
            self.step("claim", agent="solo", value=self.solo_value(solo))
        if delta.get("diff") is not None:
            self.step("diff", value=self.diff_value(_validated(Diff, delta["diff"])))
        if delta.get("test_report") is not None:
            report = _validated(TestReport, delta["test_report"])
            self.step("tests", value=self.tests_value(report))
        if delta.get("review") is not None:
            review = _validated(Review, delta["review"])
            self.step("review", value=self.review_value(review))
        issue = delta.get("issue")
        if isinstance(issue, dict):
            self.issue = issue
        if isinstance(delta.get("outcome"), dict) and self.node == "report_failure":
            self.outcome_from_report_failure = True

    def plan_value(self, plan: Plan) -> dict:
        return {
            "actionable": plan.actionable,
            "decline_reason": self.decline(plan.decline_reason),
            "summary": self.answer(plan.summary),
            "files_to_inspect": self.answers(plan.files_to_inspect),
            "steps": self.answers(plan.steps),
            "test_strategy": self.answer(plan.test_strategy),
        }

    def patch_value(self, patch: PatchResult) -> dict:
        return {
            "summary": self.answer(patch.summary),
            "files_changed": self.answers(patch.files_changed),
            "tests_added": self.answers(patch.tests_added),
            "notes": self.answer(patch.notes),
        }

    def solo_value(self, solo: SoloResult) -> dict:
        return {
            "declined": solo.declined,
            "decline_reason": self.decline(solo.decline_reason),
            "summary": self.answer(solo.summary),
            "files_changed": self.answers(solo.files_changed),
        }

    def diff_value(self, diff: Diff) -> dict:
        unified = self.scrub(diff.unified_diff)
        return {
            "files": [self.answer(name) for name in diff.files],
            "insertions": diff.insertions,
            "deletions": diff.deletions,
            "unified_diff": _CutDiff(unified),
            "cut": len(unified) > DIFF_CAP,
        }

    def tests_value(self, report: TestReport) -> dict:
        return {
            "passed": report.passed,
            "exit_code": report.exit_code,
            "failed_tests": _CutList(
                [self.text(name, TOOL_CAP) for name in report.failed_tests]
            ),
            "output_tail": self.text(report.output_tail, TEST_TAIL_CAP),
            "duration_s": round(report.duration_s, 2),
        }

    def review_value(self, review: Review) -> dict:
        return {
            "verdict": review.verdict,
            "comments": [
                {
                    "file": self.answer(comment.file),
                    "line": comment.line,
                    "severity": comment.severity,
                    "issue": self.answer(comment.issue),
                }
                for comment in review.comments
            ],
            "must_fix": self.answers(review.must_fix),
        }


def _public_reason(record: RunRecord, row: dict, steps: _Steps) -> str | _Cut:
    """The outcome's reason and a stop's text (design 4.5): what happened, never an
    exception's or a provider's text."""
    if row.get("crashed"):
        return PUBLIC_CRASH
    if record.outcome == "patch_written":
        return ""
    if record.outcome == "declined":
        return steps.text(steps.decline_reason or record.reason, DECLINE_CAP)
    if record.failure_kind == "budget":
        return steps.scrub(
            record.reason
        )  # built from numbers by BudgetPlugin or the driver
    if record.failure_kind == "agent" and steps.outcome_from_report_failure:
        return steps.scrub(record.reason)  # report_failure's fixed sentences
    if record.failure_kind == "agent":
        return PUBLIC_AGENT
    return PUBLIC_INFRA


def convert_run(
    inputs: RunInputs,
    *,
    graphs: dict,
    project: str | None,
    redact: Sequence[str],
    repo_root: Path,
    home: Path,
) -> dict:
    """One replay (design 4.2 to 4.4) from one recorded run, or `ReplayRefused`.

    Every string is rewritten (design 5.3), then the leak check runs on the whole
    replay before the caps cut anything and again on the finished one.
    """
    record, row = inputs.record, inputs.row
    if (
        inputs.run_id != record.run_id
        or row.get("run_id") != record.run_id
        or row.get("task_id") != record.task_id
        or row.get("outcome") != record.outcome
    ):
        raise ReplayRefused(RECORD_ROW_DIFFER)
    scrub = _Rewriter(project=project, redact=redact, repo_root=repo_root, home=home)
    try:
        built = _build(inputs, graphs, scrub)
        exact = tuple(v for v in (project or "", *redact) if v)
        allow = frozenset((path, rule) for path, rule in inputs.allow)
        name = f"{inputs.run_id}.json"
        whole = _materialize(built, cut=False)
        hits = scan_value(whole, file=name, exact=exact, allow=allow)
        replay = _materialize(built, cut=True)
        hits += scan_value(replay, file=name, exact=exact, allow=allow)
    except RecursionError:
        raise ReplayRefused(LOG_UNREADABLE) from None
    if not _all_finite(replay):
        raise ReplayRefused(NOT_FINITE)
    if hits:
        raise ReplayRefused(LEAK_FOUND, list(dict.fromkeys(hits)))
    return replay


def _all_finite(value) -> bool:
    """No NaN or infinity anywhere: a record or a state delta may hold one (pydantic
    accepts them), and JSON cannot."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, float) and not math.isfinite(node):
            return False
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return True


def _build(inputs: RunInputs, graphs: dict, scrub: _Rewriter) -> dict:
    """The replay with every string rewritten and wrapped with its cap."""
    record, row, task = inputs.record, inputs.row, inputs.task
    system = row.get("system")
    graph = _obj(_obj(graphs.get("graphs")).get(system))
    nodes = {n["id"] for n in _seq(graph.get("nodes")) if isinstance(n, dict)}
    steps = _Steps(nodes, scrub)
    for event in _read_events(inputs.events_path):
        steps.add(event)
    if (
        abs(steps.cost - record.cost_usd) > COST_TOLERANCE_USD + 1e-9
        or steps.tool_calls != record.tool_calls
    ):
        raise ReplayRefused(TOTALS_DIFFER)

    reason = _public_reason(record, row, steps)
    for stop in steps.stops:
        stop["text"] = reason
    steps.t = max(steps.t, round(record.duration_s, 2))
    final = steps.step("outcome")
    final["cost_usd"] = round(record.cost_usd, 4)

    issue = steps.issue or {"title": task.title, "body": task.body}
    title, body = issue.get("title"), issue.get("body")
    return {
        "schema": SCHEMA,
        "run": {
            "run_id": scrub(record.run_id),
            "task_id": scrub(record.task_id),
            "repo": scrub(task.repo),
            "category": task.category,
            "difficulty": task.difficulty,
            "tempting": task.tempting,
            "levers": [scrub(lever) for lever in task.levers],
            "system": scrub(str(system)),
            "graph": scrub(str(system)),
            "preset": scrub(str(row.get("preset"))),
            "models": dict(steps.models),
            "prompt_version": scrub(
                str(row.get("prompt_version") or LEGACY_PROMPT_VERSION)
            ),
            "mode": scrub(record.mode),
            "recorded_at": steps.recorded_at,
            "issue": {
                "title": steps.text(
                    title if isinstance(title, str) else "", MESSAGE_CAP
                ),
                "body": steps.text(
                    body if isinstance(body, str) else "", ISSUE_BODY_CAP
                ),
            },
        },
        "caps": {
            "cost_usd": float(inputs.caps["cost_usd"]),
            "tool_calls": int(inputs.caps["tool_calls"]),
            "wall_clock_s": int(inputs.caps["wall_clock_s"]),
        },
        "outcome": {
            "outcome": record.outcome,
            "failure_kind": record.failure_kind,
            "reason": reason,
            "resolved": bool(row.get("resolved")),
            "cost_usd": round(record.cost_usd, 4),
            "tool_calls": record.tool_calls,
            "tokens_in": record.tokens_in,
            "tokens_out": record.tokens_out,
            "duration_s": round(record.duration_s, 2),
            "test_attempts": record.test_attempts,
            "review_rounds": record.review_rounds,
            "audit": [scrub(str(line)) for line in _seq(row.get("audit"))],
        },
        "steps": steps.steps,
    }


def dumps(replay: dict) -> str:
    """The file text: indent 2, UTF-8 as is, keys in the order they were built."""
    return json.dumps(replay, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


# --- manifest and build (design 5.1, 5.2 and 6.1) -----------------------------------

MANIFEST_PATH = Path("web/replays.yaml")
OUT_DIR = Path("web/public/replays")
RUNS_DIR = Path("runs")
RESULTS_DIR = Path("results")
INDEX_FILE = "index.json"
RUN_ID = re.compile(
    r"^(md|sr|tc)-\d{3}-(multi|single)-(flash|pro|mixed)-r\d+-\d{8}T\d{6}Z$", re.ASCII
)
CAP_KEYS = ("cost_usd", "tool_calls", "wall_clock_s")
CLEARABLE = frozenset(RULES) - UNCLEARABLE
CAPTION_MAX = 140
SIZE_WARN_BYTES = 300 * 1024
SIZE_LIMIT_BYTES = 1024 * 1024

# Fixed refusal messages. Entry problems are prefixed with "entry <n>: ", n being the
# entry's 1-based position in the manifest; none names a task, a file or content.
MANIFEST_UNREADABLE = "manifest cannot be read"
ENTRY_INVALID = "entry fields are not valid"
ENTRY_TWICE = "run is listed twice"
NOT_DEV_RUN = "not a dev bench run"
RUN_FILES_MISSING = "run files missing"
RECORD_MISMATCH = "record does not match the run id"
NO_SINGLE_ROW = "no single results row"
ROW_NOT_SCORED = "results row is not a scored dev run"
BAD_PAIR = "pair is not the same task on the other system"
BAD_CAPTION = f"caption must be one line of 1 to {CAPTION_MAX} characters"
TOO_BIG = "replay over 1 MB"


@dataclass(frozen=True)
class ManifestEntry:
    run_id: str
    caption: str
    pair: str | None
    caps: dict | None  # overrides the manifest's caps, key by key
    allow: tuple[tuple[str, str], ...]  # (JSON path, rule)


@dataclass(frozen=True)
class Manifest:
    note: str
    caps: dict
    replays: tuple[ManifestEntry, ...]


def _number_ok(value, *, whole: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)):
        return False
    return math.isfinite(value) and value > 0


def _caps_ok(caps, *, partial: bool) -> bool:
    if not isinstance(caps, dict):
        return False
    keys = set(caps)
    if not (keys <= set(CAP_KEYS) if partial else keys == set(CAP_KEYS)):
        return False
    return all(_number_ok(caps[k], whole=k != "cost_usd") for k in keys)


def _allow(value) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(value, list):
        return None
    pairs = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "rule"}:
            return None
        path, rule = item["path"], item["rule"]
        if not isinstance(path, str) or not path.startswith("$"):
            return None
        if not isinstance(rule, str) or rule not in CLEARABLE:
            return None
        pairs.append((path, rule))
    return tuple(pairs)


def _entry(item) -> ManifestEntry | None:
    if not isinstance(item, dict):
        return None
    keys = set(item)
    if (
        not {"run_id", "caption"}
        <= keys
        <= {"run_id", "caption", "pair", "caps", "allow"}
    ):
        return None
    run_id, caption, pair = item["run_id"], item["caption"], item.get("pair")
    caps = item.get("caps")
    allow = _allow(item.get("allow", []))
    if not isinstance(run_id, str) or not isinstance(caption, str) or allow is None:
        return None
    if pair is not None and not isinstance(pair, str):
        return None
    if caps is not None and not _caps_ok(caps, partial=True):
        return None
    return ManifestEntry(
        run_id=run_id,
        caption=caption,
        pair=pair,
        caps=None if caps is None else dict(caps),
        allow=allow,
    )


def load_manifest(path: Path) -> Manifest:
    """The manifest's shape only; the checks of design 5.2 are `build`'s."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise ReplayRefused(MANIFEST_UNREADABLE) from None
    if not isinstance(doc, dict) or set(doc) != {"note", "caps", "replays"}:
        raise ReplayRefused(MANIFEST_UNREADABLE)
    note, caps, items = doc["note"], doc["caps"], doc["replays"]
    if not isinstance(note, str) or not isinstance(items, list):
        raise ReplayRefused(MANIFEST_UNREADABLE)
    if not _caps_ok(caps, partial=False):
        raise ReplayRefused(MANIFEST_UNREADABLE)
    entries: list[ManifestEntry] = []
    for n, item in enumerate(items, 1):
        parsed = _entry(item)
        if parsed is None:
            raise ReplayRefused(f"entry {n}: {ENTRY_INVALID}")
        if any(e.run_id == parsed.run_id for e in entries):
            raise ReplayRefused(f"entry {n}: {ENTRY_TWICE}")
        entries.append(parsed)
    return Manifest(note=note, caps=dict(caps), replays=tuple(entries))


def _task_and_system(run_id: str) -> tuple[str, str] | None:
    """(task id, system) of a dev bench run id; None for any other shape."""
    match = RUN_ID.fullmatch(run_id)
    if match is None:
        return None
    task_id = "-".join(run_id.split("-", 2)[:2])
    if _HELDOUT_ID.search(task_id):
        return None
    return task_id, match.group(2)


def _read_rows(path: Path) -> list:
    """One results file's rows. A seam: a test replaces it to watch which keys are read."""
    return json.loads(path.read_text(encoding="utf-8"))


def find_row(results_dir: Path, run_id: str) -> dict:
    """The one row of `results_dir/*.json` with this run id.

    A row of a held-out task is skipped by its `task_id` before any other key of it
    is read.
    """
    found = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            rows = _read_rows(path)
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            raise ReplayRefused(NO_SINGLE_ROW) from None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            task_id = row.get("task_id")
            if isinstance(task_id, str) and _HELDOUT_ID.search(task_id):
                continue
            if row.get("run_id") == run_id:
                found.append(row)
    if len(found) != 1:
        raise ReplayRefused(NO_SINGLE_ROW)
    return found[0]


def _is_pair(entry: ManifestEntry, entries: Sequence[ManifestEntry]) -> bool:
    mine = _task_and_system(entry.run_id)
    for other in entries:
        if other is entry or other.run_id != entry.pair:
            continue
        theirs = _task_and_system(other.run_id)
        return (
            mine is not None
            and theirs is not None
            and theirs[0] == mine[0]
            and theirs[1] != mine[1]
        )
    return False


def _caption_ok(caption: str) -> bool:
    """One line of plain text: no line break, control or format character."""
    return (
        0 < len(caption) <= CAPTION_MAX
        and caption.strip() != ""
        and caption.splitlines() == [caption]
        and not any(_escaped(c) for c in caption)
    )


def _inputs(
    entry: ManifestEntry, manifest: Manifest, runs_dir: Path, results_dir: Path
) -> RunInputs:
    """Checks 2 to 6 of design 5.2, in order (check 1 is done for every entry first)."""
    shape = _task_and_system(entry.run_id)
    if shape is None:
        raise ReplayRefused(NOT_DEV_RUN)
    task_id = shape[0]
    try:
        task = dev_task(task_id)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValidationError, TypeError):
        raise ReplayRefused(NOT_DEV_RUN) from None
    if task is None:
        raise ReplayRefused(NOT_DEV_RUN)

    run_dir = runs_dir / entry.run_id
    record_path, events_path = run_dir / "record.json", run_dir / "events.jsonl"
    if not (record_path.is_file() and events_path.is_file()):
        raise ReplayRefused(RUN_FILES_MISSING)
    try:
        record = RunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError):
        raise ReplayRefused(RECORD_MISMATCH) from None
    if (record.run_id, record.task_id, record.mode) != (entry.run_id, task_id, "bench"):
        raise ReplayRefused(RECORD_MISMATCH)

    row = find_row(results_dir, entry.run_id)
    retries = row.get("infra_retries")
    if (
        row.get("split") != "dev"
        or row.get("crashed") is not False
        or type(retries) is not int
        or retries != 0
    ):
        raise ReplayRefused(ROW_NOT_SCORED)
    if entry.pair is not None and not _is_pair(entry, manifest.replays):
        raise ReplayRefused(BAD_PAIR)
    if not _caption_ok(entry.caption):
        raise ReplayRefused(BAD_CAPTION)
    return RunInputs(
        run_id=entry.run_id,
        events_path=events_path,
        record=record,
        row=row,
        task=task,
        caps={**manifest.caps, **(entry.caps or {})},
        allow=entry.allow,
    )


def _index_entry(entry: ManifestEntry, data: dict) -> dict:
    """Design 4.6: the summary is copied from the replay, plus `allow` when set."""
    run, outcome = data["run"], data["outcome"]
    item = {
        "run_id": entry.run_id,
        "file": f"{entry.run_id}.json",
        "caption": entry.caption,
        "pair": entry.pair,
        "task_id": run["task_id"],
        "issue_title": run["issue"]["title"],
        "repo": run["repo"],
        "category": run["category"],
        "system": run["system"],
        "preset": run["preset"],
        "outcome": outcome["outcome"],
        "failure_kind": outcome["failure_kind"],
        "resolved": outcome["resolved"],
        "cost_usd": outcome["cost_usd"],
        "tool_calls": outcome["tool_calls"],
        "duration_s": outcome["duration_s"],
        "recorded_at": run["recorded_at"],
    }
    if entry.allow:
        item["allow"] = [{"path": path, "rule": rule} for path, rule in entry.allow]
    return item


def _publish(files: dict[str, str], out_dir: Path, exact: Sequence[str]) -> None:
    """Check the finished files (raw text included), then replace `out_dir/*.json`."""
    with tempfile.TemporaryDirectory(prefix="replays-") as staging:
        for name, text in files.items():
            (Path(staging) / name).write_bytes(text.encode("utf-8"))
        try:
            hits = check_paths([Path(staging)], exact=exact)
        except ReplayFileError:
            raise ReplayRefused(LEAK_FOUND) from None
    if hits:
        raise ReplayRefused(LEAK_FOUND, hits)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out_dir / name).write_bytes(text.encode("utf-8"))
    for old in sorted(out_dir.glob("*.json")):
        if old.name not in files:
            old.unlink()


def build(
    manifest_path: Path, runs_dir: Path, results_dir: Path, out_dir: Path
) -> list[str]:
    """Every manifest entry as a replay, plus `index.json`, or `ReplayRefused`.

    All or nothing: `out_dir` changes only when every entry converted and the
    finished files passed the leak check. Returns the lines to print: one per
    replay (`<run-id>: <n> steps, <k> KB`) and the warnings, led by
    `EXACT_RULES_OFF` when neither GOOGLE_CLOUD_PROJECT nor REPLAY_REDACT is set.
    A converter hit's JSON path refers to the replay before its cuts, so it can
    name a list item or a part of a string the published file no longer holds.
    """
    manifest = load_manifest(manifest_path)
    for n, entry in enumerate(manifest.replays, 1):
        if _task_and_system(entry.run_id) is None:  # before any run file is opened
            raise ReplayRefused(f"entry {n}: {NOT_DEV_RUN}")
    project, redact = project_value(), redact_values()
    exact = tuple(v for v in (project, *redact) if v)
    lines = [] if exact else [EXACT_RULES_OFF]
    graphs = export_graphs()
    files: dict[str, str] = {}
    index: list[dict] = []
    for n, entry in enumerate(manifest.replays, 1):
        try:
            inputs = _inputs(entry, manifest, runs_dir, results_dir)
            data = convert_run(
                inputs,
                graphs=graphs,
                project=project or None,
                redact=redact,
                repo_root=REPO_ROOT,
                home=Path.home(),
            )
        except ReplayRefused as err:
            raise ReplayRefused(f"entry {n}: {err}", err.hits) from None
        text = dumps(data)
        size = len(text.encode("utf-8"))
        if size > SIZE_LIMIT_BYTES:
            raise ReplayRefused(f"entry {n}: {TOO_BIG}")
        kb = math.ceil(size / 1024)
        lines.append(f"{entry.run_id}: {len(data['steps'])} steps, {kb} KB")
        if size > SIZE_WARN_BYTES:
            lines.append(f"entry {n}: replay is {kb} KB (over 300 KB)")
        files[f"{entry.run_id}.json"] = text
        index.append(_index_entry(entry, data))
    files[INDEX_FILE] = dumps(
        {"schema": SCHEMA, "note": manifest.note, "replays": index}
    )
    _publish(files, out_dir, exact)
    return lines


# --- command line ---------------------------------------------------------------


def _build_command(args: argparse.Namespace) -> int:
    try:
        lines = build(
            Path(args.manifest),
            Path(args.runs_dir),
            Path(args.results_dir),
            Path(args.out),
        )
    except ReplayRefused as err:
        print(err, file=sys.stderr)
        for hit in err.hits:
            print(hit, file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


def _check_command(args: argparse.Namespace) -> int:
    return replay_check.main(list(args.paths))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench.replay")
    commands = parser.add_subparsers(dest="command", required=True)
    graphs = commands.add_parser("graphs", help="export both graphs for the site")
    graphs.add_argument("--out", default=str(GRAPHS_PATH))
    graphs.add_argument(
        "--check", action="store_true", help="exit 1 if the file differs from the code"
    )
    graphs.set_defaults(run=_graphs_command)

    build_cmd = commands.add_parser(
        "build", help="convert the manifest's runs into the site's replay files"
    )
    build_cmd.add_argument("--manifest", default=str(MANIFEST_PATH))
    build_cmd.add_argument("--runs-dir", default=str(RUNS_DIR))
    build_cmd.add_argument("--results-dir", default=str(RESULTS_DIR))
    build_cmd.add_argument("--out", default=str(OUT_DIR))
    build_cmd.set_defaults(run=_build_command)

    check = commands.add_parser("check", help="run the leak check on replay files")
    check.add_argument(
        "paths", nargs="*", help=f"files or directories (default {OUT_DIR})"
    )
    check.set_defaults(run=_check_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in ("build", "check"):
        load_dotenv()  # GOOGLE_CLOUD_PROJECT and REPLAY_REDACT, for the exact values
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
