# Technical Specification — The Enterprise ERP Extraction

**Project:** Headless Odoo ERP Billing-Line Extraction
**Author:** Muneeb Rana
**Date:** 2026-05-29
**Stack:** Python 3.11+, Playwright (async), `python-dotenv`
**Target:** Odoo SaaS 19.3+e (Enterprise) — selectors are version-tolerant 16→19

---

## 1. Objective

Build a resilient, headless browser automation that authenticates against a freshly
provisioned Odoo ERP trial, navigates the **Invoicing** module, filters invoices to
**Posted** status, drills into the invoice for customer **Deco Addict**, and extracts
every invoice line (`product`, `quantity`, `unit_price`, `tax_amount`) into a clean,
standardized JSON array written to disk.

The system treats all billing data as **PHI (Protected Health Information)**. No
customer name or product detail may ever reach the console or application logs. Only
the final sanitized JSON is persisted to a local file.

---

## 2. Scope & Deliverables

| # | Deliverable | Status |
|---|-------------|--------|
| 1 | This specification document | ✅ |
| 2 | Working async Python Playwright script | Phase 2 |
| 3 | In-memory PHI sanitization layer (demonstrable) | Phase 2 |
| 4 | `requirements.txt` + `.env.example` | Phase 2 |

**Out of scope:** Odoo trial provisioning and demo-data loading are performed manually
once (Part 1 of the challenge). The script targets the resulting instance.

---

## 3. Architectural Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        main() orchestrator                     │
│                                                                │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐ │
│  │  AuthModule  │ → │  NavModule   │ → │  ExtractionModule  │ │
│  │  (login)     │   │ (filter,drill│   │ (hybrid: RPC +DOM) │ │
│  └──────────────┘   │   into row)  │   └─────────┬──────────┘ │
│                     └──────────────┘             │            │
│                                                  ▼            │
│         ┌──────────────────────────────────────────────────┐ │
│         │   SanitizingLogger  (in-memory PHI redaction)     │ │
│         └──────────────────────────────────────────────────┘ │
│                                                  │            │
│                                                  ▼            │
│                                         invoice_lines.json    │
└──────────────────────────────────────────────────────────────┘
```

### Key design pillars

1. **State-driven waiting** — every step blocks on an *observable condition*
   (network idle, element visibility, response predicate), never a fixed timer.
2. **Network-first, DOM-fallback extraction** — primary data source is the Odoo
   JSON-RPC response; DOM scraping is a resilient fallback.
3. **PHI sanitization at the logging boundary** — sensitive data is redacted
   *before* any record reaches a console/log handler.

---

## 4. The "No Hardcoded Sleeps" Strategy

`page.wait_for_timeout()` and `time.sleep()` are **forbidden** (instant DQ). Odoo is an
OWL (Odoo Web Library) SPA — it renders asynchronously after XHR/JSON-RPC calls. We
synchronize on *real* application state instead:

| Situation | Wait mechanism used |
|-----------|--------------------|
| After login submit | `page.wait_for_url(...)` + wait for top-bar nav to be visible |
| After navigating to a module | `page.wait_for_load_state("networkidle")` + first list row `visible` |
| After applying a filter | **`page.expect_response()`** on `**/web/dataset/call_kw/**` *web_search_read* — the list only repaints once data returns |
| After opening a record | `expect_response()` on the `read` call for `account.move` + form view `visible` |
| Element interaction | Playwright auto-waiting (`expect(locator).to_be_visible()`) — built-in actionability checks |

The filter and drill-down steps use **`page.expect_response()` as the authoritative
trigger**: we click, await the matching JSON-RPC response, *then* assert UI state. This
is both faster and more deterministic than any sleep.

---

## 5. Resilient Locator Strategy

Odoo's OWL framework assigns **ephemeral, regenerating element IDs** (e.g.
`o_field_widget_o_1742`). Any selector bound to these breaks on the next render.
Locator rules for this project:

- **Never** select by generated numeric ID or auto-class hashes.
- **Prefer**, in order:
  1. Stateless Odoo data attributes — `[name="..."]`, `[data-menu-xmlid]`,
     `[data-tooltip]`, `[role="..."]`. These map to model field names and are stable
     across renders.
  2. Semantic roles + accessible names — `get_by_role("button", name="Filters")`,
     `get_by_role("row")`.
  3. Visible text — `get_by_text("Posted", exact=True)`.
  4. **Structural / relative XPath** as last resort — e.g. locate a row by the
     customer cell, then traverse to the sibling status cell:
     `//tr[.//*[normalize-space()="Deco Addict"]]`.
- Scope every locator to its container (search panel, list view, the Invoice Lines
  notebook page) to avoid cross-matching duplicate field names elsewhere on the form.

**Worked example — finding the Deco Addict row** (no IDs, structural only):

```python
row = page.locator("tr.o_data_row").filter(has_text="Deco Addict").first
```

**Worked example — the Invoice Lines tab** (semantic, by tab name not index):

```python
page.get_by_role("tab", name="Invoice Lines").click()
```

---

## 6. Network Interception (Hybrid Extraction)

Odoo's web client talks to the server over **JSON-RPC** at
`/web/dataset/call_kw/<model>/<method>`. Reading the invoice form fires a `read`
(or `web_read`) on `account.move` whose payload contains the full `invoice_line_ids`
with all numeric fields already computed server-side.

### Primary path — RPC capture
We register a `page.on("response", ...)` handler that inspects responses to
`**/web/dataset/call_kw/**` and `**/web/dataset/web_read/**`. When the response for the
`account.move` record arrives, we parse `result[0].invoice_line_ids` and map each line:

```
product_id[1]     → product_name
quantity          → quantity
price_unit        → unit_price
price_total - price_subtotal  → tax_amount   (or read tax_ids breakdown)
```

This is the most reliable source: data is structured, complete, and untouched by UI
rendering/pagination.

### Fallback path — resilient DOM scrape
If the RPC shape changes or the field is loaded lazily, we scrape the rendered
Invoice Lines table by **column field name** (`[name="product_id"]`,
`[name="quantity"]`, `[name="price_unit"]`, `[name="price_total"]`) per data row —
not by column index — and reconcile.

The orchestrator prefers RPC data and validates row count against the DOM; on mismatch
it logs a *sanitized* warning and falls back to the DOM result.

---

## 7. PHI Sanitization Design (The HIPAA Twist)

**Requirement:** customer names and product details must never appear in console output
or logs. Only the final JSON file may contain real values.

### Mechanism — redaction at the logging boundary, in memory

We implement a `SanitizingLogger` wrapper around Python's `logging`. Two complementary
layers:

1. **A logging `Filter`** that runs a set of compiled regex/term substitutions over
   every `LogRecord.msg` (and args) *before* it is emitted. Known PHI tokens — the
   target customer name and any extracted product strings — are registered at runtime
   and replaced with `[REDACTED_CUSTOMER]` / `[REDACTED_PRODUCT]`.
2. **A structured-logging discipline**: code never f-strings PHI into a message.
   Sensitive fields are passed as a separate `extra={"phi": {...}}` dict which the
   formatter *drops entirely* from console output. Counts and IDs (non-PHI) may be
   logged for observability (e.g. `"Extracted 4 invoice lines"`).

The clean, *unsanitized* data lives only in a local variable and is serialized
directly to `invoice_lines.json` via `json.dump` — it is never routed through the
logger.

```
 in-memory data ──► json.dump ──► invoice_lines.json     (real values)
       │
       └─► logger.info("Extracted %d lines", n)          (counts only, sanitized)
```

**Demonstration of sanitization:** the script intentionally calls
`log.info("Processing customer %s", customer_name)` with the real name; the console
shows `Processing customer [REDACTED_CUSTOMER]`, proving the in-memory scrub works
while the JSON file still holds the true value.

---

## 8. Execution Flow (step by step)

1. **Bootstrap** — load `.env` (URL, email, password); init `SanitizingLogger`;
   launch **persistent** Chromium context (`launch_persistent_context`) headless.
2. **Authenticate** — go to `/web/login`, fill credentials by `[name="login"]` /
   `[name="password"]`, submit, `wait_for_url("**/odoo/**" or "**/web**")`.
