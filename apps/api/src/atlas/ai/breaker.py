"""Circuit breaker per (provider, model) — docs/20 §5.3, classic
three-state design (Nygard).

Closed → Open on ≥ 5 consecutive failures, or ≥ 50% failures across the
last 20 calls inside a 60 s window. Open skips the model instantly for
30 s, then Half-open allows exactly one probe: success closes, failure
re-opens. Converts a hard vendor outage from "every chat waits three
timeouts" into "first token from the fallback in milliseconds".
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

BreakerState = Literal["closed", "open", "half_open"]

CONSECUTIVE_FAILURES_TO_OPEN = 5
WINDOW_CALLS = 20
WINDOW_SECONDS = 60.0
WINDOW_FAILURE_RATE_TO_OPEN = 0.5
OPEN_SECONDS = 30.0


@dataclass
class CircuitBreaker:
    """One breaker per (provider, model) key; `clock` is injectable so
    tests drive time explicitly."""

    clock: Callable[[], float]
    state: BreakerState = "closed"
    _consecutive_failures: int = 0
    _opened_at: float = 0.0
    _window: deque[tuple[float, bool]] = field(default_factory=lambda: deque(maxlen=WINDOW_CALLS))

    def allows(self) -> bool:
        """May a call proceed right now? Open transitions to half-open
        after the cool-off, admitting exactly one probe."""
        if self.state == "open":
            if self.clock() - self._opened_at >= OPEN_SECONDS:
                self.state = "half_open"
                return True
            return False
        return True  # closed and half_open both admit (half_open: the probe)

    def record_success(self) -> None:
        if self.state == "half_open":
            self.state = "closed"
        self._consecutive_failures = 0
        self._window.append((self.clock(), True))

    def record_failure(self) -> None:
        now = self.clock()
        self._window.append((now, False))
        self._consecutive_failures += 1
        if self.state == "half_open":
            self._trip(now)
            return
        if self._consecutive_failures >= CONSECUTIVE_FAILURES_TO_OPEN or self._window_rate_trips(
            now
        ):
            self._trip(now)

    def _trip(self, now: float) -> None:
        self.state = "open"
        self._opened_at = now
        self._consecutive_failures = 0

    def _window_rate_trips(self, now: float) -> bool:
        recent = [ok for at, ok in self._window if now - at <= WINDOW_SECONDS]
        if len(recent) < WINDOW_CALLS:
            return False
        failures = sum(1 for ok in recent if not ok)
        return failures / len(recent) >= WINDOW_FAILURE_RATE_TO_OPEN


class BreakerBoard:
    """Registry of breakers keyed by (provider, model)."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._breakers: dict[tuple[str, str], CircuitBreaker] = {}

    def for_model(self, provider: str, model: str) -> CircuitBreaker:
        key = (provider, model)
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(self._clock)
        return self._breakers[key]
