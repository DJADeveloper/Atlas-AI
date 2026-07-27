"""Ingestion use cases (M04/M05): detect, parse+chunk, embed+index."""

from atlas.application.ingestion.uploads import (
    UPLOADS_SOURCE_NAME,
    IncomingFile,
    RejectedFile,
    UploadFiles,
    UploadReport,
)
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
    "UPLOADS_SOURCE_NAME",
    "DetectChanges",
    "DetectReport",
    "EmbedDocument",
    "IncomingFile",
    "IngestDocument",
    "IngestOutcome",
    "RegisterSource",
    "ReindexSource",
    "RejectedFile",
    "UploadFiles",
    "UploadReport",
]
