"""Application version, sourced from installed package metadata."""

from importlib import metadata

_FALLBACK_VERSION = "0.0.0+dev"


def get_version() -> str:
    """Return the installed atlas-api version, or a dev fallback.

    The fallback covers running from a source tree that has not been
    installed (e.g. some packaging intermediate states); normal `uv sync`
    editable installs always resolve real metadata.
    """
    try:
        return metadata.version("atlas-api")
    except metadata.PackageNotFoundError:
        return _FALLBACK_VERSION
