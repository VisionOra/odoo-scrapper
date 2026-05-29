"""RPC interception buffer (Specification §6)."""

from __future__ import annotations

from playwright.async_api import Page, Response


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
