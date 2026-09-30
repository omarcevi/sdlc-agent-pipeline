"""Opt-in export of run traces to Cloud Trace.

Local runs (the bench driver) export nothing unless TRACE_TO_CLOUD=1. When it is
on, ADK's own spans (workflow, agent, model call, tool call) and the driver's
root span go to Cloud Trace in GOOGLE_CLOUD_PROJECT.
"""

import logging
import os

import google.auth
from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource
from google.adk.telemetry.setup import maybe_set_otel_providers
from google.auth.exceptions import DefaultCredentialsError
from opentelemetry import trace

logger = logging.getLogger(__name__)

TRACER_NAME = "issue_to_pr"
ROOT_SPAN_NAME = "issue_to_pr.run"
_FLUSH_TIMEOUT_MS = 30_000

_enabled = False


def enable_cloud_trace() -> bool:
    """Turn on span export to Cloud Trace. Returns whether tracing is on.

    Does nothing unless TRACE_TO_CLOUD=1 and GOOGLE_CLOUD_PROJECT is set. The
    exporter's credentials are pinned to that project for quota, because the
    local default credentials may name a different quota project.
    """
    global _enabled
    if _enabled:
        return True
    if os.environ.get("TRACE_TO_CLOUD") != "1":
        return False
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        logger.warning("TRACE_TO_CLOUD=1 but GOOGLE_CLOUD_PROJECT is not set")
        return False
    try:
        credentials, _ = google.auth.default(quota_project_id=project_id)
    except DefaultCredentialsError:
        logger.warning(
            "TRACE_TO_CLOUD=1 but no Google credentials were found; "
            "run `gcloud auth application-default login`. Tracing is off."
        )
        return False
    hooks = get_gcp_exporters(
        enable_cloud_tracing=True, google_auth=(credentials, project_id)
    )
    maybe_set_otel_providers([hooks], otel_resource=get_gcp_resource(project_id))
    _enabled = True
    return True


def flush_traces() -> None:
    """Send any buffered spans now. Call before a short-lived process exits."""
    provider = trace.get_tracer_provider()
    flush = getattr(provider, "force_flush", None)
    if flush is not None:
        flush(_FLUSH_TIMEOUT_MS)


def trace_explorer_url(project_id: str) -> str:
    return f"https://console.cloud.google.com/traces/explorer?project={project_id}"
