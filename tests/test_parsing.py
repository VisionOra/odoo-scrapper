"""Unit tests for value normalization (no browser required)."""

from __future__ import annotations

import pytest

from odoo_extract.parsing import name_of, to_float


@pytest.mark.parametrize(
    "value, expected",
    [
        (1234.5, 1234.5),
        (10, 10.0),
        (0, 0.0),
        ("", 0.0),
        (None, 0.0),
        (False, 0.0),
        ("1,234.50", 1234.50),  # US thousands + decimal
        ("1.234,50", 1234.50),  # European thousands + decimal
        ("1,234,567.89", 1234567.89),  # US, multiple thousands
        ("1.234.567,89", 1234567.89),  # European, multiple thousands
        ("$ 295.00", 295.00),  # currency symbol stripped
        ("44,25 €", 44.25),  # trailing symbol, European decimal
        ("-12.50", -12.50),  # negative
        ("not a number", 0.0),  # garbage -> 0.0, never raises
        ("1 234,56", 1234.56),  # space thousands + European decimal
    ],
)
def test_to_float(value, expected):
    assert to_float(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "field, expected",
    [
        ([7, "Acoustic Bloc Screens"], "Acoustic Bloc Screens"),  # legacy m2o
        ((7, "Cabinet"), "Cabinet"),  # tuple form
        ({"id": 7, "display_name": "Desk"}, "Desk"),  # Odoo 19 web_read
        ({"id": 7}, ""),  # id only -> empty
        (False, ""),  # unset many2one
        (None, ""),
        ("Plain String", "Plain String"),
        ([7], "[7]"),  # malformed single-element -> str() fallback, never raises
    ],
)
def test_name_of(field, expected):
    assert name_of(field) == expected
