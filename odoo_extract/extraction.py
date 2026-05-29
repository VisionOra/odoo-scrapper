"""ExtractionModule — hybrid RPC + DOM (Specification §6, §8.6)."""

from __future__ import annotations

import logging

from playwright.async_api import Locator, Page, TimeoutError as PWTimeout

from .constants import FORM_TIMEOUT_MS
from .errors import ExtractionError
from .models import InvoiceLine
from .parsing import name_of, to_float
from .rpc_capture import RpcCapture
from .sanitizer import PhiRedactionFilter


async def extract_invoice_lines(
    page: Page,
    capture: RpcCapture,
    log: logging.Logger,
    redactor: PhiRedactionFilter,
) -> list[InvoiceLine]:
    # Activate the Invoice Lines tab by its accessible NAME, not index (§5).
    await _open_invoice_lines_tab(page, log)

    # Primary path: parse the intercepted account.move payload.
    rpc_lines = _lines_from_rpc(capture)

    # Fallback path: resilient DOM scrape, scoped to the lines table.
    dom_lines = await _lines_from_dom(page)

    primary, fallback = (rpc_lines, dom_lines)
    chosen = primary if primary else fallback

    if not chosen:
        raise ExtractionError(
            "No invoice lines could be extracted (RPC and DOM empty)."
        )

    # Register product names with the redactor the MOMENT they exist in memory,
    # *before* any further log call. This makes the PHI guarantee structural:
    # even an accidental future log of a product name is scrubbed (§7).
    redactor.register([ln.product_name for ln in chosen], "[REDACTED_PRODUCT]")

    # Reconciliation: warn (sanitized) if the two sources disagree on count.
    if primary and fallback and len(primary) != len(fallback):
        log.warning(
            "RPC/DOM row-count mismatch (rpc=%d dom=%d); using RPC.",
            len(primary),
            len(fallback),
        )

    source = "RPC interception" if primary else "DOM fallback"
    log.info("Extracted %d invoice line(s) via %s.", len(chosen), source)
    return chosen


def _lines_table(page: Page) -> Locator:
    """Version-tolerant locator for the invoice-lines field/table (§5).

    Odoo 19 may drop the legacy `.o_field_x2many` wrapper class; the stable
    contract is the `name="invoice_line_ids"` field attribute. We match the
    field by name, falling back to any list table that follows it.
    """
    return page.locator(
        '[name="invoice_line_ids"], .o_field_x2many[name="invoice_line_ids"]'
    ).first


async def _open_invoice_lines_tab(page: Page, log: logging.Logger) -> None:
    # Tab label is "Invoice Lines" on most builds. Locate by role+name; the
    # underlying notebook page then becomes visible (auto-wait, no sleep).
    tab = page.get_by_role("tab", name="Invoice Lines")
    if await tab.count() == 0:
        # Some versions label it differently / use a link element.
        tab = page.locator("a.nav-link, .o_notebook .nav-link").filter(
            has_text="Invoice Lines"
        )

    if await tab.count():
        # If the tab isn't already the active one, click it.
        try:
            await tab.first.click()
        except PWTimeout:
            pass  # already active / not clickable — proceed to verify table

    # Verify the lines table is present. It is often the default-visible page,
    # so this succeeds whether or not we needed to click. We do NOT hard-fail
    # here: the RPC interception already holds the data; the DOM is a fallback.
    table = _lines_table(page)
    try:
        await table.wait_for(state="visible", timeout=FORM_TIMEOUT_MS)
        log.info("Invoice Lines tab active; lines table visible.")
    except PWTimeout:
        log.warning("Invoice Lines table not located in DOM; relying on RPC capture.")


def _lines_from_rpc(capture: RpcCapture) -> list[InvoiceLine]:
    rec = capture.last_move_record
    if not rec:
        return []
    raw_lines = rec.get("invoice_line_ids") or []
    # web_read returns nested dicts; older read returns just ids (unusable here).
    if raw_lines and not isinstance(raw_lines[0], dict):
        return []

    lines: list[InvoiceLine] = []
    for ln in raw_lines:
        dtype = ln.get("display_type")
        if dtype not in (False, None, "", "product"):
            continue
        subtotal = to_float(ln.get("price_subtotal"))
        total = to_float(ln.get("price_total"))
        tax = round(total - subtotal, 2)
        # Product name: many2one display name, then the line label, then a
        # placeholder if the line has neither (e.g. an empty test line).
        name = name_of(ln.get("product_id"))
        if not name:
            label = ln.get("name")
            name = label if isinstance(label, str) and label else "(no product)"
        lines.append(
            InvoiceLine(
                product_name=name,
                quantity=to_float(ln.get("quantity")),
                unit_price=to_float(ln.get("price_unit")),
                tax_amount=tax,
            )
        )
    return lines


async def _lines_from_dom(page: Page) -> list[InvoiceLine]:
    """Scrape the rendered lines table by column FIELD NAME, not index (§6)."""
    table = _lines_table(page)
    if await table.count() == 0:
        return []

    rows = table.locator("tr.o_data_row")
    count = await rows.count()
    lines: list[InvoiceLine] = []

    for i in range(count):
        row = rows.nth(i)
        product = await _cell_text(row, "product_id")
        if not product:
            # likely a section/note row
            product = await _cell_text(row, "name")
            if not product:
                continue
        qty = await _cell_text(row, "quantity")
        unit = await _cell_text(row, "price_unit")
        total = await _cell_text(row, "price_total")
        subtotal = await _cell_text(row, "price_subtotal")
        tax = round(to_float(total) - to_float(subtotal), 2)
        lines.append(
            InvoiceLine(
                product_name=product,
                quantity=to_float(qty),
                unit_price=to_float(unit),
                tax_amount=tax,
            )
        )
    return lines


async def _cell_text(row: Locator, field_name: str) -> str:
    cell = row.locator(f'[name="{field_name}"]')
    if await cell.count() == 0:
        return ""
    return (await cell.first.inner_text()).strip()
