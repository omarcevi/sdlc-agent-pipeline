"""Draws the whole-system overview: docs/architecture/overview.svg and overview.png.

make diagrams   (runs this file with `uv run --no-project --with diagrams==0.25.1`)

Needs Graphviz (`brew install graphviz`), so it runs on a laptop, not in CI. This
file imports nothing from the project, so it runs without the project's own
dependencies. After rendering it embeds every icon in the SVG as a data URI (the
`diagrams` library references its icon files by absolute path, which would leak a
local path and not render on GitHub) and writes the SHA-256 of this file into the
SVG as an XML comment. It also writes overview.sha256 with this file's hash and
the hash of each rendered file; tests/unit/test_architecture.py compares them, so a
changed source cannot ship with a stale picture and a picture cannot be edited by
hand.

Everything drawn exists in deployment/terraform, .github/workflows and the code;
the names are the real ones (a test looks each one up in the Terraform files).
The layout runs top to bottom, the laptop and GitHub above the GCP project, and
edge labels are short, so the picture reads at README width; the Mermaid views in
docs/architecture.md carry the details. Node ids are fixed, so re-rendering an
unchanged file gives the same SVG.
"""

import base64
import hashlib
import re
import sys
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.gcp.analytics import BigQuery, PubSub
from diagrams.gcp.compute import Functions, Run
from diagrams.gcp.devtools import Build, ContainerRegistry
from diagrams.gcp.management import Billing, Project
from diagrams.gcp.ml import AIPlatform, VertexAI
from diagrams.gcp.operations import Logging, Monitoring
from diagrams.gcp.security import IAP, Iam
from diagrams.gcp.storage import GCS
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.client import Client
from diagrams.onprem.container import Docker
from diagrams.onprem.network import Internet
from diagrams.onprem.vcs import Github

HERE = Path(__file__).resolve().parent
STEM = HERE / "overview"
SOURCE = HERE / "overview.py"
SIDECAR = HERE / "overview.sha256"

GRAPH = {
    "rankdir": "TB",
    "fontsize": "44",
    "fontname": "Helvetica-Bold",
    "pad": "0.4",
    "nodesep": "1.1",
    "ranksep": "1.0",
    "splines": "spline",
    "labelloc": "t",
}
NODE = {"fontsize": "26", "fontname": "Helvetica", "imagescale": "true"}
EDGE = {
    "fontsize": "24",
    "fontname": "Helvetica",
    "color": "#5f6368",
    "fontcolor": "#202124",
    "penwidth": "1.6",
}
RED = {"color": "#d93025", "fontcolor": "#d93025"}
SAME = {"minlen": "0"}


def link(label: str = "", **attrs) -> Edge:
    """An edge with readable text: diagrams' Edge sets 13 pt on every edge."""
    return Edge(label=label, fontsize="24", fontname="Helvetica", **attrs)


def cluster(label: str, fill: str, border: str = "#9aa0a6") -> dict:
    return {
        "label": label,
        "style": "rounded,filled",
        "fillcolor": fill,
        "color": border,
        "fontsize": "30",
        "fontname": "Helvetica-Bold",
        "labeljust": "l",
        "margin": "22",
    }


def node(kind, label: str, nodeid: str):
    """An icon node tall enough for its label, the icon on top, with a stable id
    (diagrams sizes nodes for 13 pt labels and gives them random ids)."""
    lines = label.count("\n") + 1
    return kind(
        label, nodeid=nodeid, height=str(1.5 + 0.45 * lines), imagepos="tc"
    )


