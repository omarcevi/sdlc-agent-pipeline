terraform {
  required_version = ">= 1.11.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.28.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
}

# The Budget API needs the quota project set when it is called with user
# credentials.
provider "google" {
  alias                 = "billing_override"
  billing_project       = var.project_id
  region                = var.region
  user_project_override = true
}

# Enables the budget_services APIs. user_project_override is deliberately
# unset so the call is billed to the caller, not to the target project.
provider "google" {
  alias  = "api_bootstrap"
  region = var.region
}
