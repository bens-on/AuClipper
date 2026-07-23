"""YouTube Data API v3 signal collector (metadata only — no video downloads)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from modules.common.models import FormatType, SignalSource, TrendSignal
from modules.common.settings import AppSettings, EnvSecrets
from modules.trend_signal.errors import ConfigurationError

logger = logging.getLogger(__name__)


def _guess_format(title: str) -> FormatType:
    lower = title.lower()
    if any(tok in lower for tok in ("skit", "voiceover", "narrat", "storytime")):
        return FormatType.AI_VOICEOVER_SKIT
    if any(tok in lower for tok in ("meme", "text", "caption", "overlay")):
        return FormatType.TEXT_OVERLAY_MEME
    return FormatType.STOCK_PUNCHLINE


def _engagement_from_stats(stats: dict[str, Any]) -> float:
    views = float(stats.get("viewCount", 0) or 0)
    likes = float(stats.get("likeCount", 0) or 0)
    comments = float(stats.get("commentCount", 0) or 0)
    # Log-ish blend so mega-viral items do not dominate forever.
    raw = (views ** 0.5) + likes * 2.0 + comments * 3.0
    return round(raw, 4)


async def fetch_youtube_signals(
    settings: AppSettings,
    secrets: EnvSecrets,
) -> list[TrendSignal]:
    """Fetch mostPopular + search.list results for topic seeds (metadata only)."""
    if not secrets.youtube_api_key:
        raise ConfigurationError(
            "YOUTUBE_API_KEY is missing. Set it in .env, or pass use_fixtures=True / "
            "allow_offline=True to load fixture signals."
        )

    return await asyncio.to_thread(_fetch_youtube_sync, settings, secrets.youtube_api_key)


def _fetch_youtube_sync(settings: AppSettings, api_key: str) -> list[TrendSignal]:
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
    yt_cfg = settings.youtube
    max_results = min(yt_cfg.max_results, 50)
    signals: list[TrendSignal] = []

    # --- mostPopular chart ---
    popular = (
        youtube.videos()
        .list(
            part="snippet,statistics",
            chart="mostPopular",
            regionCode=yt_cfg.region_code,
            videoCategoryId=yt_cfg.category_id,
            maxResults=max_results,
        )
        .execute()
    )
    for item in popular.get("items", []):
        snippet = item.get("snippet", {})
        title = str(snippet.get("title") or "").strip()
        if not title:
            continue
        topic = _topic_from_title(title, settings.topic_seeds)
        signals.append(
            TrendSignal(
                topic=topic,
                format_type=_guess_format(title),
                engagement_score=_engagement_from_stats(item.get("statistics", {})),
                source=SignalSource.YOUTUBE,
                sample_titles=[title],
            )
        )

    # --- search.list per topic seed ---
    for seed in settings.topic_seeds:
        search = (
            youtube.search()
            .list(
                part="snippet",
                q=seed,
                type="video",
                order="viewCount",
                regionCode=yt_cfg.region_code,
                maxResults=min(10, max_results),
                relevanceLanguage="en",
            )
            .execute()
        )
        video_ids = [
            it["id"]["videoId"]
            for it in search.get("items", [])
            if it.get("id", {}).get("videoId")
        ]
        titles = [
            str(it.get("snippet", {}).get("title") or "").strip()
            for it in search.get("items", [])
        ]
        titles = [t for t in titles if t]
        if not video_ids:
            continue

        details = (
            youtube.videos()
            .list(part="statistics,snippet", id=",".join(video_ids))
            .execute()
        )
        scores = [
            _engagement_from_stats(it.get("statistics", {}))
            for it in details.get("items", [])
        ]
        score = max(scores) if scores else 0.0
        signals.append(
            TrendSignal(
                topic=seed,
                format_type=_guess_format(" ".join(titles[:3])),
                engagement_score=round(score, 4),
                source=SignalSource.YOUTUBE,
                sample_titles=titles[:5],
            )
        )
        logger.debug("youtube search seed=%s titles=%d score=%.2f", seed, len(titles), score)

    return signals


def _topic_from_title(title: str, seeds: list[str]) -> str:
    lower = title.lower()
    for seed in seeds:
        if seed.lower() in lower:
            return seed
    # Fall back to a shortened title as topic label.
    return title if len(title) <= 80 else title[:77] + "..."
