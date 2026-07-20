"""Feature flags: config seed + DB-backed overrides (M03).

Layering: the config seed (`FeatureFlags`, env-driven) is the base; rows
in the `feature_flags` table override it and are flippable without a
redeploy or restart (M03 acceptance: ≤ 30 s propagation — the default
refresh interval is 10 s). Unknown flags are OFF: a typo can never
silently enable behavior. If the database is unreachable, flags degrade
gracefully to the last snapshot (or the seed) rather than failing reads.
"""

import logging
import time
from collections.abc import Callable, Mapping
from typing import Protocol

_logger = logging.getLogger(__name__)


class FeatureFlags:
    """The in-memory seed layer, sourced from ATLAS_FEATURE_FLAGS."""

    def __init__(self, seed: Mapping[str, bool] | None = None) -> None:
        self._flags: dict[str, bool] = dict(seed or {})

    def is_enabled(self, flag: str) -> bool:
        return self._flags.get(flag, False)

    def snapshot(self) -> dict[str, bool]:
        """A defensive copy for diagnostics and the future settings UI."""
        return dict(self._flags)


class FlagOverridesReader(Protocol):
    """Port for the DB override source; implemented in persistence."""

    async def read_all(self) -> dict[str, bool]: ...


class LayeredFeatureFlags:
    """Seed + overrides with TTL-cached reads. Overrides win."""

    def __init__(
        self,
        seed: FeatureFlags,
        overrides: FlagOverridesReader,
        *,
        refresh_seconds: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._seed = seed
        self._overrides = overrides
        self._refresh_seconds = refresh_seconds
        self._monotonic = monotonic
        self._cache: dict[str, bool] = {}
        self._fetched_at: float | None = None

    async def is_enabled(self, flag: str) -> bool:
        await self._refresh_if_stale()
        if flag in self._cache:
            return self._cache[flag]
        return self._seed.is_enabled(flag)

    async def snapshot(self) -> dict[str, bool]:
        await self._refresh_if_stale()
        return {**self._seed.snapshot(), **self._cache}

    async def _refresh_if_stale(self) -> None:
        now = self._monotonic()
        if self._fetched_at is not None and now - self._fetched_at < self._refresh_seconds:
            return
        try:
            self._cache = await self._overrides.read_all()
        except Exception:
            # Flags must never take a request down: serve the last
            # snapshot (or the seed) and try again next interval.
            _logger.warning("feature_flags: override refresh failed", exc_info=True)
        self._fetched_at = now
