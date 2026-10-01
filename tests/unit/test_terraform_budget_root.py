"""Text-level checks of the budget Terraform root (Terraform is not installed in CI).

Comments are stripped before matching, so a commented-out resource cannot pass.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "deployment" / "terraform" / "budget"
FILES = ("providers", "variables", "apis", "budget", "guard", "outputs")


def strip_comments(text: str) -> str:
    """Remove #, // and /* */ comments, leaving string literals alone."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif ch == "#" or text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tf(name: str) -> str:
    return strip_comments((ROOT / f"{name}.tf").read_text())


def all_tf() -> str:
    return "\n".join(tf(name) for name in FILES)


def block(text: str, header: str) -> str:
    """The body of the first block whose header line starts with `header`."""
    match = re.search(r"^\s*" + re.escape(header) + r"\s*\{", text, re.MULTILINE)
    assert match, f"no block {header!r}"
    depth, i = 1, match.end()
    while depth:
        ch = text[i]
        if ch == '"':
            i = text.index('"', i + 1)
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return text[match.end() : i - 1]


def resource(text: str, kind: str, name: str) -> str:
    return block(text, f'resource "{kind}" "{name}"')


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_root_has_exactly_the_planned_files():
    assert {p.name for p in ROOT.glob("*.tf")} == {f"{n}.tf" for n in FILES}


def test_budget_is_500_dollars_in_try_before_credits_from_the_project_start():
    text = tf("budget")
    budget = resource(text, "google_billing_budget", "project")
    flat = squash(budget)
    assert "provider = google.billing_override" in flat
    assert "billing_account = local.billing_account" in flat
    assert 'projects = ["projects/${data.google_project.project.number}"]' in flat
    assert 'credit_types_treatment = "EXCLUDE_ALL_CREDITS"' in flat
    period = squash(block(budget, "custom_period"))
    start = squash(block(budget, "start_date"))
    assert "end_date" not in period
    for part in ("year", "month", "day"):
        assert f"{part} = var.budget_start.{part}" in start
    spec = squash(block(budget, "specified_amount"))
    assert 'currency_code = "TRY"' in spec
    assert "units = tostring(floor(var.budget_amount_try))" in spec
    thresholds = re.findall(r"threshold_percent\s*=\s*([0-9.]+)", budget)
    assert sorted(thresholds) == ["0.5", "0.8", "1.0"]
    rule = squash(block(budget, "all_updates_rule"))
    assert "pubsub_topic = google_pubsub_topic.budget.id" in rule
    assert 'schema_version = "1.0"' in rule

    variables = tf("variables")
    amount = squash(block(variables, 'variable "budget_amount_try"'))
    assert "type = number" in amount
    assert "default" not in amount
    start_var = squash(block(variables, 'variable "budget_start"'))
    assert "year = 2026" in start_var
    assert "month = 9" in start_var
    assert "day = 29" in start_var
    fallback = squash(block(variables, 'variable "billing_account"'))
    assert 'default = ""' in fallback
    assert 'default = "us-central1"' in squash(block(variables, 'variable "region"'))


def test_billing_locals_are_shared_by_the_function_and_the_outputs():
    text = all_tf()
    flat = squash(block(tf("budget"), "locals"))
    assert (
        'budget_id = element(split("/", google_billing_budget.project.name), 3)' in flat
    )
    assert (
        "billing_account = coalesce(var.billing_account, "
        "data.google_project.project.billing_account)" in flat
    )
    guard_text = tf("guard")
    env = squash(guard_text[guard_text.index("environment_variables") :])
    assert "GUARD_BUDGET_ID = local.budget_id" in env
    assert "GUARD_BILLING_ACCOUNT = local.billing_account" in env
    assert "GUARD_PROJECT_ID = var.project_id" in env
    outputs = tf("outputs")
    assert "value = local.budget_id" in squash(block(outputs, 'output "budget_id"'))
    assert text.count("split(") == 1


def test_billing_service_can_publish_to_the_topic():
    text = tf("budget")
    assert 'name = "issue-to-pr-budget"' in squash(
        resource(text, "google_pubsub_topic", "budget")
    )
    member = squash(
        resource(text, "google_pubsub_topic_iam_member", "billing_publishes")
    )
    assert "topic = google_pubsub_topic.budget.name" in member
    assert 'role = "roles/pubsub.publisher"' in member
    assert (
        'member = "serviceAccount:billing-budget-alert@system.gserviceaccount.com"'
        in member
    )
    # No other publisher is granted on the topic.
    assert text.count('resource "google_pubsub_topic_iam_') == 1
    assert 'data "google_project" "project"' in text


def test_guard_function_is_internal_single_instance_and_retries():
    text = tf("guard")
    fn = resource(text, "google_cloudfunctions2_function", "guard")
    flat = squash(fn)
    assert 'name = "budget-guard"' in flat
    assert "location = var.region" in flat
    build = squash(block(fn, "build_config"))
    assert 'runtime = "python312"' in build
    assert 'entry_point = "stop_billing"' in build
    assert "service_account = google_service_account.guard_build.id" in build
    service = squash(block(fn, "service_config"))
    for line in (
        "max_instance_count = 1",
        "min_instance_count = 0",
        'available_memory = "256M"',
        "timeout_seconds = 60",
        'ingress_settings = "ALLOW_INTERNAL_ONLY"',
        "all_traffic_on_latest_revision = true",
        "service_account_email = google_service_account.guard.email",
    ):
        assert line in service
    trigger = squash(block(fn, "event_trigger"))
    assert 'event_type = "google.cloud.pubsub.topic.v1.messagePublished"' in trigger
    assert "pubsub_topic = google_pubsub_topic.budget.id" in trigger
    assert 'retry_policy = "RETRY_POLICY_RETRY"' in trigger
    assert "service_account_email = google_service_account.guard.email" in trigger
    assert "allUsers" not in all_tf()


