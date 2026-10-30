from __future__ import annotations

import logging
import sys

from evalplat.core.logging import (
    SensitiveDataFilter,
    _redact_structlog_event,
    setup_logging,
)


def test_standard_log_filter_redacts_bearer_tokens():
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider rejected %s",
        args=("Bearer abcdefghijklmnop",),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record) is True
    assert record.getMessage() == "provider rejected Bearer [REDACTED]"


def test_structlog_processor_redacts_sensitive_fields():
    event = _redact_structlog_event(
        None,
        "error",
        {"event": "request failed", "Authorization": "Bearer abcdefghijklmnop"},
    )

    assert event["Authorization"] == "[REDACTED]"


def test_setup_logging_installs_record_factory_redaction():
    setup_logging()
    logger = logging.getLogger("test.logging")

    record = logger.makeRecord(
        logger.name,
        logging.ERROR,
        __file__,
        1,
        "request failed: %s",
        ("Bearer abcdefghijklmnop",),
        None,
    )

    assert record.getMessage() == "request failed: Bearer [REDACTED]"


def test_standard_log_filter_redacts_exception_traceback():
    try:
        raise RuntimeError("upstream returned Bearer abcdefghijklmnop")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    SensitiveDataFilter().filter(record)
    rendered = logging.Formatter().format(record)
    assert "abcdefghijklmnop" not in rendered
    assert "Bearer [REDACTED]" in rendered


def test_access_log_filter_preserves_uvicorn_argument_shape():
    args = ("127.0.0.1:1234", "GET /healthz HTTP/1.1", 200, 1.2, "")
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s" %s',
        args=args,
        exc_info=None,
    )

    SensitiveDataFilter().filter(record)
    assert isinstance(record.args, tuple)
    assert len(record.args) == len(args)


def test_standard_log_filter_redacts_structured_short_credentials():
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider response: %s",
        args=({"api_key": "short-secret", "password": "pw"},),
        exc_info=None,
    )

    SensitiveDataFilter().filter(record)
    rendered = record.getMessage()
    assert "short-secret" not in rendered
    assert "'api_key': '[REDACTED]'" in rendered
    assert "'password': '[REDACTED]'" in rendered
