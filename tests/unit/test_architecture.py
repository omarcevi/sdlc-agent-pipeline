"""Keeps the architecture documents honest: the picture matches its source, the
generated graph block is current, and no relative link is broken."""

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARCH_DIR = ROOT / "docs" / "architecture"
SVG = ARCH_DIR / "overview.svg"
SOURCE = ARCH_DIR / "overview.py"
PAGE = ROOT / "docs" / "architecture.md"


SIDECAR = ARCH_DIR / "overview.sha256"


def _sidecar() -> dict:
    return dict(line.split() for line in SIDECAR.read_text().splitlines())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_overview_svg_matches_its_source():
    expected = _sha(SOURCE)
    found = re.search(r"<!-- source-sha256: ([0-9a-f]{64}) -->", SVG.read_text())
    assert found, "overview.svg carries no source hash; run: make diagrams"
    assert found.group(1) == expected, "overview.svg is stale; run: make diagrams"


def test_overview_png_and_svg_match_the_recorded_hashes():
    recorded = _sidecar()
    assert recorded["source"] == _sha(SOURCE), "overview.sha256 is stale: make diagrams"
    assert recorded["svg"] == _sha(SVG), "overview.svg was changed by hand"
    assert recorded["png"] == _sha(ARCH_DIR / "overview.png"), "overview.png is stale"


IDENTITIES = (
    "issue-to-pr-app",
    "sandbox-caller",
    "ci-runner",
    "deployer",
    "github-actions",
    "github-oidc",
    "budget-guard",
    "issue-to-pr-budget",
    "issue_to_pr_telemetry",
)


def test_names_drawn_in_the_overview_exist_in_terraform():
    source = SOURCE.read_text()
    terraform = "\n".join(
        p.read_text() for p in (ROOT / "deployment" / "terraform").rglob("*.tf")
    )
    variables = (ROOT / "deployment/terraform/single-project/variables.tf").read_text()
    project = re.search(
        r'variable "project_name".*?default\s*=\s*"([^"]+)"', variables, re.S
    )
    assert project, "project_name has no default"
    # The app account and the dataset are named from the project name.
    terraform = terraform.replace("${var.project_name}", project.group(1))
    terraform = terraform.replace(
        'replace("issue-to-pr_telemetry", "-", "_")', '"issue_to_pr_telemetry"'
    )
    for name in IDENTITIES:
        assert name in source, f"{name} is no longer drawn; update IDENTITIES"
        assert f'"{name}' in terraform or f'{name}"' in terraform, (
            f"{name} is not in deployment/terraform"
        )


def test_overview_svg_holds_no_local_paths():
    text = SVG.read_text()
    for needle in ("/Users/", "/home/", "site-packages", "file://", "C:\\"):
        assert needle not in text
    # Every image is embedded as data, so GitHub renders it without local files.
    hrefs = re.findall(r'(?:xlink:)?href="([^"]*)"', text)
    images = re.findall(r"<image\b[^>]*>", text)
    assert images, "the overview has no icons"
    for tag in images:
        assert re.search(r'href="data:image/', tag), tag[:120]
    assert all(h.startswith(("data:", "#")) for h in hrefs)


def test_agent_graph_block_is_current():
    spec = importlib.util.spec_from_file_location(
        "agent_graph", ARCH_DIR / "agent_graph.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check(), "the agent-graph block is stale; run: make diagrams"


LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _documents():
    docs = [ROOT / "README.md", PAGE, *sorted((ROOT / "docs" / "adr").glob("*.md"))]
    return [d for d in docs if d.exists()]


def test_relative_links_resolve():
    missing = []
    for doc in _documents():
        text = re.sub(r"```.*?```", "", doc.read_text(), flags=re.S)
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = target.split("#", 1)[0]
            if path and not (doc.parent / path).resolve().exists():
                missing.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert not missing, missing


def test_page_has_the_overview_and_the_four_views():
    text = PAGE.read_text()
    assert "architecture/overview.svg" in text
    assert text.count("```mermaid") >= 4


@pytest.mark.parametrize("name", ["overview.py", "agent_graph.py"])
def test_scripts_exist(name):
    assert (ARCH_DIR / name).is_file()
