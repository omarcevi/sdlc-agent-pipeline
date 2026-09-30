from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Priority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass
class Task:
    id: int
    title: str
    priority: Priority = Priority.MEDIUM
    due: date | None = None
    tags: list[str] = field(default_factory=list)
    done: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "priority": self.priority.name.lower(),
            "due": self.due.isoformat() if self.due else None,
            "tags": list(self.tags),
            "done": self.done,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        due = data.get("due")
        return cls(
            id=data["id"],
            title=data["title"],
            priority=Priority[data.get("priority", "medium").upper()],
            due=date.fromisoformat(due) if due else None,
            tags=list(data.get("tags", [])),
            done=bool(data.get("done", False)),
        )
