"""Logging pipeline: JSON-only output, trace correlation, foreign loggers."""

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from atlas.observability.logging import bind_trace_id, clear_log_context, configure_logging


@pytest.fixture
def log_buffer() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging("INFO", stream=buffer)
    clear_log_context()
    yield buffer
    clear_log_context()


def _json_lines(buffer: io.StringIO) -> list[dict[str, object]]:
    lines = [line for line in buffer.getvalue().splitlines() if line]
    return [json.loads(line) for line in lines]


def test_events_render_as_one_json_object_per_line(log_buffer: io.StringIO) -> None:
    logger = structlog.get_logger("atlas.test")
    logger.info("first", key="value")
    logger.info("second")
    events = _json_lines(log_buffer)
    assert [event["event"] for event in events] == ["first", "second"]
    assert events[0]["key"] == "value"
    assert events[0]["level"] == "info"
    assert "timestamp" in events[0]


def test_bound_trace_id_appears_on_events(log_buffer: io.StringIO) -> None:
    bind_trace_id("trace-123")
    structlog.get_logger("atlas.test").info("with-trace")
    clear_log_context()
    structlog.get_logger("atlas.test").info("without-trace")
    with_trace, without_trace = _json_lines(log_buffer)
    assert with_trace["trace_id"] == "trace-123"
    assert "trace_id" not in without_trace


def test_foreign_stdlib_loggers_render_as_json(log_buffer: io.StringIO) -> None:
    bind_trace_id("trace-456")
    logging.getLogger("uvicorn.error").warning("foreign event")
    (event,) = _json_lines(log_buffer)
    assert event["event"] == "foreign event"
    assert event["level"] == "warning"
    assert event["trace_id"] == "trace-456"


def test_exceptions_stay_inside_the_json_envelope(log_buffer: io.StringIO) -> None:
    logger = structlog.get_logger("atlas.test")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed")
    lines = [line for line in log_buffer.getvalue().splitlines() if line]
    assert len(lines) == 1, "traceback must not escape the JSON envelope"
    event = json.loads(lines[0])
    assert event["event"] == "failed"
    assert event["level"] == "error"
    assert "ValueError" in str(event["exception"])
