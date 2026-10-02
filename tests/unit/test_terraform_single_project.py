"""Text-level checks of deployment/terraform/single-project.

Terraform is not needed here: the files are read as text, with comments
stripped first so a commented-out line cannot satisfy a check. Failure
messages are fixed strings and never print file content (the tfvars file
holds the project id).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "deployment/terraform/single-project"


def _strip_comments(text: str) -> str:
    """Remove #, // and /* */ comments, leaving double-quoted strings intact."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif c == "#" or text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _tf(name: str) -> str:
    return _strip_comments((ROOT / name).read_text())


def _block(text: str, header: str) -> str:
    """Return the body of the block that starts with `header` (brace-matched)."""
    m = re.search(re.escape(header) + r"\s*\{", text)
    assert m, "block not found"
    depth, i = 1, m.end()
    while depth:
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return text[m.end() : i - 1]


def _has(body: str, pattern: str) -> bool:
    return re.search(pattern, body) is not None


def test_engine_is_created_scaled_to_zero_and_small():
    engine = _block(
        _tf("service.tf"), 'resource "google_vertex_ai_reasoning_engine" "app"'
    )
    spec = _block(engine, "deployment_spec")
    assert _has(spec, r"min_instances\s*=\s*0\b"), "engine min_instances must be 0"
    assert _has(spec, r"max_instances\s*=\s*1\b"), "engine max_instances must be 1"
    assert _has(spec, r"container_concurrency\s*=\s*4\b"), (
        "engine concurrency must be 4"
    )
    limits = _block(spec, "resource_limits =")
    assert _has(limits, r'cpu\s*=\s*"1"'), "engine cpu must be 1"
    assert _has(limits, r'memory\s*=\s*"4Gi"'), "engine memory must be 4Gi"


def test_logs_bucket_expires_objects_after_30_days_and_can_be_destroyed():
    bucket = _block(
        _tf("storage.tf"), 'resource "google_storage_bucket" "logs_data_bucket"'
    )
    assert _has(bucket, r"force_destroy\s*=\s*true"), "logs bucket needs force_destroy"
    rule = _block(bucket, "lifecycle_rule")
    assert _has(_block(rule, "condition"), r"age\s*=\s*30\b"), (
        "bucket rule must be age 30"
    )
    assert _has(_block(rule, "action"), r'type\s*=\s*"Delete"'), (
        "bucket rule must delete"
    )


def test_dataset_expires_partitions_after_30_days():
    text = _tf("telemetry.tf")
    dataset = _block(text, 'resource "google_bigquery_dataset" "telemetry_dataset"')
    assert _has(dataset, r"default_partition_expiration_ms\s*=\s*2592000000\b"), (
        "dataset needs 30-day default partition expiry"
    )
    assert _has(dataset, r"delete_contents_on_destroy\s*=\s*true"), (
        "dataset needs delete_contents_on_destroy"
    )
    table = _block(text, 'resource "google_bigquery_table" "genai_logs_table"')
    partitioning = _block(table, "time_partitioning")
    assert _has(partitioning, r"expiration_ms\s*=\s*2592000000\b"), (
        "logs table needs 30-day partition expiry"
    )


def test_sandbox_resources_exist():
    text = _tf("sandbox.tf")
    repo = _block(text, 'resource "google_artifact_registry_repository" "sandbox"')
    assert _has(repo, r'repository_id\s*=\s*"issue-to-pr"'), (
        "repository id must be issue-to-pr"
    )
    assert _has(repo, r'format\s*=\s*"DOCKER"'), "repository must be Docker format"
    assert _has(repo, r"location\s*=\s*var\.region\b"), "repository must use var.region"

    sa = _block(text, 'resource "google_service_account" "sandbox_caller"')
    assert _has(sa, r'account_id\s*=\s*"sandbox-caller"'), (
        "account id must be sandbox-caller"
    )

    user = _block(
        text, 'resource "google_project_iam_member" "sandbox_caller_aiplatform_user"'
    )
    assert _has(user, r'role\s*=\s*"roles/aiplatform\.user"'), (
        "caller needs aiplatform.user"
    )

    assert _has(text, r'data\s+"google_client_openid_userinfo"\s+"me"'), (
        "userinfo data missing"
    )
    for name in ("app_sa_signs_sandbox_tokens", "operator_signs_sandbox_tokens"):
        grant = _block(text, f'resource "google_service_account_iam_member" "{name}"')
        assert _has(grant, r'role\s*=\s*"roles/iam\.serviceAccountTokenCreator"'), (
            "token creator role missing"
        )
        assert _has(
            grant,
            r"service_account_id\s*=\s*google_service_account\.sandbox_caller\.name",
        ), "grant must be on sandbox_caller"
    operator = _block(
        text,
        'resource "google_service_account_iam_member" "operator_signs_sandbox_tokens"',
    )
    assert _has(operator, r"var\.operator_member"), (
        "operator grant must fall back to the variable"
    )
    assert _has(operator, r"google_client_openid_userinfo\.me\[\*\]\.email"), (
        "operator grant must use the userinfo email"
    )

    variables = _tf("variables.tf")
    var = _block(variables, 'variable "operator_member"')
    assert _has(var, r"type\s*=\s*string") and _has(var, r'default\s*=\s*""'), (
        "operator_member must be a string defaulting to empty"
    )

    outputs = _tf("outputs.tf")
    assert _has(outputs, r'output\s+"sandbox_caller_email"'), (
        "sandbox_caller_email output missing"
    )
    repo_out = _block(outputs, 'output "sandbox_image_repository"')
    assert _has(
        repo_out,
        r'value\s*=\s*"\$\{var\.region\}-docker\.pkg\.dev/\$\{var\.project_id\}/issue-to-pr"',
    ), "sandbox_image_repository value is wrong"


