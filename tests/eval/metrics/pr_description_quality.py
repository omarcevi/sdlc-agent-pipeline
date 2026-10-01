"""LLM judge for the pull request body (1 to 5, fixed rubric, temperature 0).

Inputs: the final response (the pull request body the pipeline delivers) and the
issue from the case's `reference`. One client per grading thread.

Self-contained: agents-cli inlines this file and runs it with exec, so it imports
only the standard library and google.genai and does not use __file__.
"""

import json
import threading

from google import genai
from google.genai import types

_local = threading.local()

_RUBRIC = """\
Grade the pull request description on a 1-5 scale (1 poor, 5 excellent):
1. It matches the issue below: it says what was fixed or built and why.
2. It matches its own diff stat: the files and sizes it lists are consistent.
3. It reports the tests honestly: what ran and the result, with nothing overstated
   or invented.
4. It claims nothing unsupported: no features, files, fixes or guarantees that
   neither the issue nor the body's own evidence supports.
Return JSON with `score` (integer 1 to 5) and `explanation` (short)."""

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


def evaluate(instance):
    body = _text(instance.get("response")).strip()
    if not body:
        return {
            "score": 0,
            "explanation": "no pull request description in the response",
        }
    reference = _as_dict(_text(instance.get("reference")))
    prompt = (
        f"{_RUBRIC}\n\n"
        f"Issue title: {reference.get('issue_title', '')}\n"
        f"Issue body:\n{reference.get('issue_body', '')}\n\n"
        f"Pull request description:\n{body}\n"
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
