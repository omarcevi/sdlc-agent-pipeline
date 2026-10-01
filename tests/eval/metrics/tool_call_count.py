"""Number of function calls in the whole trace (an effort and cost proxy).

Self-contained: agents-cli inlines this file and runs it with exec, so it imports
only the standard library and does not use __file__.
"""

import json


def _trace(instance):
    data = instance.get("agent_data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def evaluate(instance):
    count = 0
    for turn in _trace(instance).get("turns") or []:
        for event in turn.get("events") or []:
            for part in (event.get("content") or {}).get("parts") or []:
                if part.get("function_call") or part.get("functionCall"):
                    count += 1
    return {"score": count, "explanation": f"{count} function calls in the trace"}
