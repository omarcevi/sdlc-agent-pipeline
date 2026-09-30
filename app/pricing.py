"""USD per 1M tokens (input, output). Update by hand when prices change.

Gemini Flash promotional pricing ends 2027-01-01 (spec §4).
"""

PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.1-pro": (2.00, 12.00),
}
LONG_CONTEXT_THRESHOLD = 200_000
LONG_CONTEXT_PRICES: dict[str, tuple[float, float]] = {"gemini-3.1-pro": (4.00, 18.00)}
UNKNOWN_MODEL_PRICE = (5.00, 25.00)  # deliberately high so caps stay safe


def _lookup(
    name: str, table: dict[str, tuple[float, float]]
) -> tuple[float, float] | None:
    matches = [key for key in table if name.startswith(key)]
    return table[max(matches, key=len)] if matches else None


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    name = model.rsplit("/", 1)[-1]
    prices = _lookup(name, PRICES) or UNKNOWN_MODEL_PRICE
    if tokens_in > LONG_CONTEXT_THRESHOLD:
        prices = _lookup(name, LONG_CONTEXT_PRICES) or prices
    return (tokens_in * prices[0] + tokens_out * prices[1]) / 1_000_000
