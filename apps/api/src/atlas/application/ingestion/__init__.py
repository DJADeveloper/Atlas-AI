"""Ingestion use cases (M04/M05): detect, parse+chunk, embed+index."""

from atlas.application.ingestion.use_cases import (
    DetectChanges,
    DetectReport,
    EmbedDocument,
    IngestDocument,
    IngestOutcome,
    RegisterSource,
    ReindexSource,
)

__all__ = [
    "DetectChanges",
    "DetectReport",
    "EmbedDocument",
    "IngestDocument",
    "IngestOutcome",
    "RegisterSource",
    "ReindexSource",
]
