"""In-memory PHI redaction logging layer (Specification §7)."""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable


class PhiRedactionFilter(logging.Filter):
    """Scrubs registered PHI terms out of log records before they are emitted.

    Redaction happens at the logging boundary: the message, its %-args, any
    ``extra={"phi": ...}`` payload, and the formatted exception traceback are
    all scrubbed. This makes the guarantee *structural* — PHI cannot reach the
    console even if a future caller logs a sensitive value or raises an
    exception whose text embeds one — provided the term has been registered.
    """

    def __init__(self) -> None:
        super().__init__()
        # Distinct compiled patterns, de-duplicated by (term, placeholder).
        self._patterns: list[tuple[re.Pattern[str], str]] = []
        self._seen: set[tuple[str, str]] = set()

    def register(self, terms: Iterable[str], placeholder: str) -> None:
        """Register PHI strings to redact with the given placeholder token.

        Idempotent: re-registering the same term is a no-op, so callers can
        register incrementally as data is discovered without unbounded growth.
        """
        for raw in terms:
            term = (raw or "").strip()
            if len(term) < 2:  # skip empties / single chars that match everything
                continue
            key = (term.casefold(), placeholder)
            if key in self._seen:
                continue
            self._seen.add(key)
            self._patterns.append(
                (re.compile(re.escape(term), re.IGNORECASE), placeholder)
            )

    def _scrub(self, text: str) -> str:
        for pattern, placeholder in self._patterns:
            text = pattern.sub(placeholder, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        # Render args into the message now, then scrub the final string. This
        # guarantees PHI passed via %-args is also caught.
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = self._scrub(message)
        record.args = None  # already merged into msg

        # Scrub the exception traceback too, then pin it as pre-formatted text
        # so the handler emits the scrubbed copy instead of re-formatting the
        # live exc_info. This lets us safely log full tracebacks.
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = self._scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)

        # Drop any structured PHI payload so it can never be formatted out.
        if hasattr(record, "phi"):
            record.phi = "[REDACTED]"
        return True


def _resolve_level(level: int | str | None) -> int:
    """Resolve a log level from an explicit arg or the LOG_LEVEL env var."""
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    if isinstance(level, str):
        return logging.getLevelName(level.strip().upper()) if level.strip() else logging.INFO
    return level


def build_logger(
    name: str = "erp-extract",
    level: int | str | None = None,
) -> tuple[logging.Logger, PhiRedactionFilter]:
    """Create a console logger guarded by the PHI redaction filter.

    Level comes from ``level`` if given, else the ``LOG_LEVEL`` env var, else
    INFO.
    """
    resolved = _resolve_level(level)
    if not isinstance(resolved, int):  # getLevelName returned "Level XYZ"
        resolved = logging.INFO

    logger = logging.getLogger(name)
    logger.setLevel(resolved)
    logger.handlers.clear()
    logger.propagate = False

    redactor = PhiRedactionFilter()

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")
    )
    # Filter on the handler => applies to everything written to the console.
    handler.addFilter(redactor)
    logger.addHandler(handler)

    return logger, redactor
