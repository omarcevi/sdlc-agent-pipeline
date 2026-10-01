"""The helpers behind the sandbox integration tests: the credential-name check and the
header echo server. Local only: no sandbox, no network beyond the loopback."""

import http.client
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.integration.sandbox_helpers import (
    TEMPLATE_LEFT_BEHIND,
    credential_like_variables,
    delete_template_with_retry,
)

_SPEC = importlib.util.spec_from_file_location(
    "header_echo_server",
    Path(__file__).resolve().parents[1] / "integration" / "header_echo_server.py",
)
echo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(echo)


def test_credential_check_returns_names_and_never_values():
    output = "PATH=/usr/bin\nGITHUB_TOKEN=s3cret\nGOOGLE_API_KEY=abc\nHOME=/home/x"
    found = credential_like_variables(output)
    assert found == ["GITHUB_TOKEN", "GOOGLE_API_KEY"]
    assert "s3cret" not in str(found)


def test_the_images_public_gpg_key_is_not_a_credential_but_another_value_is():
    public = "GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305"
    assert credential_like_variables(public) == []
    assert credential_like_variables("GPG_KEY=something-else") == ["GPG_KEY"]


def test_a_continuation_line_is_not_taken_for_a_variable():
    assert credential_like_variables("BANNER=line one\nSECRET_LOOKING text=x") == []


def _ask(headers: dict[str, str]) -> tuple[int, dict, bytes]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), echo.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/", headers=headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw), raw
    finally:
        server.shutdown()
        server.server_close()


def test_the_echo_server_reports_which_headers_arrived_as_booleans():
    status, answer, _ = _ask({"Authorization": "Bearer x", "X-Sandbox-Port": "8081"})
    assert status == 200
    assert answer == {
        "authorization": True,
        "x_sandbox_routing_token": False,
        "x_sandbox_port": True,
    }
    _, answer, _ = _ask({})
    assert set(answer.values()) == {False}


def test_the_echo_server_never_echoes_a_value():
    secret = "Bearer eyJ-canary-token"
    _, _, raw = _ask(
        {"Authorization": secret, "X-Sandbox-Routing-Token": "route-canary"}
    )
    assert b"canary" not in raw
    assert echo.PORT == 8081


class _Platform:
    def __init__(self, refusals, error=None):
        self.refusals, self.calls = refusals, 0
        self.error = error or RuntimeError("refused projects/123")

    def delete_template(self, name):
        self.calls += 1
        if self.refusals is None or self.calls <= self.refusals:
            raise self.error


async def _no_sleep(_):
    pass


async def test_template_delete_retries_until_the_platform_accepts():
    platform = _Platform(refusals=2)
    await delete_template_with_retry(platform, "t", sleep=_no_sleep)
    assert platform.calls == 3


async def test_template_delete_is_bounded_and_fails_with_a_fixed_message():
    platform = _Platform(refusals=None)
    with pytest.raises(AssertionError) as caught:
        await delete_template_with_retry(
            platform, "t", interval_s=10, timeout_s=50, sleep=_no_sleep
        )
    assert str(caught.value) == TEMPLATE_LEFT_BEHIND
    assert "123" not in str(caught.value)
    assert platform.calls == 6


async def test_template_delete_treats_not_found_as_done():
    error = type("E", (Exception,), {"code": 404})()
    platform = _Platform(refusals=None, error=error)
    await delete_template_with_retry(platform, "t", sleep=_no_sleep)
    assert platform.calls == 1
