from __future__ import annotations

import logging
import re

SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((x-api-key|api-key):\s*)[^\s,;]+"),
    re.compile(r"(?i)((api[_-]?key|token|password|secret)=)[^&\s,;]+"),
    re.compile(
        r"(?i)((X-Amz-Signature|X-Amz-Credential|AWSAccessKeyId|"
        r"access[_-]?key[_-]?id|signature)=)[^&\s,;]+"
    ),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
    return redacted


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_text(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_text(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def _has_redaction_filter(filters: list[logging.Filter]) -> bool:
    return any(isinstance(filter_obj, RedactionFilter) for filter_obj in filters)


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO))
    root = logging.getLogger()
    if not _has_redaction_filter(root.filters):
        root.addFilter(RedactionFilter())
    for handler in root.handlers:
        if not _has_redaction_filter(handler.filters):
            handler.addFilter(RedactionFilter())
    for logger_name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        if not _has_redaction_filter(logger.filters):
            logger.addFilter(RedactionFilter())
        for handler in logger.handlers:
            if not _has_redaction_filter(handler.filters):
                handler.addFilter(RedactionFilter())
