"""The helpers behind the sandbox integration tests: the credential-name check and the
header echo server. Local only: no sandbox, no network beyond the loopback."""

import http.client
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests.integration.sandbox_helpers import credential_like_variables

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
