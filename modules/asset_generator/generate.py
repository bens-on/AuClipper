"""Asset generation entrypoint with pluggable strategies."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

from modules.asset_generator.ai_video import AIVideoStrategy
from modules.asset_generator.ai_voice import AIVoiceStrategy
from modules.asset_generator.errors import ConfigurationError
from modules.asset_generator.protocol import AssetStrategy
from modules.asset_generator.stock import StockStrategy
from modules.common.models import AssetsManifest, Concept
from modules.common.settings import AppSettings, EnvSecrets

logger = logging.getLogger(__name__)


class TTSStockStrategy:
    """Combo strategy: stock footage + AI voiceover (default for many concepts)."""

    name = "tts+stock"

    def __init__(self) -> None:
        self._stock = StockStrategy()
        self._voice = AIVoiceStrategy()

    async def generate(
        self,
        concept: Concept,
        settings: AppSettings,
        secrets: EnvSecrets,
        concept_dir: Path,
        *,
        smoke: bool = False,
    ) -> list:
        video_items = await self._stock.generate(
            concept, settings, secrets, concept_dir, smoke=smoke
        )
        audio_items = await self._voice.generate(
            concept, settings, secrets, concept_dir, smoke=smoke
        )
        return [*video_items, *audio_items]


_STRATEGIES: dict[str, AssetStrategy] = {
    "stock": StockStrategy(),
    "ai_voice": AIVoiceStrategy(),
    "ai_video": AIVideoStrategy(),
    "tts+stock": TTSStockStrategy(),
}


def get_strategy(name: str) -> AssetStrategy:
    key = (name or "tts+stock").strip().lower()
    # normalize aliases
    aliases = {
        "tts_stock": "tts+stock",
        "tts-stock": "tts+stock",
        "voice": "ai_voice",
        "tts": "ai_voice",
    }
    key = aliases.get(key, key)
    if key not in _STRATEGIES:
        known = ", ".join(sorted(_STRATEGIES))
        raise ConfigurationError(
            f"Unknown asset_strategy {name!r}. Known strategies: {known}."
        )
    return _STRATEGIES[key]


async def generate_assets(
    concept: Concept,
    settings: AppSettings,
    secrets: EnvSecrets,
    assets_root: Path,
    *,
    smoke: bool = False,
) -> AssetsManifest:
    """Generate assets for one concept into assets_root/{concept_id}/."""
    assets_root = Path(assets_root)
    concept_dir = assets_root / concept.concept_id
    concept_dir.mkdir(parents=True, exist_ok=True)

    strategy_name = concept.asset_strategy or "tts+stock"
    strategy = get_strategy(strategy_name)
    logger.info(
        "generating assets concept=%s strategy=%s smoke=%s → %s",
        concept.concept_id,
        strategy.name,
        smoke,
        concept_dir,
    )
    items = await strategy.generate(
        concept, settings, secrets, concept_dir, smoke=smoke
    )

    manifest = AssetsManifest(
        concept_id=concept.concept_id,
        assets=items,
        strategy=strategy.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    manifest_path = concept_dir / "assets_manifest.json"
    payload = manifest.model_dump_json(indent=2)
    async with aiofiles.open(manifest_path, "w", encoding="utf-8") as fh:
        await fh.write(payload + "\n")
    logger.info("wrote assets_manifest → %s (%d items)", manifest_path, len(items))
    return AssetsManifest.model_validate_json(payload)
