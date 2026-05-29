"""Entry point for the Odoo customer-invoice line extractor.

Run:  python main.py

The workflow itself lives in the ``odoo_extract`` package; this module only
wires configuration, logging, and the top-level error handling together.
"""

from __future__ import annotations

import asyncio
import sys

from odoo_extract.config import Config
from odoo_extract.errors import ConfigError, WorkflowError
from odoo_extract.orchestrator import run, write_output
from odoo_extract.sanitizer import build_logger


def main() -> int:
    log, redactor = build_logger()
    try:
        cfg = Config.load()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    try:
        invoices = asyncio.run(run(cfg, log, redactor))
        write_output(invoices, cfg, log)
        log.info("Done.")
        return 0
    except WorkflowError as exc:
        log.error("Workflow failed: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 — top-level safety net
        log.error("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
