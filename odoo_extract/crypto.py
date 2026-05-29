"""Encryption-at-rest for PHI output — HIPAA §164.312(a)(2)(iv).

Uses Fernet (AES-128-CBC + HMAC-SHA256 authenticated encryption) from the
``cryptography`` library. The symmetric key is supplied out-of-band via the
``PHI_ENCRYPTION_KEY`` environment variable — never written to the repo or the
output. Generate one with::

    python -m odoo_extract.crypto

In production the key would come from a KMS / secrets manager, not an env var;
the indirection here keeps that swap a one-line change.
"""

from __future__ import annotations

import os

from .errors import ConfigError

try:  # cryptography is an optional dep until encryption is enabled.
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - exercised only without the dep
    Fernet = None  # type: ignore[assignment]

    class InvalidToken(Exception):  # type: ignore[no-redef]
        pass


ENV_KEY = "PHI_ENCRYPTION_KEY"


def _require_lib() -> None:
    if Fernet is None:
        raise ConfigError(
            "Encryption requires the 'cryptography' package. "
            "Install it (pip install -r requirements.txt) or set "
            "ENCRYPT_OUTPUT=false."
        )


def generate_key() -> str:
    """Return a fresh, URL-safe base64 Fernet key as a string."""
    _require_lib()
    return Fernet.generate_key().decode("ascii")


def load_key(key: str | None = None) -> bytes:
    """Resolve the key from the argument or ``PHI_ENCRYPTION_KEY``."""
    _require_lib()
    resolved = (key or os.getenv(ENV_KEY, "")).strip()
    if not resolved:
        raise ConfigError(
            f"{ENV_KEY} is not set. Generate one with: python -m odoo_extract.crypto"
        )
    return resolved.encode("ascii")


def _cipher(key: bytes) -> "Fernet":
    _require_lib()
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"{ENV_KEY} is not a valid Fernet key (expected URL-safe base64, "
            f"32 bytes). Generate one with: python -m odoo_extract.crypto"
        ) from exc


def encrypt(data: bytes, key: bytes) -> bytes:
    """Encrypt ``data`` with the given key."""
    return _cipher(key).encrypt(data)


def decrypt(token: bytes, key: bytes) -> bytes:
    """Decrypt ``token``; raises ConfigError on a wrong key / tampered file."""
    try:
        return _cipher(key).decrypt(token)
    except InvalidToken as exc:
        raise ConfigError(
            "Decryption failed: wrong key or the file has been corrupted/tampered."
        ) from exc


if __name__ == "__main__":  # `python -m odoo_extract.crypto` prints a new key
    print(generate_key())
