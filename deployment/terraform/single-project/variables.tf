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

variable "project_name" {
  type        = string
  description = "Project name used as a base for resource naming"
  default     = "issue-to-pr"
}

variable "project_id" {
  type        = string
  description = "Google Cloud Project ID for resource deployment."
}

variable "region" {
  type        = string
  description = "Google Cloud region for resource deployment."
  default     = "us-central1"
}

variable "telemetry_logs_filter" {
  type        = string
  description = "Log Sink filter for capturing telemetry data. Captures logs with the `traceloop.association.properties.log_type` attribute set to `tracing`."
  default     = "labels.service_name=\"issue-to-pr\" labels.type=\"agent_telemetry\""
}

variable "app_sa_roles" {
  description = "List of roles to assign to the application service account"
  type        = list(string)
  default = [

    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/bigquery.dataOwner",
    "roles/bigquery.jobUser",
  ]
}

variable "agent_framework" {
  description = "Framework label on the Agent Runtime deployment. The Google Cloud console reads it to pick a playground; override it when the container is not an ADK app."
  type        = string
  default     = "google-adk"
}

variable "github_repository_id" {
  type        = string
  description = "Numeric id of the GitHub repository the workload identity provider trusts (not its name: ids survive a rename)."
  default     = "1401169707"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id must be digits only."
  }
}

variable "github_repository" {
  type        = string
  description = "owner/name of the GitHub repository whose paid.yaml workflow may use the CI runner service account."
  default     = "omarcevi/sdlc-agent-pipeline"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name."
  }
}

variable "operator_member" {
  description = "IAM member (for example user:name@example.com) allowed to sign sandbox tokens. Empty: use the email of the credentials running Terraform."
  type        = string
  default     = ""
}