def test_userinfo_is_read_only_without_an_operator_member():
    text = _tf("sandbox.tf")
    data = _block(text, 'data "google_client_openid_userinfo" "me"')
    assert _has(data, r'count\s*=\s*var\.operator_member\s*==\s*""\s*\?\s*1\s*:\s*0'), (
        "userinfo data source must be conditional on operator_member"
    )


def test_new_apis_are_enabled():
    m = re.search(r"^\s*services\s*=\s*\[(.*?)\]", _tf("apis.tf"), re.M | re.S)
    assert m, "local.services not found"
    for api in ("artifactregistry.googleapis.com", "iamcredentials.googleapis.com"):
        assert f'"{api}"' in m.group(1), "required API missing from local.services"


def test_no_tf_file_names_the_project():
    tfvars = (ROOT / "vars/env.tfvars").read_text()
    m = re.search(r'^\s*project_id\s*=\s*"([^"]+)"', _strip_comments(tfvars), re.M)
    assert m, "project_id not found in vars/env.tfvars"
    project = m.group(1)
    offenders = [p.name for p in ROOT.rglob("*.tf") if project in p.read_text()]
    if offenders:
        pytest.fail("a .tf file names the project id", pytrace=False)


def test_engine_output_is_the_full_resource_name_with_the_project_number():
    # The provider's `name` is the bare engine id (seen on the first apply,
    # 2026-10-02). SANDBOX_ENGINE, teardown and template matching need the full
    # name, in the projects/<number>/... form the API returns for templates.
    body = _block(_tf("service_outputs.tf"), 'output "agent_runtime_resource_name"')
    value = re.search(r"value\s*=\s*\"([^\"]+)\"", body)
    assert value, "the output must build the full name as a string"
    assert value.group(1) == (
        "projects/${data.google_project.project.number}/locations/${var.region}"
        "/reasoningEngines/${google_vertex_ai_reasoning_engine.app.name}"
    )


def test_terraform_leaves_what_the_deploy_and_the_log_sink_change():
    # After the first deploy (2026-10-02) a plan wanted to strip the engine's
    # class_methods (written by agents-cli deploy) and replace the logs table
    # (whose schema the log sink extends), deleting its rows.
    engine = _block(
        _tf("service.tf"), 'resource "google_vertex_ai_reasoning_engine" "app"'
    )
    ignored = _block(engine, "lifecycle")
    assert "spec[0].class_methods" in ignored
    table = _block(
        _tf("telemetry.tf"), 'resource "google_bigquery_table" "genai_logs_table"'
    )
    assert re.search(
        r"ignore_changes\s*=\s*\[\s*schema\s*\]", _block(table, "lifecycle")
    )


def _roles(text: str, resource: str) -> set[str]:
    body = _block(text, f'resource "google_project_iam_member" "{resource}"')
    return set(re.findall(r'"(roles/[^"]+)"', body))


