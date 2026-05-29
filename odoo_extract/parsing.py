"""Value-normalization helpers (Specification §9)."""

from __future__ import annotations


def to_float(value: object) -> float:
    """Normalize a numeric/currency string or number to float (§9)."""
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    cleaned = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".,-")
    if "," in cleaned and "." in cleaned:
        # Both present: the LAST-occurring separator is the decimal point.
        # Handles US "1,234.50" and European "1.234,50" alike.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned or 0)
    except ValueError:
        return 0.0


def name_of(field: object) -> str:
    """
    Resolve a many2one field's display name across Odoo serialization formats.

    - Legacy (<=18 read):  [id, "Display Name"]
    - Odoo 19 web_read:    {"id": .., "display_name": ".."}  (or just {"id": ..})
    - Empty / unset:       False  ->  ""
    """
    if isinstance(field, dict):
        return str(field.get("display_name") or "")
    if isinstance(field, (list, tuple)) and len(field) == 2:
        return str(field[1])
    if field is False or field is None:
        return ""
    return str(field)
