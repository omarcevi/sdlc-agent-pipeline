FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git procps ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        pytest==9.0.2 \
        uv==0.11.21 \
        fastapi==0.115.0 \
        pydantic==2.9.2 \
        "uvicorn[standard]==0.30.6"

WORKDIR /opt/runtime
COPY runtime/protocol.py runtime/server.py runtime/entrypoint.sh /opt/runtime/

RUN useradd --create-home --uid 1000 --shell /bin/bash sandbox \
    && mkdir -p /workspace \
    && chmod +x /opt/runtime/entrypoint.sh \
    && chown -R sandbox:sandbox /workspace /opt/runtime

USER sandbox
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_ADDOPTS="-p no:cacheprovider"

EXPOSE 8080
CMD ["/opt/runtime/entrypoint.sh"]
