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
    # repr=False so the password can never leak via an accidental log/repr of
    # the Config object.
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

        return Config(
            url=url,
            email=required("ODOO_EMAIL"),
            password=required("ODOO_PASSWORD"),
            target_customer=os.getenv("TARGET_CUSTOMER", "Deco Addict").strip(),
            headless=_as_bool(os.getenv("HEADLESS", "true"), default=True),
            output_file=os.getenv("OUTPUT_FILE", "invoice_lines.json").strip(),
            user_data_dir=os.getenv("USER_DATA_DIR", ".pw-profile").strip(),
            no_sandbox=_as_bool(os.getenv("NO_SANDBOX", "false"), default=False),
            save_artifacts=_as_bool(
                os.getenv("SAVE_ARTIFACTS", "true"), default=True
            ),
        )
