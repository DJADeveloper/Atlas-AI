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


class SourceFileWriter(Protocol):
    """Write side of a source root that Atlas itself owns.

    A watched folder belongs to the user and is read-only by contract,
    so writing is a separate, narrower capability rather than more
    methods on SourceFileStore: only a managed root (the uploads
    directory) is ever handed to an implementation of this port. The
    same path-safety rules apply — a relative path that escapes the
    root must be refused, not written.
    """

    def ensure_root(self, source_uri: str) -> None:
        """Create the managed root if it does not exist yet."""
        ...

    def write(self, source_uri: str, relative_path: str, data: bytes) -> None:
        """Place one file under the root, replacing any namesake."""
        ...


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
