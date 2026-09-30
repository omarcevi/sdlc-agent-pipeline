from google.adk.events import Event
from google.genai import types

from bench.progress import format_event


def event(author: str, *parts: types.Part, **kwargs) -> Event:
    return Event(
        author=author, content=types.Content(role="model", parts=list(parts)), **kwargs
    )


def fcall(name: str, **args) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def fresp(name: str, **response) -> types.Part:
    return types.Part(
        function_response=types.FunctionResponse(name=name, response=response)
    )


def test_workflow_status_message_is_a_pipeline_line():
    ev = event("issue_to_pr", types.Part(text="Sandbox ready\nsecond line"))
    assert format_event("tc-001", ev) == ["tc-001  pipeline · Sandbox ready"]


def test_pipeline_status_is_truncated_to_100_characters():
    (line,) = format_event("t", event("issue_to_pr", types.Part(text="x" * 300)))
    assert line == "t  pipeline · " + "x" * 100


def test_bash_call_shows_the_command_truncated_at_80_characters():
    (line,) = format_event("t", event("coder", fcall("bash", command="a" * 200)))
    assert line == "t     coder → bash " + "a" * 80


def test_bash_command_is_collapsed_to_one_line():
    (line,) = format_event("t", event("coder", fcall("bash", command="ls\n  -la")))
    assert line.endswith("→ bash ls -la")


def test_read_file_call_shows_the_path():
    (line,) = format_event("t", event("planner", fcall("read_file", path="a/b.py")))
    assert line == "t   planner → read_file a/b.py"


def test_grep_call_shows_pattern_and_path():
    ev = event("coder", fcall("grep", pattern="parse_due", path="taskcli"))
    assert format_event("t", ev) == ["t     coder → grep 'parse_due' in taskcli"]


def test_set_model_response_has_no_detail():
    ev = event("reviewer", fcall("set_model_response", verdict="approve"))
    assert format_event("t", ev) == ["t  reviewer → set_model_response"]


def test_call_with_missing_arguments_does_not_raise():
    ev = event("coder", fcall("read_file"), fcall("grep"), fcall("bash"))
    assert len(format_event("t", ev)) == 3


def test_error_response_is_a_cross_line():
    ev = event("coder", fresp("write_file", error="path is protected"))
    assert format_event("t", ev) == ["t     coder ✗ write_file: path is protected"]


def test_lines_that_should_not_appear():
    assert format_event("t", event("coder", fresp("bash", stdout="ok"))) == []
    assert format_event("t", event("coder", types.Part(text="thinking"))) == []
    assert format_event("t", Event(author="coder")) == []
    assert format_event("t", Event(author="coder", content=types.Content())) == []


def test_partial_events_produce_no_lines():
    assert (
        format_event("t", event("coder", fcall("bash", command="ls"), partial=True))
        == []
    )


def test_several_parts_give_several_lines_in_order():
    ev = event("coder", fcall("read_file", path="a.py"), fcall("list_dir", path="."))
    assert format_event("t", ev) == [
        "t     coder → read_file a.py",
        "t     coder → list_dir .",
    ]


def test_whitespace_only_pipeline_text_prints_nothing():
    assert format_event("t", event("issue_to_pr", types.Part(text="  \n "))) == []


def test_function_call_without_a_name_prints_a_question_mark():
    part = types.Part(function_call=types.FunctionCall(args={}))
    (line,) = format_event("t", event("coder", part))
    assert "?" in line and "None" not in line
