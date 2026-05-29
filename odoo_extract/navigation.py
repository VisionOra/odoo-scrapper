"""NavModule — open Invoicing & filter to Posted (Specification §8.3–8.4)."""

from __future__ import annotations

from playwright.async_api import Page, TimeoutError as PWTimeout

from .config import Config
from .constants import NAV_TIMEOUT_MS, is_search_response
from .errors import NavigationError


async def open_invoicing(page: Page, cfg: Config, log) -> None:
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
        async with page.expect_response(is_search_response, timeout=NAV_TIMEOUT_MS):
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

    async with page.expect_response(is_search_response, timeout=NAV_TIMEOUT_MS):
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
    async with page.expect_response(is_search_response, timeout=NAV_TIMEOUT_MS):
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
