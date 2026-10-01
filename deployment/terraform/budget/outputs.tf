output "budget_topic" {
  description = "The budget's Pub/Sub topic (short name)."
  value       = google_pubsub_topic.budget.name
}

output "budget_id" {
  description = "The budget's short id, as in the budgetId attribute of its notifications."
  value       = local.budget_id
}

output "budget_amount_try" {
  description = "The budget amount in TRY."
  value       = var.budget_amount_try
}

output "guard_function" {
  description = "The guard function's name."
  value       = google_cloudfunctions2_function.guard.name
}
