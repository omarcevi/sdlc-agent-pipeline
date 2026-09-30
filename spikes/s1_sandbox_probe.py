"""Spike S1 (throwaway): can an Agent Runtime Sandbox built from our image
run pytest on an uploaded repo? Creates billable resources — owner approval only.

Usage:
  uv run python spikes/s1_sandbox_probe.py --project P --image IMAGE --caller-sa SA
"""

import argparse
import io
import json
import time
import zipfile

import httpx
import vertexai
from vertexai._genai.types import AgentEngineConfig

ENGINE_DISPLAY_NAME = "issue-to-pr-sandbox-host"
TEMPLATE_DISPLAY_NAME = "issue-to-pr-hermetic-v0-1-0"
PORT = "8080"

REPO_FILES = {
    "calc.py": "def add(a, b):\n    return a + b\n",
    "tests/test_calc.py": (
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    ),
}


def find_or_create_engine(client) -> str:
    for engine in client.agent_engines.list():
        res = getattr(engine, "api_resource", None)
        if (
            res is not None
            and getattr(res, "display_name", None) == ENGINE_DISPLAY_NAME
        ):
            return str(res.name)
    created = client.agent_engines.create(
        config=AgentEngineConfig(display_name=ENGINE_DISPLAY_NAME)
    )
    return str(created.api_resource.name)


def _template_image(template) -> str | None:
    env = getattr(template, "custom_container_environment", None)
    spec = getattr(env, "custom_container_spec", None)
    return getattr(spec, "image_uri", None)


def find_or_create_template(client, engine: str, image: str) -> str:
    """Reuse an ACTIVE template for this image, else create one.

    The API does not return the display name we send, so match on the image.
    Creating a template takes one to four minutes; creating a sandbox from an
    existing template takes about two seconds.
    """
    for stub in client.agent_engines.sandboxes.templates.list(name=engine):
        full = client.agent_engines.sandboxes.templates.get(name=str(stub.name))
        state = str(getattr(full, "state", ""))
        if _template_image(full) == image and state.endswith("ACTIVE"):
            return str(full.name)
    op = client.agent_engines.sandboxes.templates.create(
        name=engine,
        display_name=TEMPLATE_DISPLAY_NAME,
        config={
            "custom_container_environment": {
                "custom_container_spec": {"image_uri": image},
                "ports": [{"port": int(PORT), "protocol": "TCP"}],
            },
            "egress_control_config": {"internet_access": False},
        },
    )
    return str(op.response.name)


def repo_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in REPO_FILES.items():
            zf.writestr(path, content)
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--image", required=True)
    parser.add_argument("--caller-sa", required=True)
    parser.add_argument(
        "--hold-seconds",
        type=int,
        default=0,
        help="keep the sandbox alive this long after the checks, to inspect it",
    )
    args = parser.parse_args()

    client = vertexai.Client(
        project=args.project,
        location=args.location,
        http_options={"api_version": "v1beta1"},
    )
    timings: dict[str, float] = {}
    t0 = time.monotonic()
    engine = find_or_create_engine(client)
    print(f"engine: {engine}", flush=True)
    template = find_or_create_template(client, engine, args.image)
    print(f"template: {template}", flush=True)
    timings["engine_and_template_s"] = round(time.monotonic() - t0, 1)

    t0 = time.monotonic()
    op = client.agent_engines.sandboxes.create(
        name=engine,
        config={
            "owner": "s1-spike",
            "sandbox_environment_template": template,
            "ttl": "1800s",
        },
    )
    sandbox = op.response
    timings["provision_s"] = round(time.monotonic() - t0, 1)
    print(f"sandbox: {sandbox.name} ({timings['provision_s']} s)", flush=True)
    results: dict[str, object] = {
        "engine": engine,
        "template": template,
        "sandbox": str(sandbox.name),
    }
    try:
        token = str(
            client.agent_engines.sandboxes.generate_access_token(args.caller_sa)
        )
        http = httpx.Client(
            base_url=f"https://{sandbox.connection_info.load_balancer_hostname}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Sandbox-Routing-Token": str(sandbox.connection_info.routing_token),
                "X-Sandbox-Port": PORT,
            },
            timeout=120,
        )
        # The platform answers /healthz itself ("OK") about 30 s before our
        # container listens, so readiness must be a call that reaches the shim.
        t0 = time.monotonic()
        while True:
            try:
                ready = http.post("/exec", json={"command": "true"}, timeout=15)
                status, body = ready.status_code, ready.text[:120]
            except httpx.HTTPError as exc:
                status, body = None, f"{type(exc).__name__}: {exc}"
            waited = round(time.monotonic() - t0, 1)
            print(f"ready check after {waited} s: {status} {body!r}", flush=True)
            if status == 200:
                break
            if waited > 240:
                raise RuntimeError("sandbox never became ready")
            time.sleep(3)
        timings["ready_s"] = waited
        results["platform_healthz"] = http.get("/healthz").text[:50]

        def show(label: str, response: httpx.Response) -> object:
            try:
                body: object = response.json()
            except ValueError:
                body = response.text[:300]
            print(f"{label}: HTTP {response.status_code} {body!r}"[:600], flush=True)
            return body

        t0 = time.monotonic()
        up = http.post(
            "/files/zip",
            params={"path": "repo"},
            content=repo_zip(),
            headers={"Content-Type": "application/zip"},
        )
        timings["upload_s"] = round(time.monotonic() - t0, 2)
        results["upload"] = show("upload", up)
        t0 = time.monotonic()
        run = http.post(
            "/exec",
            json={
                "command": "python -m pytest -q",
                "cwd": "/workspace/repo",
                "timeout": 120,
            },
        )
        timings["pytest_s"] = round(time.monotonic() - t0, 2)
        results["pytest"] = show("pytest", run)
        net = http.post(
            "/exec",
            json={
                "command": 'python -c "import socket; '
                "socket.create_connection(('1.1.1.1', 53), timeout=3)\"",
                "timeout": 20,
            },
        )
        net_body = show("network", net)
        results["network_blocked"] = (
            isinstance(net_body, dict) and net_body.get("exit_code") != 0
        )
        who = http.post(
            "/exec",
            json={
                "command": "id -u && env | cut -d= -f1 | sort | tr '\\n' ' '",
                "timeout": 20,
            },
        )
        results["identity_and_env_names"] = show("identity", who)
        down = http.get("/files/zip", params={"path": "repo"})
        print(
            f"download: HTTP {down.status_code} {len(down.content)} bytes", flush=True
        )
        try:
            results["download_names"] = sorted(
                zipfile.ZipFile(io.BytesIO(down.content)).namelist()
            )
        except zipfile.BadZipFile:
            results["download_names"] = f"not a zip: {down.text[:200]}"
        results["timings"] = timings
        print(json.dumps(results, indent=2, default=str), flush=True)
        if args.hold_seconds:
            print(
                f"HOLDING {args.hold_seconds} s before deleting {sandbox.name}",
                flush=True,
            )
            time.sleep(args.hold_seconds)
    finally:
        client.agent_engines.sandboxes.delete(name=str(sandbox.name))
        print(f"DELETED {sandbox.name}", flush=True)


if __name__ == "__main__":
    main()