def draw() -> None:
    with Diagram(
        "issue-to-pr: where everything runs",
        filename=str(STEM),
        outformat=["svg", "png"],
        show=False,
        direction="TB",
        graph_attr=GRAPH,
        node_attr=NODE,
        edge_attr=EDGE,
    ):
        with Cluster("Laptop", graph_attr=cluster("Laptop (owner)", "#f8f9fa")):
            laptop = node(Client, "bench, app.live,\nmake targets", "laptop")
            local_docker = node(Docker, "Docker sandboxes", "local_docker")

        with Cluster(
            "GitHub", graph_attr=cluster("GitHub", "#f6f8fa", "#57606a")
        ):
            demo = node(Github, "demo\nrepositories", "demo")
            ci = node(GithubActions, "ci", "ci")
            paid = node(GithubActions, "paid\n(dispatch)", "paid")
            release = node(GithubActions, "release\n(approval)", "release")
            pages = node(GithubActions, "pages", "pages")
            runner_docker = node(Docker, "Docker sandboxes\n(runner)", "runner_docker")
            site = node(Internet, "replay site", "site")

        with Cluster("GCP", graph_attr=cluster("GCP project", "#e8f0fe", "#1a73e8")):
            with Cluster("Identity", graph_attr=cluster("Identity (WIF: github-actions / github-oidc)", "#ffffff")):
                wif = node(IAP, "Workload\nIdentity\nFederation", "wif")
                ci_runner = node(Iam, "ci-runner", "ci_runner")
                deployer = node(Iam, "deployer", "deployer")
                app_sa = node(Iam, "issue-to-pr-app", "app_sa")
                caller = node(Iam, "sandbox-caller", "caller")

            with Cluster("Run", graph_attr=cluster("Agent Runtime", "#ffffff")):
                engine = node(AIPlatform, "engine\nissue-to-pr", "engine")
                sandbox = node(Run, "sandboxes", "sandbox")

            gemini = node(VertexAI, "Vertex AI\nGemini", "gemini")

            with Cluster("Image", graph_attr=cluster("Sandbox image", "#ffffff")):
                build = node(Build, "Cloud Build", "build")
                registry = node(ContainerRegistry, "Artifact\nRegistry", "registry")

            with Cluster("Obs", graph_attr=cluster("Observability (dataset issue_to_pr_telemetry)", "#ffffff")):
                trace = node(Monitoring, "Cloud Trace", "trace")
                bucket = node(GCS, "logs bucket", "bucket")
                sink = node(Logging, "log sink", "sink")
                bq = node(BigQuery, "BigQuery", "bq")

            with Cluster(
                "Cost", graph_attr=cluster("Cost hard stop (budget issue-to-pr-budget)", "#fef7e0", "#f9ab00")
            ):
                budget = node(Billing, "budget", "budget")
                topic = node(PubSub, "Pub/Sub", "topic")
                guard = node(Functions, "budget-guard", "guard")
                billing = node(Project, "project billing", "billing")

        # Laptop: local runs
        laptop >> link("commands") >> local_docker
        laptop >> link("token file", constraint="false") >> demo
        laptop >> link("model calls") >> gemini
        laptop >> link("signed token") >> caller
        laptop >> link("make deploy") >> engine
        laptop >> link() >> build

        # GitHub workflows
        ci >> link() >> runner_docker
        paid >> link() >> runner_docker
        pages >> link() >> site
        paid >> link("OIDC") >> wif
        release >> link("OIDC") >> wif
        wif >> link() >> ci_runner
        wif >> link() >> deployer
        ci_runner >> link("model calls") >> gemini
        deployer >> link("deploy") >> engine

        # A deployed run
        engine >> link("runs as", style="dashed", **SAME) >> app_sa
        app_sa >> link("signs", **SAME) >> caller
        caller >> link("commands") >> sandbox
        engine >> link("model calls") >> gemini
        engine >> link("spans, no text") >> trace
        engine >> link("prompts") >> bucket
        engine >> link("events") >> bq
        engine >> link("logs") >> sink
        bucket >> link(**SAME) >> bq
        sink >> link(**SAME) >> bq

        # Sandbox image
        build >> link(**SAME) >> registry
        registry >> link("image") >> sandbox

        # Rows: Laptop and GitHub on top, the whole GCP project below them
        local_docker >> Edge(style="invis") >> build
        runner_docker >> Edge(style="invis") >> wif

        # Cost, on the bottom row
        engine >> Edge(style="invis") >> budget
        budget >> link("50, 80, 100 %", **SAME) >> topic
        topic >> link(**SAME) >> guard
        guard >> link("disable billing", **RED, **SAME) >> billing


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def embed_and_stamp() -> None:
    """Embeds the icons as data URIs, stamps the SVG and writes the sidecar."""
    svg_path = STEM.with_suffix(".svg")
    svg = svg_path.read_text(encoding="utf-8")

    def inline(match: re.Match) -> str:
        data = base64.b64encode(Path(match.group(2)).read_bytes()).decode("ascii")
        return f'{match.group(1)}="data:image/png;base64,{data}"'

    svg = re.sub(r'(xlink:href)="(/[^"]+\.png)"', inline, svg)
    # A leaked local path must stop the build, not ship.
    for needle in ("/Users/", "/home/", "site-packages"):
        if needle in svg:
            raise SystemExit(f"error: the SVG still holds a local path ({needle})")
    digest = sha256(SOURCE)
    svg = svg.replace("<svg ", f"<!-- source-sha256: {digest} -->\n<svg ", 1)
    svg_path.write_text(svg, encoding="utf-8")
    SIDECAR.write_text(
        f"source {digest}\n"
        f"svg {sha256(svg_path)}\n"
        f"png {sha256(STEM.with_suffix('.png'))}\n",
        encoding="utf-8",
    )


def main() -> int:
    draw()
    embed_and_stamp()
    print("wrote overview.svg, overview.png and overview.sha256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
