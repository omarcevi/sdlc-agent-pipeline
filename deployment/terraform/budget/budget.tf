data "google_project" "project" {
  project_id = var.project_id
}

locals {
  # The short id, equal to the budgetId attribute of every notification.
  budget_id = element(split("/", google_billing_budget.project.name), 3)
  # The variable wins when it is set; otherwise the project's own account.
  billing_account = coalesce(var.billing_account, data.google_project.project.billing_account)
}

resource "google_pubsub_topic" "budget" {
  name = "issue-to-pr-budget"

  depends_on = [google_project_service.budget_services]
}

# Cloud Billing publishes budget notifications as this service account.
resource "google_pubsub_topic_iam_member" "billing_publishes" {
  topic  = google_pubsub_topic.budget.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:billing-budget-alert@system.gserviceaccount.com"
}

resource "google_billing_budget" "project" {
  provider = google.billing_override

  billing_account = local.billing_account
  display_name    = "issue-to-pr project limit"

  budget_filter {
    projects               = ["projects/${data.google_project.project.number}"]
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"

    custom_period {
      start_date {
        year  = var.budget_start.year
        month = var.budget_start.month
        day   = var.budget_start.day
      }
    }
  }

  amount {
    specified_amount {
      currency_code = "TRY"
      units         = tostring(floor(var.budget_amount_try))
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 0.8
  }

  threshold_rules {
    threshold_percent = 1.0
  }

  all_updates_rule {
    pubsub_topic   = google_pubsub_topic.budget.id
    schema_version = "1.0"
  }

  depends_on = [
    google_project_service.budget_services,
    google_pubsub_topic_iam_member.billing_publishes,
  ]
}
