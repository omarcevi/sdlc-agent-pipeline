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

# Cloud sandboxes: the image repository and the identity that calls them.

# Email of the credentials running Terraform. Needs the email scope; set
# var.operator_member when it comes back empty.
data "google_client_openid_userinfo" "me" {}

resource "google_artifact_registry_repository" "sandbox" {
  project       = var.project_id
  location      = var.region
  repository_id = "issue-to-pr"
  format        = "DOCKER"
  description   = "Sandbox images"

  depends_on = [google_project_service.services]
}

resource "google_service_account" "sandbox_caller" {
  project      = var.project_id
  account_id   = "sandbox-caller"
  display_name = "Sandbox caller"

  depends_on = [google_project_service.services]
}

resource "google_project_iam_member" "sandbox_caller_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.sandbox_caller.email}"
}

# The orchestrator signs a short-lived token as sandbox-caller for each call.
resource "google_service_account_iam_member" "app_sa_signs_sandbox_tokens" {
  service_account_id = google_service_account.sandbox_caller.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_service_account_iam_member" "operator_signs_sandbox_tokens" {
  service_account_id = google_service_account.sandbox_caller.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.operator_member != "" ? var.operator_member : "user:${data.google_client_openid_userinfo.me.email}"
}

# The Week 1 spike created these by hand. The blocks adopt them on the first
# apply and are removed in the commit after it.
import {
  to = google_artifact_registry_repository.sandbox
  id = "projects/${var.project_id}/locations/${var.region}/repositories/issue-to-pr"
}

import {
  to = google_service_account.sandbox_caller
  id = "projects/${var.project_id}/serviceAccounts/sandbox-caller@${var.project_id}.iam.gserviceaccount.com"
}
