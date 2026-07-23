"""Errors for the asset generator."""

from __future__ import annotations


class ConfigurationError(RuntimeError):
    """Raised when required providers/credentials are missing or misconfigured."""


class AssetGenerationError(RuntimeError):
    """Raised when asset generation fails after configuration was valid."""
