"""bench.replay build and check: the manifest, the refusals in design 5.2's order,
the held-out guard, all-or-nothing output and index.json (design 4.6, 5.1, 5.2, 6.1).

Every run, results file and task here is synthetic and lives in tmp_path. No test
reads the real runs/, results/ or .env: `bench.replay.load_dotenv` is replaced.
Every build runs under a guard that fails the test on any file access under a
held-out (`*-hNN`) task directory, or on a listing of the tasks directory.
"""

from __future__ import annotations

import builtins
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

import app.task_store as task_store
import bench.replay as replay
from bench.replay_check import EXACT_RULES_OFF, Hit, check_paths
from tests.fakes import make_bench_task
from tests.unit import replay_events as ev
from tests.unit.replay_events import Log, multi_run, single_run

MULTI = ev.MULTI_RUN_ID  # md-001-multi-flash-r1-20261001T062611Z
SINGLE = "md-001-single-flash-r1-20261001T084838Z"
OTHER = "sr-002-multi-flash-r1-20261001T062611Z"
OTHER_SINGLE = "sr-002-single-flash-r1-20261001T084838Z"
HELDOUT_TASK = "md-h01"
HELDOUT_RUN = "md-h01-multi-flash-r1-20261001T062611Z"
SENTINEL = "SEALEDqq"
CAPS = {"cost_usd": 1.0, "tool_calls": 75, "wall_clock_s": 3000}
NOTE = "Synthetic runs for the build tests."
CAPTION = "The coder fixes add; the tests pass."
ENTROPIC = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"  # a false positive of high-entropy
EMAIL = "alice" + "@" + "corp.io"
PROJECT = "demo-proj-4242"
NUMBER = "123456789012"
INDEX_KEYS = [
    "run_id",
    "file",
    "caption",
    "pair",
    "task_id",
    "issue_title",
    "repo",
    "category",
    "system",
    "preset",
    "outcome",
    "failure_kind",
    "resolved",
    "cost_usd",
    "tool_calls",
    "duration_s",
    "recorded_at",
]
MESSAGES = [
    "not a dev bench run",
    "run files missing",
    "record does not match the run id",
    "no single results row",
    "results row is not a scored dev run",
    "pair is not the same task on the other system",
    "caption must be one line of 1 to 140 characters",
    "replay over 1 MB",
    "entry fields are not valid",
    "run is listed twice",
    replay.TOTALS_DIFFER,
    replay.UNKNOWN_NODE,
    replay.RESULT_WITHOUT_CALL,
    replay.RECORD_ROW_DIFFER,
    replay.LEAK_FOUND,
    replay.LOG_UNREADABLE,
    replay.NOT_FINITE,
]


