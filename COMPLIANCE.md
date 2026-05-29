# HIPAA Technical Safeguards — Implementation & Monitoring

**Scope of this document.** This is a *demonstration* of how HIPAA **technical
safeguards** (45 CFR §164.312) and their **monitoring** are implemented in code,
using Odoo invoice data as a stand-in for PHI. It shows the engineering controls
and how to observe them at runtime.

> **What this is NOT.** This project is **not HIPAA-certified or legally
> compliant**, and the demo data is not real PHI. Production compliance also
> requires controls that live *outside* a script — see
> [Out of scope](#out-of-scope-required-for-real-compliance). Treat this as
> evidence of *control design and monitoring competence*, not a compliance
> attestation.

---

## 1. Control matrix (45 CFR §164.312)

| HIPAA citation | Safeguard | How it's implemented here | Where | How to monitor / demo |
|---|---|---|---|---|
| §164.312(b) — **Audit controls** | Record access to ePHI | Append-only, **hash-chained** PHI access log (JSONL). Every access logs WHO/WHAT/WHEN + a field **count** — never the value. | `audit.py`, wired in `drilldown.py` / `main.py` | End-of-run "PHI access monitoring summary"; inspect `phi_audit.log` |
| §164.312(c)(1) — **Integrity** | Detect improper alteration | Each record embeds the prior record's SHA-256; `verify()` recomputes the chain and flags any edit/removal. | `audit.PhiAuditLog.verify` | Summary prints `audit chain : VERIFIED ✓` / `TAMPERED ✗` |
| §164.312(a)(2)(iv) — **Encryption at rest** | Encrypt ePHI | Output is AES-encrypted (Fernet: AES-128-CBC + HMAC) before touching disk → `invoice_lines.json.enc`. Key supplied via env / KMS, never in repo. | `crypto.py`, `orchestrator.write_output` | `ls` shows `.enc`; `decrypt_output.py` recovers it only with the key |
| §164.312(e)(1)(2) — **Transmission security** | Protect ePHI in transit | `ODOO_URL` must be `https://` (TLS); plain `http://` is rejected unless explicitly opted in for local non-PHI testing. | `config.Config.load` | Try an `http://` URL → config error |
| §164.312(a)(2)(i) — **Unique user / minimum necessary** | Limit access; de-identify | Logging boundary **redacts** customer/product strings to `[REDACTED_*]`; only the encrypted file holds real values. Audit subjects are **pseudonymized** (salted SHA-256). | `sanitizer.py`, `audit.pseudonymize` | Console shows `[REDACTED_CUSTOMER]`; audit shows `sha256:…` |
| §164.312(a)(1) — **Access control** | Least privilege | Output and audit log are created `0600` (owner-only). Browser sandbox stays on by default. | `orchestrator._atomic_write_bytes`, `audit._append` | `stat -f '%A'` on the files → `600` |
| §164.308(a)(8) — **Evaluation** | Verify controls work | Fail-closed **self-test**: a canary PHI value is pushed through the redactor at startup; if it isn't scrubbed, the run aborts before any PHI is processed. | `control_health.py` | Startup logs `PHI redaction control: ACTIVE ✓` |
| §164.312(c)(1) — **Integrity (output)** | No partial/corrupt writes | Atomic write (temp file + `os.replace`); a crash mid-write can't leave truncated ePHI. | `orchestrator._atomic_write_bytes` | — |

---

## 2. PHI handling principles applied

- **Data minimization (§164.502(b)).** Only the four required fields per line are
  extracted; nothing else is persisted.
- **De-identification at the log boundary (§164.514).** Real values never reach
  the console; redaction is *structural* (product names are registered with the
  redactor the moment they're read, and even exception tracebacks are scrubbed).
- **Pseudonymization for audit.** The audited "subject" is a salted SHA-256
  reference, so access can be correlated without exposing identity.
- **Fail-closed.** If the redaction control can't be verified, we **stop** rather
  than risk a leak.
- **Defense in depth.** Encryption at rest *and* in transit *and* redaction in
  logs *and* an immutable audit trail — no single control is load-bearing.

---

## 3. Data-flow & trust boundaries

```
 Odoo SaaS ──TLS (https only)──▶ Playwright/Chromium (sandboxed, local profile 0600)
                                        │
                                        ├─▶ console logs  ──▶ PHI REDACTED ([REDACTED_*])
                                        │
                                        ├─▶ phi_audit.log ──▶ hash-chained, pseudonymized, 0600
                                        │
                                        └─▶ in-memory data ──▶ AES-encrypt ──▶ invoice_lines.json.enc (0600)
                                                                                   │
                                                              key (env/KMS) ──▶ decrypt_output.py ──▶ cleartext
```

The only place cleartext PHI rests on disk is inside the **encrypted** file;
recovering it requires the out-of-band key.

---

## 4. Threat model (abridged)

| Threat | Mitigation |
|---|---|
| PHI leaks into logs / CI output | Redaction filter + fail-closed self-test |
| PHI leaks via an exception/stack trace | Filter scrubs `exc_text` / `stack_info` |
| ePHI readable on disk | AES encryption at rest; `0600` perms |
| ePHI intercepted in transit | TLS-only (`https://` enforced) |
| Audit log altered to hide access | Hash-chained records; `verify()` detects tampering |
| Stale/forgotten artifacts leak PHI | `trace.zip`/`failure.png` gitignored, off-switch (`SAVE_ARTIFACTS=false`), documented as raw |
| Key committed to source | Key only via env/KMS; `.env` gitignored; `repr=False` on the field |

---

## 5. Demo script (≈3 minutes)

1. **Control health (fail-closed).** Run `python main.py`; point out the first
   line: `PHI redaction control: ACTIVE ✓`. Explain: if the scrubber were
   broken, the run would abort here.
2. **Redaction live.** Show the console line `Processing customer
   [REDACTED_CUSTOMER]` — the real name never appears, even though the code
   logged it deliberately.
3. **Encryption at rest.** `cat invoice_lines.json.enc` → ciphertext. Then
   `PHI_ENCRYPTION_KEY=… python decrypt_output.py invoice_lines.json.enc` →
   real JSON. "Recoverable only with the key."
4. **Audit + monitoring.** Show the end-of-run **monitoring summary** and open
   `phi_audit.log`: WHO/WHAT/WHEN, pseudonymized subject, counts — no PHI.
5. **Integrity.** Edit one line in `phi_audit.log`, re-run the summary (or the
   `verify()` test) → `TAMPERED ✗`. The chain catches it.
6. **Tests.** `pytest` → controls are unit-tested (redaction, traceback scrub,
   encryption round-trip, audit chain integrity, fail-closed self-test).

---

## 6. Out of scope (required for real compliance)

These are **organizational/legal**, not code, and must not be implied by this demo:

- **Business Associate Agreement (BAA)** with every processor of PHI (e.g. the
  ERP/SaaS vendor and any host). *Without a BAA, no amount of code is compliant.*
- Administrative safeguards (§164.308): risk analysis, workforce training,
  sanction policy, contingency plan, incident response.
- Physical safeguards (§164.310): facility/device controls.
- Breach notification (§164.404), retention schedules, secure media disposal.
- Centralized, access-controlled, time-synchronized audit storage (this demo
  writes a local file; production would ship to a tamper-evident SIEM).
- Key management lifecycle (rotation, escrow, HSM/KMS) — here the key is a single
  env var for demonstration.

A formal determination requires legal counsel and a qualified security assessor.
