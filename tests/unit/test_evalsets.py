import json
from pathlib import Path

import pytest

from app.schemas import RunRequest
from bench import evalsets
from bench.evalsets import PIPELINE_CASES, build_pipeline_dataset, render

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_KEYS = {"task_id", "category", "issue_title", "issue_body", "solution_files"}


def test_committed_dataset_matches_the_builder():
    committed = (ROOT / "tests/eval/datasets/pipeline-dev.json").read_text()
    assert committed == render(build_pipeline_dataset())
    assert evalsets.main(["--check"]) == 0


def test_check_fails_when_the_file_differs(tmp_path, monkeypatch):
    stale = tmp_path / "pipeline-dev.json"
    stale.write_text("{}\n")
    monkeypatch.setattr(evalsets, "DATASET_PATH", stale)
    assert evalsets.main(["--check"]) == 1
    assert evalsets.main(["--write"]) == 0
    assert evalsets.main(["--check"]) == 0


def test_the_case_list():
    assert PIPELINE_CASES == ("md-001", "md-002", "sr-002", "sr-003", "sr-005")
    cases = build_pipeline_dataset()["eval_cases"]
    assert [c["eval_case_id"] for c in cases] == list(PIPELINE_CASES)


def write_task(root: Path, task_id: str, split: str) -> None:
    directory = root / task_id
    (directory / "solution" / "tests").mkdir(parents=True)
    (directory / "hidden_tests").mkdir()
    (directory / "task.yaml").write_text(
        f"repo: r\ntitle: T\nbody: B\ncategory: bug\ndifficulty: easy\nsplit: {split}\n"
    )
    (directory / "solution" / "mod.py").write_text("SECRET_SOLUTION = 1\n")
    (directory / "solution" / "tests" / "test_mod.py").write_text("x = 1\n")
    (directory / "hidden_tests" / "test_hidden_name.py").write_text("HIDDEN = 1\n")


def test_evalsets_refuses_heldout(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks"
    write_task(tasks, "zz-h01", "heldout")  # held-out by its id
    write_task(tasks, "zz-001", "heldout")  # held-out by its split
    write_task(tasks, "zz-002", "dev")
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tasks))

    opened: list[str] = []
    listed: list[str] = []
    real_read, real_rglob = Path.read_text, Path.rglob

    def spy_read(self, *args, **kwargs):
        opened.append(str(self))
        return real_read(self, *args, **kwargs)

    def spy_rglob(self, pattern):
        listed.append(str(self))
        return real_rglob(self, pattern)

    monkeypatch.setattr(Path, "read_text", spy_read)
    monkeypatch.setattr(Path, "rglob", spy_rglob)

    for task_id in ("zz-h01", "zz-001"):
        with pytest.raises(ValueError, match="task is not in the dev split") as caught:
            build_pipeline_dataset((task_id,))
        assert "SECRET" not in str(caught.value)
    touched = opened + listed
    assert not any("zz-h01" in p for p in touched)  # refused by name: untouched
    assert not any("zz-001" in p and "task.yaml" not in p for p in touched)

    # A dev task reads task.yaml and lists solution/ names, never hidden_tests/.
    opened.clear()
    listed.clear()
    case = build_pipeline_dataset(("zz-002",))["eval_cases"][0]
    assert not any("hidden_tests" in p for p in opened + listed)
    assert not any(p.endswith(".py") for p in opened)
    reference = json.loads(case["reference"]["response"]["parts"][0]["text"])
    assert reference["solution_files"] == ["mod.py"]
    assert "HIDDEN" not in json.dumps(case) and "hidden_name" not in json.dumps(case)
    assert "SECRET" not in json.dumps(case)


def test_reference_has_only_the_allowed_keys():
    for case in build_pipeline_dataset()["eval_cases"]:
        assert set(case) == {"eval_case_id", "prompt", "reference"}
        reference = json.loads(case["reference"]["response"]["parts"][0]["text"])
        assert set(reference) == ALLOWED_KEYS
        assert reference["task_id"] == case["eval_case_id"]
        assert reference["solution_files"]
        for name in reference["solution_files"]:
            assert name.endswith(".py")
            assert not name.startswith("tests/")
            assert "hidden" not in name


def test_prompts_are_valid_run_requests():
    for case in build_pipeline_dataset()["eval_cases"]:
        prompt = case["prompt"]
        assert prompt["role"] == "user"
        (part,) = prompt["parts"]
        request = RunRequest.model_validate_json(part["text"])
        assert request.task_id == case["eval_case_id"]
        assert request.run_id == f"eval-{case['eval_case_id']}"
        assert request.mode == "bench"
