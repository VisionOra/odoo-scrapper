"""Shared constants and JSON-RPC response predicates."""

from __future__ import annotations

from playwright.async_api import Response

# JSON-RPC method names the Odoo web client uses for reads/searches.
SEARCH_METHODS = {"web_search_read", "search_read"}
READ_METHODS = {"web_read", "read"}
NAV_TIMEOUT_MS = 45_000


def is_search_response(resp: Response) -> bool:
    if "/web/dataset/" not in resp.url:
        return False
    return any(m in resp.url for m in SEARCH_METHODS) or resp.url.endswith(
        "/web/dataset/call_kw"
    )


def is_read_response(resp: Response) -> bool:
    if "/web/dataset/" not in resp.url:
        return False
    return any(m in resp.url for m in READ_METHODS) or resp.url.endswith(
        "/web/dataset/call_kw"
    )
