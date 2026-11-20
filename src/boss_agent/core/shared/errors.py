"""
boss_agent.core.shared.errors
=================
Typed failure hierarchy for broker/persistence seams (Spec #303, ADR 0013) and the
LLM decision port.
"""

from __future__ import annotations


class BrokerError(RuntimeError):
    """Base exception for all broker and persistence repository store failures."""


class TransportError(BrokerError):
    """Raised when network, connection, timeout, or server-side communication fails."""


class ValidationError(BrokerError):
    """Raised when request data fails schema or domain validation."""


class ConflictError(BrokerError):
    """Raised when a concurrent modification, lease mismatch, or unique constraint conflicts."""


class RecordNotFoundError(BrokerError):
    """Raised when an explicit entity is not found by ID when required."""


class LLMError(Exception):
    """Base exception for LLM operations (connection, HTTP status, unparsable output)."""


class LLMAuthError(LLMError):
    """Authentication or authorization failure when invoking an LLM API."""


class LLMTimeoutError(LLMError):
    """Request timed out while waiting for an LLM response."""
