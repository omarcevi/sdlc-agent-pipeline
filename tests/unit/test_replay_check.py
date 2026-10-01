"""Tests for the replay leak check (bench/replay_check.py).

Secret-shaped samples are assembled at run time so this file holds no literal
token for a secret scanner to flag.
"""

import ast
import json
import sys
from pathlib import Path

import pytest

from bench import replay_check as rc
from bench.replay_check import (
    EXACT_RULES_OFF,
    RULES,
    UNCLEARABLE,
    Hit,
    check_paths,
    exact_values,
    main,
    scan_text,
    scan_value,
)

PROJECT = "demo-proj-4242"
ALNUM = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"  # 36 chars, mixed, high entropy
SAMPLES = {
    "host-path": "see /Users/alice/code/x.py",
    "project": f"deployed to {PROJECT} today",
    "gcp-resource": "projects/" + "other-123/locations/x",
    "github-token": "gh" + "p_" + "A1" * 12,
    "google-key": "AI" + "za" + "A1_-" * 8 + "xyz",
    "jwt": "ey" + "J" + "hbGciOi" + ".ey" + "J" + "zdWIi",
    "private-key": "-----" + "BEGIN RSA PRIVATE KEY" + "-----",
    "other-token": "AK" + "IA" + "ABCDEFGH12345678",
    "high-entropy": f"token {ALNUM}",
    "email": "write to alice" + "@" + "corp.io please",
    "sandbox": "container itp-0123456789ab",
}


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("REPLAY_REDACT", raising=False)


def test_samples_cover_every_rule():
    assert set(SAMPLES) == set(RULES)


@pytest.mark.parametrize("rule", RULES)
def test_each_rule_catches_its_sample(rule):
    exact = (PROJECT,) if rule == "project" else ()
    assert rule in scan_text(SAMPLES[rule], exact)


def test_project_is_whole_word_and_underscore_counts_as_boundary():
    assert "project" in scan_text(f"{PROJECT.upper()}_cloudbuild", (PROJECT,))
    assert "project" not in scan_text(f"x{PROJECT}y", (PROJECT,))


def test_gcp_resource_allows_the_placeholder():
    assert "gcp-resource" not in scan_text("projects/<project>/locations/x")
    assert "gcp-resource" in scan_text("sa@x.iam" + ".gserviceaccount.com")


def test_sandbox_matches_64_hex_but_not_a_git_sha():
    assert "sandbox" in scan_text("0123456789abcdef" * 4)
    assert scan_text("0123456789abcdef0123456789abcdef01234567") == []


ORDINARY = [
    "+@pytest.mark.parametrize('x', [1, 2])",
    "git config user.email pipeline@localhost",
    "base 0123456789abcdef0123456789abcdef01234567",
    "index 81ea946..68e5b97 100644",
    "/workspace/src/app.py and /tmp/pytest-of-user/x",
    "<sandbox> <container> <project> <redacted> <repo> <host-path>",
    "mail me at someone@example.com or a@example.org",
    "task-abcdefghijklmnopqrstuvwxyz is a name",
]


def _replay_fixture() -> dict:
    return {
        "schema": 1,
        "run": {"run_id": "md-001-multi-flash-r1-x", "task_id": "md-001"},
        "steps": [
            {
                "kind": "tool",
                "name": "run_shell",
                "args": {"command": ORDINARY[0]},
                "result": {"stdout": "\n".join(ORDINARY[1:])},
            }
        ],
        "diff": "diff --git a/x b/x\nindex 81ea946..68e5b97 100644\n",
    }


def test_ordinary_content_is_clean():
    assert scan_text("\n".join(ORDINARY), (PROJECT,)) == []
    assert scan_value(_replay_fixture(), file="f.json", exact=(PROJECT,)) == []


def test_scan_text_reports_rules_in_order_once():
    text = SAMPLES["sandbox"] + " " + SAMPLES["host-path"] + SAMPLES["host-path"]
    assert scan_text(text) == ["host-path", "sandbox"]


def test_object_keys_are_scanned():
    hits = scan_value({"run": {SAMPLES["host-path"]: 1}}, file="f.json")
    assert hits == [Hit("f.json", "$.run{key}", "host-path")]
    odd = scan_value({"run": {"odd key": SAMPLES["email"]}}, file="f.json")
    assert odd == [Hit("f.json", '$.run["odd key"]', "email")]


def test_paths_name_lists_and_nesting():
    value = {"steps": [{}, {}, {}, {"result": {"stdout": SAMPLES["sandbox"]}}]}
    (hit,) = scan_value(value, file="f.json")
    assert str(hit) == "f.json: $.steps[3].result.stdout: sandbox"


