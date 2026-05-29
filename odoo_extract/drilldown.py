"""Drill-down — find the target customer's invoices (Specification §8.5)."""

from __future__ import annotations

import logging

from playwright.async_api import Locator, Page, TimeoutError as PWTimeout

from .audit import PhiAuditLog
from .config import Config
from .constants import NAV_TIMEOUT_MS, is_read_response
from .errors import ExtractionError, NavigationError, RecordNotFoundError
from .extraction import extract_invoice_lines
from .models import Invoice
from .rpc_capture import RpcCapture
from .sanitizer import PhiRedactionFilter


def _target_rows(page: Page, cfg: Config) -> tuple[Locator, Locator]:
    """All list rows whose Customer cell matches the target (§5).

    Matching is scoped to the customer field cell — not an arbitrary substring
    across the whole row — so the target name cannot accidentally match another
    column or a similarly named customer elsewhere in the row. Returns
    ``(scoped, whole_row)``; the caller prefers the scoped match and falls back
    to the whole-row match if the column name differs on this build.
    """
    customer_cell = page.locator(
        'tr.o_data_row td[name="partner_id"], '
        'tr.o_data_row td.o_field_cell[name="partner_id"]'
    ).filter(has_text=cfg.target_customer)
    rows_by_cell = page.locator("tr.o_data_row").filter(has=customer_cell)
    rows_by_text = page.locator("tr.o_data_row").filter(has_text=cfg.target_customer)
    return rows_by_cell, rows_by_text


async def _resolve_rows(page: Page, cfg: Config) -> Locator:
    """Pick the scoped row set if it matches, else the whole-row fallback."""
    scoped, whole_row = _target_rows(page, cfg)
    return scoped if await scoped.count() else whole_row


async def extract_all_target_invoices(
    page: Page,
    cfg: Config,
    capture: RpcCapture,
    log: logging.Logger,
    redactor: PhiRedactionFilter,
    audit: PhiAuditLog,
    subject: str,
) -> list[Invoice]:
    """
    Find EVERY invoice for the target customer and extract each one's lines.

    Clicking a row navigates to the form view, which invalidates the list-row
    locators. We therefore (1) count matches up front, then (2) iterate by
    index, navigating back to the list between invoices so the locators are
    re-resolved fresh each time. The RPC buffer is reset before each open so a
    given extraction never reads a previous invoice's payload.
    """
    log.info("Locating target invoice rows by structural relationship.")

    rows = await _resolve_rows(page, cfg)
    try:
        await rows.first.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
    except PWTimeout as exc:
        raise RecordNotFoundError(
            "No invoice row found for the target customer in the current list. "
            "Check TARGET_CUSTOMER matches a customer that has an invoice."
        ) from exc

    total = await rows.count()
    log.info("Found %d invoice(s) for the target customer.", total)

    invoices: list[Invoice] = []
    for index in range(total):
        invoices.append(
            await _extract_one(
                page, cfg, capture, log, redactor, audit, subject, index, total
            )
        )
        # Return to the list for the next iteration (state-driven, no sleep).
        if index < total - 1:
            await _back_to_list(page, log)

    return invoices


async def _extract_one(
    page: Page,
    cfg: Config,
    capture: RpcCapture,
    log: logging.Logger,
    redactor: PhiRedactionFilter,
    audit: PhiAuditLog,
    subject: str,
    index: int,
    total: int,
) -> Invoice:
    # Re-resolve the row set each iteration (DOM was rebuilt after going back),
    # then select by index. Indexes are stable within a single rendered order.
    cur_rows = await _resolve_rows(page, cfg)
    await cur_rows.first.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
    row = cur_rows.nth(index)

    capture.reset()
    # State trigger: opening fires a web_read on account.move (intercepted).
    async with page.expect_response(is_read_response, timeout=NAV_TIMEOUT_MS):
        await row.click()
    try:
        await page.locator(".o_form_view").first.wait_for(
            state="visible", timeout=NAV_TIMEOUT_MS
        )
    except PWTimeout as exc:
        raise NavigationError("Invoice form view did not render.") from exc

    number = await _read_invoice_number(page)
    try:
        lines = await extract_invoice_lines(page, capture, log, redactor)
    except ExtractionError:
        # One empty/odd invoice shouldn't abort the whole batch.
        log.warning(
            "Invoice %d/%d yielded no lines; recording empty.", index + 1, total
        )
        lines = []
    # Audit the PHI access (§164.312(b)): WHO/WHAT/WHEN + a count — never the
    # customer name (pseudonymized subject) or any product content.
    audit.record(
        "READ",
        resource="account.move",
        resource_id=number,
        subject=subject,
        field_count=len(lines),
        outcome="SUCCESS" if lines else "EMPTY",
    )
    log.info("Extracted invoice %d/%d.", index + 1, total)  # count only
    return Invoice(invoice_number=number, lines=lines)


async def _read_invoice_number(page: Page) -> str:
    """Read the invoice's displayed number from the form (non-PHI identifier)."""
    candidates = page.locator(
        '.o_form_view [name="name"] span, .o_form_view [name="name"] input, '
        ".o_breadcrumb .active, .o_last_breadcrumb_item span"
    )
    if await candidates.count():
        text = (await candidates.first.inner_text()).strip()
        if text:
            return text
    return "UNKNOWN"


async def _back_to_list(page: Page, log: logging.Logger) -> None:
    """Navigate back to the list view via the breadcrumb (state-driven).

    The list view becoming visible — and its first data row repainting — is the
    authoritative signal. We do NOT wait on a search RPC here: Odoo's OWL client
    frequently rebuilds the list from its in-memory model with no network round
    trip, which would make an ``expect_response`` hang until timeout.
    """
    crumb = page.locator(".o_breadcrumb a, .breadcrumb-item a").first
    await crumb.click()
    await page.locator(".o_list_view, .o_list_renderer").first.wait_for(
        state="visible", timeout=NAV_TIMEOUT_MS
    )
    await page.locator("tr.o_data_row").first.wait_for(
        state="visible", timeout=NAV_TIMEOUT_MS
    )
