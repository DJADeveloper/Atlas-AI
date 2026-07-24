"""Token cost accounting (docs/20 §8): USD per million tokens — data,
not code. Local models cost 0 by definition; their "cost" shows up as
latency and quality, which the eval harness measures.

The default table below is a starting point and MUST be verified
against the provider's current price list before money conversations
rely on it (M10 moves rates into a reviewed config file with the cost
meter's Prometheus counters). Tests pin the arithmetic against an
injected fixture, so correctness never depends on these defaults.
"""

from dataclasses import dataclass

from atlas.domain.ai.provider import Usage


@dataclass(frozen=True, slots=True)
class ModelRates:
    input_usd_per_million: float
    output_usd_per_million: float


# Verify against the live price list on every model change (M10 gate).
DEFAULT_RATES: dict[str, ModelRates] = {
    "claude-sonnet-5": ModelRates(3.00, 15.00),
    "claude-opus-4-8": ModelRates(15.00, 75.00),
    "claude-haiku-4-5-20251001": ModelRates(1.00, 5.00),
    "llama3.1:8b": ModelRates(0.0, 0.0),  # local: cost is latency, not dollars
}


class CostMeter:
    def __init__(self, rates: dict[str, ModelRates] | None = None) -> None:
        self._rates = rates if rates is not None else DEFAULT_RATES

    def cost_usd(self, model: str, usage: Usage) -> float:
        """Rounded to 6 decimal places — matching numeric(12,6) storage.
        Unknown models cost 0 rather than guessing: a wrong zero is
        visible in dashboards; a wrong nonzero silently lies."""
        rates = self._rates.get(model)
        if rates is None:
            return 0.0
        cost = (
            usage.input_tokens * rates.input_usd_per_million
            + usage.output_tokens * rates.output_usd_per_million
        ) / 1_000_000
        return round(cost, 6)
