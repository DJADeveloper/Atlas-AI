"""UUIDv7 generator: M03 acceptance — time-ordered ids (ADR-0009)."""

import time
from uuid import UUID

from atlas.shared.ids import uuid7, uuid7_unix_ms


def test_ten_thousand_ids_have_monotonic_nondecreasing_timestamps() -> None:
    """M03 acceptance criterion, verbatim: 10,000 generated ids carry
    monotonically non-decreasing millisecond timestamp prefixes."""
    ids = [uuid7() for _ in range(10_000)]
    prefixes = [uuid7_unix_ms(value) for value in ids]
    assert prefixes == sorted(prefixes)


def test_version_and_variant_bits() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_timestamp_prefix_matches_wall_clock() -> None:
    before_ms = time.time_ns() // 1_000_000
    value = uuid7()
    after_ms = time.time_ns() // 1_000_000
    assert before_ms <= uuid7_unix_ms(value) <= after_ms


def test_injected_timestamp_is_deterministic() -> None:
    a = uuid7(unix_ms=1_753_000_000_000)
    b = uuid7(unix_ms=1_753_000_000_000)
    assert uuid7_unix_ms(a) == uuid7_unix_ms(b) == 1_753_000_000_000
    assert a != b  # random tail still differs


def test_ids_are_unique() -> None:
    ids = {uuid7() for _ in range(10_000)}
    assert len(ids) == 10_000


def test_roundtrip_through_string_form() -> None:
    value = uuid7()
    assert UUID(str(value)) == value
