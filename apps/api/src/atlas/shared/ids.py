"""Application-side UUIDv7 generation (ADR-0009, RFC 9562).

Postgres has no native UUIDv7 generator and the database must never mint
identities anyway (docs/11 §1.1): the domain creates ids before persistence,
tests inject deterministic factories, and time-ordered ids keep append-heavy
B-trees right-edge-friendly.

Layout (RFC 9562 §5.7): 48-bit big-endian unix timestamp in milliseconds,
4-bit version (7), 12 random bits, 2-bit variant (0b10), 62 random bits.
"""

import secrets
import time
from collections.abc import Callable
from uuid import UUID

IdFactory = Callable[[], UUID]

_VERSION_7 = 0x7
_VARIANT_RFC = 0b10


def uuid7(*, unix_ms: int | None = None) -> UUID:
    """Mint a UUIDv7. ``unix_ms`` is injectable for deterministic tests."""
    if unix_ms is None:
        unix_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (
        ((unix_ms & 0xFFFF_FFFF_FFFF) << 80)
        | (_VERSION_7 << 76)
        | (rand_a << 64)
        | (_VARIANT_RFC << 62)
        | rand_b
    )
    return UUID(int=value)


def uuid7_unix_ms(value: UUID) -> int:
    """Extract the millisecond timestamp prefix from a UUIDv7."""
    return value.int >> 80