@pytest.fixture(autouse=True)
def _no_exact_values_and_no_dotenv(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("REPLAY_REDACT", raising=False)
    monkeypatch.setattr(replay, "load_dotenv", lambda *args, **kwargs: False)


# --- the guard -------------------------------------------------------------------

_SEALED = re.compile(r"-h\d\d$")
_WATCHED = (
    "open",
    "read_text",
    "read_bytes",
    "exists",
    "is_file",
    "is_dir",
    "stat",
    "iterdir",
    "glob",
    "rglob",
)
_LISTING = {"iterdir", "glob", "rglob"}


class Guard:
    """Records every path the code under test touches; a held-out task directory,
    or a listing of the tasks directory, fails the test."""

    def __init__(self, tasks: Path) -> None:
        self.tasks = tasks
        self.touched: list[Path] = []
        self.violations: list[str] = []

    def see(self, target, op: str) -> None:
        if isinstance(target, int):
            return
        try:
            path = Path(os.fspath(target))
        except TypeError:
            return
        self.touched.append(path)
        sealed = any(_SEALED.search(part) for part in path.parts)
        listing = op in _LISTING and path == self.tasks
        if sealed or listing:
            self.violations.append(op)
            raise AssertionError(f"{op}: a held-out task directory or the task list")

    @contextmanager
    def watching(self) -> Iterator[Guard]:
        with pytest.MonkeyPatch.context() as patch:
            real_open = builtins.open

            def guarded_open(file, *args, **kwargs):
                self.see(file, "open")
                return real_open(file, *args, **kwargs)

            patch.setattr(builtins, "open", guarded_open)
            patch.setattr(io, "open", guarded_open)
            for name in _WATCHED:
                real = getattr(Path, name)

                def method(path, *args, _real=real, _name=name, **kwargs):
                    self.see(path, _name)
                    return _real(path, *args, **kwargs)

                patch.setattr(Path, name, method)
            try:
                yield self
            finally:
                assert self.violations == []


# --- the synthetic world ---------------------------------------------------------


def _task_yaml(title: str, split: str) -> str:
    return (
        f"repo: mini\ntitle: {title}\nbody: {title}\ncategory: bug\n"
        f"difficulty: easy\nsplit: {split}\n"
    )


def entry(run_id: str, caption: str = CAPTION, **extra) -> dict:
    return {"run_id": run_id, "caption": caption, **extra}


def task_of(run_id: str) -> str:
    return "-".join(run_id.split("-")[:2])


class World:
    def __init__(self, root: Path, monkeypatch) -> None:
        self.root = root
        self.tasks = root / "tasks"
        self.runs = root / "runs"
        self.results = root / "results"
        self.out = root / "site" / "replays"
        self.manifest_path = root / "replays.yaml"
        self.rows: dict[str, list] = {}
        make_bench_task(root, "md-001")
        for task_id, split in (("sr-002", "dev"), ("tc-003", "heldout")):
            (self.tasks / task_id).mkdir(parents=True)
            (self.tasks / task_id / "task.yaml").write_text(_task_yaml("x", split))
        sealed = self.tasks / HELDOUT_TASK
        (sealed / "hidden_tests").mkdir(parents=True)
        (sealed / "task.yaml").write_text(_task_yaml(SENTINEL, "heldout"))
        (sealed / "hidden_tests" / "test_x.py").write_text(f"# {SENTINEL}\n")
        self.results.mkdir()
        monkeypatch.setenv("BENCH_TASKS_DIR", str(self.tasks))
        self.guard = Guard(self.tasks)

    def run(
        self,
        run_id: str,
        *,
        log: Log | None = None,
        record: dict | None = None,
        rows: int = 1,
        file: str = "flash.json",
        edit=None,
        **row_fields,
    ) -> Log:
        system = run_id.split("-")[2]
        if log is None:
            log = (
                single_run(run_id=run_id)
                if system == "single"
                else multi_run(run_id=run_id)
            )
        log.task_id = task_of(run_id)
        log.write(self.root, edit)
        made = log.record(**(record or {}))
        (self.runs / run_id / "record.json").write_text(made.model_dump_json(indent=2))
        for _ in range(rows):
            self.add_rows(file, log.row(made, system=system, **row_fields))
        return log

    def add_rows(self, file: str, *rows: dict, first: bool = False) -> None:
        kept = self.rows.setdefault(file, [])
        if first:
            kept[:0] = rows
        else:
            kept.extend(rows)
        (self.results / file).write_text(json.dumps(kept, indent=2))

    def manifest(self, entries: list[dict], *, caps=None, note: str = NOTE) -> None:
        doc = {"note": note, "caps": caps or CAPS, "replays": entries}
        self.manifest_path.write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
        )

    def build(self) -> list[str]:
        with self.guard.watching():
            return replay.build(self.manifest_path, self.runs, self.results, self.out)

    def refused(self) -> replay.ReplayRefused:
        with pytest.raises(replay.ReplayRefused) as caught:
            self.build()
        return caught.value

    def cli(self, *extra: str) -> int:
        args = [
            "build",
            "--manifest",
            str(self.manifest_path),
            "--runs-dir",
            str(self.runs),
            "--results-dir",
            str(self.results),
            "--out",
            str(self.out),
            *extra,
        ]
        with self.guard.watching():
            return replay.main(args)

    def published(self) -> dict[str, bytes]:
        if not self.out.is_dir():
            return {}
        return {p.name: p.read_bytes() for p in sorted(self.out.iterdir())}

    def under_root(self) -> list[Path]:
        """Touched paths in the world other than the manifest."""
        return [
            p
            for p in self.guard.touched
            if p.is_relative_to(self.root) and p != self.manifest_path
        ]


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    return World(tmp_path, monkeypatch)


def _issue_log(run_id: str, body: str) -> Log:
    """The shortest run that publishes an issue body: fetch, provision, deliver."""
    log = Log(run_id)
    log.start(issue={"body": body})
    log.deliver()
    return log


# --- held-out seal (review focus 3) -------------------------------------------------


def test_the_guard_fails_on_a_heldout_task_access_or_a_task_listing(world):
    sealed = world.tasks / HELDOUT_TASK
    touches = [
        lambda: (sealed / "task.yaml").read_text(),
        lambda: open(sealed / "hidden_tests" / "test_x.py").close(),
        lambda: (sealed / "task.yaml").exists(),
        lambda: sealed.is_dir(),
        lambda: list(world.tasks.glob("*/task.yaml")),
        lambda: list(world.tasks.iterdir()),
        task_store.list_tasks,
    ]
    for touch in touches:
        guard = Guard(world.tasks)
        with pytest.raises(AssertionError), guard.watching():
            touch()
        assert guard.violations