3. **Navigate** — open Invoicing via the apps menu / direct action URL; await list view.
4. **Filter** — open the search panel, **remove default facets** (close each filter
   chip via its `aria-label="Remove"` control), apply **Posted**: primary path uses the
   *Filters* dropdown menu item; if that label is absent (Odoo relabels menus between
   releases) it falls back to typing "Posted" into the search input and selecting the
   *Status* autocomplete facet. Either way it awaits the `web_search_read` response.
5. **Drill down** — locate the Deco Addict row structurally, click; await the
   `account.move` read response + form view visible.
6. **Extract** — activate the **Invoice Lines** tab; assemble lines from the captured
   RPC payload (fallback to DOM); normalize into the JSON schema.
7. **Persist & report** — `json.dump` to `invoice_lines.json`; log a sanitized summary;
   close context.

---

## 9. Output Schema

```json
[
  {
    "product_name": "string",
    "quantity": 0.0,
    "unit_price": 0.0,
    "tax_amount": 0.0
  }
]
```

Field names mirror the challenge's required columns verbatim — *Product Name*,
*Quantity*, *Unit Price*, *Tax Amount* — in snake_case. All numeric fields are floats;
currency symbols and thousands separators are stripped during normalization.

---

## 10. Error Handling & Robustness

- **Typed failure points** — auth, navigation, filter, row-not-found, extraction —
  each raises a domain-specific exception caught by the orchestrator and logged
  (sanitized) with an actionable message and non-zero exit code.
- **Idempotent filtering** — clearing existing facets before applying *Posted* makes
  the step independent of the account's default search state.
- **RPC/DOM reconciliation** — row-count check guards against silent partial extracts.
- **Artifacts on failure** — on exception, capture a screenshot + `trace.zip`
  (Playwright tracing) for debugging; screenshots are stored locally only.

---

## 11. Configuration & Run

`.env` (never committed):

```
ODOO_URL=https://your-instance.odoo.com
ODOO_EMAIL=...
ODOO_PASSWORD=...
TARGET_CUSTOMER=Deco Addict
HEADLESS=true
```

Run:

```bash
pip install -r requirements.txt
playwright install chromium
python extract_invoice_lines.py
```

Produces `invoice_lines.json` in the project root.

---

## 12. Why these choices (senior rationale)

- **Async Playwright** gives first-class `expect_response` interception — central to the
  no-sleep and network-hook requirements.
- **Field-name locators over IDs** directly answer Odoo's ephemeral-ID problem.
- **RPC-first extraction** yields server-computed, fully-precise figures and is immune
  to UI pagination/virtual scrolling that would trip a pure DOM scrape.
- **Redaction at the logging boundary** (not at call sites) means PHI cannot leak even
  if a future developer logs a sensitive variable — the guardrail is structural.
