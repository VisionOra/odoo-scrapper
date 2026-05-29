"""Configuration loading from environment (Specification §8.1)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .errors import ConfigError


@dataclass(frozen=True)
class Config:
    url: str
    email: str
    password: str
    target_customer: str
    headless: bool
    output_file: str
    user_data_dir: str

    @staticmethod
    def load() -> "Config":
        load_dotenv()

        def required(key: str) -> str:
            val = os.getenv(key, "").strip()
            if not val:
                raise ConfigError(f"Missing required env var: {key}")
            return val

        return Config(
            url=required("ODOO_URL").rstrip("/"),
            email=required("ODOO_EMAIL"),
            password=required("ODOO_PASSWORD"),
            target_customer=os.getenv("TARGET_CUSTOMER", "Deco Addict").strip(),
            headless=os.getenv("HEADLESS", "true").lower() != "false",
            output_file=os.getenv("OUTPUT_FILE", "invoice_lines.json").strip(),
            user_data_dir=os.getenv("USER_DATA_DIR", ".pw-profile").strip(),
        )
