"""NavModule — open Invoicing & filter to Posted (Specification §8.3–8.4)."""

from __future__ import annotations

import logging
import re

from playwright.async_api import Page, TimeoutError as PWTimeout

from .config import Config
from .constants import NAV_TIMEOUT_MS, SHORT_TIMEOUT_MS, is_search_response
from .errors import NavigationError

# Safety bound so a misbehaving remove-control can never spin forever.
MAX_FACET_REMOVALS = 25


async def open_invoicing(page: Page, cfg: Config, log: logging.Logger) -> None:
    """Navigate to the Customer Invoices list via the stable action URL."""
    log.info("Opening Invoicing module.")
    # Customer Invoices list (account.move, out_invoice). The /odoo/customer-invoices
    # action URL is the stable client-side route in Odoo 17+; it avoids brittle
    # multi-click menu traversal while still exercising the real router.
    async with page.expect_response(is_search_response, timeout=NAV_TIMEOUT_MS):
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


async def clear_default_filters(page: Page, log: logging.Logger) -> None:
    """
    Remove every pre-applied search facet so filtering is idempotent (§10).

    Facets are the chips inside the search bar; we close each via its remove
    button, located structurally (role/text), never by generated id. Each
    removal's signal is the control detaching from the DOM — not a network
    response, since a removal may re-render the list from the in-memory model.
    The loop is bounded by ``MAX_FACET_REMOVALS`` to preclude a spin.
    """
    search_box = page.locator('[role="search"], .o_searchview, .o_cp_searchview').first
    await search_box.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)

    for _ in range(MAX_FACET_REMOVALS):
        facet_remove = search_box.locator(
            ".o_facet_remove, .o_searchview_facet .o_facet_remove, "
            'button[aria-label="Remove"], [role="img"][aria-label="Remove"]'
        )
        if await facet_remove.count() == 0:
            break
        btn = facet_remove.first
        await btn.click()
        # Wait for THIS control to detach (the facet is gone), so we don't race
        # the next iteration's count against a not-yet-updated DOM.
        try:
            await btn.wait_for(state="detached", timeout=SHORT_TIMEOUT_MS)
        except PWTimeout:
            pass
    else:
        log.warning(
            "Stopped clearing search facets after %d iterations.", MAX_FACET_REMOVALS
        )
        return
    log.info("Default search filters cleared.")


async def _visible_row_count(page: Page) -> int:
    return await page.locator("tr.o_data_row").count()


async def apply_posted_filter(page: Page, log: logging.Logger) -> None:
    """
    Apply the Posted status filter (§8.4), self-healing.

    Primary: the 'Filters' dropdown exposes a 'Posted' filter on account.move.
    Fallback: pick the *Status* facet from the search-input autocomplete.

    If neither control can apply a genuine *Status = Posted* facet we proceed
    with the unfiltered list and say so loudly — we never silently apply a
    free-text "contains Posted" search and pass it off as a status filter.

    Self-heal: if applying the filter empties the list (e.g. the only invoice is
    still in Draft on this instance), the filter is removed again so the
    workflow can still drill into the available record.
    """
    if await _apply_posted_via_dropdown(page, log):
        applied = True
    elif await _apply_posted_via_search(page, log):
        applied = True
    else:
        applied = False
        log.warning(
            "Could not apply a 'Posted' status filter via any known control; "
            "proceeding with the unfiltered list."
        )

    if applied and await _visible_row_count(page) == 0:
        log.warning(
            "'Posted' filter returned 0 rows on this instance; "
            "removing it to proceed with available records."
        )
        await clear_default_filters(page, log)


async def _apply_posted_via_dropdown(page: Page, log: logging.Logger) -> bool:
    filters_toggle = page.get_by_role("button", name="Filters")
    try:
        await filters_toggle.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
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

    async with page.expect_response(is_search_response, timeout=NAV_TIMEOUT_MS):
        await posted_option.first.click()
    await page.keyboard.press("Escape")
    log.info("Applied 'Posted' status filter.")
    return True


async def _apply_posted_via_search(page: Page, log: logging.Logger) -> bool:
    """Fallback: select a real *Status* facet from the search autocomplete.

    Crucially we only accept an autocomplete option that represents a Status
    field filter — never a free-text "Search … for: Posted" row, and never a
    blind Enter press (which would create a content search, not a status
    filter). If no genuine Status option appears, we decline (return False).
    """
    search_input = page.locator(
        '[role="search"] input, .o_searchview input.o_searchview_input, '
        ".o_cp_searchview input"
    ).first
    await search_input.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
    await search_input.click()
    await search_input.fill("Posted")

    dropdown = page.locator(
        ".o_searchview_autocomplete, .o-autocomplete--dropdown-menu, .dropdown-menu"
    ).first
    try:
        await dropdown.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
    except PWTimeout:
        return False

    # Accept a Status-category suggestion; exclude the free-text search row
    # (which reads "Search … for:"). Match on the Status group label.
    options = dropdown.get_by_role("option")
    if await options.count() == 0:
        options = dropdown.locator("li, .o_menu_item, .dropdown-item")
    status_option = options.filter(has_text=re.compile(r"status", re.I)).filter(
        has_not_text=re.compile(r"search", re.I)
    )
    if await status_option.count() == 0:
        await page.keyboard.press("Escape")
        return False

    async with page.expect_response(is_search_response, timeout=NAV_TIMEOUT_MS):
        await status_option.first.click()

    # Confirm a 'Posted' facet is now present in the search bar.
    facet = page.locator(
        '[role="search"], .o_searchview, .o_cp_searchview'
    ).get_by_text("Posted", exact=False)
    try:
        await facet.first.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
    except PWTimeout:
        return False
    log.info("Applied 'Posted' status filter.")
    return True
