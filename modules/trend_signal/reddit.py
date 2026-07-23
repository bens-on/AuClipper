"""Reddit signal collector via PRAW (upvote / comment velocity)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from modules.common.models import FormatType, SignalSource, TrendSignal
from modules.common.settings import AppSettings, EnvSecrets
from modules.trend_signal.errors import ConfigurationError

logger = logging.getLogger(__name__)


def _guess_format(title: str, subreddit: str) -> FormatType:
    lower = f"{title} {subreddit}".lower()
    if "meme" in lower or "dank" in lower:
        return FormatType.TEXT_OVERLAY_MEME
    if any(tok in lower for tok in ("skit", "story", "aita", "tifu")):
        return FormatType.AI_VOICEOVER_SKIT
    return FormatType.STOCK_PUNCHLINE


def _velocity(score: int, num_comments: int, created_utc: float, now: float) -> float:
    age_hours = max((now - created_utc) / 3600.0, 1.0 / 60.0)  # min 1 minute
    return (float(score) + float(num_comments) * 2.0) / age_hours


async def fetch_reddit_signals(
    settings: AppSettings,
    secrets: EnvSecrets,
) -> list[TrendSignal]:
    """Hot posts from configured subreddits, scored by upvote/comment velocity."""
    if not secrets.reddit_client_id or not secrets.reddit_client_secret:
        raise ConfigurationError(
            "REDDIT_CLIENT_ID and/or REDDIT_CLIENT_SECRET missing. Set them in .env, "
            "or pass use_fixtures=True / allow_offline=True to load fixture signals."
        )

    return await asyncio.to_thread(_fetch_reddit_sync, settings, secrets)


def _fetch_reddit_sync(settings: AppSettings, secrets: EnvSecrets) -> list[TrendSignal]:
    import praw  # type: ignore[import-untyped]

    reddit = praw.Reddit(
        client_id=secrets.reddit_client_id,
        client_secret=secrets.reddit_client_secret,
        user_agent=secrets.reddit_user_agent,
    )
    now = time.time()
    subreddits = settings.reddit.subreddits or ["funny"]
    # Aggregate by topic seed / subreddit bucket
    buckets: dict[str, dict[str, Any]] = {}

    for sub_name in subreddits:
        try:
            subreddit = reddit.subreddit(sub_name)
            for post in subreddit.hot(limit=25):
                if getattr(post, "stickied", False):
                    continue
                title = str(getattr(post, "title", "") or "").strip()
                if not title:
                    continue
                vel = _velocity(
                    int(getattr(post, "score", 0) or 0),
                    int(getattr(post, "num_comments", 0) or 0),
                    float(getattr(post, "created_utc", now) or now),
                    now,
                )
                topic = _match_seed(title, settings.topic_seeds) or f"r/{sub_name}"
                bucket = buckets.setdefault(
                    topic,
                    {
                        "titles": [],
                        "velocities": [],
                        "subreddit": sub_name,
                        "format": _guess_format(title, sub_name),
                    },
                )
                bucket["titles"].append(title)
                bucket["velocities"].append(vel)
        except Exception as exc:  # pragma: no cover - network/auth edge
            logger.warning("reddit subreddit=%s failed: %s", sub_name, exc)

    signals: list[TrendSignal] = []
    for topic, data in buckets.items():
        vels: list[float] = data["velocities"]
        if not vels:
            continue
        score = round(max(vels), 4)
        signals.append(
            TrendSignal(
                topic=topic,
                format_type=data["format"],
                engagement_score=score,
                source=SignalSource.REDDIT,
                sample_titles=list(dict.fromkeys(data["titles"]))[:5],
            )
        )
        logger.debug("reddit topic=%s score=%.2f", topic, score)

    return signals


def _match_seed(title: str, seeds: list[str]) -> str | None:
    lower = title.lower()
    for seed in seeds:
        tokens = seed.lower().split()
        if all(tok in lower for tok in tokens) or seed.lower() in lower:
            return seed
    return None