def test_heldout_run_id_is_refused_before_any_file_is_opened(world):
    world.run(MULTI)
    world.run(HELDOUT_RUN)
    world.manifest([entry(MULTI), entry(HELDOUT_RUN)])
    assert str(world.refused()) == "entry 2: not a dev bench run"
    assert world.under_root() == []  # no run, results or task file of any entry
    assert world.published() == {}


def test_heldout_task_in_a_record_is_refused(world):
    world.run(MULTI, record={"task_id": HELDOUT_TASK})
    world.manifest([entry(MULTI)])
    assert str(world.refused()) == "entry 1: record does not match the run id"
    assert world.published() == {}


def test_heldout_rows_in_results_files_are_skipped_unread(world, monkeypatch, capsys):
    world.run(MULTI)
    sealed_row = {"task_id": f"{SENTINEL}-h01"}
    for key in ("split", "system", "outcome", "resolved", "cost_usd", "reason", "repo"):
        sealed_row[key] = SENTINEL
    world.add_rows("flash.json", sealed_row, first=True)
    world.add_rows("other.json", dict(sealed_row))
    world.manifest([entry(MULTI)])

    class Watched(dict):
        def __init__(self, pairs) -> None:
            super().__init__(pairs)
            self.seen: list[str] = []

        def __getitem__(self, key):
            self.seen.append(key)
            return super().__getitem__(key)

        def get(self, key, default=None):
            self.seen.append(key)
            return super().get(key, default)

        def __contains__(self, key) -> bool:
            self.seen.append(key)
            return super().__contains__(key)

        def __iter__(self):
            self.seen.append("*")
            return super().__iter__()

        def keys(self):
            self.seen.append("*")
            return super().keys()

        def items(self):
            self.seen.append("*")
            return super().items()

        def values(self):
            self.seen.append("*")
            return super().values()

    sealed: list[Watched] = []

    def read_rows(path: Path) -> list:
        rows = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=Watched)
        sealed.extend(r for r in rows if _SEALED.search(dict.get(r, "task_id", "")))
        return rows

    monkeypatch.setattr(replay, "_read_rows", read_rows)
    assert world.cli() == 0
    assert len(sealed) == 2
    assert [row.seen for row in sealed] == [["task_id"], ["task_id"]]
    printed = capsys.readouterr()
    assert SENTINEL not in printed.out + printed.err
    assert all(SENTINEL.encode() not in data for data in world.published().values())


def test_list_tasks_is_never_called(world, monkeypatch):
    calls: list[int] = []

    def list_tasks():
        calls.append(1)
        raise AssertionError("list_tasks reads every task.yaml, held-out ones too")

    real = task_store.list_tasks
    for module in list(sys.modules.values()):
        if getattr(module, "list_tasks", None) is real:
            monkeypatch.setattr(module, "list_tasks", list_tasks)
    world.run(MULTI)
    world.manifest([entry(MULTI)])
    world.build()
    assert calls == []


# --- refusals, in design 5.2's order -------------------------------------------------


@pytest.mark.parametrize(
    "run_id",
    [
        "md-001-review-flash-rp-01-r1-20261001T062611Z",  # a reviewer probe run
        MULTI + "-retry1",  # a rerun
        "live-20261001T062611Z-mdlite-12",  # a live run
        "md-001-solo-flash-r1-20261001T062611Z",  # an unknown system
        "md-001-multi-turbo-r1-20261001T062611Z",  # an unknown preset
        "xx-001-multi-flash-r1-20261001T062611Z",  # an unknown repository
        "../md-001-multi-flash-r1-20261001T062611Z",
        MULTI + "\n",
        HELDOUT_RUN,
        "md-h01",
    ],
)
def test_run_id_shapes_are_refused(world, run_id):
    world.run(MULTI)
    world.manifest([entry(MULTI), entry(run_id)])
    assert str(world.refused()) == "entry 2: not a dev bench run"
    assert world.under_root() == []


@pytest.mark.parametrize(
    "run_id",
    [
        "tc-003-multi-flash-r1-20261001T062611Z",  # its task is not in the dev split
        "tc-009-multi-flash-r1-20261001T062611Z",  # no such task
    ],
)
def test_a_task_outside_the_dev_split_is_refused(world, run_id):
    world.run(run_id)
    world.manifest([entry(run_id)])
    assert str(world.refused()) == "entry 1: not a dev bench run"


