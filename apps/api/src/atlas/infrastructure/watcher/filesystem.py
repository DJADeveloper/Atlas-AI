"""Local-folder implementation of the SourceFileStore port.

Path safety from the first read (docs/25 rules, applied early): every
relative path is resolved and must stay inside the source root —
symlinked escapes yield None/exclusion, never a read outside the root.
Hashes stream in chunks so large files never sit in memory twice.
"""

import hashlib
from fnmatch import fnmatch
from pathlib import Path

from atlas.domain.knowledge.files import FileStat

_CHUNK = 1024 * 1024

_IGNORED_PARTS = frozenset({".git", "node_modules", "__pycache__", ".venv"})


def _ignored(relative_posix: str, ignore_globs: tuple[str, ...]) -> bool:
    parts = relative_posix.split("/")
    if any(part in _IGNORED_PARTS for part in parts) or parts[-1] == ".DS_Store":
        return True
    return any(fnmatch(relative_posix, glob) for glob in ignore_globs)


class LocalFileStore:
    def _root(self, source_uri: str) -> Path:
        return Path(source_uri).expanduser().resolve()

    def _safe_path(self, source_uri: str, relative_path: str) -> Path | None:
        root = self._root(source_uri)
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            return None  # symlink or ../ escape — never read outside the root
        return candidate

    def exists(self, source_uri: str) -> bool:
        return self._root(source_uri).is_dir()

    def scan(self, source_uri: str, ignore_globs: tuple[str, ...]) -> list[str]:
        root = self._root(source_uri)
        if not root.is_dir():
            return []
        found: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            relative = path.relative_to(root).as_posix()
            if _ignored(relative, ignore_globs):
                continue
            found.append(relative)
        return sorted(found)

    def stat(self, source_uri: str, relative_path: str) -> FileStat | None:
        path = self._safe_path(source_uri, relative_path)
        if path is None or not path.is_file():
            return None
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(_CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError:
            return None
        return FileStat(content_hash=digest.hexdigest(), size_bytes=size)

    def read(self, source_uri: str, relative_path: str) -> bytes | None:
        path = self._safe_path(source_uri, relative_path)
        if path is None or not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None
