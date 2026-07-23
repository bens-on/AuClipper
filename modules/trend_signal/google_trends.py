"""Google Trends momentum via pytrends."""

from __future__ import annotations

import asyncio
import logging

from modules.common.models import FormatType, SignalSource, TrendSignal
from modules.common.settings import AppSettings

logger = logging.getLogger(__name__)


async def fetch_google_trends_signals(settings: AppSettings) -> list[TrendSignal]:
    """Compute short-horizon momentum for topic seeds using pytrends."""
    seeds = settings.topic_seeds
    if not seeds:
        return []
    return await asyncio.to_thread(_fetch_trends_sync, seeds, settings.demographic.region)


def _fetch_trends_sync(seeds: list[str], region: str | None) -> list[TrendSignal]:
    from pytrends.request import TrendReq  # type: ignore[import-untyped]

    geo = (region or "US").upper()
    # TrendReq is sync/HTTP; keep batches small (pytrends allows ≤5 keywords).
    pytrends = TrendReq(hl="en-US", tz=360)
    signals: list[TrendSignal] = []

    for batch_start in range(0, len(seeds), 5):
        batch = seeds[batch_start : batch_start + 5]
        try:
            pytrends.build_payload(batch, timeframe="now 7-d", geo=geo)
            interest = pytrends.interest_over_time()
        except Exception as exc:  # pragma: no cover - network flakiness
            logger.warning("pytrends batch=%s failed: %s", batch, exc)
            continue

        if interest is None or interest.empty:
            continue

        for seed in batch:
            if seed not in interest.columns:
                continue
            series = interest[seed].astype(float)
            momentum = _momentum(series.tolist())
            signals.append(
                TrendSignal(
                    topic=seed,
                    format_type=FormatType.STOCK_PUNCHLINE,
                    engagement_score=round(momentum, 4),
                    source=SignalSource.GOOGLE_TRENDS,
                    sample_titles=[f"Google Trends momentum: {seed}"],
                )
            )
            logger.debug("trends topic=%s momentum=%.2f", seed, momentum)

    return signals


def _momentum(values: list[float]) -> float:
    """Recent-half mean minus early-half mean, floored at 0, scaled by peak."""
    if not values:
        return 0.0
    mid = max(len(values) // 2, 1)
    early = values[:mid]
    late = values[mid:] or values[-1:]
    early_mean = sum(early) / len(early)
    late_mean = sum(late) / len(late)
    peak = max(values) or 1.0
    delta = max(late_mean - early_mean, 0.0)
    # Blend absolute late interest with rise so flat-high topics still score.
    return (late_mean * 0.6) + (delta * 0.4) + (peak * 0.1)
