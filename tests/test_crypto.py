"""Unit tests for encryption-at-rest (§164.312(a)(2)(iv))."""

from __future__ import annotations

import pytest

# Skip cleanly if the optional crypto dep isn't installed.
pytest.importorskip("cryptography")

from odoo_extract import crypto  # noqa: E402
from odoo_extract.control_health import assert_redaction_active  # noqa: E402
from odoo_extract.errors import ConfigError, WorkflowError  # noqa: E402
from odoo_extract.sanitizer import PhiRedactionFilter, build_logger  # noqa: E402


def test_roundtrip():
    key = crypto.generate_key().encode()
    data = b'[{"product_name": "Acoustic Bloc Screens"}]'
    token = crypto.encrypt(data, key)
    assert token != data  # ciphertext differs from plaintext
    assert b"Acoustic Bloc Screens" not in token  # content not visible
    assert crypto.decrypt(token, key) == data


def test_wrong_key_fails_closed():
    k1 = crypto.generate_key().encode()
    k2 = crypto.generate_key().encode()
    token = crypto.encrypt(b"secret", k1)
    with pytest.raises(ConfigError, match="Decryption failed"):
        crypto.decrypt(token, k2)


def test_tampered_ciphertext_rejected():
    key = crypto.generate_key().encode()
    token = bytearray(crypto.encrypt(b"secret payload", key))
    token[-1] ^= 0x01  # flip a bit
    with pytest.raises(ConfigError):
        crypto.decrypt(bytes(token), key)


def test_invalid_key_raises_config_error():
    with pytest.raises(ConfigError, match="valid Fernet key"):
        crypto.encrypt(b"x", b"not-a-real-key")


def test_load_key_requires_env(monkeypatch):
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    with pytest.raises(ConfigError, match=crypto.ENV_KEY):
        crypto.load_key()


# --- control self-test (fail-closed) ---------------------------------------


def test_redaction_self_test_passes():
    log, _ = build_logger("health-ok")
    assert_redaction_active(log)  # should not raise


def test_self_test_fails_closed_when_filter_broken(monkeypatch):
    # Simulate a broken control: the filter does nothing.
    monkeypatch.setattr(PhiRedactionFilter, "filter", lambda self, record: True)
    log, _ = build_logger("health-broken")
    with pytest.raises(WorkflowError, match="fail-closed"):
        assert_redaction_active(log)
