"""Unit tests for the PHI access audit log (§164.312(b), (c)(1))."""

from __future__ import annotations

import json

import pytest

from odoo_extract.audit import GENESIS_HASH, PhiAuditLog, pseudonymize


@pytest.fixture
def audit_path(tmp_path):
    return tmp_path / "phi_audit.log"


def _read(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_records_are_hash_chained(audit_path):
    log = PhiAuditLog(audit_path, run_id="r1")
    log.record("RUN_START")
    log.record("READ", resource="account.move", resource_id="INV/1", field_count=3)
    log.record("RUN_END", outcome="SUCCESS")

    events = _read(audit_path)
    assert len(events) == 3
    assert events[0]["prev_hash"] == GENESIS_HASH
    # Each record chains to the previous one.
    assert events[1]["prev_hash"] == events[0]["hash"]
    assert events[2]["prev_hash"] == events[1]["hash"]


def test_verify_passes_for_untampered_chain(audit_path):
    log = PhiAuditLog(audit_path, run_id="r1")
    log.record("RUN_START")
    log.record("READ", resource_id="INV/1", field_count=2)
    assert log.verify() is True


def test_verify_detects_tampering(audit_path):
    log = PhiAuditLog(audit_path, run_id="r1")
    log.record("RUN_START")
    log.record("READ", resource_id="INV/1", field_count=2)

    # Tamper: bump a field_count without recomputing the chain.
    events = _read(audit_path)
    events[1]["field_count"] = 999
    audit_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    assert PhiAuditLog(audit_path, run_id="r1").verify() is False


def test_chain_resumes_across_instances(audit_path):
    first = PhiAuditLog(audit_path, run_id="r1")
    first.record("RUN_START")
    last_hash = _read(audit_path)[-1]["hash"]

    # A new instance (e.g. write_output) continues the same chain.
    second = PhiAuditLog(audit_path, run_id="r1")
    second.record("PERSIST", field_count=4)
    events = _read(audit_path)
    assert events[-1]["prev_hash"] == last_hash
    assert second.verify() is True


def test_no_phi_in_records(audit_path):
    """The audit log must never contain raw customer/product values."""
    log = PhiAuditLog(audit_path, run_id="r1")
    subject = pseudonymize("Deco Addict")
    log.record("READ", resource="account.move", resource_id="INV/1", subject=subject)

    raw = audit_path.read_text()
    assert "Deco Addict" not in raw
    assert subject in raw
    assert subject.startswith("sha256:")


def test_summary_counts_actions(audit_path):
    log = PhiAuditLog(audit_path, run_id="r1")
    log.record("READ")
    log.record("READ")
    log.record("PERSIST")
    assert log.summary() == {"READ": 2, "PERSIST": 1}


def test_pseudonymize_is_stable_and_irreversible():
    a = pseudonymize("Deco Addict")
    b = pseudonymize("Deco Addict")
    assert a == b  # stable for correlation
    assert "Deco Addict" not in a
    assert pseudonymize("Other Co") != a  # distinct subjects differ
