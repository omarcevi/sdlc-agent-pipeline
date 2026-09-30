from datetime import date, datetime, timedelta


def parse_due(text: str, today: date | None = None) -> date:
    """Parse a due date: 'today', 'tomorrow', '+Nd', or an ISO date/datetime."""
    today = today or date.today()
    value = text.strip().lower()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value.startswith("+") and value.endswith("d") and value[1:-1].isdigit():
        return today + timedelta(days=int(value[1:-1]))
    try:
        return datetime.fromisoformat(text.strip()).date()
    except ValueError as exc:
        raise ValueError(f"unrecognised due date: {text!r}") from exc
