"""Recall of the reference solution's files in the planner's `files_to_inspect`.

The planner's structured answer is read from the trace: the first
`set_model_response` function call whose args hold `actionable`. The reference
solution files come from the case's `reference` (a JSON object with
`solution_files`).

Self-contained: agents-cli inlines this file and runs it with exec, so it imports
only the standard library and does not use __file__.
"""

import json

_PREFIXES = ("/workspace/repo/", "./")


def _norm(path):
    path = str(path).strip()
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if path.startswith(prefix):
                path = path[len(prefix) :]
                changed = True
    return path


def _as_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _text(value):
    """Text of a Content, of a ResponseCandidate wrapping one, or a plain string."""
    if isinstance(value, str):
        return value
    value = _as_dict(value)
    value = value.get("response", value)
    parts = value.get("parts") if isinstance(value, dict) else None
    return "".join(str(p.get("text") or "") for p in parts or [])


def _plan(instance):
    data = _as_dict(instance.get("agent_data"))
    for turn in data.get("turns") or []:
        for event in turn.get("events") or []:
            for part in (event.get("content") or {}).get("parts") or []:
                call = part.get("function_call") or part.get("functionCall") or {}
                args = call.get("args")
                if (
                    call.get("name") == "set_model_response"
                    and isinstance(args, dict)
                    and "actionable" in args
                ):
                    return args
    return None


def evaluate(instance):
    plan = _plan(instance)
    if plan is None:
        return {"score": 0, "explanation": "no plan in trace"}
    reference = _as_dict(_text(instance.get("reference")))
    expected = {_norm(f) for f in reference.get("solution_files") or []}
    if not expected:
        return {"score": 0, "explanation": "no solution files in the reference"}
    planned = {_norm(f) for f in plan.get("files_to_inspect") or []}
    found = sorted(expected & planned)
    missed = sorted(expected - planned)
    return {
        "score": len(found) / len(expected),
        "explanation": f"found {found}; missed {missed}",
    }
