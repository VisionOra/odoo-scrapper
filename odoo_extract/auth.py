"""AuthModule (Specification §8.2)."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from .config import Config
from .constants import NAV_TIMEOUT_MS
from .errors import AuthError


async def authenticate(page: Page, cfg: Config, log: logging.Logger) -> None:
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
