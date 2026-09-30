"""agents-cli entry point: exposes the pipeline as `root_agent` and `app`."""

import logging
import os

from google.adk.apps import App

from app.budget import BudgetPlugin
from app.guardrails import GuardrailPlugin
from app.models import RoleModels
from app.pipeline import build_workflow

# Keep in sync with agents-cli-manifest.yaml (root_agent_name: issue_to_pr).
root_agent = build_workflow(RoleModels.from_env())


def _analytics_plugins() -> list:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        return []
    from google.adk.plugins.bigquery_agent_analytics_plugin import (
        BigQueryAgentAnalyticsPlugin,
        BigQueryLoggerConfig,
    )
    from google.cloud import bigquery

    dataset_id = os.environ.get("BQ_ANALYTICS_DATASET_ID", "adk_agent_analytics")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    try:
        bigquery.Client(project=project_id).create_dataset(
            f"{project_id}.{dataset_id}", exists_ok=True
        )
        return [
            BigQueryAgentAnalyticsPlugin(
                project_id=project_id,
                dataset_id=dataset_id,
                location=location,
                config=BigQueryLoggerConfig(
                    gcs_bucket_name=os.environ.get("BQ_ANALYTICS_GCS_BUCKET"),
                    connection_id=os.environ.get("BQ_ANALYTICS_CONNECTION_ID"),
                ),
            )
        ]
    except Exception as exc:
        logging.warning("Failed to initialize BigQuery Analytics: %s", exc)
        return []


app = App(
    root_agent=root_agent,
    name="app",
    plugins=[BudgetPlugin(), GuardrailPlugin(), *_analytics_plugins()],
)
