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
EXTRA = {
    "host-path": ["see /home/runner/x", "cd /root/.ssh", "C:\\Users\\bob"],
    "github-token": ["github_pat_" + "11AB"],
    "google-key": ["ya29" + ".a0Af"],
    "other-token": ["xo" + "xb-1234", "sk-" + "A1" * 12],
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
    "risk-mitigation-strategy-abcdefghijklmnopqrstuvwxyz is prose",
    "@@ -1,3 +1,3 @@\n-    return re.sub(r'[a-z]+', '', s)\n+    return re.sub(r'[a-z0-9]+', '', s)",
    "tests/test_x.py::test_a PASSED [ 50%]\n=== 2 passed in 0.03s ===",
    "The planner finds the README rule the code breaks and widens one regex.",
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
    assert hits == [Hit("f.json", "$.run{key:0}", "host-path")]
    odd = scan_value({"run": {"odd key": SAMPLES["email"]}}, file="f.json")
    assert odd == [Hit("f.json", '$.run["odd key"]', "email")]


def test_paths_name_lists_and_nesting():
    value = {"steps": [{}, {}, {}, {"result": {"stdout": SAMPLES["sandbox"]}}]}
    (hit,) = scan_value(value, file="f.json")
    assert str(hit) == "f.json: $.steps[3].result.stdout: sandbox"


def test_reports_never_contain_the_matched_text(tmp_path, capsys):
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


@pytest.mark.parametrize(
    ("rule", "sample"),
    [(r, x) for r, xs in EXTRA.items() for x in xs],
)
def test_more_samples_per_rule(rule, sample):
    assert rule in scan_text(sample)


@pytest.mark.parametrize(
    "text",
    [
        "a\\n" + "sk-" + "A1" * 12,
        "0sk-" + "A1" * 12,
        "x\nsk-" + "abcdefghijklmnopqrst",
        "sk-" + "proj-" + "Ab1_-" * 6,
        "sk-" + "ant-api03-" + "Ab1_-" * 6,
        "AS" + "IA" + "ABCDEFGH12345678",
    ],
)
def test_other_token_catches_prefixed_and_wide_forms(text):
    assert "other-token" in scan_text(text)


@pytest.mark.parametrize("marker", ["+", "-", "_", ".", ""])
def test_email_at_start_of_diff_line(marker):
    assert "email" in scan_text(f"{marker}alice@corp.io\n")
    assert "email" in scan_text(f"ctx\n{marker}alice@corp.io\n")


def test_email_clean_cases_in_diffs():
    assert "email" not in scan_text("+@pytest.mark.parametrize('x', [1])\n-@a.b")
    assert "email" not in scan_text("+a@example.com\n-b@example.org")


def test_duplicate_keys_are_refused(tmp_path, capsys):
    path = tmp_path / "d.json"
    secret = "gh" + "p_" + "A1" * 12
    path.write_text('{"a": "' + secret + '", "a": "x"}')
    assert main([str(path)]) == 2
    err = capsys.readouterr().err
    assert "duplicate key" in err and secret not in err and "d.json" in err
    path.write_text('{"x": {"a": 1, "b": {"c": 1, "c": 2}}}')
    with pytest.raises(rc.ReplayFileError, match="duplicate key"):
        check_paths([path])


@pytest.mark.parametrize(
    "text",
    [
        "\\/Users\\/omar\\/x",
        "\\/home\\/omar",
        "c:\\users\\bob",
        "c:\\Users\\bob",
        "D:\\Users\\bob",
        "\\/var\\/folders\\/ab",
    ],
)
def test_host_path_escaped_and_case_variants(text):
    assert "host-path" in scan_text(text)


def test_project_after_a_literal_escape():
    assert "project" in scan_text("a\\n" + PROJECT, (PROJECT,))
    assert "project" in scan_text("a\\t" + PROJECT + "\\n", (PROJECT,))
    assert "project" not in scan_text("n" + PROJECT, (PROJECT,))


def test_jwt_rule_is_linear_and_still_catches_after_escape():
    import time

    start = time.perf_counter()
    scan_text("eyJ" * 200_000)
    assert time.perf_counter() - start < 2.0
    assert "jwt" in scan_text("x\\n" + SAMPLES["jwt"])
    assert "jwt" not in scan_text("eyJabc.def")


def test_deep_nesting_does_not_escape_main(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text("[" * 50_000 + "]" * 50_000)
    assert main([str(path)]) in (0, 2)
    deep: dict = {}
    node = deep
    for _ in range(5000):
        node["k"] = {}
        node = node["k"]
    node["k"] = SAMPLES["sandbox"]
    (hit,) = scan_value(deep, file="f")
    assert hit.rule == "sandbox"


def test_directories_are_scanned_recursively(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.json").write_text(json.dumps({"a": SAMPLES["sandbox"]}))
    assert [h.file for h in check_paths([tmp_path])] == ["sub/x.json"]


def test_key_hits_are_numbered_per_key():
    value = {
        "ok": 1,
        SAMPLES["email"]: "v",
        SAMPLES["sandbox"]: {"z": SAMPLES["email"]},
    }
    hits = scan_value(value, file="f")
    paths = {(h.path, h.rule) for h in hits}
    assert ("$.{key:1}", "email") not in paths
    assert ("${key:1}", "email") in paths
    assert ("${key:2}", "sandbox") in paths
    assert ("${value:2}.z", "email") in paths
    assert all("corp.io" not in h.path and "0123456789ab" not in h.path for h in hits)


def test_split_tokens_are_caught_after_stripping_invisibles():
    token = "gh" + "p_" + "A1" * 12
    assert "github-token" in scan_text(token[:3] + "\u200b" + token[3:])
    assert "github-token" in scan_text(token[:3] + "<U+200B>" + token[3:])
    assert "private-key" in scan_text("-----BEGIN PGP PRIVATE KEY BLOCK-----")


@pytest.mark.parametrize(
    "text",
    [
        "GET /users/42",
        "src/users/models.py",
        "tests\\users\\test_x.py",
        "docs/Home/index.md",
    ],
)
def test_host_path_has_no_false_positives_on_ordinary_paths(text):
    assert "host-path" not in scan_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "/Users/omar/x",
        "/home/runner/x",
        "/root/.ssh",
        "/var/folders/ab/c",
        "C:\\users\\b",
    ],
)
def test_host_path_real_paths_still_hit(text):
    assert "host-path" in scan_text(text)


def test_nothing_to_check_is_exit_2(tmp_path, capsys):
    assert main([str(tmp_path)]) == 2
    assert "no replay files found" in capsys.readouterr().err
    (tmp_path / "notes.txt").write_text("x")
    assert main([str(tmp_path)]) == 2
    with pytest.raises(rc.ReplayFileError, match="no replay files found"):
        check_paths([tmp_path])


def test_hit_file_is_relative_to_the_scanned_root(tmp_path):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.json").write_text(json.dumps({"k": SAMPLES["sandbox"]}))
    files = sorted(h.file for h in check_paths([tmp_path]))
    assert files == ["a/x.json", "b/x.json"]
    (single,) = check_paths([tmp_path / "a" / "x.json"])
    assert single.file == "x.json"


# --- run ids (controller ruling: exempt from high-entropy by exact shape only) ----

RUN_IDS = [
    "md-001-single-flash-r1-20261001T084838Z",
    "tc-005-single-flash-r1-20261001T084838Z",
    "sr-003-single-flash-r1-20261001T084838Z",
    "md-001-multi-flash-r1-20261001T062611Z",
    "md-003-review-pro-rp-04-r12-20261001T062611Z",
]


def test_a_run_id_is_entropic_enough_to_need_the_exemption():
    # without the exemption this id is a high-entropy run, so the ruling is not vacuous
    assert rc._high_entropy(RUN_IDS[0])


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_a_string_that_is_exactly_a_run_id_is_not_high_entropy(run_id):
    assert scan_text(run_id) == []
    assert scan_text(run_id + ".json") == []
    assert scan_value({"run_id": run_id, run_id: [run_id]}, file="f") == []


@pytest.mark.parametrize(
    "text",
    [
        ALNUM,
        "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
        RUN_IDS[0] + ALNUM,
        ALNUM + RUN_IDS[0],
        RUN_IDS[0] + "-" + ALNUM,
        RUN_IDS[0] + "\n",
        RUN_IDS[0] + "\n" + ALNUM,
        "x " + RUN_IDS[0],
        RUN_IDS[0] + ".json.bak",
        RUN_IDS[0].replace("md-", "Md-"),
        RUN_IDS[0] + "\u200b",
    ],
)
def test_token_shaped_strings_never_match_the_run_id_exemption(text):
    assert rc._RUN_ID_SHAPE.fullmatch(text) is None
    assert "high-entropy" in scan_text(text)


def test_the_run_id_shape_is_ascii_only():
    assert rc._RUN_ID_SHAPE.fullmatch(RUN_IDS[0])
    assert not rc._RUN_ID_SHAPE.fullmatch(
        RUN_IDS[0].replace("-001-", "-\u0661\u0662\u0663-")
    )


def test_index_json_run_id_file_and_pair_fields_pass(tmp_path):
    single, multi = RUN_IDS[0], RUN_IDS[3]
    for run_id in (single, multi):
        doc = {"run": {"run_id": run_id}}
        (tmp_path / f"{run_id}.json").write_text(json.dumps(doc))
    index = {
        "schema": 1,
        "replays": [
            {"run_id": multi, "file": f"{multi}.json", "pair": single},
            {"run_id": single, "file": f"{single}.json", "pair": multi},
        ],
    }
    (tmp_path / "index.json").write_text(json.dumps(index))
    assert check_paths([tmp_path]) == []
