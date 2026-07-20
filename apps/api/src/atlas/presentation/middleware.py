"""Trace-id middleware: every response carries `X-Trace-Id`.

Pure ASGI (no BaseHTTPMiddleware) so streaming responses pass through
untouched. Inbound ids are honored when well-formed — a desktop client
can correlate its own logs with the sidecar's — otherwise a fresh id is
generated. The id is bound into the structlog context for the request's
lifetime, and one `request.completed` event is emitted per request.
"""

import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from atlas.observability.logging import bind_trace_id, clear_log_context

TRACE_ID_HEADER = "X-Trace-Id"

# Accept sane client-supplied ids only; anything else gets replaced.
_VALID_TRACE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

_logger = structlog.get_logger("atlas.request")


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(TRACE_ID_HEADER)
        trace_id = inbound if inbound and _VALID_TRACE_ID.match(inbound) else str(uuid4())
        clear_log_context()
        bind_trace_id(trace_id)
        started = time.perf_counter()
        status_code = 500  # if we never see response.start, an error escaped

        async def send_with_trace_header(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers[TRACE_ID_HEADER] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace_header)
        finally:
            _logger.info(
                "request.completed",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            # Deliberately no clear here: on the unhandled-exception path
            # the outermost 500 handler runs after this frame and still
            # needs the bound trace id. Isolation between requests comes
            # from the clear at request start plus task-scoped
            # contextvars (each request runs in its own task).
