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
}

variable "billing_account" {
  type        = string
  description = "Fallback billing account id, used only when the project's own account cannot be read. Supplied at apply time."
  default     = ""
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
