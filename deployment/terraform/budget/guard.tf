resource "google_service_account" "guard" {
  account_id   = "budget-guard"
  display_name = "Budget guard: disables billing at the hard stop"
}

resource "google_service_account" "guard_build" {
  account_id   = "budget-guard-build"
  display_name = "Budget guard: builds the function"
}

# Unlinking the project from its billing account needs only this project
# permission. Nothing is granted on the billing account.
resource "google_project_iam_member" "guard_billing_project_manager" {
  project = var.project_id
  role    = "roles/billing.projectManager"
  member  = google_service_account.guard.member
}

resource "google_project_iam_member" "guard_build_builder" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = google_service_account.guard_build.member
}

resource "google_storage_bucket" "guard_source" {
  name                        = "${var.project_id}-budget-guard-source"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  depends_on = [google_project_service.budget_services]
}

# The zip goes to a git-ignored path under build/. Bytecode is excluded so a
# test run never changes the archive, and the object name carries the hash so a
# source change reaches the function.
data "archive_file" "guard_source" {
  type        = "zip"
  source_dir  = "${path.module}/function"
  output_path = "${path.module}/build/budget-guard-source.zip"
  excludes    = ["__pycache__", "__pycache__/**", "**/__pycache__/**", "*.pyc", "**/*.pyc"]
}

resource "google_storage_bucket_object" "guard_source" {
  name   = "budget-guard-${data.archive_file.guard_source.output_md5}.zip"
  bucket = google_storage_bucket.guard_source.name
  source = data.archive_file.guard_source.output_path
}

resource "google_cloudfunctions2_function" "guard" {
  name     = "budget-guard"
  location = var.region

  build_config {
    runtime         = "python312"
    entry_point     = "stop_billing"
    service_account = google_service_account.guard_build.id

    source {
      storage_source {
        bucket = google_storage_bucket.guard_source.name
        object = google_storage_bucket_object.guard_source.name
      }
    }
  }

  service_config {
    service_account_email          = google_service_account.guard.email
    max_instance_count             = 1
    min_instance_count             = 0
    available_memory               = "256M"
    timeout_seconds                = 60
    ingress_settings               = "ALLOW_INTERNAL_ONLY"
    all_traffic_on_latest_revision = true

    environment_variables = {
      GUARD_PROJECT_ID      = var.project_id
      GUARD_BUDGET_ID       = local.budget_id
      GUARD_BILLING_ACCOUNT = local.billing_account
    }
  }

  event_trigger {
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.budget.id
    retry_policy          = "RETRY_POLICY_RETRY"
    service_account_email = google_service_account.guard.email
  }

  depends_on = [
    google_project_service.budget_services,
    google_project_iam_member.guard_build_builder,
    google_project_iam_member.guard_billing_project_manager,
  ]
}

resource "google_cloud_run_service_iam_member" "guard_invoker" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.guard.name
  role     = "roles/run.invoker"
  member   = google_service_account.guard.member
}
