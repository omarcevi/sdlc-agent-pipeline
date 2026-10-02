"""The committed replay files in web/public/replays (design 9): structure, leaks and
numbers that come from record.json. Nothing here asserts on what a model wrote.

Without .env (pytest never loads it) the leak check runs its pattern rules only;
`bench.replay build` and `check` on the owner's machine add the exact values.
"""

import json
import re
from pathlib import Path

import pytest

import bench.replay as replay
from bench.probes import dev_task
from bench.replay_check import check_paths

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / replay.OUT_DIR
SEALED = re.compile(r"-h\d\d$")
SUMMARY_RUN = (
    "run_id",
    "task_id",
    "repo",
    "category",
    "system",
    "preset",
    "recorded_at",
)
SUMMARY_OUTCOME = (
    "outcome",
    "failure_kind",
    "resolved",
    "cost_usd",
    "tool_calls",
    "duration_s",
)


@pytest.fixture(autouse=True)
def _no_exact_values(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("REPLAY_REDACT", raising=False)


def _index() -> dict:
    return json.loads((OUT / replay.INDEX_FILE).read_text(encoding="utf-8"))


def _replay(entry: dict) -> dict:
    return json.loads((OUT / entry["file"]).read_text(encoding="utf-8"))


def test_committed_replays_pass_the_leak_check():
    assert check_paths([OUT]) == []


def test_index_and_files_agree():
    index = _index()
    entries = index["replays"]
    assert index["schema"] == replay.SCHEMA
    on_disk = {p.name for p in OUT.glob("*.json")} - {replay.INDEX_FILE}
    assert on_disk == {e["file"] for e in entries}
    assert len({e["run_id"] for e in entries}) == len(entries)

    manifest = replay.load_manifest(ROOT / replay.MANIFEST_PATH)
    assert index["note"] == manifest.note
    assert [
        (e["run_id"], e["caption"], e["pair"], e.get("allow", [])) for e in entries
    ] == [
        (m.run_id, m.caption, m.pair, [{"path": p, "rule": r} for p, r in m.allow])
        for m in manifest.replays
    ]

    by_id = {e["run_id"]: e for e in entries}
    for entry in entries:
        assert entry["file"] == f"{entry['run_id']}.json"
        data = _replay(entry)
        run, outcome, steps = data["run"], data["outcome"], data["steps"]
        assert data["schema"] == replay.SCHEMA
        assert entry["issue_title"] == run["issue"]["title"]
        assert all(entry[key] == run[key] for key in SUMMARY_RUN)
        assert all(entry[key] == outcome[key] for key in SUMMARY_OUTCOME)
        assert steps[-1]["kind"] == "outcome"
        assert steps[-1]["cost_usd"] == outcome["cost_usd"]
        assert steps[-1]["tool_calls"] == outcome["tool_calls"]
        if entry["pair"] is not None:
            other = by_id[entry["pair"]]
            assert other["task_id"] == entry["task_id"]
            assert other["system"] != entry["system"]


def test_no_heldout_task_and_every_task_is_dev(monkeypatch):
    entries = _index()["replays"]
    for entry in entries:  # by id first: a held-out task is never opened
        assert not SEALED.search(entry["task_id"])
        assert entry["task_id"].split("-")[1].isdigit()
        assert replay.RUN_ID.fullmatch(entry["run_id"])
        assert entry["run_id"].startswith(entry["task_id"] + "-")
    monkeypatch.setenv("BENCH_TASKS_DIR", str(ROOT / "bench" / "tasks"))
    for entry in entries:
        task = dev_task(entry["task_id"])
        assert task is not None and task.split == "dev"
        assert _replay(entry)["run"]["task_id"] == entry["task_id"]


def test_captions_are_one_line_of_at_most_140_characters():
    for entry in _index()["replays"]:
        caption = entry["caption"]
        assert 0 < len(caption) <= 140
        assert caption.splitlines() == [caption]
        assert replay._caption_ok(caption)


def test_every_replay_is_within_the_size_limits():
    for entry in _index()["replays"]:
        assert (OUT / entry["file"]).stat().st_size <= replay.SIZE_LIMIT_BYTES


def test_committed_replays_use_only_known_nodes():
    graphs = json.loads((ROOT / replay.GRAPHS_PATH).read_text(encoding="utf-8"))
    for entry in _index()["replays"]:
        data = _replay(entry)
        graph = graphs["graphs"][data["run"]["graph"]]
        assert data["run"]["graph"] == entry["system"]
        nodes = {node["id"] for node in graph["nodes"]}
        edges = {(e["from"], e["to"]): e["route"] for e in graph["edges"]}
        for step in data["steps"]:
            assert step["node"] in nodes
            if step["kind"] == "node":
                edge = (step["from"] or "START", step["node"])
                assert edge in edges
                assert step["via"] == edges[edge]
