"""Demo helper: decrypt an encrypted PHI output file.

The extractor writes ``invoice_lines.json.enc`` (AES via Fernet) when encryption
is enabled. This script decrypts it using the same ``PHI_ENCRYPTION_KEY`` and
prints the JSON to stdout — demonstrating that the data is recoverable only with
the key.

Usage::

    PHI_ENCRYPTION_KEY=... python decrypt_output.py invoice_lines.json.enc
    # or redirect to a file:
    PHI_ENCRYPTION_KEY=... python decrypt_output.py invoice_lines.json.enc > out.json
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from odoo_extract import crypto
from odoo_extract.errors import ConfigError


def main(argv: list[str]) -> int:
    load_dotenv()
    if len(argv) != 2:
        print("usage: python decrypt_output.py <file.enc>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    if not path.exists():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 2

    try:
        key = crypto.load_key()
        plaintext = crypto.decrypt(path.read_bytes(), key)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(plaintext.decode("utf-8"))
    if not plaintext.endswith(b"\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
