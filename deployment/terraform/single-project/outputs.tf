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

output "app_service_account_email" {
  description = "Application service account email"
  value       = google_service_account.app_sa.email
}

output "logs_bucket_name" {
  description = "Logs storage bucket name"
  value       = google_storage_bucket.logs_data_bucket.name
}

output "sandbox_caller_email" {
  description = "Service account that signs the tokens used to call sandboxes"
  value       = google_service_account.sandbox_caller.email
}

output "sandbox_image_repository" {
  description = "Artifact Registry repository for the sandbox image"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/issue-to-pr"
}

output "project_id" {
  description = "The project this root was applied to; make's project pin compares it with GOOGLE_CLOUD_PROJECT."
  value       = var.project_id
}
