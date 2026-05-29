"""Entry point for the Odoo customer-invoice line extractor.

Run:  python main.py

The workflow itself lives in the ``odoo_extract`` package; this module only
wires configuration, logging, HIPAA-style controls, and the top-level error
handling together.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from odoo_extract.audit import PhiAuditLog, pseudonymize
from odoo_extract.config import Config
from odoo_extract.control_health import assert_redaction_active
from odoo_extract.errors import ConfigError, WorkflowError
from odoo_extract.orchestrator import run, write_output
from odoo_extract.sanitizer import build_logger


def _report_monitoring(audit: PhiAuditLog, log) -> None:
    """End-of-run monitoring summary (§164.312(b) audit controls)."""
    log.info("── PHI access monitoring summary ──")
    for action, count in sorted(audit.summary().items()):
        log.info("  %-10s : %d", action, count)
    integrity = "VERIFIED ✓" if audit.verify() else "TAMPERED ✗"
    log.info("  audit chain : %s (%s)", integrity, audit.path.name)


def main() -> int:
    log, redactor = build_logger()

    # Fail-closed control self-test BEFORE any PHI is touched.
    try:
        assert_redaction_active(log)
    except WorkflowError as exc:
        log.error("Control health check failed: %s", exc)
        return 3

    try:
        cfg = Config.load()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    run_id = uuid.uuid4().hex
    audit = PhiAuditLog(cfg.audit_log_file, run_id, log=log)
    subject = pseudonymize(cfg.target_customer)
    audit.record("RUN_START", subject=subject, detail=f"run_id={run_id}")

    try:
        invoices = asyncio.run(run(cfg, log, redactor, audit))
        out_path = write_output(invoices, cfg, log, audit)
        total_lines = sum(len(inv.lines) for inv in invoices)
        audit.record(
            "RUN_END",
            subject=subject,
            field_count=total_lines,
            outcome="SUCCESS",
            detail=f"output={out_path}",
        )
        _report_monitoring(audit, log)
        log.info("Done.")
        return 0
    except WorkflowError as exc:
        # Typed, expected failure — message is actionable; no stack needed.
        audit.record("RUN_END", subject=subject, outcome="FAILURE", detail=str(exc))
        log.error("Workflow failed: %s", exc)
        _report_monitoring(audit, log)
        return 1
    except Exception:  # noqa: BLE001 — top-level safety net
        # Unexpected: keep the full (PHI-scrubbed) traceback for diagnosis.
        audit.record("RUN_END", subject=subject, outcome="ERROR")
        log.exception("Unexpected error")
        _report_monitoring(audit, log)
        return 1


if __name__ == "__main__":
    sys.exit(main())