def test_guard_roles_are_project_billing_manager_and_invoker_only():
    text = tf("guard")
    everything = all_tf()
    assert "google_billing_account_iam" not in everything
    assert "billingAccounts/" not in everything
    assert "google_organization_iam" not in everything
    assert "google_folder_iam" not in everything
    assert "google_project_iam_binding" not in everything
    assert "google_project_iam_policy" not in everything

    members = re.findall(r'resource "google_project_iam_member" "(\w+)" \{', text)
    guard_roles: set[str] = set()
    build_roles: set[str] = set()
    for name in members:
        body = squash(resource(text, "google_project_iam_member", name))
        role = re.search(r'role = "([^"]+)"', body).group(1)
        if "google_service_account.guard_build.member" in body:
            build_roles.add(role)
        else:
            assert "google_service_account.guard.member" in body, name
            guard_roles.add(role)
    assert {"roles/billing.projectManager"} <= guard_roles
    assert guard_roles <= {"roles/billing.projectManager", "roles/browser"}
    assert build_roles == {"roles/cloudbuild.builds.builder"}

    invoker = squash(
        resource(text, "google_cloud_run_service_iam_member", "guard_invoker")
    )
    assert 'role = "roles/run.invoker"' in invoker
    assert "service = google_cloudfunctions2_function.guard.name" in invoker
    assert "member = google_service_account.guard.member" in invoker
    assert 'account_id = "budget-guard"' in squash(
        resource(text, "google_service_account", "guard")
    )
    assert 'account_id = "budget-guard-build"' in squash(
        resource(text, "google_service_account", "guard_build")
    )


def test_budget_root_does_not_reference_the_single_project_root():
    for name in FILES:
        text = tf(name)
        assert "single-project" not in text
        assert "terraform_remote_state" not in text
        assert "../" not in text


def test_guard_source_is_the_function_directory():
    text = tf("guard")
    archive = squash(block(text, 'data "archive_file" "guard_source"'))
    assert 'type = "zip"' in archive
    assert 'source_dir = "${path.module}/function"' in archive
    assert 'output_path = "${path.module}/build/' in archive
    assert "__pycache__" in archive
    assert "*.pyc" in archive
    obj = squash(resource(text, "google_storage_bucket_object", "guard_source"))
    assert "data.archive_file.guard_source.output_md5" in obj
    assert "source = data.archive_file.guard_source.output_path" in obj
    bucket = squash(resource(text, "google_storage_bucket", "guard_source"))
    assert 'name = "${var.project_id}-budget-guard-source"' in bucket
    assert "force_destroy = true" in bucket
    source = squash(
        block(
            resource(text, "google_cloudfunctions2_function", "guard"), "storage_source"
        )
    )
    assert "bucket = google_storage_bucket.guard_source.name" in source
    assert "object = google_storage_bucket_object.guard_source.name" in source
    # The zip path is git-ignored (a top-level `build/` rule), the function stays tracked.
    assert (ROOT / "function" / "main.py").is_file()
    assert not (ROOT / "build").exists()


def test_apis_providers_and_outputs():
    apis = squash(tf("apis"))
    for api in (
        "billingbudgets",
        "cloudbilling",
        "pubsub",
        "cloudfunctions",
        "run",
        "cloudbuild",
        "eventarc",
        "artifactregistry",
        "logging",
    ):
        assert f'"{api}.googleapis.com"' in apis
    assert "disable_on_destroy = false" in apis
    assert 'resource "google_project_service" "budget_services"' in apis

    providers = squash(tf("providers"))
    assert 'source = "hashicorp/archive"' in providers
    assert 'source = "hashicorp/google"' in providers
    assert 'version = "~> 7.28.0"' in providers
    assert 'alias = "billing_override"' in providers
    assert 'alias = "api_bootstrap"' in providers
    assert "billing_project = var.project_id" in providers
    assert "project = var.project_id" in providers

    outputs = tf("outputs")
    for name in ("budget_topic", "budget_id", "budget_amount_try", "guard_function"):
        assert f'output "{name}"' in outputs
    assert "var.budget_amount_try" in squash(
        block(outputs, 'output "budget_amount_try"')
    )


def test_no_tf_file_names_the_project():
    raw = "\n".join(p.read_text() for p in ROOT.glob("*.tf"))
    assert not re.search(r"\b\d{10,}\b", raw), "a project number or similar"
    assert not re.search(r"\b[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}\b", raw)
    variables = tf("variables")
    project = squash(block(variables, 'variable "project_id"'))
    assert "type = string" in project
    assert "default" not in project
    for name in FILES:
        for line in tf(name).splitlines():
            if re.match(r"\s*(project|billing_project)\s*=", line):
                assert "var.project_id" in line or "data.google_project" in line
    assert not list(ROOT.glob("*.tfvars"))


@pytest.mark.parametrize("name", FILES)
def test_files_have_balanced_braces(name):
    text = tf(name)
    assert text.count("{") == text.count("}")
