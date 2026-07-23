"""Filesystem port for source content.

The knowledge context reads files only through this port: local folders
now (`atlas.infrastructure.watcher.filesystem`), connector-backed
stores at M22. Paths are always source-relative; implementations MUST
refuse escapes from the source root (canonicalization, symlink checks —
docs/25 path-safety rules apply from the first read, not from S3).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FileStat:
    """Streaming hash + size, computed without holding the file in memory."""

    content_hash: str  # sha256 lowercase hex
    size_bytes: int


class SourceFileStore(Protocol):
    def exists(self, source_uri: str) -> bool:
        """Whether the source root exists and is readable."""
        ...

    def scan(self, source_uri: str, ignore_globs: tuple[str, ...]) -> list[str]:
        """All regular files under the root as sorted relative paths."""
        ...

    def stat(self, source_uri: str, relative_path: str) -> FileStat | None:
        """Hash+size of one file; None when it no longer exists."""
        ...

    def read(self, source_uri: str, relative_path: str) -> bytes | None:
        """Raw bytes of one file; None when it no longer exists."""
        ...
