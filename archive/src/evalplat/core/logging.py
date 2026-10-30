"""structlog JSON 日志配置。

不记录 Authorization header，防止密钥泄露到日志。
"""

import logging
import traceback
import structlog


def _redact_structlog_event(_logger, _method_name, event_dict):
    """Apply the same field/text redaction to structured log events."""

    from evalplat.services.security_service import redact_sensitive_value

    return redact_sensitive_value(event_dict)


class SensitiveDataFilter(logging.Filter):
    """Redact standard-library log messages before handlers emit them."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_log_record(record)
        return True


def _redact_log_record(record: logging.LogRecord) -> None:
    """Redact rendered message and traceback text without dropping the stack."""

    from evalplat.services.security_service import (
        redact_sensitive_text,
        redact_sensitive_value,
    )

    # Uvicorn's access formatter unpacks a fixed five-item ``args`` tuple;
    # preserve that contract while redacting each value in place.
    if record.name == "uvicorn.access" and isinstance(record.args, tuple):
        record.args = tuple(redact_sensitive_value(item) for item in record.args)
        return

    if isinstance(record.args, tuple):
        record.args = tuple(redact_sensitive_value(item) for item in record.args)
    elif isinstance(record.args, dict):
        record.args = redact_sensitive_value(record.args)
    if isinstance(record.msg, (dict, list, tuple)):
        record.msg = redact_sensitive_value(record.msg)
    try:
        rendered = record.getMessage()
    except Exception:
        rendered = str(record.msg)
    record.msg = redact_sensitive_text(rendered)
    record.args = ()
    if (
        isinstance(record.exc_info, tuple)
        and len(record.exc_info) == 3
        and record.exc_info[0] is not None
    ):
        try:
            traceback_text = "".join(traceback.format_exception(*record.exc_info))
            # Formatter appends exc_text when present, so retain the original
            # tuple for stack metadata while replacing only its rendered text.
            record.exc_text = redact_sensitive_text(traceback_text)
        except Exception:
            record.exc_text = None


def _install_log_record_redaction() -> None:
    """Redact standard log records even when a framework owns the handlers."""

    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_evalplat_redacting", False):
        return

    def redacting_factory(*args, **kwargs):
        record = current_factory(*args, **kwargs)
        _redact_log_record(record)
        return record

    redacting_factory._evalplat_redacting = True
    logging.setLogRecordFactory(redacting_factory)


def setup_logging(level: str = "INFO") -> None:
    """配置 structlog JSON 输出 + 标准 logging。"""

    _install_log_record_redaction()
    redaction_filter = SensitiveDataFilter()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if not any(isinstance(item, SensitiveDataFilter) for item in handler.filters):
            handler.addFilter(redaction_filter)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_structlog_event,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