@pytest.mark.parametrize("missing", ["record.json", "events.jsonl", "both"])
def test_missing_run_files_are_refused(world, missing):
    world.run(MULTI)
    run_dir = world.runs / MULTI
    for name in ("record.json", "events.jsonl"):
        if missing in (name, "both"):
            (run_dir / name).unlink()
    world.manifest([entry(MULTI)])
    assert str(world.refused()) == "entry 1: run files missing"


@pytest.mark.parametrize(
    "record",
    [{"task_id": "sr-002"}, {"run_id": OTHER}, {"mode": "live"}, None],
)
def test_record_must_match_the_run_id_and_be_bench_mode(world, record):
    world.run(MULTI, record=record)
    if record is None:
        (world.runs / MULTI / "record.json").write_text("{ not a record")
    world.manifest([entry(MULTI)])
    assert str(world.refused()) == "entry 1: record does not match the run id"


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ({"rows": 0}, "no single results row"),
        ({"rows": 2}, "no single results row"),
        ({"split": "test"}, "results row is not a scored dev run"),
        ({"crashed": True}, "results row is not a scored dev run"),
        ({"infra_retries": 1}, "results row is not a scored dev run"),
    ],
)
def test_results_row_must_be_unique_scored_and_dev(world, setup, message):
    world.run(MULTI, **setup)
    world.manifest([entry(MULTI)])
    assert str(world.refused()) == f"entry 1: {message}"


def test_two_rows_in_two_results_files_and_an_unreadable_file_are_refused(world):
    log = world.run(MULTI)
    world.add_rows("again.json", log.row(log.record()))
    world.manifest([entry(MULTI)])
    assert str(world.refused()) == "entry 1: no single results row"
    (world.results / "again.json").write_text("[ not json")
    assert str(world.refused()) == "entry 1: no single results row"
    (world.results / "again.json").unlink()
    world.build()


@pytest.mark.parametrize(
    "entries",
    [
        [entry(MULTI, pair=SINGLE)],  # not in the manifest
        [entry(MULTI, pair=OTHER), entry(OTHER)],  # same system, other task
        [entry(MULTI, pair=OTHER_SINGLE), entry(OTHER_SINGLE)],  # other task
        [entry(MULTI, pair=MULTI)],  # itself
    ],
)
def test_pair_must_be_the_same_task_on_the_other_system(world, entries):
    for run_id in (MULTI, SINGLE, OTHER, OTHER_SINGLE):
        world.run(run_id)
    world.manifest(entries)
    expected = "entry 1: pair is not the same task on the other system"
    assert str(world.refused()) == expected
    world.manifest([entry(MULTI, pair=SINGLE), entry(SINGLE, pair=MULTI)])
    world.build()


@pytest.mark.parametrize(
    "caption",
    [
        "",
        "   ",
        "x" * 141,
        "one\ntwo",
        "one\rtwo",
        "one\u2028two",
        "a\u202eb",
        "a\x07b",
    ],
)
def test_caption_rules(world, caption):
    world.run(MULTI)
    world.manifest([entry(MULTI, caption=caption)])
    expected = "entry 1: caption must be one line of 1 to 140 characters"
    assert str(world.refused()) == expected
    world.manifest([entry(MULTI, caption="\u00e9" * 140)])
    world.build()


def test_refusal_messages_name_only_the_entry_position(world, capsys):
    for run_id in (MULTI, SINGLE, OTHER):
        world.run(run_id)
    world.run(OTHER_SINGLE, log=_issue_log(OTHER_SINGLE, f"see {ENTROPIC}"))
    cases = [
        ([entry(MULTI), entry(SINGLE), entry(OTHER, caption="")], 3),
        ([entry(MULTI), entry("tc-001-multi-flash-r1-20261001T062611Z")], 2),
        ([entry(MULTI), entry(OTHER_SINGLE)], 2),
        ([entry(SINGLE, pair=OTHER), entry(OTHER)], 1),
    ]
    forbidden = [
        MULTI,
        SINGLE,
        OTHER,
        OTHER_SINGLE,
        "md-001",
        "sr-002",
        ev.ISSUE["title"],
        CAPTION,
        ENTROPIC,
    ]
    for entries, position in cases:
        world.manifest(entries)
        error = world.refused()
        message = str(error)
        assert message in [f"entry {position}: {m}" for m in MESSAGES]
        assert not any(word in message or word in repr(error) for word in forbidden)
        assert world.cli() == 1
        printed = capsys.readouterr()
        assert printed.out == ""
        first, *hits = printed.err.splitlines()
        assert first == message
        assert hits == [str(hit) for hit in error.hits]
        assert all(ENTROPIC not in line for line in hits)


