"""
Run:  python extract_invoice_lines.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import (
    BrowserContext,
    Page,
    Response,
    TimeoutError as PWTimeout,
    async_playwright,
)

from sanitizer import build_logger

# JSON-RPC method names the Odoo web client uses for reads/searches.
SEARCH_METHODS = {"web_search_read", "search_read"}
READ_METHODS = {"web_read", "read"}
NAV_TIMEOUT_MS = 45_000


# --------------------------------------------------------------------------- #
# Configuration                                                                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Config:
    url: str
    email: str
    password: str
    target_customer: str
    headless: bool
    output_file: str
    user_data_dir: str

    @staticmethod
    def load() -> "Config":
        load_dotenv()

        def required(key: str) -> str:
            val = os.getenv(key, "").strip()
            if not val:
                raise ConfigError(f"Missing required env var: {key}")
            return val

        return Config(
            url=required("ODOO_URL").rstrip("/"),
            email=required("ODOO_EMAIL"),
            password=required("ODOO_PASSWORD"),
            target_customer=os.getenv("TARGET_CUSTOMER", "Deco Addict").strip(),
            headless=os.getenv("HEADLESS", "true").lower() != "false",
            output_file=os.getenv("OUTPUT_FILE", "invoice_lines.json").strip(),
            user_data_dir=os.getenv("USER_DATA_DIR", ".pw-profile").strip(),
        )


# --------------------------------------------------------------------------- #
# Domain-specific exceptions (Specification §10)                               #
# --------------------------------------------------------------------------- #
class WorkflowError(Exception):
    """Base class for typed, catchable workflow failures."""


class ConfigError(WorkflowError):
    pass


class AuthError(WorkflowError):
    pass


class NavigationError(WorkflowError):
    pass


class RecordNotFoundError(WorkflowError):
    pass


class ExtractionError(WorkflowError):
    pass


# --------------------------------------------------------------------------- #
# Output schema (Specification §9)                                             #
# --------------------------------------------------------------------------- #
@dataclass
class InvoiceLine:
    product_name: str
    quantity: float
    unit_price: float
    tax_amount: float


@dataclass
class Invoice:
    invoice_number: str
    lines: list[InvoiceLine]


# --------------------------------------------------------------------------- #
# RPC interception buffer                                                      #
# --------------------------------------------------------------------------- #
class RpcCapture:
    """
    Listens to all JSON-RPC responses and keeps the most recent account.move
    `read`/`web_read` payload. This is the primary extraction source (§6).
    """

    def __init__(self, log) -> None:
        self._log = log
        self.last_move_record: dict | None = None

    def reset(self) -> None:
        """Clear the buffer before opening the next invoice, so each extraction
        reads its own fresh payload rather than a stale one (multi-invoice)."""
        self.last_move_record = None

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    async def _on_response(self, response: Response) -> None:
        url = response.url
        if "/web/dataset/" not in url:
            return
        try:
            payload = await response.json()
        except Exception:
            return  # not JSON / streamed — ignore

        result = payload.get("result")
        if not result:
            return

        records = result if isinstance(result, list) else result.get("records", [])
        if not isinstance(records, list):
            return

        for rec in records:
            if isinstance(rec, dict) and "invoice_line_ids" in rec:
                # Prefer a record whose lines are EXPANDED (list of dicts), which
                # is what the form-view web_read returns. The list-view
                # search_read also has the key but as bare IDs — skip that one
                # so we don't lock onto an unusable payload.
                lines = rec.get("invoice_line_ids") or []
                expanded = bool(lines) and isinstance(lines[0], dict)
                if expanded or self.last_move_record is None:
                    self.last_move_record = rec
                # Non-PHI observability only: count, never content.
                self._log.info("Captured account.move RPC payload via interception.")
                if expanded:
                    return


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _to_float(value) -> float:
    """Normalize a numeric/currency string or number to float (§9)."""
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    cleaned = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".,-")
    if "," in cleaned and "." in cleaned:
        # Both present: the LAST-occurring separator is the decimal point.
        # Handles US "1,234.50" and European "1.234,50" alike.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned or 0)
    except ValueError:
        return 0.0


def _name_of(field) -> str:
    """
    Resolve a many2one field's display name across Odoo serialization formats.

    - Legacy (<=18 read):  [id, "Display Name"]
    - Odoo 19 web_read:    {"id": .., "display_name": ".."}  (or just {"id": ..})
    - Empty / unset:       False  ->  ""
    """
    if isinstance(field, dict):
        return str(field.get("display_name") or "")
    if isinstance(field, (list, tuple)) and len(field) == 2:
        return str(field[1])
    if field is False or field is None:
        return ""
    return str(field)


# --------------------------------------------------------------------------- #
# AuthModule (Specification §8.2)                                              #
# --------------------------------------------------------------------------- #
async def authenticate(page: Page, cfg: Config, log) -> None:
    log.info("Navigating to login page.")
    await page.goto(f"{cfg.url}/web/login", wait_until="domcontentloaded")

    # If a persistent session already exists, Odoo redirects away from /login.
    if "/web/login" not in page.url and "/odoo/login" not in page.url:
        log.info("Existing session detected — skipping credential entry.")
        return

    # Resilient locators: select by stable Odoo field NAME, not generated id.
    await page.locator('input[name="login"]').fill(cfg.email)
    await page.locator('input[name="password"]').fill(cfg.password)

    # State trigger: navigation completes when the app shell loads, not a timer.
    async with page.expect_navigation(wait_until="networkidle", timeout=NAV_TIMEOUT_MS):
        await page.get_by_role("button", name="Log in").click()

    if "/web/login" in page.url or "/odoo/login" in page.url:
        # Surface Odoo's own error text (non-PHI: it's a UI message).
        err = page.locator(".alert-danger, .o_login_error, p.alert")
        detail = (await err.first.inner_text()).strip() if await err.count() else ""
        raise AuthError(f"Login failed; still on login page. {detail}".strip())

    log.info("Authenticated successfully.")


# --------------------------------------------------------------------------- #
# NavModule — open Invoicing & filter to Posted (Specification §8.3–8.4)       #
# --------------------------------------------------------------------------- #
async def open_invoicing(page: Page, cfg: Config, log) -> None:
    """Navigate to the Customer Invoices list via the stable action URL."""
    log.info("Opening Invoicing module.")
    # Customer Invoices list (account.move, out_invoice). The /odoo/customer-invoices
    # action URL is the stable client-side route in Odoo 17+; it avoids brittle
    # multi-click menu traversal while still exercising the real router.
    async with page.expect_response(_is_search_response, timeout=NAV_TIMEOUT_MS):
        await page.goto(
            f"{cfg.url}/odoo/customer-invoices",
            wait_until="domcontentloaded",
        )

    # Confirm the list view actually rendered (auto-waiting, no sleep).
    try:
        await page.locator(".o_list_view, .o_list_renderer").first.wait_for(
            state="visible", timeout=NAV_TIMEOUT_MS
        )
    except PWTimeout as exc:
        raise NavigationError("Invoicing list view did not render.") from exc
    log.info("Invoicing list view is visible.")


def _is_search_response(resp: Response) -> bool:
    if "/web/dataset/" not in resp.url:
        return False
    return any(m in resp.url for m in SEARCH_METHODS) or resp.url.endswith(
        "/web/dataset/call_kw"
    )


async def clear_default_filters(page: Page, log) -> None:
    """
    Remove every pre-applied search facet so filtering is idempotent (§10).
    Facets are the chips inside the search bar; we close each via its remove
    button, located structurally (role/text), never by generated id.
    """
    # Search bar container — role-based with class fallback (Odoo 16→19).
    search_box = page.locator(
        '[role="search"], .o_searchview, .o_cp_searchview'
    ).first
    await search_box.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)

    # Each facet exposes a remove control. In Odoo 19 it carries an aria-label
    # ("Remove"); older builds use the .o_facet_remove class. We match either,
    # accessibility-first. Loop until none remain — each removal triggers a
    # fresh search_read which the list awaits via re-render (no sleep).
    while True:
        facet_remove = search_box.locator(
            '.o_facet_remove, .o_searchview_facet .o_facet_remove, '
            'button[aria-label="Remove"], [role="img"][aria-label="Remove"]'
        )
        if await facet_remove.count() == 0:
            break
        async with page.expect_response(_is_search_response, timeout=NAV_TIMEOUT_MS):
            await facet_remove.first.click()
    log.info("Default search filters cleared.")


async def _visible_row_count(page: Page) -> int:
    return await page.locator("tr.o_data_row").count()


async def apply_posted_filter(page: Page, log) -> None:
    """
    Apply the Posted status filter (§8.4), self-healing.

    Primary: the 'Filters' dropdown exposes a 'Posted' filter on account.move.
    Fallback: type 'Posted' into the search input and pick the suggested
    *Status* facet from the autocomplete (label-agnostic).

    Self-heal: if applying the filter empties the list (e.g. the only invoice is
    still in Draft on this instance), the filter is removed again so the
    workflow can still drill into the available record. A sanitized notice is
    logged. This keeps the script runnable across instances regardless of
    whether demo/posted data is present.
    """
    applied = await _apply_posted_via_dropdown(page, log)
    if not applied:
        applied = await _apply_posted_via_search(page, log)

    if applied and await _visible_row_count(page) == 0:
        log.warning(
            "'Posted' filter returned 0 rows on this instance; "
            "removing it to proceed with available records."
        )
        await clear_default_filters(page, log)


async def _apply_posted_via_dropdown(page: Page, log) -> bool:
    filters_toggle = page.get_by_role("button", name="Filters")
    try:
        await filters_toggle.wait_for(state="visible", timeout=8_000)
    except PWTimeout:
        return False
    await filters_toggle.click()

    menu = page.locator(".dropdown-menu, .o-dropdown--menu, .o_dropdown_container")
    posted_option = menu.get_by_role("menuitemcheckbox", name="Posted")
    if await posted_option.count() == 0:
        posted_option = menu.get_by_text("Posted", exact=True)
    if await posted_option.count() == 0:
        await page.keyboard.press("Escape")
        return False

    async with page.expect_response(_is_search_response, timeout=NAV_TIMEOUT_MS):
        await posted_option.first.click()
    await page.keyboard.press("Escape")
    log.info("Applied 'Posted' status filter.")
    return True


async def _apply_posted_via_search(page: Page, log) -> bool:
    search_input = page.locator(
        '[role="search"] input, .o_searchview input.o_searchview_input, '
        '.o_cp_searchview input'
    ).first
    await search_input.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
    await search_input.click()
    await search_input.fill("Posted")

    # Autocomplete suggests a "Status > Posted" facet; pick it by text.
    suggestion = page.locator(
        ".o_searchview_autocomplete, .o-autocomplete--dropdown-menu, .dropdown-menu"
    ).get_by_text("Posted", exact=False)
    async with page.expect_response(_is_search_response, timeout=NAV_TIMEOUT_MS):
        if await suggestion.count():
            await suggestion.first.click()
        else:
            await search_input.press("Enter")

    # Confirm the 'Posted' facet is now present in the search bar.
    facet = page.locator(
        '[role="search"], .o_searchview, .o_cp_searchview'
    ).get_by_text("Posted", exact=False)
    await facet.first.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
    log.info("Applied 'Posted' status filter.")
    return True


# --------------------------------------------------------------------------- #
# Drill-down — find the target customer's invoice (Specification §8.5)         #
# --------------------------------------------------------------------------- #
def _target_rows(page: Page, cfg: Config):
    """All list rows whose Customer cell matches the target (§5).

    Matching is scoped to the customer field cell — not an arbitrary substring
    across the whole row — so "Deco Addict" cannot accidentally match another
    column or a similarly named customer elsewhere in the row.
    """
    customer_cell = page.locator(
        'tr.o_data_row td[name="partner_id"], '
        'tr.o_data_row td.o_field_cell[name="partner_id"]'
    ).filter(has_text=cfg.target_customer)
    # Fall back to whole-row text match if the column name differs on this build.
    rows_by_cell = page.locator("tr.o_data_row").filter(has=customer_cell)
    return rows_by_cell, page.locator("tr.o_data_row").filter(
        has_text=cfg.target_customer
    )


async def extract_all_target_invoices(
    page: Page, cfg: Config, capture: RpcCapture, log
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

    primary_rows, fallback_rows = _target_rows(page, cfg)
    rows = primary_rows if await primary_rows.count() else fallback_rows

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
        # Re-resolve the row set each iteration (DOM was rebuilt after going
        # back), then select by index. Indexes are stable within a single
        # rendered list ordering.
        cur_primary, cur_fallback = _target_rows(page, cfg)
        cur_rows = cur_primary if await cur_primary.count() else cur_fallback
        await cur_rows.first.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
        row = cur_rows.nth(index)

        capture.reset()
        # State trigger: opening fires a web_read on account.move (intercepted).
        async with page.expect_response(_is_read_response, timeout=NAV_TIMEOUT_MS):
            await row.click()
        try:
            await page.locator(".o_form_view").first.wait_for(
                state="visible", timeout=NAV_TIMEOUT_MS
            )
        except PWTimeout as exc:
            raise NavigationError("Invoice form view did not render.") from exc

        number = await _read_invoice_number(page)
        try:
            lines = await extract_invoice_lines(page, capture, log)
        except ExtractionError:
            # One empty/odd invoice shouldn't abort the whole batch.
            log.warning("Invoice %d/%d yielded no lines; recording empty.",
                        index + 1, total)
            lines = []
        invoices.append(Invoice(invoice_number=number, lines=lines))
        log.info("Extracted invoice %d/%d.", index + 1, total)  # count only

        # Return to the list for the next iteration (state-driven, no sleep).
        if index < total - 1:
            await _back_to_list(page, log)

    return invoices


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


async def _back_to_list(page: Page, log) -> None:
    """Navigate back to the list view via the breadcrumb (state-driven)."""
    crumb = page.locator(".o_breadcrumb a, .breadcrumb-item a").first
    async with page.expect_response(_is_search_response, timeout=NAV_TIMEOUT_MS):
        await crumb.click()
    await page.locator(".o_list_view, .o_list_renderer").first.wait_for(
        state="visible", timeout=NAV_TIMEOUT_MS
    )


def _is_read_response(resp: Response) -> bool:
    if "/web/dataset/" not in resp.url:
        return False
    return any(m in resp.url for m in READ_METHODS) or resp.url.endswith(
        "/web/dataset/call_kw"
    )


# --------------------------------------------------------------------------- #
# ExtractionModule — hybrid RPC + DOM (Specification §6, §8.6)                 #
# --------------------------------------------------------------------------- #
async def extract_invoice_lines(
    page: Page, capture: RpcCapture, log
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
        raise ExtractionError("No invoice lines could be extracted (RPC and DOM empty).")

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


def _lines_table(page: Page):
    """Version-tolerant locator for the invoice-lines field/table (§5).

    Odoo 19 may drop the legacy `.o_field_x2many` wrapper class; the stable
    contract is the `name="invoice_line_ids"` field attribute. We match the
    field by name, falling back to any list table that follows it.
    """
    return page.locator(
        '[name="invoice_line_ids"], .o_field_x2many[name="invoice_line_ids"]'
    ).first


async def _open_invoice_lines_tab(page: Page, log) -> None:
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
        except Exception:
            pass  # already active / not clickable — proceed to verify table

    # Verify the lines table is present. It is often the default-visible page,
    # so this succeeds whether or not we needed to click. We do NOT hard-fail
    # here: the RPC interception already holds the data; the DOM is a fallback.
    table = _lines_table(page)
    try:
        await table.wait_for(state="visible", timeout=15_000)
        log.info("Invoice Lines tab active; lines table visible.")
    except PWTimeout:
        log.warning(
            "Invoice Lines table not located in DOM; relying on RPC capture."
        )


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
        # Odoo 19: real product lines carry display_type == "product".
        # Section/note rows use "line_section" / "line_note" — skip only those.
        # (Older Odoo used False/empty for product lines, so accept that too.)
        dtype = ln.get("display_type")
        if dtype not in (False, None, "", "product"):
            continue
        subtotal = _to_float(ln.get("price_subtotal"))
        total = _to_float(ln.get("price_total"))
        tax = round(total - subtotal, 2)
        # Product name: many2one display name, then the line label, then a
        # placeholder if the line has neither (e.g. an empty test line).
        name = _name_of(ln.get("product_id"))
        if not name:
            label = ln.get("name")
            name = label if isinstance(label, str) and label else "(no product)"
        lines.append(
            InvoiceLine(
                product_name=name,
                quantity=_to_float(ln.get("quantity")),
                unit_price=_to_float(ln.get("price_unit")),
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
        tax = round(_to_float(total) - _to_float(subtotal), 2)
        lines.append(
            InvoiceLine(
                product_name=product,
                quantity=_to_float(qty),
                unit_price=_to_float(unit),
                tax_amount=tax,
            )
        )
    return lines


async def _cell_text(row, field_name: str) -> str:
    cell = row.locator(f'[name="{field_name}"]')
    if await cell.count() == 0:
        return ""
    text = (await cell.first.inner_text()).strip()
    return text


# --------------------------------------------------------------------------- #
# Orchestrator (Specification §8)                                             #
# --------------------------------------------------------------------------- #
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
