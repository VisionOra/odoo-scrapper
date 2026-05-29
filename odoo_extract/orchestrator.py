"""Orchestrator (Specification §8) — wires the workflow together."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from playwright.async_api import BrowserContext, Page, async_playwright

from .auth import authenticate
from .config import Config
from .constants import NAV_TIMEOUT_MS
from .drilldown import extract_all_target_invoices
from .errors import WorkflowError
from .models import Invoice
from .navigation import apply_posted_filter, clear_default_filters, open_invoicing
from .retry import with_retries
from .rpc_capture import RpcCapture
from .sanitizer import PhiRedactionFilter


@contextmanager
def _profile_lock(user_data_dir: str, log: logging.Logger) -> Iterator[None]:
    """Guard the persistent browser profile against concurrent runs.

    Two runs sharing one ``user_data_dir`` would corrupt the Chromium profile.
    We take an exclusive lock file holding the owner PID; a lock left by a dead
    process is treated as stale and reclaimed, so a crash can't wedge us.
    """
    profile = Path(user_data_dir)
    profile.mkdir(parents=True, exist_ok=True)
    lock = profile / ".extractor.lock"

    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, ValueError):
            return False
        except PermissionError:
            return True  # exists but owned by another user
        return True

    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            owner = int(lock.read_text().strip() or "-1")
        except (OSError, ValueError):
            owner = -1
        if owner > 0 and _alive(owner):
            raise WorkflowError(
                f"Another extraction is already using the profile at "
                f"{user_data_dir!r} (PID {owner}). Use a distinct USER_DATA_DIR "
                f"for concurrent runs."
            )
        log.warning("Reclaiming stale profile lock (owner PID %s not running).", owner)
        lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


async def run(
    cfg: Config, log: logging.Logger, redactor: PhiRedactionFilter
) -> list[Invoice]:
    # Register PHI terms up front so anything logged later is auto-redacted.
    # Product names are registered incrementally inside extraction (§7), the
    # moment they exist — not after the fact.
    redactor.register([cfg.target_customer], "[REDACTED_CUSTOMER]")

    sandbox_args = ["--no-sandbox"] if cfg.no_sandbox else []

    with _profile_lock(cfg.user_data_dir, log):
        async with async_playwright() as pw:
            context: BrowserContext = await pw.chromium.launch_persistent_context(
                user_data_dir=str(Path(cfg.user_data_dir).resolve()),
                headless=cfg.headless,
                args=sandbox_args,
            )
            context.set_default_timeout(NAV_TIMEOUT_MS)
            tracing = cfg.save_artifacts
            if tracing:
                await context.tracing.start(screenshots=True, snapshots=True)

            page = context.pages[0] if context.pages else await context.new_page()

            capture = RpcCapture(log)
            capture.attach(page)

            try:
                # Demonstration of in-memory sanitization (§7): we deliberately
                # log the REAL customer name; the console must show it redacted.
                log.info("Processing customer %s", cfg.target_customer)

                # Auth + navigation are idempotent: retry transient failures.
                await with_retries(
                    lambda: authenticate(page, cfg, log),
                    what="Authentication",
                    log=log,
                )
                await with_retries(
                    lambda: open_invoicing(page, cfg, log),
                    what="Opening Invoicing",
                    log=log,
                )
                await clear_default_filters(page, log)
                await apply_posted_filter(page, log)
                invoices = await extract_all_target_invoices(
                    page, cfg, capture, log, redactor
                )

                if tracing:
                    await context.tracing.stop()
                return invoices
            except Exception:
                if tracing:
                    await _dump_artifacts(context, page, log)
                raise
            finally:
                await context.close()


async def _dump_artifacts(
    context: BrowserContext, page: Page, log: logging.Logger
) -> None:
    """Persist debugging artifacts locally (§10).

    NOTE: trace.zip and failure.png contain UNREDACTED page content. They are
    gitignored and never transmitted, but treat them as sensitive on disk.
    Each artifact is attempted independently so one failure can't suppress the
    other; failures are surfaced (not silently swallowed).
    """
    try:
        await page.screenshot(path="failure.png", full_page=True)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not mask root cause
        log.warning("Could not save failure.png: %s", type(exc).__name__)
    try:
        await context.tracing.stop(path="trace.zip")
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not save trace.zip: %s", type(exc).__name__)
    log.info("Saved failure artifacts (failure.png, trace.zip) — contain raw data.")


def write_output(invoices: list[Invoice], cfg: Config, log: logging.Logger) -> None:
    """Serialize REAL values straight to disk — never through the logger (§7).

    The write is atomic: data goes to a temp file in the same directory which is
    then ``os.replace``-d over the target, so a crash mid-write can never leave a
    truncated/corrupt JSON file. Output is an array of invoices, each with its
    own lines, so multiple invoices for the same customer are distinct.
    """
    payload = [asdict(inv) for inv in invoices]
    out = Path(cfg.output_file)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, out)

    total_lines = sum(len(inv.lines) for inv in invoices)
    # Counts only — never content.
    log.info(
        "Wrote %d invoice(s) / %d line(s) to output file.",
        len(invoices),
        total_lines,
    )
