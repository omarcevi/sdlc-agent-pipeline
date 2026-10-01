"""Budget guard decision: standard library only.

Cloud Billing publishes a budget's status to a Pub/Sub topic. `decide` turns one message
into an action. It says "disable" only for a real notification of this budget, from this
billing account, whose cost has reached the budget. A dry-run message (attribute
`itp_dry_run` = "1") can never give "disable".

Environment read by `GuardConfig.from_env` (set by Terraform on the function):
  GUARD_PROJECT_ID       the project id
  GUARD_BUDGET_ID        the budget's short id, as in the `budgetId` message attribute
                         (a `billingAccounts/<id>/budgets/<id>` name is reduced to its
                         last segment)
  GUARD_BILLING_ACCOUNT  the billing account id, as in the `billingAccountId` attribute
                         (a `billingAccounts/<id>` name is reduced to its id)
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

DRY_RUN_ATTRIBUTE = "itp_dry_run"
PERMISSIONS = (
    "resourcemanager.projects.deleteBillingAssignment",
    "resourcemanager.projects.get",
)


@dataclass(frozen=True)
class GuardConfig:
    project_id: str
    budget_id: str
    billing_account: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "GuardConfig":
        def read(name: str) -> str:
            value = (environ.get(name) or "").strip().rsplit("/", 1)[-1].strip()
            if not value:
                raise ValueError(f"{name} is not set")
            return value

        project_id = read("GUARD_PROJECT_ID")
        budget_id = read("GUARD_BUDGET_ID")
        billing_account = read("GUARD_BILLING_ACCOUNT")
        return cls(project_id, budget_id, billing_account)


@dataclass(frozen=True)
class Decision:
    action: Literal["ignore", "none", "dry_run", "disable"]
    reason: str
    cost: float | None = None
    budget: float | None = None
    currency: str | None = None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:  # an integer too large for a float
        return None
    return number if math.isfinite(number) else None


def _parse(data: bytes) -> tuple[float, float, str] | None:
    try:
        body = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    cost = _number(body.get("costAmount"))
    budget = _number(body.get("budgetAmount"))
    currency = body.get("currencyCode")
    if cost is None or budget is None or budget <= 0:
        return None
    if not isinstance(currency, str) or not currency:
        return None
    return cost, budget, currency


def decide(data: bytes, attributes: Mapping[str, str], config: GuardConfig) -> Decision:
    parsed = _parse(data)
    if parsed is None:
        return Decision("ignore", "malformed", None, None, None)
    cost, budget, currency = parsed
    if attributes.get("budgetId") != config.budget_id:
        return Decision("ignore", "other budget", cost, budget, currency)
    if cost < budget:
        return Decision("none", "below budget", cost, budget, currency)
    if attributes.get(DRY_RUN_ATTRIBUTE) == "1":
        return Decision("dry_run", "dry run", cost, budget, currency)
    if attributes.get("billingAccountId") != config.billing_account:
        return Decision("ignore", "not from Cloud Billing", cost, budget, currency)
    return Decision("disable", "cost at or above budget", cost, budget, currency)
