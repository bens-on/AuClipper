"""Trend signal collection entrypoint.

Wave 1 (Grok): implement `collect_signals`.
"""

from __future__ import annotations

from pathlib import Path

from modules.common.models import SignalsFile
from modules.common.settings import AppSettings, EnvSecrets


async def collect_signals(
    settings: AppSettings,
    secrets: EnvSecrets,
    output_path: Path,
) -> SignalsFile:
    """Collect and rank trend signals; write signals.json; return validated model."""
    raise NotImplementedError("trend_signal.collector.collect_signals — Wave 1 Grok")