# --- output ------------------------------------------------------------------------


def test_build_is_all_or_nothing(world):
    world.out.mkdir(parents=True)
    (world.out / "old.json").write_text('{"old": true}\n')
    (world.out / "index.json").write_text(
        '{"schema": 1, "note": "old", "replays": []}\n'
    )
    before = world.published()
    world.run(MULTI)
    world.run(OTHER, log=_issue_log(OTHER, f"see {ENTROPIC}"))
    failing = [
        [entry(MULTI), entry(SINGLE)],  # a check fails on entry 2
        [entry(MULTI), entry(OTHER)],  # entry 2 fails to convert
        [entry(MULTI, caption="see /Users/alice/notes")],  # the finished files fail
    ]
    for entries in failing:
        world.manifest(entries)
        world.refused()
        assert world.published() == before
        assert [p.name for p in world.out.parent.iterdir()] == ["replays"]


def test_the_finished_files_are_checked_before_they_replace_the_output(world):
    world.run(MULTI)
    world.manifest([entry(MULTI, caption="see /Users/alice/notes")], note=EMAIL)
    error = world.refused()
    assert str(error) == replay.LEAK_FOUND
    assert set(error.hits) == {
        Hit("index.json", "$.note", "email"),
        Hit("index.json", "$.replays[0].caption", "host-path"),
        Hit("index.json", "$", "host-path"),
    }
    assert world.published() == {}


def test_removing_an_entry_unpublishes_its_file(world):
    world.run(MULTI)
    world.run(SINGLE)
    world.manifest([entry(MULTI, pair=SINGLE), entry(SINGLE, pair=MULTI)])
    world.build()
    assert set(world.published()) == {"index.json", f"{MULTI}.json", f"{SINGLE}.json"}
    (world.out / "notes.txt").write_text("not a replay")
    world.manifest([entry(MULTI)])
    world.build()
    assert set(world.published()) == {"index.json", f"{MULTI}.json", "notes.txt"}
    index = json.loads((world.out / "index.json").read_text())
    assert [e["run_id"] for e in index["replays"]] == [MULTI]


def test_index_follows_the_manifest_and_copies_the_replay_summary(world):
    for run_id in (MULTI, SINGLE, OTHER):
        world.run(run_id)
    world.manifest(
        [
            entry(SINGLE, caption="b", pair=MULTI),
            entry(OTHER, caption="c"),
            entry(MULTI, caption="a", pair=SINGLE),
        ]
    )
    lines = world.build()
    first = world.published()
    index = json.loads(first["index.json"])
    assert list(index) == ["schema", "note", "replays"]
    assert (index["schema"], index["note"]) == (1, NOTE)
    assert [e["run_id"] for e in index["replays"]] == [SINGLE, OTHER, MULTI]
    assert [(e["caption"], e["pair"]) for e in index["replays"]] == [
        ("b", MULTI),
        ("c", None),
        ("a", SINGLE),
    ]
    expected_lines = []
    for item in index["replays"]:
        assert list(item) == INDEX_KEYS
        assert item["file"] == f"{item['run_id']}.json"
        data = first[item["file"]]
        replay_doc = json.loads(data)
        run, outcome = replay_doc["run"], replay_doc["outcome"]
        assert item["task_id"] == run["task_id"] == task_of(item["run_id"])
        assert item["issue_title"] == run["issue"]["title"]
        for key in ("run_id", "repo", "category", "system", "preset", "recorded_at"):
            assert item[key] == run[key]
        for key in ("outcome", "failure_kind", "resolved", "cost_usd", "tool_calls"):
            assert item[key] == outcome[key]
        assert item["duration_s"] == outcome["duration_s"]
        kb = math.ceil(len(data) / 1024)
        expected_lines.append(
            f"{item['run_id']}: {len(replay_doc['steps'])} steps, {kb} KB"
        )
    assert lines == [EXACT_RULES_OFF, *expected_lines]
    assert data.decode("utf-8").endswith("}\n")
    world.build()
    assert world.published() == first  # byte-identical when built again


def test_entry_caps_override_the_manifest_caps(world):
    world.run(MULTI)
    world.run(SINGLE)
    world.manifest(
        [entry(MULTI, caps={"tool_calls": 100}), entry(SINGLE)],
        caps={"cost_usd": 2.5, "tool_calls": 75, "wall_clock_s": 1500},
    )
    world.build()
    caps = {
        run_id: json.loads(world.published()[f"{run_id}.json"])["caps"]
        for run_id in (MULTI, SINGLE)
    }
    assert caps[MULTI] == {"cost_usd": 2.5, "tool_calls": 100, "wall_clock_s": 1500}
    assert caps[SINGLE] == {"cost_usd": 2.5, "tool_calls": 75, "wall_clock_s": 1500}


