"""Routers after LLM nodes (LLM nodes cannot emit routes themselves)."""

from google.adk.events.event import Event

from app.schemas import Plan, Review

MAX_REVIEW_RETURNS = 2


def route_plan(node_input: Plan) -> Event:
    if node_input.actionable:
        return Event(output=node_input, route="actionable")
    reason = node_input.decline_reason or "planner declined without giving a reason"
    return Event(
        output=node_input,
        route="declined",
        state={"failure": {"kind": "declined", "reason": reason}},
    )


def route_review(node_input: Review, review_rounds: int) -> Event:
    if node_input.verdict == "approve":
        return Event(output=node_input, route="approve")
    rounds = review_rounds + 1
    if rounds > MAX_REVIEW_RETURNS:
        return Event(
            output=node_input,
            route="exhausted",
            state={
                "review_rounds": rounds,
                "failure": {
                    "kind": "agent",
                    "reason": f"reviewer still requested changes after {MAX_REVIEW_RETURNS} rounds",
                },
            },
        )
    return Event(output=node_input, route="changes", state={"review_rounds": rounds})
