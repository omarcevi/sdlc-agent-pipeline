import json
import os
from pathlib import Path

from taskcli.models import Task


def default_path() -> Path:
    return Path(os.environ.get("TASKCLI_FILE", str(Path.home() / ".taskcli.json")))


class TaskStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_path()

    def load(self) -> list[Task]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text())
        return [Task.from_dict(item) for item in data.get("tasks", [])]

    def save(self, tasks: list[Task]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"tasks": [t.to_dict() for t in tasks]}, indent=2))
        tmp.replace(self.path)

    def next_id(self, tasks: list[Task]) -> int:
        return max((t.id for t in tasks), default=0) + 1