def test_allow_reaches_index_json_and_clears_only_its_rule(world):
    world.run(MULTI, log=_issue_log(MULTI, f"build {ENTROPIC} by {EMAIL}"))
    body = "$.run.issue.body"
    file = f"{MULTI}.json"

    world.manifest(
        [entry(MULTI, allow=[{"path": "$.run.issue.title", "rule": "email"}])]
    )
    error = world.refused()
    assert str(error) == f"entry 1: {replay.LEAK_FOUND}"
    assert set(error.hits) == {
        Hit(file, body, "high-entropy"),
        Hit(file, body, "email"),
    }

    world.manifest([entry(MULTI, allow=[{"path": body, "rule": "high-entropy"}])])
    assert set(world.refused().hits) == {Hit(file, body, "email")}

    allow = [{"path": body, "rule": "high-entropy"}, {"path": body, "rule": "email"}]
    world.manifest([entry(MULTI, allow=allow)])
    world.build()
    index = json.loads(world.published()["index.json"])
    assert index["replays"][0]["allow"] == allow
    assert list(index["replays"][0]) == [*INDEX_KEYS, "allow"]
    assert check_paths([world.out]) == []
    del index["replays"][0]["allow"]
    (world.out / "index.json").write_text(json.dumps(index))
    assert {(h.path, h.rule) for h in check_paths([world.out])} == {
        (body, "high-entropy"),
        (body, "email"),
    }


def _bulky_log(run_id: str, results: int) -> Log:
    words = "".join(f"w{i} " for i in range(1000))[:3990]
    agent = "solo" if "-single-" in run_id else "coder"
    log = Log(run_id)
    log.start()
    for i in range(results):
        log.tool(agent, "read_file", {"path": f"f{i}.py"}, {"content": words})
    log.deliver()
    return log


def test_size_warning_and_refusal(world):
    world.run(MULTI, log=_bulky_log(MULTI, 80))
    world.manifest([entry(MULTI)])
    lines = world.build()
    size = len(world.published()[f"{MULTI}.json"])
    kb = math.ceil(size / 1024)
    assert 300 * 1024 < size <= 1024 * 1024
    assert lines[-2:] == [
        f"{MULTI}: {80 * 2 + 5} steps, {kb} KB",
        f"entry 1: replay is {kb} KB (over 300 KB)",
    ]
    world.run(SINGLE, log=_bulky_log(SINGLE, 260))
    world.manifest([entry(MULTI), entry(SINGLE)])
    before = world.published()
    assert str(world.refused()) == "entry 2: replay over 1 MB"
    assert world.published() == before


# --- the manifest ------------------------------------------------------------------


def test_load_manifest_reads_every_field(world):
    allow = [{"path": "$.run.issue.body", "rule": "email"}]
    world.manifest(
        [entry(MULTI, pair=SINGLE, caps={"tool_calls": 9}, allow=allow), entry(SINGLE)]
    )
    manifest = replay.load_manifest(world.manifest_path)
    assert manifest == replay.Manifest(
        note=NOTE,
        caps=CAPS,
        replays=(
            replay.ManifestEntry(
                run_id=MULTI,
                caption=CAPTION,
                pair=SINGLE,
                caps={"tool_calls": 9},
                allow=(("$.run.issue.body", "email"),),
            ),
            replay.ManifestEntry(
                run_id=SINGLE, caption=CAPTION, pair=None, caps=None, allow=()
            ),
        ),
    )


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ("note: [unclosed", "manifest cannot be read"),
        ("- just a list", "manifest cannot be read"),
        ({"note": NOTE, "caps": CAPS}, "manifest cannot be read"),
        (
            {"note": NOTE, "caps": {"cost_usd": 1}, "replays": []},
            "manifest cannot be read",
        ),
        (
            {"note": NOTE, "caps": {**CAPS, "cost_usd": float("nan")}, "replays": []},
            "manifest cannot be read",
        ),
        (
            {"note": NOTE, "caps": CAPS, "replays": [], "extra": 1},
            "manifest cannot be read",
        ),
        (
            {"note": NOTE, "caps": CAPS, "replays": [entry(MULTI), {"run_id": SINGLE}]},
            "entry 2: entry fields are not valid",
        ),
        (
            {"note": NOTE, "caps": CAPS, "replays": [entry(MULTI, allowed=[])]},
            "entry 1: entry fields are not valid",
        ),
        (
            {
                "note": NOTE,
                "caps": CAPS,
                "replays": [entry(MULTI, caps={"tool_calls": 0})],
            },
            "entry 1: entry fields are not valid",
        ),
        (
            {
                "note": NOTE,
                "caps": CAPS,
                "replays": [entry(MULTI, allow=[{"path": "$.a", "rule": "host-path"}])],
            },
            "entry 1: entry fields are not valid",
        ),
        (
            {
                "note": NOTE,
                "caps": CAPS,
                "replays": [entry(MULTI, allow=[{"path": "$.a", "rule": "nope"}])],
            },
            "entry 1: entry fields are not valid",
        ),
        (
            {"note": NOTE, "caps": CAPS, "replays": [entry(MULTI), entry(MULTI)]},
            "entry 2: run is listed twice",
        ),
    ],
)
def test_a_malformed_manifest_is_refused(world, doc, message):
    text = doc if isinstance(doc, str) else yaml.safe_dump(doc, sort_keys=False)
    world.manifest_path.write_text(text)
    with pytest.raises(replay.ReplayRefused) as caught:
        replay.load_manifest(world.manifest_path)
    assert str(caught.value) == message
    assert str(world.refused()) == message
    assert world.under_root() == []


