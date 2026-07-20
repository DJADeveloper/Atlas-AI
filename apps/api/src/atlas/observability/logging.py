"""Structured logging: one JSON object per line, correlated by trace_id.

This is the structlog half of ADR-0010 (OTel spans arrive at M10). Two
rules make the M02 acceptance criteria hold:

1. Every process configures logging exactly once, at its composition
   root, via :func:`configure_logging`. Foreign stdlib loggers (uvicorn,
   sqlalchemy) are routed through the same JSON formatter, so *all*
   output is machine-parseable — no interleaved plain-text lines.
2. Request-scoped correlation flows through :func:`bind_trace_id` — the
   single helper both the HTTP middleware and (from M04) the worker
   bootstrap use, so trace ids can never diverge between contexts.
"""

import logging
import sys
from typing import IO

import structlog

_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]

_FOREIGN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy")


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> None:
    """Install the JSON pipeline on the root logger. Idempotent.

    Args:
        level: Root log level name.
        stream: Output stream; defaults to the current ``sys.stdout``.
            Tests inject a buffer here — the same DI principle as
            everything else wired at the composition root.
    """
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn installs its own plain-text handlers; strip them so its
    # records propagate to the root JSON handler instead.
    for name in _FOREIGN_LOGGERS:
        foreign = logging.getLogger(name)
        foreign.handlers.clear()
        foreign.propagate = True


def bind_trace_id(trace_id: str) -> None:
    """Bind the request/task trace id into log context (contextvar-scoped)."""
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


def clear_log_context() -> None:
    """Reset request-scoped context; call at request/task boundaries."""
    structlog.contextvars.clear_contextvars()
