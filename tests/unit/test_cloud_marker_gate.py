"""Tests marked `cloud` or `cloud_slow` talk to real Agent Runtime sandboxes and
spend money: the real `tests/conftest.py` skips them unless ITP_CLOUD_TESTS=1 (as
`make test-cloud` sets it). Checked by running pytest on a small test file with the
real conftest's text (pytester), not a copy of its hook."""

from pathlib import Path

import pytest

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
REASON = "cloud test: set ITP_CLOUD_TESTS=1 (make test-cloud)"

INI = """\
[pytest]
asyncio_default_fixture_loop_scope = function
markers =
    cloud: a cloud test
    cloud_slow: a slow cloud test
"""

TESTS = """\
import pytest


@pytest.mark.cloud
def test_cloud():
    pass


@pytest.mark.cloud_slow
def test_cloud_slow():
    pass


@pytest.mark.parametrize(
    "backend", ["docker", pytest.param("cloud", marks=pytest.mark.cloud)]
)
def test_contract(backend):
    pass


def test_plain():
    pass
"""


def _run(pytester: pytest.Pytester) -> pytest.RunResult:
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makeini(INI)
    pytester.makepyfile(test_gate=TESTS)
    return pytester.runpytest("-rs", "-p", "no:cacheprovider")


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1", "1 "])
def test_cloud_tests_are_skipped_unless_opted_in(pytester, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ITP_CLOUD_TESTS", raising=False)
    else:
        monkeypatch.setenv("ITP_CLOUD_TESTS", value)
    result = _run(pytester)
    # test_plain and the docker parameter run; the three cloud tests are skipped,
    # each with the reason (the -rs summary: "SKIPPED [n] <where>: <reason>").
    result.assert_outcomes(passed=2, skipped=3)
    skipped = [line for line in result.stdout.lines if line.startswith("SKIPPED ")]
    assert skipped and all(line.endswith(f": {REASON}") for line in skipped)
    assert sum(int(line.split("[")[1].split("]")[0]) for line in skipped) == 3


def test_cloud_tests_run_when_opted_in(pytester, monkeypatch):
    monkeypatch.setenv("ITP_CLOUD_TESTS", "1")
    result = _run(pytester)
    result.assert_outcomes(passed=5)
