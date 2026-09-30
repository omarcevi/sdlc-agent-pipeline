# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""agents-cli entry point: exposes the pipeline as `root_agent` and `app`.

Importing this module has no cloud side effects. BigQuery analytics is opt-in.
"""

import logging
import os

from google.adk.apps import App
from google.adk.plugins import ReflectAndRetryModelPlugin

from app.budget import BudgetPlugin
from app.guardrails import GuardrailPlugin
from app.models import RoleModels
from app.pipeline import build_workflow

# Keep in sync with agents-cli-manifest.yaml (root_agent_name: issue_to_pr).
root_agent = build_workflow(RoleModels.from_env())


def _analytics_plugins() -> list:
    """The BigQuery analytics plugin, only when BQ_ANALYTICS_ENABLED=1 and a project
    is set. Enabling it creates the dataset, which needs the owner's approval, so
    nothing is imported from BigQuery and no client is built unless it is on."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if os.environ.get("BQ_ANALYTICS_ENABLED") != "1" or not project_id:
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


# Plugin order matters: a plugin that returns a value stops the ones after it.
# Budget first so every model response is costed, then the retry for malformed
# function calls, then guardrails, then analytics. app/driver.py uses the same order.
app = App(
    root_agent=root_agent,
    name="app",
    plugins=[
        BudgetPlugin(),
        ReflectAndRetryModelPlugin(max_retries=2),
        GuardrailPlugin(),
        *_analytics_plugins(),
    ],
)
