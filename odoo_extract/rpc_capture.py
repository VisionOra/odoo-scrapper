"""RPC interception buffer (Specification §6)."""

from __future__ import annotations

import logging

from playwright.async_api import Page, Response

from .constants import ACCOUNT_MOVE_MODEL, READ_METHODS, rpc_meta


class RpcCapture:
    """
    Listens to JSON-RPC responses and keeps the most recent account.move
    `read`/`web_read` payload. This is the primary extraction source (§6).

    Efficiency: the response handler runs for *every* network response on the
    page for the whole session. We therefore resolve the cheap (model, method)
    metadata first and bail out before deserialising the body for anything that
    is not an account.move read — so large list-view search payloads are never
    parsed (see constants.rpc_meta).
    """

    def __init__(self, log: logging.Logger) -> None:
        self._log = log
        self.last_move_record: dict | None = None

    def reset(self) -> None:
        """Clear the buffer before opening the next invoice, so each extraction
        reads its own fresh payload rather than a stale one (multi-invoice)."""
        self.last_move_record = None

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    async def _on_response(self, response: Response) -> None:
        model, method = rpc_meta(response)
        # Gate BEFORE reading the body: only account.move reads are of interest.
        if method not in READ_METHODS:
            return
        if model not in (None, ACCOUNT_MOVE_MODEL):
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
                # is what the form-view web_read returns. A read that returns
                # bare IDs is unusable here — keep it only if we have nothing.
                lines = rec.get("invoice_line_ids") or []
                expanded = bool(lines) and isinstance(lines[0], dict)
                if expanded or self.last_move_record is None:
                    self.last_move_record = rec
                # Non-PHI observability only: presence, never content.
                self._log.info("Captured account.move RPC payload via interception.")
                if expanded:
                    return
