"""One-line-per-step progress display for a running bench task."""

from google.adk.events import Event

PIPELINE_AUTHOR = "issue_to_pr"
_PATH_TOOLS = {"read_file", "write_file", "edit_file", "list_dir"}


def _clip(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _detail(name: str, args: dict) -> str:
    if name == "bash":
        return _clip(args.get("command", ""), 80)
    if name in _PATH_TOOLS:
        return str(args.get("path", ""))
    if name == "grep":
        return f"'{args.get('pattern', '')}' in {args.get('path', '')}"
    return ""


def format_event(task_id: str, event: Event) -> list[str]:
    """Display lines for one event: pipeline status, tool calls and tool errors."""
    if event.partial or not event.content or not event.content.parts:
        return []
    lines = []
    for part in event.content.parts:
        if part.text and event.author == PIPELINE_AUTHOR:
            first_line = part.text.strip().split("\n", 1)[0]
            lines.append(f"{task_id}  pipeline · {first_line[:100]}")
        elif part.function_call:
            call = part.function_call
            detail = _detail(call.name or "", call.args or {})
            lines.append(
                f"{task_id}  {event.author:>8} → {call.name} {detail}".rstrip()
            )
        elif part.function_response:
            response = part.function_response.response
            if isinstance(response, dict) and "error" in response:
                error = _clip(response["error"], 100)
                lines.append(
                    f"{task_id}  {event.author:>8} ✗ {part.function_response.name}: "
                    f"{error}"
                )
    return lines
