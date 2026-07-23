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
# Pipeline stages a job passes through (docs/21 §3); recorded on the job
# row for per-stage visibility, advanced forward-only.
IngestionStage = Literal["parse", "chunk", "embed", "index"]

SOURCE_KINDS: tuple[SourceKind, ...] = get_args(SourceKind)
SOURCE_STATUSES: tuple[SourceStatus, ...] = get_args(SourceStatus)
INGESTION_STATES: tuple[IngestionState, ...] = get_args(IngestionState)
INGESTION_STAGES: tuple[IngestionStage, ...] = get_args(IngestionStage)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ContentHash:
    """SHA-256 content digest, lowercase hex: a document version's raw
    bytes, or a chunk's exact embedded text (the embedding-cache key)."""

    value: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.match(self.value):
            msg = f"not a lowercase sha256 hex digest: {self.value!r}"
            raise ValueError(msg)

    def __str__(self) -> str:
        return self.value
