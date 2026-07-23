"""Ingestion use cases (M04): DetectChanges, IngestDocument, ReindexSource."""

from atlas.application.ingestion.use_cases import (
    DetectChanges,
    DetectReport,
    IngestDocument,
    IngestOutcome,
    RegisterSource,
    ReindexSource,
)

__all__ = [
    "DetectChanges",
    "DetectReport",
    "IngestDocument",
    "IngestOutcome",
    "RegisterSource",
    "ReindexSource",
]
