"""Normalize and rank TrendSignal lists into a capped SignalsFile."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from modules.common.models import SignalsFile, TrendSignal


def _normalize_scores(signals: list[TrendSignal]) -> list[TrendSignal]:
    if not signals:
        return []
    max_score = max(s.engagement_score for s in signals) or 1.0
    normalized: list[TrendSignal] = []
    for sig in signals:
        scaled = round((sig.engagement_score / max_score) * 100.0, 4)
        normalized.append(
            sig.model_copy(update={"engagement_score": scaled})
        )
    return normalized


def _merge_by_topic(signals: list[TrendSignal]) -> list[TrendSignal]:
    """Collapse duplicate topics, keeping highest score and unioning titles."""
    by_topic: dict[str, TrendSignal] = {}
    for sig in signals:
        key = sig.topic.strip().lower()
        existing = by_topic.get(key)
        if existing is None:
            by_topic[key] = sig
            continue
        titles = list(dict.fromkeys([*existing.sample_titles, *sig.sample_titles]))[:8]
        winner = sig if sig.engagement_score >= existing.engagement_score else existing
        loser = existing if winner is sig else sig
        # Prefer non-fixture source labels when scores tie-ish; keep winner score.
        by_topic[key] = winner.model_copy(
            update={
                "sample_titles": titles,
                "engagement_score": max(winner.engagement_score, loser.engagement_score),
            }
        )
    return list(by_topic.values())


def rank_signals(
    signals: list[TrendSignal],
    *,
    max_topics: int,
    demographic: dict[str, Any] | None = None,
) -> SignalsFile:
    merged = _merge_by_topic(signals)
    ranked = sorted(
        _normalize_scores(merged),
        key=lambda s: s.engagement_score,
        reverse=True,
    )[: max(0, max_topics)]
    return SignalsFile(
        signals=ranked,
        generated_at=datetime.now(timezone.utc).isoformat(),
        demographic=demographic,
    )
