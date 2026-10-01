"""Builds the `agents-cli eval` dataset from dev tasks.

uv run python -m bench.evalsets --write   # write tests/eval/datasets/pipeline-dev.json
uv run python -m bench.evalsets --check   # exit 1 if the committed file differs

Each case runs the real pipeline on one dev task (the prompt is a `RunRequest`). The
reference holds the issue text and the names of the solution's source files, which
the metrics in tests/eval/metrics/ use. Dev tasks only: a held-out id is refused by
its name, and a task whose split is not `dev` is refused after reading its
`task.yaml` and nothing else. From a dev task the builder reads `task.yaml` and the
names (never the contents) of the files under `solution/`. No hidden test file name,
test name or content enters the dataset.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from app.task_store import load_task, task_dir, test_files

PIPELINE_CASES = ("md-001", "md-002", "sr-002", "sr-003", "sr-005")
DATASET_PATH = Path("tests/eval/datasets/pipeline-dev.json")

_HELDOUT_ID = re.compile(r"-h\d\d$")
_NOT_DEV = "task is not in the dev split"


def solution_files(task_id: str) -> list[str]:
    """Repo-relative names of the `.py` files under `solution/` that are not test
    files. Names only: no file is opened."""
    root = task_dir(task_id) / "solution"
    if not root.is_dir():
        return []
    sources = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    return sorted(sources - set(test_files(root)))


def build_case(task_id: str) -> dict:
    if _HELDOUT_ID.search(task_id):
        raise ValueError(_NOT_DEV)  # refused by name: nothing of the task is read
    task = load_task(task_id)
    if task.split != "dev":
        raise ValueError(_NOT_DEV)
    request = json.dumps({"task_id": task_id, "run_id": f"eval-{task_id}"})
    reference = {
        "task_id": task_id,
        "category": task.category,
        "issue_title": task.title,
        "issue_body": task.body,
        "solution_files": solution_files(task_id),
    }
    return {
        "eval_case_id": task_id,
        "prompt": {"role": "user", "parts": [{"text": request}]},
        "reference": {
            "response": {
                "role": "model",
                "parts": [{"text": json.dumps(reference, ensure_ascii=False)}],
            }
        },
    }


def build_pipeline_dataset(task_ids: tuple[str, ...] = PIPELINE_CASES) -> dict:
    return {"eval_cases": [build_case(task_id) for task_id in task_ids]}


def render(dataset: dict) -> str:
    return json.dumps(dataset, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.evalsets")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the dataset file")
    mode.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the file differs from the builder",
    )
    args = parser.parse_args(argv)
    text = render(build_pipeline_dataset())
    if args.write:
        DATASET_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {DATASET_PATH}")
        return 0
    current = DATASET_PATH.read_text(encoding="utf-8") if DATASET_PATH.is_file() else ""
    if current != text:
        print(f"{DATASET_PATH} is out of date: run --write", file=sys.stderr)
        return 1
    print(f"{DATASET_PATH} is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
