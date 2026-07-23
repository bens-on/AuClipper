"""Unit tests for trend_signal collectors (mocked APIs)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.common.models import FormatType, SignalSource, SignalsFile, TrendSignal
from modules.common.settings import (
    AppSettings,
    DemographicConfig,
    EnvSecrets,
    PipelineConfig,
    RedditConfig,
    YouTubeConfig,
)
from modules.trend_signal.collector import collect_signals
from modules.trend_signal.errors import ConfigurationError
from modules.trend_signal.ranking import rank_signals


def _settings(**overrides: Any) -> AppSettings:
    base = AppSettings(
        demographic=DemographicConfig(
            age_band="18-24",
            region="US",
            interests=["comedy", "memes"],
        ),
        topic_seeds=["office humor", "gym culture"],
        pipeline=PipelineConfig(max_signal_topics=5),
        reddit=RedditConfig(subreddits=["funny", "dankmemes"]),
        youtube=YouTubeConfig(region_code="US", category_id="23", max_results=10),
    )
    if overrides:
        return base.model_copy(update=overrides)
    return base


def _secrets(**kwargs: Any) -> EnvSecrets:
    return EnvSecrets(
        youtube_api_key=kwargs.get("youtube_api_key", "yt-test-key"),
        reddit_client_id=kwargs.get("reddit_client_id", "reddit-id"),
        reddit_client_secret=kwargs.get("reddit_client_secret", "reddit-secret"),
        reddit_user_agent="AuCl-test/0.1",
    )


@pytest.mark.asyncio
async def test_collect_signals_fixtures(tmp_path: Path) -> None:
    settings = _settings()
    out = tmp_path / "signals.json"
    result = await collect_signals(
        settings,
        EnvSecrets(),
        out,
        use_fixtures=True,
    )
    assert isinstance(result, SignalsFile)
    assert result.signals
    assert out.exists()
    loaded = SignalsFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert loaded.signals[0].engagement_score >= loaded.signals[-1].engagement_score
    assert loaded.demographic is not None
    assert loaded.demographic["age_band"] == "18-24"


@pytest.mark.asyncio
async def test_collect_signals_missing_keys_raises() -> None:
    settings = _settings()
    with pytest.raises(ConfigurationError, match="Missing credentials|YOUTUBE_API_KEY"):
        await collect_signals(settings, EnvSecrets(), Path("/tmp/aucl_nope_signals.json"))


@pytest.mark.asyncio
async def test_collect_signals_mocked_live(tmp_path: Path) -> None:
    settings = _settings()
    secrets = _secrets()
    yt_signals = [
        TrendSignal(
            topic="office humor",
            format_type=FormatType.AI_VOICEOVER_SKIT,
            engagement_score=500.0,
            source=SignalSource.YOUTUBE,
            sample_titles=["Boss skull emoji compilation"],
        )
    ]
    reddit_signals = [
        TrendSignal(
            topic="gym culture",
            format_type=FormatType.STOCK_PUNCHLINE,
            engagement_score=200.0,
            source=SignalSource.REDDIT,
            sample_titles=["Leg day fails"],
        )
    ]
    trends_signals = [
        TrendSignal(
            topic="office humor",
            format_type=FormatType.STOCK_PUNCHLINE,
            engagement_score=40.0,
            source=SignalSource.GOOGLE_TRENDS,
            sample_titles=["Google Trends momentum: office humor"],
        )
    ]

    out = tmp_path / "signals.json"
    with (
        patch(
            "modules.trend_signal.collector.fetch_youtube_signals",
            new=AsyncMock(return_value=yt_signals),
        ),
        patch(
            "modules.trend_signal.collector.fetch_reddit_signals",
            new=AsyncMock(return_value=reddit_signals),
        ),
        patch(
            "modules.trend_signal.collector.fetch_google_trends_signals",
            new=AsyncMock(return_value=trends_signals),
        ),
    ):
        result = await collect_signals(settings, secrets, out)

    assert out.exists()
    assert len(result.signals) >= 2
    assert result.signals[0].engagement_score == 100.0  # normalized max
    topics = {s.topic for s in result.signals}
    assert "office humor" in topics
    assert "gym culture" in topics


def test_rank_signals_caps_and_normalizes() -> None:
    signals = [
        TrendSignal(
            topic="a",
            format_type=FormatType.OTHER,
            engagement_score=10,
            source=SignalSource.YOUTUBE,
            sample_titles=["A"],
        ),
        TrendSignal(
            topic="b",
            format_type=FormatType.OTHER,
            engagement_score=50,
            source=SignalSource.REDDIT,
            sample_titles=["B"],
        ),
        TrendSignal(
            topic="a",
            format_type=FormatType.OTHER,
            engagement_score=20,
            source=SignalSource.GOOGLE_TRENDS,
            sample_titles=["A2"],
        ),
    ]
    ranked = rank_signals(signals, max_topics=2, demographic={"age_band": "18-24"})
    assert len(ranked.signals) == 2
    assert ranked.signals[0].topic == "b"
    assert ranked.signals[0].engagement_score == 100.0
    assert ranked.signals[1].topic == "a"
    assert set(ranked.signals[1].sample_titles) >= {"A", "A2"} or (
        "A" in ranked.signals[1].sample_titles or "A2" in ranked.signals[1].sample_titles
    )


@pytest.mark.asyncio
async def test_youtube_fetch_builds_api() -> None:
    from modules.trend_signal import youtube as yt_mod

    popular_resp = {
        "items": [
            {
                "snippet": {"title": "Office humor compilation"},
                "statistics": {
                    "viewCount": "10000",
                    "likeCount": "500",
                    "commentCount": "40",
                },
            }
        ]
    }
    search_resp = {
        "items": [
            {
                "id": {"videoId": "abc123"},
                "snippet": {"title": "Gym culture fails"},
            }
        ]
    }
    details_resp = {
        "items": [
            {
                "snippet": {"title": "Gym culture fails"},
                "statistics": {
                    "viewCount": "2000",
                    "likeCount": "100",
                    "commentCount": "10",
                },
            }
        ]
    }

    def _execute_videos(**kwargs: Any) -> dict[str, Any]:
        if kwargs.get("chart") == "mostPopular":
            return popular_resp
        return details_resp

    videos_resource = MagicMock()
    videos_resource.list.side_effect = lambda **kw: MagicMock(
        execute=lambda: _execute_videos(**kw)
    )
    search_resource = MagicMock()
    search_resource.list.side_effect = lambda **_kw: MagicMock(
        execute=lambda: search_resp
    )

    service = MagicMock()
    service.videos.return_value = videos_resource
    service.search.return_value = search_resource
    mock_build = MagicMock(return_value=service)

    with patch("googleapiclient.discovery.build", mock_build):
        sync_signals = yt_mod._fetch_youtube_sync(_settings(), "yt-test-key")
        async_signals = await yt_mod.fetch_youtube_signals(_settings(), _secrets())

    assert sync_signals
    assert async_signals
    assert all(s.source == SignalSource.YOUTUBE for s in sync_signals)
    mock_build.assert_called()


@pytest.mark.asyncio
async def test_reddit_velocity() -> None:
    import time

    from modules.trend_signal import reddit as reddit_mod

    class FakePost:
        stickied = False
        title = "Office humor Slack fails"
        score = 1200
        num_comments = 80
        created_utc = time.time() - 3600

    class FakeSub:
        def hot(self, limit: int = 25):  # noqa: ARG002
            return [FakePost()]

    class FakeReddit:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        def subreddit(self, name: str) -> FakeSub:  # noqa: ARG002
            return FakeSub()

    with patch("praw.Reddit", FakeReddit):
        signals = await reddit_mod.fetch_reddit_signals(_settings(), _secrets())

    assert signals
    assert signals[0].source == SignalSource.REDDIT
    assert signals[0].engagement_score > 0
