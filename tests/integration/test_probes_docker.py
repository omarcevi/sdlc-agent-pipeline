import shutil
from pathlib import Path

import pytest
import yaml

from bench.probes import list_probes, load_probe, validate_probe
from tests.fakes import make_bench_task

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]

PLANT_PATCH = (
    "diff --git a/mini.py b/mini.py\n--- a/mini.py\n+++ b/mini.py\n"
    "@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return {expr}\n"
)


def test_committed_probes_validate():
    """Every probe in the repository's probe directory is sound (vacuous until the
    probe set is committed)."""
    problems = {p.probe_id: validate_probe(p) for p in list_probes()}
    assert problems == {p.probe_id: [] for p in list_probes()}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("REVIEW_PROBES_DIR", str(tmp_path / "probes"))
    make_bench_task(tmp_path)
    return tmp_path


def write_probe(root: Path, probe_id: str, kind: str, patch: str) -> None:
    directory = root / "probes" / probe_id
    directory.mkdir(parents=True)
    (directory / "probe.yaml").write_text(
        yaml.safe_dump(
            {
                "task_id": "t-1",
                "kind": kind,
                "source": "hand-written",
                "source_run": None,
                "note": "",
            }
        )
    )
    (directory / "patch.diff").write_text(patch)


def test_the_validator_tells_good_bad_and_broken_patches_apart_in_a_sandbox(store):
    write_probe(store, "rp-01", "good", PLANT_PATCH.format(expr="a + b"))
    write_probe(store, "rp-02", "bad", PLANT_PATCH.format(expr="0"))
    write_probe(store, "rp-03", "bad", PLANT_PATCH.format(expr="a + b"))
    write_probe(store, "rp-04", "good", PLANT_PATCH.format(expr="0"))
    write_probe(
        store,
        "rp-05",
        "bad",
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n",
    )
    write_probe(
        store, "rp-06", "good", PLANT_PATCH.format(expr="a + b").replace("a + b", "a +")
    )
    got = {p.probe_id: validate_probe(p) for p in list_probes()}
    assert got["rp-01"] == []
    assert got["rp-02"] == []
    assert got["rp-03"] == ["bad probe passes the hidden tests"]
    assert got["rp-04"] == ["good probe fails the hidden tests"]
    assert got["rp-05"] == ["patch does not apply"]
    assert got["rp-06"] == ["visible tests fail"]
    assert load_probe("rp-01").kind == "good"
