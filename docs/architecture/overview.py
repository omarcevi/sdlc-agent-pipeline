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
"""

import base64
import hashlib
import re
import sys
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.gcp.analytics import BigQuery, PubSub
from diagrams.gcp.compute import Functions, Run
from diagrams.gcp.devtools import Build, ContainerRegistry, Tasks
from diagrams.gcp.ml import AIPlatform, VertexAI
from diagrams.gcp.operations import Logging, Monitoring
from diagrams.gcp.security import IAP, Iam, ResourceManager
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
    "rankdir": "LR",
    "fontsize": "30",
    "fontname": "Helvetica-Bold",
    "pad": "0.4",
    "nodesep": "0.45",
    "ranksep": "1.1",
    "splines": "spline",
    "labelloc": "t",
}
NODE = {"fontsize": "17", "fontname": "Helvetica", "imagescale": "true"}
EDGE = {
    "fontsize": "16",
    "fontname": "Helvetica",
    "color": "#5f6368",
    "fontcolor": "#202124",
}
RED = {"color": "#d93025", "fontcolor": "#d93025"}


def cluster(label: str, fill: str, border: str = "#9aa0a6") -> dict:
    return {
        "label": label,
        "style": "rounded,filled",
        "fillcolor": fill,
        "color": border,
        "fontsize": "22",
        "fontname": "Helvetica-Bold",
        "labeljust": "l",
        "margin": "20",
    }


def draw() -> None:
    with Diagram(
        "issue-to-pr: where everything runs",
        filename=str(STEM),
        outformat=["svg", "png"],
        show=False,
        direction="LR",
        graph_attr=GRAPH,
        node_attr=NODE,
        edge_attr=EDGE,
    ):
        with Cluster(
            "GitHub",
            graph_attr=cluster(
                "GitHub: the repository, its four workflows and the demo organisation",
                "#f6f8fa",
                "#57606a",
            ),
        ):
            with Cluster("Actions", graph_attr=cluster("Actions workflows", "#ffffff")):
                ci = GithubActions("ci")
                paid = GithubActions("paid\n(dispatch, guard)")
                release = GithubActions("release\n(tag v*, production\napproval)")
                pages = GithubActions("pages")
            runner_docker = Docker("Docker sandboxes\n(GitHub runners)")
            site = Internet("Pages site\n(replay)")
            demo = Github("demo organisation\n(issues, pull requests)")

        with Cluster(
            "Laptop",
            graph_attr={**cluster("Laptop (owner)", "#f8f9fa"), "labeljust": "r"},
        ):
            laptop = Client("bench, app.live,\nmake targets")
            local_docker = Docker("Docker sandboxes\n(laptop)")

        with Cluster("GCP", graph_attr=cluster("GCP project", "#e8f0fe", "#1a73e8")):
            with Cluster("Identity", graph_attr=cluster("Identity", "#ffffff")):
                wif = IAP(
                    "Workload Identity\nFederation\n(github-actions,\ngithub-oidc)"
                )
                ci_runner = Iam("ci-runner")
                deployer = Iam("deployer")
                app_sa = Iam("issue-to-pr-app")
                caller = Iam("sandbox-caller")

            with Cluster("Run", graph_attr=cluster("Agent Runtime", "#ffffff")):
                engine = AIPlatform("engine\nissue-to-pr")
                sandbox = Run("sandbox template\nand per-run\nsandboxes")

            gemini = VertexAI("Vertex AI\nGemini")

            with Cluster("Image", graph_attr=cluster("Sandbox image", "#ffffff")):
                build = Build("Cloud Build")
                registry = ContainerRegistry("Artifact Registry")

            with Cluster("Obs", graph_attr=cluster("Observability", "#ffffff")):
                trace = Monitoring("Cloud Trace\n(spans)")
                bucket = GCS("logs bucket")
                sink = Logging("log sink")
                bq = BigQuery("BigQuery\nissue_to_pr_telemetry")

            with Cluster(
                "Cost", graph_attr=cluster("Cost hard stop", "#fef7e0", "#f9ab00")
            ):
                budget = Tasks("Billing budget\nissue-to-pr-budget")
                topic = PubSub("Pub/Sub topic\nissue-to-pr-budget")
                guard = Functions("budget-guard")
                billing = ResourceManager("project billing\n(billing, not identity)")

        # Laptop: local runs
        laptop >> Edge(label="commands in,\noutput out") >> local_docker
        (
            laptop
            >> Edge(constraint="false", label="issue read, pull request\n(token file)")
            >> demo
        )
        (laptop >> Edge(label="model calls\n(owner credentials)") >> gemini)
        (laptop >> Edge(label="cloud sandboxes,\ntoken signed\nby the owner") >> caller)
        (laptop >> Edge(label="make deploy\n(owner approval)") >> engine)
        laptop >> Edge(label="make sandbox-cloud") >> build

        # GitHub workflows
        ci >> Edge(label="docker job,\nno cloud credentials") >> runner_docker
        paid >> Edge(label="sandboxes on\nthe runner") >> runner_docker
        pages >> Edge(label="web/ build") >> site
        paid >> Edge(label="OIDC token,\npaid.yaml on main") >> wif
        release >> Edge(label="OIDC token,\nproduction environment") >> wif
        wif >> Edge(label="WIF to ci-runner") >> ci_runner
        wif >> Edge(label="deployer,\nafter approval") >> deployer
        ci_runner >> Edge(label="model calls") >> gemini
        deployer >> Edge(label="deploy, acts as\nissue-to-pr-app") >> engine

        # A deployed run
        engine >> Edge(label="runs as", style="dashed") >> app_sa
        app_sa >> Edge(label="signs sandbox-caller\ntoken") >> caller
        caller >> Edge(label="commands in,\noutput out") >> sandbox
        engine >> Edge(label="model calls\n(issue-to-pr-app)") >> gemini
        engine >> Edge(label="spans\n(no message text)") >> trace
        engine >> Edge(label="prompts as files") >> bucket
        engine >> Edge(label="analytics plugin\nevents") >> bq
        engine >> Edge(label="GenAI logs") >> sink
        bucket >> Edge(label="completions\n(external table)") >> bq
        sink >> Edge(label="filtered logs") >> bq

        # Sandbox image
        build >> Edge(label="sandbox image") >> registry
        registry >> Edge(label="image") >> sandbox

        # Cost (kept to the right of the engine)
        engine >> Edge(style="invis") >> budget
        budget >> Edge(label="50, 80, 100 %") >> topic
        topic >> Edge(label="message") >> guard
        guard >> Edge(label="over budget:\ndisable billing", **RED) >> billing


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
