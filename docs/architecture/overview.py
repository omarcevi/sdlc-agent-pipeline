"""Draws the whole-system overview: docs/architecture/overview.svg and overview.png.

uv run --group docs python docs/architecture/overview.py     (or: make diagrams)

Needs Graphviz (`brew install graphviz`), so it runs on a laptop, not in CI. After
rendering it embeds every icon in the SVG as a data URI (the `diagrams` library
references its icon files by absolute path, which would leak a local path and not
render on GitHub) and writes the SHA-256 of this file into the SVG as an XML
comment. tests/unit/test_architecture.py compares that hash with this file's, so a
changed source cannot ship with a stale picture.

Everything drawn exists in deployment/terraform, .github/workflows and the code;
the names are the real ones.
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

GRAPH = {
    "rankdir": "TB",
    "fontsize": "26",
    "fontname": "Helvetica-Bold",
    "pad": "0.4",
    "nodesep": "0.6",
    "ranksep": "1.3",
    "splines": "spline",
    "compound": "true",
    "labelloc": "t",
}
NODE = {"fontsize": "15", "fontname": "Helvetica", "imagescale": "true"}
EDGE = {
    "fontsize": "13",
    "fontname": "Helvetica",
    "color": "#5f6368",
    "fontcolor": "#202124",
}


def cluster(label: str, fill: str, border: str = "#9aa0a6") -> dict:
    return {
        "label": label,
        "style": "rounded,filled",
        "fillcolor": fill,
        "color": border,
        "fontsize": "20",
        "fontname": "Helvetica-Bold",
        "labeljust": "l",
        "margin": "18",
    }


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
            laptop = Client("bench, app.live,\nmake targets")

        with Cluster("Local Docker", graph_attr=cluster("Local Docker", "#f1f3f4")):
            docker = Docker("sandboxes\n(laptop and CI runs)")

        with Cluster("GitHub", graph_attr=cluster("GitHub", "#f6f8fa", "#57606a")):
            Github("repository")
            with Cluster("Actions", graph_attr=cluster("Actions workflows", "#ffffff")):
                ci = GithubActions("ci")
                paid = GithubActions("paid\n(dispatch, guard)")
                release = GithubActions("release\n(tag v*, production\napproval)")
                pages = GithubActions("pages")
            site = Internet("Pages site\n(replay)")
            demo = Github("demo organisation\n(issues, pull requests)")

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
                trace = Monitoring("Cloud Trace")
                bucket = GCS("logs bucket")
                sink = Logging("log sink")
                bq = BigQuery("BigQuery\nissue_to_pr_telemetry")

            with Cluster(
                "Cost", graph_attr=cluster("Cost hard stop", "#fef7e0", "#f9ab00")
            ):
                budget = Tasks("Billing budget\nissue-to-pr-budget")
                topic = PubSub("Pub/Sub topic")
                guard = Functions("budget-guard")
                billing = ResourceManager("project billing link")

        # Laptop
        laptop >> Edge(label="commands in,\noutput out") >> docker
        (
            laptop
            >> Edge(label="issue read, pull request\n(token file, orchestrator only)")
            >> demo
        )
        (
            laptop
            >> Edge(label="make deploy,\nmake sandbox-cloud\n(owner approval)")
            >> engine
        )
        laptop >> Edge(label="make sandbox-cloud") >> build

        # GitHub
        ci >> Edge(label="docker job:\nno cloud credentials") >> docker
        paid >> Edge(label="sandboxes on\nthe runner") >> docker
        paid >> Edge(label="OIDC token,\npaid.yaml on main") >> wif
        release >> Edge(label="OIDC token,\nproduction environment") >> wif
        pages >> Edge(label="web/ build") >> site
        wif >> Edge(label="WIF to ci-runner") >> ci_runner
        wif >> Edge(label="deployer, after approval") >> deployer
        ci_runner >> Edge(label="model calls") >> gemini
        deployer >> Edge(label="deploy, acts as\nissue-to-pr-app") >> engine

        # A run on the engine
        engine >> Edge(label="runs as", style="dashed") >> app_sa
        app_sa >> Edge(label="signs sandbox-caller\ntoken") >> caller
        caller >> Edge(label="commands in,\noutput out") >> sandbox
        engine >> Edge(label="model calls\n(issue-to-pr-app)") >> gemini
        engine >> Edge(label="spans\n(no message text)") >> trace
        engine >> Edge(label="prompts as files") >> bucket
        engine >> Edge(label="analytics plugin\nevents") >> bq
        bucket >> Edge(label="completions\n(external table)") >> bq
        sink >> Edge(label="GenAI logs") >> bq

        # Sandbox image
        build >> Edge(label="sandbox image") >> registry
        registry >> Edge(label="image") >> sandbox

        # Cost
        budget >> Edge(label="50, 80, 100 %") >> topic
        topic >> Edge(label="message") >> guard
        (
            guard
            >> Edge(
                label="over budget:\ndisable billing",
                color="#d93025",
                fontcolor="#d93025",
            )
            >> billing
        )


def embed_and_stamp(source: Path = SOURCE) -> None:
    """Embeds the icons as data URIs and writes the source hash into the SVG."""
    svg = (STEM.with_suffix(".svg")).read_text(encoding="utf-8")

    def inline(match: re.Match) -> str:
        path = Path(match.group(2))
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'{match.group(1)}="data:image/png;base64,{data}"'

    svg = re.sub(r'(xlink:href)="(/[^"]+\.png)"', inline, svg)
    # Graphviz writes the working path into the title and comments only when asked,
    # but check anyway: a leaked local path must stop the build, not ship.
    for needle in ("/Users/", "/home/", "site-packages"):
        if needle in svg:
            raise SystemExit(f"error: the SVG still holds a local path ({needle})")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    svg = svg.replace("<svg ", f"<!-- source-sha256: {digest} -->\n<svg ", 1)
    STEM.with_suffix(".svg").write_text(svg, encoding="utf-8")


def main() -> int:
    draw()
    embed_and_stamp()
    print(f"wrote {STEM.with_suffix('.svg').name} and {STEM.with_suffix('.png').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
