"""Errors for the trend signal collector."""

from __future__ import annotations


class ConfigurationError(RuntimeError):
    """Raised when required API credentials or offline config are missing."""


class CollectorError(RuntimeError):
    """Raised when a signal source fails in a non-recoverable way."""
