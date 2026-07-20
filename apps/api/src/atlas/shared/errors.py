"""Domain error taxonomy.

Codes come from the append-only registry in `docs/12-api-specification.md`
§3.5 — never rename or reuse one (M02 risk note). The HTTP status lives
here as plain data so the taxonomy is expressible without importing any
web framework; the import-linter contract keeps it that way.
"""

from collections.abc import Mapping, Sequence
from typing import ClassVar


class AtlasError(Exception):
    """Base class for every domain error Atlas raises on purpose.

    Anything *not* an AtlasError that escapes a request is, by
    definition, a bug — the presentation layer maps it to
    ``internal_error`` without leaking internals.
    """

    code: ClassVar[str] = "internal_error"
    status: ClassVar[int] = 500
    title: ClassVar[str] = "Internal error"

    def __init__(self, detail: str, *, context: Mapping[str, object] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context: dict[str, object] = dict(context or {})


class NotFound(AtlasError):
    code = "not_found"
    status = 404
    title = "Not found"


class Conflict(AtlasError):
    code = "conflict"
    status = 409
    title = "Conflict"


class PermissionDenied(AtlasError):
    code = "permission_denied"
    status = 403
    title = "Permission denied"


class ValidationFailed(AtlasError):
    code = "validation_error"
    status = 422
    title = "Validation failed"

    def __init__(
        self,
        detail: str,
        *,
        errors: Sequence[Mapping[str, str]] = (),
        context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(detail, context=context)
        self.errors: list[dict[str, str]] = [dict(error) for error in errors]


class ProviderUnavailable(AtlasError):
    code = "provider_unavailable"
    status = 503
    title = "Provider unavailable"
