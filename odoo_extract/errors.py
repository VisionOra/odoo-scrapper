"""Domain-specific exceptions (Specification §10)."""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for typed, catchable workflow failures."""


class ConfigError(WorkflowError):
    pass


class AuthError(WorkflowError):
    pass


class NavigationError(WorkflowError):
    pass


class RecordNotFoundError(WorkflowError):
    pass


class ExtractionError(WorkflowError):
    pass
