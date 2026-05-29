# The Enterprise ERP Extraction

Headless Playwright automation that logs into an Odoo ERP trial, filters
Customer Invoices to **Posted**, drills into the **Deco Addict** invoice, and
extracts every invoice line into a clean JSON array — with a set of demonstrable
**HIPAA-style technical safeguards** layered on top: in-memory PHI redaction,
AES encryption at rest, TLS-only transport, and a tamper-evident PHI **access
audit log** with end-of-run monitoring.

See [SPECIFICATION.md](SPECIFICATION.md) for the design rationale and
[COMPLIANCE.md](COMPLIANCE.md) for the HIPAA control matrix, threat model, and a
3-minute demo script.

> ⚠️ This is a **demonstration of control design and monitoring**, not a HIPAA
> compliance attestation — the demo data isn't real PHI, and real compliance
> needs a BAA plus organizational safeguards. See COMPLIANCE.md §6.

## Files

| File | Purpose |
|------|---------|
| `SPECIFICATION.md` | Professional specification document (deliverable 1) |
| `COMPLIANCE.md` | HIPAA technical-safeguard control matrix, threat model, demo script |
| `main.py` | Entry point — config, logging, controls, error handling |
| `decrypt_output.py` | Demo helper — decrypts `invoice_lines.json.enc` with the key |
| `odoo_extract/` | Async Playwright workflow, split into focused modules (deliverable 2) |
| `odoo_extract/sanitizer.py` | In-memory PHI redaction logging layer (§7) |
| `odoo_extract/audit.py` | Hash-chained PHI access audit log + monitoring |
| `odoo_extract/crypto.py` | AES (Fernet) encryption at rest |
| `odoo_extract/control_health.py` | Fail-closed redaction self-test |
| `tests/` | Browser-free unit tests (parsing, sanitizer, predicates, config, audit, crypto) |
| `requirements.txt` | Pinned runtime dependencies |
| `requirements-dev.txt` | Adds the test toolchain (`pytest`) |
| `.env.example` | Configuration template (copy to `.env`) |

The `odoo_extract` package is organized one responsibility per module:
`config`, `errors`, `models`, `constants`, `parsing`, `rpc_capture`, `auth`,
`navigation`, `extraction`, `drilldown`, `retry`, `orchestrator`, and the HIPAA
controls `sanitizer`, `audit`, `crypto`, `control_health`. Each file is kept
small and single-purpose.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # runtime only
# or: pip install -r requirements-dev.txt   # runtime + test tooling
playwright install chromium

cp .env.example .env        # then fill in ODOO_URL / EMAIL / PASSWORD

# Encryption at rest is ON by default — generate a key and put it in .env:
python -m odoo_extract.crypto          # prints a fresh PHI_ENCRYPTION_KEY
```

`.env` values:

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `ODOO_URL` | ✅ | — | Base origin only, e.g. `https://x.odoo.com`. **Must be `https://`** (TLS); `http://` is rejected unless `ALLOW_INSECURE_HTTP=true` |
| `ODOO_EMAIL` | ✅ | — | Trial login email |
| `ODOO_PASSWORD` | ✅ | — | Set one via Settings → Users if you used a magic link |
| `PHI_ENCRYPTION_KEY` | ✅* | — | Fernet/AES key; required when `ENCRYPT_OUTPUT=true`. Generate: `python -m odoo_extract.crypto` |
| `ENCRYPT_OUTPUT` | | `true` | Encrypt the output at rest → `<OUTPUT_FILE>.enc`. `false` writes plaintext (still `0600`) |
| `AUDIT_LOG_FILE` | | `phi_audit.log` | Hash-chained PHI access audit log path |
| `ALLOW_INSECURE_HTTP` | | `false` | Permit `http://` — local non-PHI testing only |
| `TARGET_CUSTOMER` | | `Deco Addict` | Customer to drill into |
| `HEADLESS` | | `true` | `false` to watch the browser (accepts `true/false/1/0/yes/no/on/off`) |
| `OUTPUT_FILE` | | `invoice_lines.json` | Base output name (gets `.enc` when encrypted) |
| `USER_DATA_DIR` | | `.pw-profile` | Persistent browser profile (one run per dir — see below) |
| `NO_SANDBOX` | | `false` | `true` adds Chromium `--no-sandbox`. Only enable in a hardened container; it weakens browser isolation |
| `SAVE_ARTIFACTS` | | `true` | `false` disables `failure.png` / `trace.zip`. These contain **unredacted** page data — disable where that's unacceptable |
| `LOG_LEVEL` | | `INFO` | Console verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |

## Run

```bash
python main.py
```

With encryption on (the default) this produces **`invoice_lines.json.enc`** plus
a `phi_audit.log`, and prints a monitoring summary. Decrypt to view the data:

```bash
PHI_ENCRYPTION_KEY=… python decrypt_output.py invoice_lines.json.enc
```

The decrypted payload (or the plaintext file when `ENCRYPT_OUTPUT=false`):

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
  console shows `[REDACTED_CUSTOMER]` / `[REDACTED_PRODUCT]`; only the encrypted
  output holds real values.
- **Production hardening** — bounded retry + backoff on auth/navigation; atomic
  output write (temp file + `os.replace`, never a truncated JSON); a PID-based
  profile lock that blocks concurrent runs (and reclaims a stale lock after a
  crash); the Chromium sandbox stays on unless `NO_SANDBOX=true`.

## HIPAA-style controls & monitoring

Full mapping to 45 CFR §164.312 + threat model + demo script in
[COMPLIANCE.md](COMPLIANCE.md). In brief:

| Safeguard | Implementation | How it's monitored |
|---|---|---|
| Audit controls (§164.312(b)) | Hash-chained, append-only PHI access log (`audit.py`) | End-of-run **monitoring summary**; `phi_audit.log` |
| Integrity (§164.312(c)(1)) | Each record chains the prior SHA-256 | Summary prints `audit chain : VERIFIED ✓ / TAMPERED ✗` |
| Encryption at rest (§164.312(a)(2)(iv)) | AES/Fernet → `invoice_lines.json.enc` (`crypto.py`) | `decrypt_output.py` recovers it only with the key |
| Transmission security (§164.312(e)) | `https://` enforced; `http://` rejected | Config error on an insecure URL |
| Minimum necessary / de-ID (§164.514) | Log redaction; **pseudonymized** audit subjects | Console shows `[REDACTED_*]`; audit shows `sha256:…` |
| Evaluation (§164.308(a)(8)) | **Fail-closed** canary self-test at startup | First log line: `PHI redaction control: ACTIVE ✓` |
| Access control (§164.312(a)(1)) | Output + audit log created `0600` | `stat` the files |

Audit records carry only WHO/WHAT/WHEN and field **counts** — never customer or
product values. **This is a control-design demo, not a compliance attestation;**
production additionally needs a BAA and organizational safeguards (COMPLIANCE.md §6).

## Tests

Browser-free unit tests cover the pure logic — number/currency normalization,
many2one name resolution, the PHI redaction filter (including traceback
scrubbing), the RPC search/read predicates, config loading/validation, the audit
hash-chain (including tamper detection), and the AES round-trip + fail-closed
self-test:

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
