"""Startup control self-test (fail-closed).

HIPAA expects safeguards to be *verified*, not assumed (§164.308(a)(8),
Evaluation). Before any PHI is processed we push a canary value through a
redaction filter and confirm it is scrubbed. If the control is not working we
**abort** rather than risk a leak — fail-closed, not fail-open.
"""

from __future__ import annotations

import logging

from .errors import WorkflowError
from .sanitizer import PhiRedactionFilter

# A value that must never survive redaction. Distinct enough that a match is
# unambiguous evidence the scrubber ran.
_CANARY = "CANARY-PHI-DO-NOT-LOG-7f3a9c"
_PLACEHOLDER = "[REDACTED_CANARY]"


def assert_redaction_active(log: logging.Logger) -> None:
    """Prove the PHI redaction control is live; raise to abort if it is not."""
    probe = PhiRedactionFilter()
    probe.register([_CANARY], _PLACEHOLDER)

    record = logging.LogRecord(
        name="control-health",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="redaction self-test: leak attempt -> %s",
        args=(_CANARY,),
        exc_info=None,
    )
    probe.filter(record)
    result = record.getMessage()

    if _CANARY in result or _PLACEHOLDER not in result:
        raise WorkflowError(
            "PHI redaction self-test FAILED — the logging guardrail is not "
            "scrubbing canary values. Aborting before any PHI is processed "
            "(fail-closed)."
        )
    log.info("PHI redaction control: ACTIVE ✓ (self-test passed).")
