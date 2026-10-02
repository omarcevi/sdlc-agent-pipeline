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


def test_overview_svg_matches_its_source():
    expected = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    found = re.search(r"<!-- source-sha256: ([0-9a-f]{64}) -->", SVG.read_text())
    assert found, "overview.svg carries no source hash; run: make diagrams"
    assert found.group(1) == expected, "overview.svg is stale; run: make diagrams"


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
