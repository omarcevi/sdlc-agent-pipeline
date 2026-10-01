"""Cloud Run function `budget-guard`. Entry point: `stop_billing`.

Wires guard.decide to the Cloud Billing and Resource Manager clients. The cloud libraries
are imported only inside make_clients(), so tests run with fake clients.
Every message writes exactly one JSON log line on stdout.
"""

import base64
import binascii
import json
import os
import sys
from collections.abc import Callable, Sequence
from typing import Protocol

import functions_framework
from guard import PERMISSIONS, Decision, GuardConfig, decide


class Clients(Protocol):
    def billing_enabled(self, project_id: str) -> bool: ...  # projects.getBillingInfo

    def disable_billing(
        self, project_id: str
    ) -> None: ...  # projects.updateBillingInfo, ""

    def granted_permissions(  # testIamPermissions
        self, project_id: str, permissions: Sequence[str]
    ) -> set[str]: ...


def make_clients() -> Clients:
    from google.cloud import billing_v1, resourcemanager_v3

    class CloudClients:
        def __init__(self) -> None:
            self.billing = billing_v1.CloudBillingClient()
            self.projects = resourcemanager_v3.ProjectsClient()

        def billing_enabled(self, project_id: str) -> bool:
            info = self.billing.get_project_billing_info(name=f"projects/{project_id}")
            return bool(info.billing_enabled)

        def disable_billing(self, project_id: str) -> None:
            self.billing.update_project_billing_info(
                name=f"projects/{project_id}",
                project_billing_info=billing_v1.ProjectBillingInfo(
                    billing_account_name=""
                ),
            )

        def granted_permissions(
            self, project_id: str, permissions: Sequence[str]
        ) -> set[str]:
            response = self.projects.test_iam_permissions(
                request={
                    "resource": f"projects/{project_id}",
                    "permissions": list(permissions),
                }
            )
            return set(response.permissions)

    return CloudClients()


def _message_parts(event_data: dict) -> tuple[bytes, dict[str, str], str]:
    message = event_data.get("message") if isinstance(event_data, dict) else None
    if not isinstance(message, dict):
        return b"", {}, ""
    try:
        data = base64.b64decode(message.get("data") or "", validate=True)
    except (binascii.Error, ValueError, TypeError):
        data = b""
    attributes = message.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    return data, attributes, str(message.get("messageId") or "")


def _line(decision: Decision, config: GuardConfig, message_id: str) -> dict:
    return {
        "severity": "INFO",
        "decision": decision.action,
        "reason": decision.reason,
        "cost": decision.cost,
        "budget": decision.budget,
        "currency": decision.currency,
        "budget_id": config.budget_id,
        "message_id": message_id,
        "result": "no action",
    }


def _amounts(decision: Decision) -> str:
    return f"cost {decision.cost:.2f} {decision.currency} >= budget {decision.budget:.2f} {decision.currency}"


def handle(
    event_data: dict, config: GuardConfig, clients: Clients, log: Callable[[dict], None]
) -> dict:
    data, attributes, message_id = _message_parts(event_data)
    decision = decide(data, attributes, config)
    line = _line(decision, config, message_id)
    project = config.project_id
    try:
        if decision.action == "ignore":
            line.update(severity="WARNING", result="ignored")
        elif decision.action == "dry_run":
            enabled = clients.billing_enabled(project)
            granted = clients.granted_permissions(project, PERMISSIONS)
            permissions = {name: name in granted for name in PERMISSIONS}
            line.update(
                result="dry run",
                billing_enabled=enabled,
                permissions=permissions,
                message=(
                    f"DRY RUN: would disable billing on {project}: {_amounts(decision)}; "
                    f"billing enabled: {enabled}; permissions: "
                    f"deleteBillingAssignment={permissions[PERMISSIONS[0]]}, "
                    f"get={permissions[PERMISSIONS[1]]}"
                ),
            )
        elif decision.action == "disable":
            if not clients.billing_enabled(project):
                line.update(
                    result="already disabled", message=f"already disabled on {project}"
                )
            else:
                clients.disable_billing(project)
                line.update(
                    severity="CRITICAL",
                    result="billing disabled",
                    message=f"billing disabled on {project}: {_amounts(decision)}",
                )
    except Exception as error:
        line.update(
            severity="ERROR",
            result="error",
            error=type(error).__name__,
            message=f"billing guard failed on {project}: {type(error).__name__}",
        )
        log(line)
        raise
    log(line)
    return line


def _stdout_log(line: dict) -> None:
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()


@functions_framework.cloud_event
def stop_billing(cloud_event) -> None:
    config = GuardConfig.from_env(os.environ)
    handle(cloud_event.data, config, make_clients(), _stdout_log)
