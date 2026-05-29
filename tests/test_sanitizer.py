"""Unit tests for the in-memory PHI redaction layer (§7)."""

from __future__ import annotations

import logging

import pytest

from odoo_extract.sanitizer import PhiRedactionFilter, build_logger


@pytest.fixture
def logger_and_buffer(caplog):
    log, redactor = build_logger("erp-extract-test")
    return log, redactor, caplog


def _emit(log: logging.Logger, redactor: PhiRedactionFilter, record_fn) -> str:
    """Capture what the handler's filter produced for a single log call."""
    captured: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage() if record.msg else "")

    sink = _Sink()
    sink.addFilter(redactor)  # same filter instance the console uses
    log.addHandler(sink)
    try:
        record_fn()
    finally:
        log.removeHandler(sink)
    return "\n".join(captured)


def test_customer_name_redacted_via_args():
    log, redactor = build_logger("t1")
    redactor.register(["Deco Addict"], "[REDACTED_CUSTOMER]")
    out = _emit(
        log, redactor, lambda: log.info("Processing customer %s", "Deco Addict")
    )
    assert "Deco Addict" not in out
    assert "[REDACTED_CUSTOMER]" in out


def test_redaction_is_case_insensitive():
    log, redactor = build_logger("t2")
    redactor.register(["Deco Addict"], "[REDACTED_CUSTOMER]")
    out = _emit(log, redactor, lambda: log.info("got DECO addict here"))
    assert "DECO addict" not in out
    assert "[REDACTED_CUSTOMER]" in out


def test_product_names_redacted():
    log, redactor = build_logger("t3")
    redactor.register(["Acoustic Bloc Screens"], "[REDACTED_PRODUCT]")
    out = _emit(log, redactor, lambda: log.warning("line: Acoustic Bloc Screens x2"))
    assert "Acoustic Bloc Screens" not in out
    assert "[REDACTED_PRODUCT]" in out


def test_short_and_empty_terms_ignored():
    redactor = PhiRedactionFilter()
    redactor.register(["", " ", "a"], "[X]")  # all too short / empty
    log, _ = build_logger("t4")
    out = _emit(log, redactor, lambda: log.info("a quick brown fox"))
    assert out.strip().endswith("a quick brown fox")  # nothing redacted


def test_register_is_idempotent():
    redactor = PhiRedactionFilter()
    redactor.register(["Deco Addict"], "[REDACTED_CUSTOMER]")
    redactor.register(["Deco Addict"], "[REDACTED_CUSTOMER]")  # dup
    redactor.register(["deco addict"], "[REDACTED_CUSTOMER]")  # case dup
    assert len(redactor._patterns) == 1


def test_phi_extra_payload_dropped():
    log, redactor = build_logger("t5")
    captured: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    sink = _Sink()
    sink.addFilter(redactor)
    log.addHandler(sink)
    log.info("count only", extra={"phi": {"name": "Deco Addict"}})
    log.removeHandler(sink)

    assert captured and getattr(captured[0], "phi") == "[REDACTED]"


def test_exception_traceback_is_scrubbed():
    """A raised exception whose text embeds PHI must not leak via the trace."""
    log, redactor = build_logger("t6")
    redactor.register(["Deco Addict"], "[REDACTED_CUSTOMER]")

    captured: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # exc_text is what the filter pinned; format the whole record.
            captured.append(self.format(record))

    sink = _Sink()
    sink.setFormatter(logging.Formatter("%(message)s"))
    sink.addFilter(redactor)
    log.addHandler(sink)
    try:
        raise ValueError("failure for customer Deco Addict")
    except ValueError:
        log.exception("Unexpected error")
    log.removeHandler(sink)

    joined = "\n".join(captured)
    assert "Deco Addict" not in joined
    assert "[REDACTED_CUSTOMER]" in joined
    assert "ValueError" in joined  # traceback still present, just scrubbed
