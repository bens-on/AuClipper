"""Unit tests for AI web trend researcher (mocked Anthropic)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from modules.common.settings import load_secrets, load_yaml_settings
from modules.trend_signal.ai_web import _parse_signals, fetch_ai_web_signals
from modules.trend_signal.collector import collect_signals


class _FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class _FakeAnthropic:
    def __init__(self, text: str) -> None:
        self.messages = _FakeMessages(text)


def test_parse_signals_schema() -> None:
    raw = """
    {
      "signals": [
        {
          "topic": "group chat chaos",
          "format_type": "AI-voiceover skit",
          "engagement_score": 88,
          "sample_titles": ["When the plan changes again", "Left on read"]
        }
      ]
    }
    """
    signals = _parse_signals(raw)
    assert len(signals) == 1
    assert signals[0].topic == "group chat chaos"
    assert signals[0].source == "ai_web"


@pytest.mark.asyncio
async def test_fetch_ai_web_with_mock_client() -> None:
    settings = load_yaml_settings()
    secrets = load_secrets()
    client = _FakeAnthropic(
        '{"signals":[{"topic":"office humor","format_type":"text-overlay meme",'
        '"engagement_score":91,"sample_titles":["Reply all disaster"]}]}'
    )
    signals = await fetch_ai_web_signals(settings, secrets, client=client)
    assert signals
    assert signals[0].topic == "office humor"
    assert client.messages.calls
    assert "tools" in client.messages.calls[0]


@pytest.mark.asyncio
async def test_collect_signals_uses_ai_web_without_yt_reddit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_yaml_settings()
    secrets = load_secrets()
    monkeypatch.setattr(secrets, "youtube_api_key", None)
    monkeypatch.setattr(secrets, "reddit_client_id", None)
    monkeypatch.setattr(secrets, "reddit_client_secret", None)
    monkeypatch.setattr(secrets, "anthropic_api_key", "test-key")

    async def _fake_ai(settings_arg, secrets_arg, *, client=None):  # noqa: ANN001
        from modules.common.models import SignalsFile, TrendSignal

        return SignalsFile(
            signals=[
                TrendSignal(
                    topic="ai path works",
                    format_type="AI-voiceover skit",
                    engagement_score=80,
                    source="ai_web",
                    sample_titles=["hook"],
                )
            ]
        )

    monkeypatch.setattr(
        "modules.trend_signal.collector.collect_ai_web_signals_file",
        _fake_ai,
    )
    out = tmp_path / "signals.json"
    result = await collect_signals(settings, secrets, out)
    assert out.is_file()
    assert result.signals[0].topic == "ai path works"
