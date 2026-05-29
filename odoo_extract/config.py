"""Configuration loading from environment (Specification §8.1)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from dotenv import load_dotenv

from .errors import ConfigError


def _as_bool(value: str, *, default: bool) -> bool:
    """Parse a truthy/falsey env string; unknown values fall back to default."""
    val = value.strip().lower()
    if val in {"true", "1", "yes", "on"}:
        return True
    if val in {"false", "0", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class Config:
    url: str
    email: str
    # repr=False so credentials/keys can never leak via an accidental
    # log/repr of the Config object.
    password: str = field(repr=False)
    target_customer: str = "Deco Addict"
    headless: bool = True
    output_file: str = "invoice_lines.json"
    user_data_dir: str = ".pw-profile"
    # Chromium sandbox is ON by default; only disable it explicitly (e.g. when
    # running as root inside a container). Disabling weakens browser isolation.
    no_sandbox: bool = False
    # Failure artifacts (screenshot + Playwright trace) contain UNREDACTED data.
    # On by default for debuggability; disable where that is unacceptable.
    save_artifacts: bool = True
    # HIPAA technical safeguards (see COMPLIANCE.md):
    encrypt_output: bool = True  # §164.312(a)(2)(iv) encryption at rest
    encryption_key: str = field(default="", repr=False)  # PHI_ENCRYPTION_KEY
    audit_log_file: str = "phi_audit.log"  # §164.312(b) audit controls

    @staticmethod
    def load() -> "Config":
        load_dotenv()

        def required(key: str) -> str:
            val = os.getenv(key, "").strip()
            if not val:
                raise ConfigError(f"Missing required env var: {key}")
            return val

        url = required("ODOO_URL").rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError(
                f"ODOO_URL must be an absolute http(s) origin, e.g. "
                f"https://your-instance.odoo.com (got: {url!r})"
            )
        allow_http = _as_bool(os.getenv("ALLOW_INSECURE_HTTP", "false"), default=False)
        if parsed.scheme == "http" and not allow_http:
            # Transmission security (§164.312(e)) — PHI must move over TLS.
            raise ConfigError(
                "ODOO_URL uses insecure http://. PHI must be transmitted over "
                "TLS. Use https://, or set ALLOW_INSECURE_HTTP=true only for "
                "local non-PHI testing."
            )

        encrypt_output = _as_bool(os.getenv("ENCRYPT_OUTPUT", "true"), default=True)
        encryption_key = os.getenv("PHI_ENCRYPTION_KEY", "").strip()
        if encrypt_output and not encryption_key:
            raise ConfigError(
                "ENCRYPT_OUTPUT is on but PHI_ENCRYPTION_KEY is not set. "
                "Generate a key with: python -m odoo_extract.crypto "
                "(or set ENCRYPT_OUTPUT=false to write plaintext)."
            )

        return Config(
            url=url,
            email=required("ODOO_EMAIL"),
            password=required("ODOO_PASSWORD"),
            target_customer=os.getenv("TARGET_CUSTOMER", "Deco Addict").strip(),
            headless=_as_bool(os.getenv("HEADLESS", "true"), default=True),
            output_file=os.getenv("OUTPUT_FILE", "invoice_lines.json").strip(),
            user_data_dir=os.getenv("USER_DATA_DIR", ".pw-profile").strip(),
            no_sandbox=_as_bool(os.getenv("NO_SANDBOX", "false"), default=False),
            save_artifacts=_as_bool(os.getenv("SAVE_ARTIFACTS", "true"), default=True),
            encrypt_output=encrypt_output,
            encryption_key=encryption_key,
            audit_log_file=os.getenv("AUDIT_LOG_FILE", "phi_audit.log").strip(),
        )
