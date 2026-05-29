"""PHI access audit log — HIPAA §164.312(b) Audit controls + §164.312(c)(1) Integrity.

An append-only, **hash-chained** JSONL trail of every PHI-access event. It
records WHO / WHAT / WHEN — but never the PHI value itself:

* customer names are **pseudonymized** to a salted SHA-256 reference, so events
  can be correlated without exposing identity (a recognised HIPAA technique);
* product names are never written here at all;
* the only human-readable identifier is the invoice number, which is a business
  document reference, not PHI.

Each record embeds the previous record's hash, so any later edit/removal breaks
the chain — ``verify()`` detects tampering. The log file is created ``0600``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

GENESIS_HASH = "0" * 64


def pseudonymize(value: str, *, salt: str = "erp-extract") -> str:
    """Return a stable, non-reversible reference for a PHI identifier."""
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


class PhiAuditLog:
    """Append-only, integrity-chained PHI access log."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        run_id: str,
        actor: str = "erp-extract",
        log: logging.Logger | None = None,
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.actor = actor
        self._log = log
        self._prev_hash = self._tail_hash()
        self._counts: dict[str, int] = {}

    # -- hashing -----------------------------------------------------------
    @staticmethod
    def _hash(event: dict) -> str:
        material = {k: v for k, v in event.items() if k != "hash"}
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _tail_hash(self) -> str:
        """Resume the chain from an existing log, if any."""
        if not self.path.exists():
            return GENESIS_HASH
        last = GENESIS_HASH
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    last = json.loads(line).get("hash", last)
        except (OSError, json.JSONDecodeError):
            return GENESIS_HASH
        return last

    # -- writing -----------------------------------------------------------
    def record(
        self,
        action: str,
        *,
        resource: str | None = None,
        resource_id: str | None = None,
        subject: str | None = None,
        field_count: int | None = None,
        outcome: str = "SUCCESS",
        detail: str | None = None,
    ) -> dict:
        """Append one audit event and return it.

        Callers MUST pass only non-PHI values. ``subject`` should already be a
        pseudonym (see :func:`pseudonymize`); ``resource_id`` is the invoice
        number; ``field_count`` is a count, never content.
        """
        event = {
            "ts": _utc_now(),
            "run_id": self.run_id,
            "actor": self.actor,
            "action": action,
            "resource": resource,
            "resource_id": resource_id,
            "subject": subject,
            "field_count": field_count,
            "outcome": outcome,
            "detail": detail,
            "prev_hash": self._prev_hash,
        }
        event["hash"] = self._hash(event)
        self._append(event)
        self._prev_hash = event["hash"]
        self._counts[action] = self._counts.get(action, 0) + 1
        return event

    def _append(self, event: dict) -> None:
        is_new = not self.path.exists()
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")
        if is_new:
            try:
                os.chmod(self.path, 0o600)  # least-privilege (§164.312(a)(1))
            except OSError:
                pass

    # -- monitoring --------------------------------------------------------
    def verify(self) -> bool:
        """Re-read the file and confirm the hash chain is intact (untampered)."""
        prev = GENESIS_HASH
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                if event.get("prev_hash") != prev:
                    return False
                if self._hash(event) != event.get("hash"):
                    return False
                prev = event["hash"]
        except (OSError, json.JSONDecodeError):
            return False
        return True

    def summary(self) -> dict[str, int]:
        """Per-action counts for the end-of-run monitoring report."""
        return dict(self._counts)
