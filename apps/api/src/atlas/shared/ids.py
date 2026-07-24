"""Application-side UUIDv7 generation (ADR-0009, RFC 9562).

Postgres has no native UUIDv7 generator and the database must never mint
identities anyway (docs/11 §1.1): the domain creates ids before persistence,
tests inject deterministic factories, and time-ordered ids keep append-heavy
B-trees right-edge-friendly.

Layout (RFC 9562 §5.7): 48-bit big-endian unix timestamp in milliseconds,
4-bit version (7), 12-bit monotonic counter, 2-bit variant (0b10), 62 random
bits.

**Monotonic within the process** (RFC 9562 §6.2, fixed-length counter):
`rand_a` carries a per-millisecond sequence instead of random bits, so ids
minted in the same millisecond still sort in mint order. This is not a
nicety — "UUIDv7 PK order = chronological order" is a schema invariant
(docs/11 §2.4): a chat exchange mints its user and assistant messages
microseconds apart, and the conversation summary watermark compares ids.
Counter overflow (4,096 ids in one ms) borrows the next millisecond; a
backwards clock step keeps counting from the high-water mark.
"""

import secrets
import threading
import time
from collections.abc import Callable
from uuid import UUID

IdFactory = Callable[[], UUID]

_VERSION_7 = 0x7
_VARIANT_RFC = 0b10
_COUNTER_MAX = 0xFFF


class _MonotonicState:
    __slots__ = ("last_ms", "seq")

    def __init__(self) -> None:
        self.last_ms = -1
        self.seq = 0


_state = _MonotonicState()
_lock = threading.Lock()


def _build(unix_ms: int, rand_a: int, rand_b: int) -> UUID:
    value = (
        ((unix_ms & 0xFFFF_FFFF_FFFF) << 80)
        | (_VERSION_7 << 76)
        | (rand_a << 64)
        | (_VARIANT_RFC << 62)
        | rand_b
    )
    return UUID(int=value)


def uuid7(*, unix_ms: int | None = None) -> UUID:
    """Mint a UUIDv7, monotonic within this process.

    ``unix_ms`` is injectable for deterministic tests; explicit
    timestamps bypass the monotonicity state (the caller owns ordering)
    and carry random ``rand_a`` bits exactly as before.
    """
    if unix_ms is not None:
        return _build(unix_ms, secrets.randbits(12), secrets.randbits(62))
    now_ms = time.time_ns() // 1_000_000
    with _lock:
        if now_ms <= _state.last_ms:
            _state.seq += 1
            if _state.seq > _COUNTER_MAX:  # 4,096 in one ms: borrow the next
                _state.last_ms += 1
                _state.seq = 0
        else:
            _state.last_ms = now_ms
            _state.seq = 0
        stamped_ms = _state.last_ms
        seq = _state.seq
    return _build(stamped_ms, seq, secrets.randbits(62))


def uuid7_unix_ms(value: UUID) -> int:
    """Extract the millisecond timestamp prefix from a UUIDv7."""
    return value.int >> 80
