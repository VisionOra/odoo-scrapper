"""Orchestrator (Specification §8) — wires the workflow together."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

from .auth import authenticate
from .config import Config
from .constants import NAV_TIMEOUT_MS
from .drilldown import extract_all_target_invoices
from .models import Invoice
from .navigation import apply_posted_filter, clear_default_filters, open_invoicing
from .rpc_capture import RpcCapture


async def run(cfg: Config, log, redactor) -> list[Invoice]:
    # Register PHI terms up front so anything logged later is auto-redacted.
    redactor.register([cfg.target_customer], "[REDACTED_CUSTOMER]")

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=str(Path(cfg.user_data_dir).resolve()),
            headless=cfg.headless,
            args=["--no-sandbox"],
        )
        context.set_default_timeout(NAV_TIMEOUT_MS)
        await context.tracing.start(screenshots=True, snapshots=True)

        page = context.pages[0] if context.pages else await context.new_page()

        capture = RpcCapture(log)
        capture.attach(page)

        try:
            # Demonstration of in-memory sanitization (§7): we deliberately log
            # the REAL customer name; the console must show it redacted.
            log.info("Processing customer %s", cfg.target_customer)

            await authenticate(page, cfg, log)
            await open_invoicing(page, cfg, log)
            await clear_default_filters(page, log)
            await apply_posted_filter(page, log)
            invoices = await extract_all_target_invoices(page, cfg, capture, log)

            # Register every extracted product name so any later log line is
            # scrubbed (PHI guardrail across all invoices).
            all_products = [ln.product_name for inv in invoices for ln in inv.lines]
            redactor.register(all_products, "[REDACTED_PRODUCT]")
            await context.tracing.stop()
            return invoices
        except Exception:
            # Persist debugging artifacts locally only (§10).
            await _dump_artifacts(context, page, log)
            raise
        finally:
            await context.close()


async def _dump_artifacts(context: BrowserContext, page: Page, log) -> None:
    try:
        await page.screenshot(path="failure.png", full_page=True)
        await context.tracing.stop(path="trace.zip")
        log.info("Saved failure artifacts (failure.png, trace.zip).")
    except Exception:
        pass


def write_output(invoices: list[Invoice], cfg: Config, log) -> None:
    """Serialize REAL values straight to disk — never through the logger (§7).

    Output is an array of invoices, each with its own lines, so multiple
    invoices for the same customer are all represented distinctly.
    """
    payload = [asdict(inv) for inv in invoices]
    Path(cfg.output_file).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    total_lines = sum(len(inv.lines) for inv in invoices)
    # Counts only — never content.
    log.info(
        "Wrote %d invoice(s) / %d line(s) to output file.",
        len(invoices),
        total_lines,
    )