def test_reports_never_contain_the_matched_text(tmp_path, capsys, monkeypatch):
    secret = "ghp_" + "Zq9" * 9  # a github-token shape
    secret_key = "/Users/" + "mallory"
    doc = {"a": secret, "b": {secret_key: "x"}, "c": {secret_key: {"d": secret}}}
    hits = scan_value(doc, file="f.json")
    assert hits
    for h in hits:
        assert secret not in str(h) and "mallory" not in str(h)
        assert secret not in repr(h) and "mallory" not in repr(h)
    (tmp_path / "f.json").write_text(json.dumps(doc))
    (tmp_path / "bad.json").write_text("{ not json " + secret)
    assert main([str(tmp_path / "f.json")]) == 1
    out = capsys.readouterr()
    assert secret not in out.out + out.err and "mallory" not in out.out + out.err
    assert main([str(tmp_path / "bad.json")]) == 2
    out = capsys.readouterr()
    assert secret not in out.out + out.err
    with pytest.raises(rc.ReplayFileError) as err:
        check_paths([tmp_path / "bad.json"])
    assert secret not in str(err.value) and secret not in repr(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_allow_clears_one_path_and_one_rule_only():
    value = {"a": SAMPLES["email"], "b": SAMPLES["email"], "c": SAMPLES["sandbox"]}
    allow = {("$.a", "email")}
    hits = scan_value(value, file="f", allow=allow)
    assert {(h.path, h.rule) for h in hits} == {("$.b", "email"), ("$.c", "sandbox")}
    wrong_rule = scan_value(
        {"c": SAMPLES["sandbox"]}, file="f", allow={("$.c", "email")}
    )
    assert [h.rule for h in wrong_rule] == ["sandbox"]


@pytest.mark.parametrize("rule", sorted(UNCLEARABLE))
def test_unclearable_rules_ignore_allow(rule):
    exact = (PROJECT,)
    hits = scan_value(
        {"a": SAMPLES[rule]}, file="f", exact=exact, allow={("$.a", rule)}
    )
    assert [h.rule for h in hits] == [rule]


def test_exact_values_come_from_both_variables():
    env = {"GOOGLE_CLOUD_PROJECT": " p-1 ", "REPLAY_REDACT": "123456, ,other-id,"}
    assert exact_values(env) == ("p-1", "123456", "other-id")
    assert exact_values({"REPLAY_REDACT": "x"}) == ("x",)
    assert exact_values({}) == ()
    assert rc.project_value(env) == "p-1"
    assert rc.redact_values(env) == ("123456", "other-id")
    assert rc.exact_pattern("abc").search("ABC_x")
    assert not rc.exact_pattern("abc").search("abcd")


def test_cli_says_when_exact_value_rules_are_off(tmp_path, capsys, monkeypatch):
    (tmp_path / "a.json").write_text("{}")
    assert main([str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == EXACT_RULES_OFF
    assert lines[-1] == "1 files checked, 0 problems"
    monkeypatch.setenv("REPLAY_REDACT", PROJECT)
    assert main([str(tmp_path)]) == 0
    assert EXACT_RULES_OFF not in capsys.readouterr().out


def test_cli_exit_codes(tmp_path, capsys, monkeypatch):
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(_replay_fixture()))
    assert main([str(clean)]) == 0
    dirty = tmp_path / "dirty.json"
    dirty.write_text(json.dumps({"x": SAMPLES["sandbox"]}))
    assert main([str(dirty)]) == 1
    assert "dirty.json: $.x: sandbox" in capsys.readouterr().out
    broken = tmp_path / "broken.json"
    broken.write_text("nope")
    assert main([str(broken)]) == 2
    assert main([str(tmp_path / "missing.json")]) == 2
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    assert main([str(clean)]) == 0
    clean.write_text(json.dumps({"x": f"{PROJECT}_cloudbuild"}))
    assert main([str(clean)]) == 1


def test_raw_text_pass_catches_unclearable_rules_under_dollar(tmp_path):
    path = tmp_path / "r.json"
    # ensure_ascii escapes the backslashes: the decoded pass and the raw pass agree
    path.write_text(json.dumps({"p": "C:\\Users\\bob"}))
    hits = check_paths([path])
    assert Hit("r.json", "$.p", "host-path") in hits
    assert Hit("r.json", "$", "host-path") in hits


def _write_index(tmp_path, allow):
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "replays": [
                    {"run_id": "r1", "file": "r1.json", "allow": allow},
                    {"run_id": "r2", "file": "r2.json"},
                ],
            }
        )
    )


def test_check_paths_reads_allow_from_index_json(tmp_path):
    doc = json.dumps({"a": SAMPLES["email"], "h": SAMPLES["host-path"]})
    (tmp_path / "r1.json").write_text(doc)
    (tmp_path / "r2.json").write_text(doc)
    _write_index(
        tmp_path,
        [
            {"path": "$.a", "rule": "email"},
            {"path": "$.h", "rule": "host-path"},
        ],
    )
    by_dir = {(h.file, h.path, h.rule) for h in check_paths([tmp_path])}
    # r1: email cleared, host-path not clearable; r2 has no allow entries
    assert ("r1.json", "$.a", "email") not in by_dir
    assert ("r1.json", "$.h", "host-path") in by_dir
    assert ("r2.json", "$.a", "email") in by_dir
    # a single file finds index.json beside it
    single = {(h.path, h.rule) for h in check_paths([tmp_path / "r1.json"])}
    assert ("$.a", "email") not in single
    assert ("$.a", "email") in {
        (h.path, h.rule) for h in check_paths([tmp_path / "r2.json"])
    }


def test_malformed_index_json_is_exit_2(tmp_path, capsys):
    (tmp_path / "r1.json").write_text("{}")
    (tmp_path / "index.json").write_text(json.dumps({"replays": [{"allow": 3}]}))
    assert main([str(tmp_path / "r1.json")]) == 2
    assert "index.json" in capsys.readouterr().err


def test_module_uses_only_the_standard_library():
    tree = ast.parse(Path(rc.__file__).read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            roots.add((node.module or "").split(".")[0])
    assert roots <= set(sys.stdlib_module_names)
