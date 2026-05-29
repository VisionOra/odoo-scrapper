"""Shared constants and JSON-RPC response predicates.

Odoo's web client talks to the server over JSON-RPC. The model and method of a
call are carried **in the request post body** (``params.model`` /
``params.method``) — not reliably in the URL: modern builds POST to the bare
``/web/dataset/call_kw`` and only encode model/method in the body, while older
builds use ``/web/dataset/call_kw/<model>/<method>``.

Earlier revisions matched on URL substrings, which is wrong twice over:
``"read"`` is a substring of ``"web_search_read"`` (so a *search* response
satisfied the *read* predicate), and on the bare endpoint both predicates
collapsed to the same thing. We therefore parse the post body as the source of
truth and fall back to the URL path only when the body is unavailable.
"""

from __future__ import annotations

import json

from playwright.async_api import Response

# JSON-RPC method names the Odoo web client uses for reads/searches. Matched
# EXACTLY (not as substrings) against the resolved method name.
SEARCH_METHODS = frozenset({"web_search_read", "search_read"})
READ_METHODS = frozenset({"web_read", "read"})

# Model that carries the invoice lines we extract.
ACCOUNT_MOVE_MODEL = "account.move"

# Timeouts (ms). Centralised so every wait shares one tunable budget.
NAV_TIMEOUT_MS = 45_000
FORM_TIMEOUT_MS = 15_000
SHORT_TIMEOUT_MS = 8_000


def rpc_meta(resp: Response) -> tuple[str | None, str | None]:
    """Resolve ``(model, method)`` for an Odoo JSON-RPC response.

    Returns ``(None, None)`` for anything that is not a ``/web/dataset/`` call.
    The model may be ``None`` if it cannot be determined; callers tolerate that.
    """
    url = resp.url
    if "/web/dataset/" not in url:
        return None, None

    model: str | None = None
    method: str | None = None

    # Primary source: the JSON-RPC request body carries model + method.
    try:
        body = resp.request.post_data
        if body:
            params = json.loads(body).get("params") or {}
            model = params.get("model")
            method = params.get("method")
    except Exception:
        # Malformed / non-JSON body — fall back to the URL path below.
        pass

    # Fallback: the call_kw route encodes them as /web/dataset/<route>[/...].
    if method is None:
        tail = url.split("/web/dataset/", 1)[1].split("?", 1)[0]
        parts = [p for p in tail.split("/") if p]
        if parts and parts[0] == "call_kw" and len(parts) >= 3:
            model = model or parts[1]
            method = parts[2]
        elif parts:
            # e.g. /web/dataset/search_read or /web/dataset/web_search_read
            method = parts[0]

    return model, method


def is_search_response(resp: Response) -> bool:
    """True only for list/search reads (``web_search_read`` / ``search_read``)."""
    _, method = rpc_meta(resp)
    return method in SEARCH_METHODS


def is_read_response(resp: Response) -> bool:
    """True only for a record ``read``/``web_read`` on ``account.move``.

    When the model cannot be determined we still accept a read method, since a
    record open reliably fires a read; the capture layer then ignores any
    payload that lacks ``invoice_line_ids``.
    """
    model, method = rpc_meta(resp)
    if method not in READ_METHODS:
        return False
    return model in (None, ACCOUNT_MOVE_MODEL)
