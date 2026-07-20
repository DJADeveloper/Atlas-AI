"""Problem+json rendering: every error leaves the API in the same envelope.

Shape per `docs/12-api-specification.md` §3.5 (RFC 9457): `type`, `title`,
`status`, `code`, `detail`, `trace_id`, plus `errors[]` on validation
failures and `context{}` when machine-usable follow-up data exists.
Handlers set `X-Trace-Id` themselves because the unhandled-exception
response is produced outside the trace middleware's send path.
"""

from http import HTTPStatus

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from atlas.observability.logging import current_trace_id
from atlas.shared.errors import AtlasError, ValidationFailed

PROBLEM_TYPE_BASE = "https://atlas.dev/errors/"
PROBLEM_MEDIA_TYPE = "application/problem+json"

# Starlette-raised statuses → registry codes. Anything unmapped is either
# a server fault (internal_error) or a generic client fault (bad_request).
_HTTP_STATUS_CODES: dict[int, tuple[str, str]] = {
    401: ("invalid_token", "Invalid token"),
    403: ("permission_denied", "Permission denied"),
    404: ("not_found", "Not found"),
    405: ("method_not_allowed", "Method not allowed"),
    409: ("conflict", "Conflict"),
    422: ("validation_error", "Validation failed"),
    429: ("rate_limited", "Rate limited"),
    503: ("provider_unavailable", "Provider unavailable"),
}

_logger = structlog.get_logger("atlas.errors")


def problem_response(
    *,
    code: str,
    title: str,
    status_code: int,
    detail: str,
    errors: list[dict[str, str]] | None = None,
    context: dict[str, object] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_BASE}{code}",
        "title": title,
        "status": status_code,
        "code": code,
        "detail": detail,
    }
    trace_id = current_trace_id()
    if trace_id is not None:
        body["trace_id"] = trace_id
    if errors:
        body["errors"] = errors
    if context:
        body["context"] = context
    headers = {"X-Trace-Id": trace_id} if trace_id is not None else None
    return JSONResponse(
        body, status_code=status_code, media_type=PROBLEM_MEDIA_TYPE, headers=headers
    )


async def _handle_atlas_error(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AtlasError):  # registration guarantees this
        raise exc
    errors = exc.errors if isinstance(exc, ValidationFailed) else None
    return problem_response(
        code=exc.code,
        title=exc.title,
        status_code=exc.status,
        detail=exc.detail,
        errors=errors,
        context=exc.context or None,
    )


async def _handle_request_validation(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # registration guarantees this
        raise exc
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": str(error["msg"]),
        }
        for error in exc.errors()
    ]
    return problem_response(
        code="validation_error",
        title="Validation failed",
        status_code=422,
        detail="Request failed validation; see errors[].",
        errors=errors,
    )


async def _handle_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # registration guarantees this
        raise exc
    fallback = (
        ("internal_error", "Internal error")
        if exc.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        else ("bad_request", "Bad request")
    )
    code, title = _HTTP_STATUS_CODES.get(exc.status_code, fallback)
    return problem_response(
        code=code, title=title, status_code=exc.status_code, detail=str(exc.detail)
    )


async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
    # Full traceback to the JSON logs; the body carries only the handle.
    _logger.exception("request.unhandled_error")
    return problem_response(
        code="internal_error",
        title="Internal error",
        status_code=500,
        detail="An unexpected error occurred; report it with the trace_id.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AtlasError, _handle_atlas_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected)
