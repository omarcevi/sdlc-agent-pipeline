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

# Workload Identity Federation for GitHub Actions: no service account keys.
# The provider trusts one repository (by its numeric id, which survives a
# rename); each service account is bound to one narrow claim.

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.services]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.ref"           = "assertion.ref"
    "attribute.environment"   = "assertion.environment"
    "attribute.event_name"    = "assertion.event_name"
  }

  attribute_condition = "assertion.repository_id == \"${var.github_repository_id}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  depends_on = [google_project_service.services]
}

# Runs the free checks and the guarded paid runs from main.
resource "google_service_account" "ci_runner" {
  project      = var.project_id
  account_id   = "ci-runner"
  display_name = "CI runner (GitHub Actions)"

  depends_on = [google_project_service.services]
}

resource "google_project_iam_member" "ci_runner_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/serviceusage.serviceUsageConsumer",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.ci_runner.email}"

  depends_on = [google_project_service.services]
}

# Deploys from the `production` environment, which needs the owner's approval.
resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "deployer"
  display_name = "Deployer (GitHub Actions)"

  depends_on = [google_project_service.services]
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/serviceusage.serviceUsageConsumer",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"

  depends_on = [google_project_service.services]
}

# The deployer may act as the application account and as no other.
resource "google_service_account_iam_member" "deployer_acts_as_app" {
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"

  depends_on = [google_project_service.services]
}

resource "google_service_account_iam_member" "ci_runner_wif" {
  service_account_id = google_service_account.ci_runner.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.ref/refs/heads/main"

  depends_on = [google_project_service.services]
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.environment/production"

  depends_on = [google_project_service.services]
}
