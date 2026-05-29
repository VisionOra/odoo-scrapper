"""Output schema (Specification §9)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InvoiceLine:
    product_name: str
    quantity: float
    unit_price: float
    tax_amount: float


@dataclass
class Invoice:
    invoice_number: str
    lines: list[InvoiceLine]
