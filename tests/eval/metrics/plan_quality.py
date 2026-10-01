"""LLM judge for the planner's plan (1 to 5, fixed rubric, temperature 0).

Inputs: the issue (from the case's `reference`), the plan from the trace (the first
`set_model_response` call whose args hold `actionable`) and the reference solution
files. One client per grading thread.

Self-contained: agents-cli inlines this file and runs it with exec, so it imports
only the standard library and google.genai and does not use __file__.
"""

import json
import threading

from google import genai
from google.genai import types

_local = threading.local()

_RUBRIC = """\
Grade the plan on a 1-5 scale (1 poor, 5 excellent) against these criteria:
1. It finds the cause of the bug, or the modules where the change belongs.
2. Its steps are concrete and ordered.
3. Its test strategy would catch the bug and its neighbouring cases.
4. It does not edit or weaken existing tests.
5. It respects the repository's written rules named in the issue or the repo docs.
The reference solution files are for your information: a plan may differ in method
and still be good. Judge only the plan shown. Return JSON with `score` (integer 1
to 5) and `explanation` (short)."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 5},
        "explanation": {"type": "string"},
    },
    "required": ["score", "explanation"],
}


def _client():
    """One client per grading thread.

    The eval SDK grades cases on its own thread pool. Each thread gets its own
    client: google-auth freezes the SSL context after the first connection when a
    client certificate is present, and a new client per case would redo ADC and the
    TLS handshake every time.
    """
    client = getattr(_local, "client", None)
    if client is None:
        client = _local.client = genai.Client()
    return client


def _as_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _text(value):
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
    prompt = (
        f"{_RUBRIC}\n\n"
        f"Issue title: {reference.get('issue_title', '')}\n"
        f"Issue body:\n{reference.get('issue_body', '')}\n\n"
        f"Reference solution files: {reference.get('solution_files', [])}\n\n"
        f"Plan:\n{json.dumps(plan, indent=2)}\n"
    )
    response = _client().models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=_SCHEMA,
        ),
    )
    try:
        verdict = json.loads(response.text or "")
        score = int(verdict["score"])
        explanation = str(verdict.get("explanation", ""))
    except (ValueError, KeyError, TypeError):
        return {"score": 0, "explanation": response.text or "judge gave no verdict"}
    return {"score": max(1, min(5, score)), "explanation": explanation}
