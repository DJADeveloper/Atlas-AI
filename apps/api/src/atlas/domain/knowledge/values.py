"""Value objects and canonical enumerations for the knowledge context.

These are the authoritative definitions; the database CHECK constraints in
`docs/11-database-schema.md` §1.3 merely mirror them.
"""

import re
from dataclasses import dataclass
from typing import Literal, get_args

SourceKind = Literal["folder", "git", "drive", "notion", "slack", "email"]
SourceStatus = Literal["active", "paused", "error"]
IngestionState = Literal["pending", "running", "succeeded", "failed", "skipped"]

SOURCE_KINDS: tuple[SourceKind, ...] = get_args(SourceKind)
SOURCE_STATUSES: tuple[SourceStatus, ...] = get_args(SourceStatus)
INGESTION_STATES: tuple[IngestionState, ...] = get_args(IngestionState)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ContentHash:
    """SHA-256 of a document version's raw bytes, lowercase hex."""

    value: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.match(self.value):
            msg = f"not a lowercase sha256 hex digest: {self.value!r}"
            raise ValueError(msg)

    def __str__(self) -> str:
        return self.value
