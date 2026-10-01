variable "project_id" {
  type        = string
  description = "The project the budget covers and the guard runs in. Supplied at apply time, never committed."
}

variable "region" {
  type        = string
  description = "Region of the guard function and its source bucket."
  default     = "us-central1"
}

variable "budget_amount_try" {
  type        = number
  description = "The $500 limit in Turkish lira at the day's rate, rounded down to whole lira. The owner supplies it at apply time, with the rate's source."

  validation {
    condition     = var.budget_amount_try >= 1 && var.budget_amount_try == floor(var.budget_amount_try)
    error_message = "budget_amount_try must be a whole number of lira, at least 1."
  }
}

variable "billing_account" {
  type        = string
  description = "Billing account id. When set, it wins over the project's own account; when empty, the project's account is read. Pass it after the hard stop has unlinked billing. Supplied at apply time."
  default     = ""

  validation {
    condition     = var.billing_account == "" || can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account))
    error_message = "billing_account must be empty or a bare account id such as XXXXXX-XXXXXX-XXXXXX."
  }
}

variable "budget_start" {
  type = object({
    year  = number
    month = number
    day   = number
  })
  description = "First day of the budget's custom period. There is no end date, so it measures the project's whole spend."
  default = {
    year  = 2026
    month = 9
    day   = 29
  }
}