# --- .env and the exact-value rules ---------------------------------------------------


def _fake_dotenv(monkeypatch, calls: list):
    def load_dotenv(*args, **kwargs) -> bool:
        calls.append(1)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
        monkeypatch.setenv("REPLAY_REDACT", NUMBER)
        return True

    monkeypatch.setattr(replay, "load_dotenv", load_dotenv)


def test_check_command_reads_the_exact_values_from_dotenv(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"a": f"deployed to {PROJECT}", "b": f"n {NUMBER}"}))
    assert replay.main(["check", str(path)]) == 0  # .env not loaded: rules off
    assert capsys.readouterr().out.splitlines() == [
        EXACT_RULES_OFF,
        "1 files checked, 0 problems",
    ]
    calls: list[int] = []
    _fake_dotenv(monkeypatch, calls)
    assert replay.main(["check", str(path)]) == 1
    printed = capsys.readouterr()
    assert calls == [1]
    assert printed.out.splitlines() == [
        "r.json: $.a: project",
        "r.json: $.b: project",
        "r.json: $: project",  # the pass over the raw file text
        "1 files checked, 3 problems",
    ]
    assert PROJECT not in printed.out + printed.err
    assert NUMBER not in printed.out + printed.err


def test_build_reads_the_exact_values_from_dotenv(world, monkeypatch, capsys):
    log = Log(MULTI)
    log.start()
    log.tool("coder", "bash", {"command": "gcloud config list"}, {"stdout": PROJECT})
    log.deliver()
    world.run(MULTI, log=log)
    world.manifest([entry(MULTI)])
    assert world.cli() == 0
    assert capsys.readouterr().out.splitlines()[0] == EXACT_RULES_OFF
    calls: list[int] = []
    _fake_dotenv(monkeypatch, calls)
    assert world.cli() == 0
    printed = capsys.readouterr().out
    assert calls == [1]
    assert EXACT_RULES_OFF not in printed and PROJECT not in printed
    data = world.published()[f"{MULTI}.json"].decode("utf-8")
    assert PROJECT not in data and '"stdout": "<project>"' in data


def test_graphs_command_does_not_load_dotenv(monkeypatch, tmp_path):
    calls: list[int] = []
    _fake_dotenv(monkeypatch, calls)
    assert replay.main(["graphs", "--out", str(tmp_path / "g.json")]) == 0
    assert calls == []


def test_build_warns_when_the_project_number_is_not_checked(world, monkeypatch):
    world.run(MULTI)
    world.manifest([entry(MULTI)])
    assert world.build()[0] == EXACT_RULES_OFF
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    lines = world.build()
    assert lines[0] == "REPLAY_REDACT unset: the project number is not checked"
    assert EXACT_RULES_OFF not in lines
    assert not any(PROJECT in line for line in lines)
    monkeypatch.setenv("REPLAY_REDACT", NUMBER)
    lines = world.build()
    assert lines == [f"{MULTI}: {lines[0].split(': ', 1)[1]}"]
    assert not any(NUMBER in line for line in lines)


# --- interrupted publishing ---------------------------------------------------------


def _old_output(world: World) -> dict[str, bytes]:
    world.out.mkdir(parents=True)
    (world.out / "old.json").write_text('{"old": true}\n')
    (world.out / "index.json").write_text('{"schema": 1, "replays": []}\n')
    (world.out / "notes.txt").write_text("not a replay\n")
    return world.published()


