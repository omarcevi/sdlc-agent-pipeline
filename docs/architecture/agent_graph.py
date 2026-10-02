"""Writes the agent graphs as Mermaid flowcharts into docs/architecture.md.

uv run python docs/architecture/agent_graph.py [--check]

The graphs come from web/src/graph/graphs.json, the file the replay site draws
(`bench.replay graphs` writes it from the code). The text between the
`agent-graph:start` and `agent-graph:end` markers is replaced; `--check` exits 1
when the file differs from what would be written and changes nothing.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRAPHS = ROOT / "web" / "src" / "graph" / "graphs.json"
PAGE = ROOT / "docs" / "architecture.md"
START = "<!-- agent-graph:start -->"
END = "<!-- agent-graph:end -->"

TITLES = {
    "multi": ("The multi-agent graph", "`app.pipeline.build_workflow`"),
    "single": ("The single-agent baseline", "`app.baseline.build_baseline_workflow`"),
}
# Mermaid shape per node kind: stadium for the start, a double-bordered box for LLM
# agents, a hexagon for routers, a plain box for function nodes.
SHAPES = {
    "start": ("([", "])"),
    "llm": ("[[", "]]"),
    "router": ("{{", "}}"),
    "function": ("[", "]"),
}


def mermaid(graph: dict) -> str:
    lines = ["flowchart TD"]
    for node in graph["nodes"]:
        left, right = SHAPES[node["kind"]]
        lines.append(f'    {node["id"]}{left}"{node["id"]}"{right}')
    for edge in graph["edges"]:
        arrow = f"-->|{edge['route']}|" if edge["route"] else "-->"
        lines.append(f"    {edge['from']} {arrow} {edge['to']}")
    lines.append("    classDef llm fill:#e8f0fe,stroke:#1a73e8,color:#202124")
    lines.append("    classDef router fill:#fef7e0,stroke:#f9ab00,color:#202124")
    lines.append("    classDef fn fill:#f1f3f4,stroke:#5f6368,color:#202124")
    for kind, cls in (("llm", "llm"), ("router", "router")):
        ids = [n["id"] for n in graph["nodes"] if n["kind"] == kind]
        if ids:
            lines.append(f"    class {','.join(ids)} {cls}")
    ids = [n["id"] for n in graph["nodes"] if n["kind"] in ("function", "start")]
    lines.append(f"    class {','.join(ids)} fn")
    return "\n".join(lines)


def block() -> str:
    data = json.loads(GRAPHS.read_text())
    parts = []
    for key, (title, source) in TITLES.items():
        parts.append(
            f"**{title}** (from {source}):\n\n```mermaid\n{mermaid(data['graphs'][key])}\n```"
        )
    return "\n\n".join(parts)


def render(page: str) -> str:
    head, sep, rest = page.partition(START)
    _, sep2, tail = rest.partition(END)
    if not sep or not sep2:
        raise SystemExit(f"error: {PAGE.name} has no {START} ... {END} markers")
    return f"{head}{START}\n\n{block()}\n\n{END}{tail}"


def check() -> bool:
    page = PAGE.read_text()
    return render(page) == page


def main(argv: list[str]) -> int:
    if argv not in ([], ["--check"]):
        print("usage: agent_graph.py [--check]", file=sys.stderr)
        return 2
    page = PAGE.read_text()
    new = render(page)
    if argv:
        if new != page:
            print("docs/architecture.md is stale; run: make diagrams", file=sys.stderr)
            return 1
        return 0
    if new != page:
        PAGE.write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
