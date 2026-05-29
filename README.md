# The Enterprise ERP Extraction

Headless Playwright automation that logs into an Odoo ERP trial, filters
Customer Invoices to **Posted**, drills into the **Deco Addict** invoice, and
extracts every invoice line into a clean JSON array — with strict in-memory PHI
sanitization so no customer/product data ever reaches the console.

See [SPECIFICATION.md](SPECIFICATION.md) for the full design rationale.

## Files

| File | Purpose |
|------|---------|
| `SPECIFICATION.md` | Professional specification document (deliverable 1) |
| `extract_invoice_lines.py` | Main async Playwright workflow (deliverable 2) |
| `sanitizer.py` | In-memory PHI redaction logging layer (§7) |
| `requirements.txt` | Pinned dependencies |
| `.env.example` | Configuration template (copy to `.env`) |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # then fill in ODOO_URL / EMAIL / PASSWORD
```

`.env` values:

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `ODOO_URL` | ✅ | — | Base origin only, e.g. `https://x.odoo.com` |
| `ODOO_EMAIL` | ✅ | — | Trial login email |
| `ODOO_PASSWORD` | ✅ | — | Set one via Settings → Users if you used a magic link |
| `TARGET_CUSTOMER` | | `Deco Addict` | Customer to drill into |
| `HEADLESS` | | `true` | `false` to watch the browser |
| `OUTPUT_FILE` | | `invoice_lines.json` | Output path |
| `USER_DATA_DIR` | | `.pw-profile` | Persistent browser profile |

## Run

```bash
python extract_invoice_lines.py
```

Produces `invoice_lines.json`:

```json
[
  {
    "product_name": "Acoustic Bloc Screens",
    "quantity": 1.0,
    "unit_price": 295.0,
    "tax_amount": 44.25
  }
]
```

## How the constraints are satisfied

- **No hardcoded sleeps** — every wait blocks on real state: `expect_response`
  on Odoo JSON-RPC calls, `expect_navigation`, and element actionability.
  There is no `wait_for_timeout` / `time.sleep` anywhere.
- **Resilient locators** — selectors use Odoo field-name attributes
  (`[name="product_id"]`), semantic roles (`get_by_role("tab", ...)`), visible
  text, and relative XPath — never ephemeral generated IDs.
- **Network interception** — `RpcCapture` listens to all `/web/dataset/`
  responses and keeps the `account.move` payload as the primary data source;
  the DOM scrape is a reconciling fallback.
- **PHI sanitization** — `sanitizer.py` redacts customer/product strings at the
  logging boundary, in memory. The console shows `[REDACTED_CUSTOMER]` /
  `[REDACTED_PRODUCT]`; only `invoice_lines.json` holds real values.

## Tests

The PHI sanitizer and number normalizer are unit-testable without a browser:

```bash
python -c "from sanitizer import build_logger; print('sanitizer import OK')"
```

On failure the script saves `failure.png` and `trace.zip` (Playwright trace)
locally for debugging.
