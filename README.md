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
| `main.py` | Entry point — wires config, logging, and error handling |
| `odoo_extract/` | Async Playwright workflow, split into focused modules (deliverable 2) |
| `odoo_extract/sanitizer.py` | In-memory PHI redaction logging layer (§7) |
| `tests/` | Browser-free unit tests (parsing, sanitizer, RPC predicates, config) |
| `requirements.txt` | Pinned runtime dependencies |
| `requirements-dev.txt` | Adds the test toolchain (`pytest`) |
| `.env.example` | Configuration template (copy to `.env`) |

The `odoo_extract` package is organized one responsibility per module:
`config`, `errors`, `models`, `constants`, `parsing`, `rpc_capture`, `auth`,
`navigation`, `extraction`, `drilldown`, `retry`, and `orchestrator`. Each file
is kept small and single-purpose.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # runtime only
# or: pip install -r requirements-dev.txt   # runtime + test tooling
playwright install chromium

cp .env.example .env        # then fill in ODOO_URL / EMAIL / PASSWORD
```

`.env` values:

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `ODOO_URL` | ✅ | — | Base origin only, e.g. `https://x.odoo.com` (validated: must be an absolute `http(s)` URL) |
| `ODOO_EMAIL` | ✅ | — | Trial login email |
| `ODOO_PASSWORD` | ✅ | — | Set one via Settings → Users if you used a magic link |
| `TARGET_CUSTOMER` | | `Deco Addict` | Customer to drill into |
| `HEADLESS` | | `true` | `false` to watch the browser (accepts `true/false/1/0/yes/no/on/off`) |
| `OUTPUT_FILE` | | `invoice_lines.json` | Output path |
| `USER_DATA_DIR` | | `.pw-profile` | Persistent browser profile (one run per dir — see below) |
| `NO_SANDBOX` | | `false` | `true` adds Chromium `--no-sandbox`. Only enable in a hardened container; it weakens browser isolation |
| `SAVE_ARTIFACTS` | | `true` | `false` disables `failure.png` / `trace.zip`. These contain **unredacted** page data — disable where that's unacceptable |
| `LOG_LEVEL` | | `INFO` | Console verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |

## Run

```bash
python main.py
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

- **No hardcoded sleeps** — every *synchronization* wait blocks on real state:
  `expect_response` on Odoo JSON-RPC calls, `expect_navigation`, element
  visibility/detachment, and auto-waiting actionability. There is no
  `wait_for_timeout` / `time.sleep` for synchronization. (The only `asyncio.sleep`
  is the exponential **backoff between retries** in `retry.py` — that is not a
  state wait.)
- **Deterministic RPC waits** — search vs. read responses are told apart by
  parsing the JSON-RPC **request post body** (`params.model` / `params.method`),
  with a URL-path fallback — not fragile URL substrings. So waiting for a record
  `web_read` on `account.move` can never resolve early on a `web_search_read`.
- **Resilient locators** — selectors use Odoo field-name attributes
  (`[name="product_id"]`), semantic roles (`get_by_role("tab", ...)`), visible
  text, and relative XPath — never ephemeral generated IDs.
- **Network interception** — `RpcCapture` inspects `/web/dataset/` responses and
  keeps the `account.move` `web_read` payload as the primary data source; the DOM
  scrape is a reconciling fallback. It gates on (model, method) **before**
  deserializing, so large list-view payloads are never parsed needlessly.
- **PHI sanitization** — `sanitizer.py` redacts customer/product strings at the
  logging boundary, in memory. Product names are registered with the redactor the
  *moment* they are extracted — before any further log call — and the filter also
  scrubs exception **tracebacks**, so PHI cannot leak even via an error. The
  console shows `[REDACTED_CUSTOMER]` / `[REDACTED_PRODUCT]`; only
  `invoice_lines.json` holds real values.
- **Production hardening** — bounded retry + backoff on auth/navigation; atomic
  output write (temp file + `os.replace`, never a truncated JSON); a PID-based
  profile lock that blocks concurrent runs (and reclaims a stale lock after a
  crash); the Chromium sandbox stays on unless `NO_SANDBOX=true`.

## Tests

Browser-free unit tests cover the pure logic — number/currency normalization,
many2one name resolution, the PHI redaction filter (including traceback
scrubbing), the RPC search/read predicates, and config loading/validation:

```bash
pip install -r requirements-dev.txt
pytest
```

## Failure artifacts

On failure (when `SAVE_ARTIFACTS=true`, the default) the script saves
`failure.png` and `trace.zip` (Playwright trace) locally for debugging.

> ⚠️ These artifacts contain **unredacted** page content (customer/product
> data). They are gitignored and never transmitted, but treat them as sensitive
> on disk, and set `SAVE_ARTIFACTS=false` in environments where writing raw data
> is unacceptable.
