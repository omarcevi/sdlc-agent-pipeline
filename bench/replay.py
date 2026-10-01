"""Replay-site tooling.

uv run python -m bench.replay graphs [--out PATH] [--check]

`graphs` exports the multi-agent and single-agent graphs to the JSON file the replay
site draws. It needs no model, no sandbox and no network.

All `app` imports sit at the top of the module, as in `bench.run`, so nothing under
`app` looks at the environment after a `.env` file has been loaded.
"""

import argparse
import json
import sys
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path

from google.adk.agents import BaseAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from app.baseline import build_baseline_workflow
from app.models import RoleModels
from app.pipeline import build_workflow

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


# --- command line ---------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench.replay")
    commands = parser.add_subparsers(dest="command", required=True)
    graphs = commands.add_parser("graphs", help="export both graphs for the site")
    graphs.add_argument("--out", default=str(GRAPHS_PATH))
    graphs.add_argument(
        "--check", action="store_true", help="exit 1 if the file differs from the code"
    )
    graphs.set_defaults(run=_graphs_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
