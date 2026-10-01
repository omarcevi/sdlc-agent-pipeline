locals {
  budget_services = [
    "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com",
    "pubsub.googleapis.com",
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "eventarc.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
  ]
}

# Through google.api_bootstrap so the Service Usage call is billed to the
# caller. for_each, not count, so adding an API does not renumber the rest.
resource "google_project_service" "budget_services" {
  provider = google.api_bootstrap
  for_each = toset(local.budget_services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
