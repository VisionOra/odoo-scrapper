"""Bounded retry with exponential backoff for transient I/O failures.

This is NOT a synchronization wait (those remain state-driven, per §4). It only
re-attempts an *idempotent* step — auth, navigation — after a transient browser
or network error, with a short exponential backoff between tries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from playwright.async_api import Error as PlaywrightError, TimeoutError as PWTimeout

T = TypeVar("T")

# Errors worth retrying: transient navigation/network/timeout conditions.
_TRANSIENT = (PWTimeout, PlaywrightError, ConnectionError, asyncio.TimeoutError)


async def with_retries(
    action: Callable[[], Awaitable[T]],
    *,
    what: str,
    log: logging.Logger,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    """Run ``action`` up to ``attempts`` times, backing off between failures.

    Raises the last exception if every attempt fails. Domain ``WorkflowError``
    subclasses raised by the action propagate immediately on the final attempt
    (they are not retried indefinitely — the loop simply re-runs the action).
    """
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except _TRANSIENT as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs.",
                what,
                attempt,
                attempts,
                type(exc).__name__,
                delay,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None  # only reachable after a failure
    raise last_exc