def test_wif_provider_trusts_only_this_repository():
    text = _tf("cicd.tf")
    pool = _block(text, 'resource "google_iam_workload_identity_pool" "github"')
    assert _has(pool, r'workload_identity_pool_id\s*=\s*"github-actions"')
    provider = _block(
        text, 'resource "google_iam_workload_identity_pool_provider" "github"'
    )
    assert _has(provider, r'workload_identity_pool_provider_id\s*=\s*"github-oidc"')
    oidc = _block(provider, "oidc")
    assert _has(
        oidc, r'issuer_uri\s*=\s*"https://token\.actions\.githubusercontent\.com"'
    )
    mapping = _block(provider, "attribute_mapping =")
    for key, value in {
        "google.subject": "assertion.sub",
        "attribute.repository_id": "assertion.repository_id",
        "attribute.ref": "assertion.ref",
        "attribute.environment": "assertion.environment",
        "attribute.event_name": "assertion.event_name",
    }.items():
        assert _has(mapping, rf'"{re.escape(key)}"\s*=\s*"{re.escape(value)}"'), (
            "attribute mapping incomplete"
        )
    condition = re.search(r"attribute_condition\s*=\s*(\"(?:[^\"\\]|\\.)*\")", provider)
    assert condition, "provider needs an attribute_condition"
    assert condition.group(1) == (
        '"assertion.repository_id == \\"${var.github_repository_id}\\""'
    )
    variable = _block(_tf("variables.tf"), 'variable "github_repository_id"')
    assert _has(variable, r'default\s*=\s*"1401169707"')
    assert _has(variable, r"type\s*=\s*string")
    assert _has(_block(variable, "validation"), r"\^\[0-9\]\+\$")


def test_ci_runner_roles_are_exact():
    text = _tf("cicd.tf")
    assert _has(
        _block(text, 'resource "google_service_account" "ci_runner"'),
        r'account_id\s*=\s*"ci-runner"',
    )
    assert _roles(text, "ci_runner_roles") == {
        "roles/aiplatform.user",
        "roles/serviceusage.serviceUsageConsumer",
    }


def test_deployer_roles_are_exact_and_acts_only_as_the_app():
    text = _tf("cicd.tf")
    assert _has(
        _block(text, 'resource "google_service_account" "deployer"'),
        r'account_id\s*=\s*"deployer"',
    )
    assert _roles(text, "deployer_roles") == {
        "roles/aiplatform.user",
        "roles/serviceusage.serviceUsageConsumer",
    }
    acts = _block(
        text, 'resource "google_service_account_iam_member" "deployer_acts_as_app"'
    )
    assert _has(acts, r'role\s*=\s*"roles/iam\.serviceAccountUser"')
    assert _has(acts, r"service_account_id\s*=\s*google_service_account\.app_sa\.name")
    assert _has(
        acts,
        r'member\s*=\s*"serviceAccount:\$\{google_service_account\.deployer\.email\}"',
    )
    others = re.findall(r'resource "google_service_account_iam_member" "(\w+)"', text)
    assert sorted(others) == [
        "ci_runner_wif",
        "deployer_acts_as_app",
        "deployer_wif",
    ]


def test_ci_runner_binding_is_main_only():
    body = _block(
        _tf("cicd.tf"), 'resource "google_service_account_iam_member" "ci_runner_wif"'
    )
    assert _has(body, r'role\s*=\s*"roles/iam\.workloadIdentityUser"')
    assert _has(
        body, r"service_account_id\s*=\s*google_service_account\.ci_runner\.name"
    )
    member = re.search(r"member\s*=\s*\"([^\"]+)\"", body)
    assert member
    assert member.group(1) == (
        "principalSet://iam.googleapis.com/"
        "${google_iam_workload_identity_pool.github.name}"
        "/attribute.ref/refs/heads/main"
    )


def test_deployer_binding_is_production_only():
    body = _block(
        _tf("cicd.tf"), 'resource "google_service_account_iam_member" "deployer_wif"'
    )
    assert _has(body, r'role\s*=\s*"roles/iam\.workloadIdentityUser"')
    assert _has(
        body, r"service_account_id\s*=\s*google_service_account\.deployer\.name"
    )
    member = re.search(r"member\s*=\s*\"([^\"]+)\"", body)
    assert member
    assert member.group(1) == (
        "principalSet://iam.googleapis.com/"
        "${google_iam_workload_identity_pool.github.name}"
        "/attribute.environment/production"
    )


def test_cicd_outputs():
    text = _tf("outputs.tf")
    for name, value in {
        "wif_provider": "google_iam_workload_identity_pool_provider.github.name",
        "ci_runner_email": "google_service_account.ci_runner.email",
        "deployer_email": "google_service_account.deployer.email",
    }.items():
        body = _block(text, f'output "{name}"')
        assert _has(body, rf"value\s*=\s*{re.escape(value)}")


def test_cicd_resources_wait_for_their_apis():
    assert "sts.googleapis.com" in _tf("apis.tf")
    text = _tf("cicd.tf")
    for header in re.findall(r'resource "[^"]+" "[^"]+"', text):
        assert "google_project_service.services" in _block(text, header), (
            "every CI resource must depend on the API list"
        )


def test_no_service_account_key_resource():
    for path in ROOT.glob("*.tf"):
        assert "google_service_account_key" not in _tf(path.name)
