"""Unit tests for asset_generator strategies (mocked HTTP / smoke ffmpeg)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from modules.asset_generator.ai_video import AIVideoStrategy
from modules.asset_generator.errors import ConfigurationError
from modules.asset_generator.generate import generate_assets, get_strategy
from modules.common.models import (
    AssetKind,
    AssetsManifest,
    Beat,
    Concept,
    ConceptFormat,
)
from modules.common.settings import (
    AppSettings,
    DemographicConfig,
    EnvSecrets,
    PipelineConfig,
    VoiceConfig,
)


def _settings() -> AppSettings:
    return AppSettings(
        demographic=DemographicConfig(age_band="18-24", region="US"),
        pipeline=PipelineConfig(
            stock_providers=["pexels", "pixabay"],
            ai_video_provider=None,
            output_width=320,
            output_height=560,
        ),
        voice=VoiceConfig(piper_voice="en_US-lessac-medium"),
    )


def _concept(**overrides: Any) -> Concept:
    data: dict[str, Any] = {
        "concept_id": "smoke_slack_skull",
        "premise": "Office Slack skull emoji skit",
        "format": ConceptFormat.AI_VOICEOVER_SKIT,
        "asset_strategy": "tts+stock",
        "stock_keywords": ["office", "laptop"],
        "target_length_sec": 8,
        "voiceover_script": "Monday morning Slack. Boss drops a skull emoji.",
        "beat_sheet": [
            Beat(
                order=0,
                description="open",
                duration_sec=4,
                voiceover_line="Monday morning Slack.",
            ),
            Beat(
                order=1,
                description="punch",
                duration_sec=4,
                voiceover_line="Boss drops a skull emoji.",
            ),
        ],
    }
    data.update(overrides)
    return Concept.model_validate(data)


@pytest.mark.asyncio
async def test_smoke_tts_stock_produces_manifest(tmp_path: Path) -> None:
    assets_root = tmp_path / "assets"
    manifest = await generate_assets(
        _concept(),
        _settings(),
        EnvSecrets(),
        assets_root,
        smoke=True,
    )
    assert isinstance(manifest, AssetsManifest)
    assert manifest.strategy == "tts+stock"
    kinds = {a.kind for a in manifest.assets}
    assert AssetKind.VIDEO in kinds
    assert AssetKind.AUDIO in kinds
    for asset in manifest.assets:
        assert Path(asset.path).is_file()
    written = assets_root / "smoke_slack_skull" / "assets_manifest.json"
    assert written.is_file()
    loaded = AssetsManifest.model_validate_json(written.read_text(encoding="utf-8"))
    assert loaded.concept_id == "smoke_slack_skull"


@pytest.mark.asyncio
async def test_stock_falls_back_to_local_without_keys(tmp_path: Path) -> None:
    """Without Pexels/Pixabay keys, stock uses local ffmpeg footage."""
    assets_root = tmp_path / "assets"
    manifest = await generate_assets(
        _concept(asset_strategy="stock"),
        _settings(),
        EnvSecrets(),
        assets_root,
        smoke=False,
    )
    assert manifest.assets
    assert Path(manifest.assets[0].path).is_file()
    assert manifest.assets[0].source in {"ffmpeg-local-fallback", "ffmpeg-smoke"}


@pytest.mark.asyncio
async def test_stock_pexels_mock(tmp_path: Path) -> None:
    concept_dir = tmp_path / "assets" / "c1"
    fake_mp4 = concept_dir / "pexels_1.mp4"

    async def fake_download(client: Any, url: str, dest: Path) -> None:  # noqa: ARG001
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00\x00fake-mp4")

    pexels_payload = {
        "videos": [
            {
                "id": 1,
                "url": "https://www.pexels.com/video/1",
                "duration": 7,
                "video_files": [
                    {
                        "link": "https://example.com/clip.mp4",
                        "width": 1080,
                        "height": 1920,
                    }
                ],
            }
        ]
    }

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return pexels_payload

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=FakeResp())
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("modules.asset_generator.stock.httpx.AsyncClient", return_value=fake_client),
        patch("modules.asset_generator.stock._download", new=fake_download),
    ):
        manifest = await generate_assets(
            _concept(concept_id="c1", asset_strategy="stock"),
            _settings(),
            EnvSecrets(pexels_api_key="pexels-test"),
            tmp_path / "assets",
            smoke=False,
        )

    assert manifest.assets[0].source == "pexels"
    assert manifest.assets[0].license and "commercial" in manifest.assets[0].license
    assert fake_mp4.is_file() or Path(manifest.assets[0].path).is_file()


@pytest.mark.asyncio
async def test_ai_video_stub_raises() -> None:
    strategy = AIVideoStrategy()
    with pytest.raises((NotImplementedError, ConfigurationError)):
        await strategy.generate(
            _concept(asset_strategy="ai_video"),
            _settings(),
            EnvSecrets(),
            Path("/tmp/aucl_ai_video"),
            smoke=False,
        )


@pytest.mark.asyncio
async def test_ai_video_smoke_raises_clearly() -> None:
    with pytest.raises(NotImplementedError, match="stubbed|tts\\+stock"):
        await generate_assets(
            _concept(asset_strategy="ai_video"),
            _settings(),
            EnvSecrets(),
            Path("/tmp/aucl_ai_video_smoke"),
            smoke=True,
        )


def test_get_strategy_aliases() -> None:
    assert get_strategy("tts+stock").name == "tts+stock"
    assert get_strategy("stock").name == "stock"
    with pytest.raises(ConfigurationError):
        get_strategy("unknown-strategy")


@pytest.mark.asyncio
async def test_elevenlabs_path_mocked(tmp_path: Path) -> None:
    class FakeResp:
        content = b"ID3fake-mp3"

        def raise_for_status(self) -> None:
            return None

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=FakeResp())
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch("modules.asset_generator.ai_voice.httpx.AsyncClient", return_value=fake_client):
        manifest = await generate_assets(
            _concept(concept_id="voice_only", asset_strategy="ai_voice"),
            _settings(),
            EnvSecrets(
                elevenlabs_api_key="el-key",
                elevenlabs_voice_id="voice-1",
            ),
            tmp_path / "assets",
            smoke=False,
        )

    assert manifest.assets[0].kind == AssetKind.AUDIO
    assert manifest.assets[0].source == "elevenlabs"
    assert Path(manifest.assets[0].path).is_file()
