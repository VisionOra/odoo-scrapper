from __future__ import annotations

import logging
import re
from typing import Iterable


class PhiRedactionFilter(logging.Filter):
    """Scrubs registered PHI terms out of log records before they are emitted."""

    def __init__(self) -> None:
        super().__init__()
        # term (compiled, case-insensitive) -> replacement token
        self._patterns: list[tuple[re.Pattern[str], str]] = []

    def register(self, terms: Iterable[str], placeholder: str) -> None:
        """Register PHI strings to redact with the given placeholder token."""
        for term in terms:
            term = (term or "").strip()
            if len(term) < 2:  # skip empties / single chars that match everything
                continue
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

        # Drop any structured PHI payload so it can never be formatted out.
        if hasattr(record, "phi"):
            record.phi = "[REDACTED]"
        return True


def build_logger(
    name: str = "erp-extract",
) -> tuple[logging.Logger, PhiRedactionFilter]:
    """Create a console logger guarded by the PHI redaction filter."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
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
