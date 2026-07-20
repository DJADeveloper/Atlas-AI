"""Layered feature flags: override precedence, TTL, degradation."""

from atlas.config.feature_flags import FeatureFlags, LayeredFeatureFlags


class FakeReader:
    def __init__(self) -> None:
        self.overrides: dict[str, bool] = {}
        self.calls = 0
        self.fail = False

    async def read_all(self) -> dict[str, bool]:
        self.calls += 1
        if self.fail:
            msg = "database unreachable"
            raise ConnectionError(msg)
        return dict(self.overrides)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _flags(seed: dict[str, bool], reader: FakeReader, clock: FakeClock) -> LayeredFeatureFlags:
    return LayeredFeatureFlags(FeatureFlags(seed), reader, refresh_seconds=10.0, monotonic=clock)


async def test_db_override_wins_over_seed() -> None:
    reader = FakeReader()
    reader.overrides = {"voice": True}
    flags = _flags({"voice": False}, reader, FakeClock())
    assert await flags.is_enabled("voice") is True


async def test_seed_answers_when_no_override_and_unknown_is_off() -> None:
    reader = FakeReader()
    flags = _flags({"graph": True}, reader, FakeClock())
    assert await flags.is_enabled("graph") is True
    assert await flags.is_enabled("unknown") is False


async def test_flip_takes_effect_after_ttl_without_restart() -> None:
    reader = FakeReader()
    reader.overrides = {"voice": False}
    clock = FakeClock()
    flags = _flags({}, reader, clock)
    assert await flags.is_enabled("voice") is False

    reader.overrides = {"voice": True}
    clock.now = 5.0  # within TTL: cached value still served, no re-read
    assert await flags.is_enabled("voice") is False
    assert reader.calls == 1

    clock.now = 11.0  # past TTL: next read refreshes
    assert await flags.is_enabled("voice") is True
    assert reader.calls == 2


async def test_reader_failure_degrades_to_last_snapshot() -> None:
    reader = FakeReader()
    reader.overrides = {"voice": True}
    clock = FakeClock()
    flags = _flags({"voice": False}, reader, clock)
    assert await flags.is_enabled("voice") is True

    reader.fail = True
    clock.now = 20.0
    assert await flags.is_enabled("voice") is True  # stale beats broken

    clock.now = 40.0
    assert await flags.is_enabled("voice") is True  # still degrading, still up


async def test_snapshot_merges_seed_and_overrides() -> None:
    reader = FakeReader()
    reader.overrides = {"voice": True}
    flags = _flags({"voice": False, "graph": True}, reader, FakeClock())
    assert await flags.snapshot() == {"voice": True, "graph": True}