def test_a_write_that_fails_midway_leaves_the_old_files(world, monkeypatch):
    """A failure at any write leaves exactly the old files, never a mix."""
    world.run(MULTI)
    world.run(SINGLE)
    world.manifest([entry(MULTI, pair=SINGLE), entry(SINGLE, pair=MULTI)])
    before = _old_output(world)
    mode = world.out.stat().st_mode & 0o777
    world.build()
    after = world.published()
    assert set(after) == {"index.json", f"{MULTI}.json", f"{SINGLE}.json", "notes.txt"}
    assert world.out.stat().st_mode & 0o777 == mode

    real = Path.write_bytes
    failed = 0
    for fail_at in range(1, 10):
        shutil.rmtree(world.out)
        assert _old_output(world) == before
        writes: list[str] = []

        def write_bytes(path: Path, data: bytes, fail_at=fail_at, writes=writes):
            writes.append(path.name)
            if len(writes) == fail_at:
                raise OSError("disk full")
            return real(path, data)

        monkeypatch.setattr(Path, "write_bytes", write_bytes)
        try:
            world.build()
        except OSError:
            failed += 1
            assert world.published() == before
        else:
            assert world.published() == after
        monkeypatch.setattr(Path, "write_bytes", real)
        assert [p.name for p in world.out.parent.iterdir()] == ["replays"]
    assert failed >= 3  # every file was written once at least


@pytest.mark.parametrize(
    ("when", "error"),
    [
        ("second rename", OSError),  # moving the new set into place fails
        ("second rename", KeyboardInterrupt),  # Ctrl-C during it
        ("after first rename", KeyboardInterrupt),  # Ctrl-C between the renames
    ],
)
def test_a_swap_that_fails_leaves_the_old_files(world, monkeypatch, when, error):
    before = _old_output(world)
    world.run(MULTI)
    world.manifest([entry(MULTI)])
    real = os.replace

    def replace(src, dst, *args, **kwargs):
        if when == "second rename" and Path(src).name.startswith(".replays-new-"):
            raise error("interrupted")
        done = real(src, dst, *args, **kwargs)
        if when == "after first rename" and Path(dst).name.startswith(".replays-old-"):
            raise error("interrupted")
        return done

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(error, match="interrupted"):
        world.build()
    monkeypatch.setattr(os, "replace", real)
    assert world.published() == before
    assert [p.name for p in world.out.parent.iterdir()] == ["replays"]


@pytest.mark.parametrize("refused", [True, False])
def test_unchecked_files_never_land_next_to_the_output(world, monkeypatch, refused):
    """The finished set is checked in the system temp directory; only checked
    bytes are ever written next to --out."""
    _old_output(world)
    world.run(MULTI)
    caption = "see /Users/alice/notes" if refused else CAPTION
    world.manifest([entry(MULTI, caption=caption)])
    real = replay.check_paths
    seen: list[tuple[list[str], bool]] = []

    def check_paths(paths, **kwargs):
        (path,) = paths
        siblings = sorted(p.name for p in world.out.parent.iterdir())
        seen.append(
            (siblings, Path(path).resolve().is_relative_to(world.root.resolve()))
        )
        return real(paths, **kwargs)

    monkeypatch.setattr(replay, "check_paths", check_paths)
    if refused:
        assert str(world.refused()) == replay.LEAK_FOUND
    else:
        world.build()
    assert seen == [(["replays"], False)]  # nothing staged yet, checked outside
    assert [p.name for p in world.out.parent.iterdir()] == ["replays"]
    assert ("notes.txt" in world.published()) and (
        (f"{MULTI}.json" in world.published()) is not refused
    )


# --- analytics -----------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]


def test_analytics_switched_on_stops_the_command_before_any_app_import():
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("BQ_ANALYTICS_ENABLED", "REPLAY_REDACT")
    }
    # An empty project keeps app.agent from any BigQuery call even without the guard.
    env.update(BQ_ANALYTICS_ENABLED="1", GOOGLE_CLOUD_PROJECT="")
    code = (
        "import runpy, sys\n"
        "sys.argv = ['bench.replay', 'graphs', '--check']\n"
        "try:\n"
        "    runpy.run_module('bench.replay', run_name='__main__')\n"
        "finally:\n"
        "    loaded = any(m == 'app' or m.startswith('app.') for m in sys.modules)\n"
        "    print('app imported:', loaded, file=sys.stderr)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 2
    assert done.stderr.splitlines() == [
        "error: BQ_ANALYTICS_ENABLED is set; unset it before running bench.replay",
        "app imported: False",
    ]
    assert done.stdout == ""
