"""A tiny server for the "no Authorization header reaches the container" check.

Standard library only: it is copied into a cloud sandbox and started there through
the shim's `POST /processes` (tests/integration/test_agent_runtime_sandbox.py).
It serves port 8081 and answers every request with JSON that says, as booleans,
which of the three platform headers arrived. It never echoes a value.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8081
HEADERS = {
    "authorization": "Authorization",
    "x_sandbox_routing_token": "X-Sandbox-Routing-Token",
    "x_sandbox_port": "X-Sandbox-Port",
}


class Handler(BaseHTTPRequestHandler):
    def _answer(self) -> None:
        body = json.dumps(
            {key: self.headers.get(name) is not None for key, name in HEADERS.items()}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _answer

    def log_message(self, format: str, *args: object) -> None:
        pass  # request lines carry no secret, but nothing needs them


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
