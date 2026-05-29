"""Unit tests for configuration loading & safety (no browser required)."""

from __future__ import annotations

import pytest

from odoo_extract.config import Config
from odoo_extract.errors import ConfigError


@pytest.fixture
def base_env(monkeypatch):
    # Neutralize any real .env on disk so tests control the environment fully.
    monkeypatch.setattr("odoo_extract.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("ODOO_URL", "https://x.odoo.com")
    monkeypatch.setenv("ODOO_EMAIL", "a@b.com")
    monkeypatch.setenv("ODOO_PASSWORD", "s3cret")
    # Encryption defaults ON and would require a key; disable for these tests
    # unless a case opts back in.
    monkeypatch.setenv("ENCRYPT_OUTPUT", "false")
    # Ensure optional vars don't leak in from the process env during tests.
    for key in (
        "HEADLESS",
        "NO_SANDBOX",
        "SAVE_ARTIFACTS",
        "TARGET_CUSTOMER",
        "ALLOW_INSECURE_HTTP",
        "PHI_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_loads_required_and_defaults(base_env):
    cfg = Config.load()
    assert cfg.url == "https://x.odoo.com"
    assert cfg.target_customer == "Deco Addict"
    assert cfg.headless is True
    assert cfg.no_sandbox is False
    assert cfg.save_artifacts is True


def test_trailing_slash_stripped(base_env, monkeypatch):
    monkeypatch.setenv("ODOO_URL", "https://x.odoo.com/")
    assert Config.load().url == "https://x.odoo.com"


def test_missing_required_raises(base_env, monkeypatch):
    monkeypatch.delenv("ODOO_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="ODOO_PASSWORD"):
        Config.load()


@pytest.mark.parametrize("bad", ["x.odoo.com", "ftp://x", "not a url", ""])
def test_invalid_url_rejected(base_env, monkeypatch, bad):
    monkeypatch.setenv("ODOO_URL", bad)
    with pytest.raises(ConfigError):
        Config.load()


def test_bool_parsing(base_env, monkeypatch):
    monkeypatch.setenv("HEADLESS", "false")
    monkeypatch.setenv("NO_SANDBOX", "1")
    cfg = Config.load()
    assert cfg.headless is False
    assert cfg.no_sandbox is True


def test_password_not_in_repr(base_env):
    cfg = Config.load()
    assert "s3cret" not in repr(cfg)
    assert cfg.password == "s3cret"  # still accessible programmatically


def test_http_rejected_without_optin(base_env, monkeypatch):
    monkeypatch.setenv("ODOO_URL", "http://x.odoo.com")
    with pytest.raises(ConfigError, match="TLS"):
        Config.load()


def test_http_allowed_with_optin(base_env, monkeypatch):
    monkeypatch.setenv("ODOO_URL", "http://localhost:8069")
    monkeypatch.setenv("ALLOW_INSECURE_HTTP", "true")
    assert Config.load().url == "http://localhost:8069"


def test_encryption_requires_key(base_env, monkeypatch):
    monkeypatch.setenv("ENCRYPT_OUTPUT", "true")
    monkeypatch.delenv("PHI_ENCRYPTION_KEY", raising=False)
    with pytest.raises(ConfigError, match="PHI_ENCRYPTION_KEY"):
        Config.load()


def test_encryption_key_not_in_repr(base_env, monkeypatch):
    monkeypatch.setenv("ENCRYPT_OUTPUT", "true")
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", "super-secret-key-material")
    cfg = Config.load()
    assert "super-secret-key-material" not in repr(cfg)
    assert cfg.encrypt_output is True
