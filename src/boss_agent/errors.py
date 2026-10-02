"""
boss_agent.errors
=================
Typed failure hierarchy for broker and persistence repository seams (Spec #303, ADR 0013).
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
