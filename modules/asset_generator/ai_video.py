"""AI video strategy stub — configure a provider to enable."""

from __future__ import annotations

from pathlib import Path

from modules.asset_generator.errors import ConfigurationError
from modules.common.models import AssetItem, Concept
from modules.common.settings import AppSettings, EnvSecrets


class AIVideoStrategy:
    name = "ai_video"

    async def generate(
        self,
        concept: Concept,
        settings: AppSettings,
        secrets: EnvSecrets,
        concept_dir: Path,
        *,
        smoke: bool = False,
    ) -> list[AssetItem]:
        provider = settings.pipeline.ai_video_provider
        available = {
            "runway": bool(secrets.runway_api_key),
            "pika": bool(secrets.pika_api_key),
            "luma": bool(secrets.luma_api_key),
        }
        hint_keys = "RUNWAY_API_KEY / PIKA_API_KEY / LUMA_API_KEY"
        if smoke:
            raise NotImplementedError(
                "ai_video strategy is stubbed in Phase 1 even for smoke runs. "
                "Use asset_strategy 'tts+stock' or 'stock' instead. "
                f"To enable later, set pipeline.ai_video_provider and {hint_keys}."
            )
        if not provider:
            raise ConfigurationError(
                "ai_video strategy requires pipeline.ai_video_provider "
                "(runway | pika | luma) and the matching API key. "
                f"Currently unset. Set provider + {hint_keys}, or switch "
                "concept.asset_strategy to 'tts+stock'."
            )
        key_ok = available.get(str(provider).lower(), False)
        if not key_ok:
            raise ConfigurationError(
                f"ai_video provider {provider!r} is configured but its API key is missing. "
                f"Set the matching key ({hint_keys}) or change asset_strategy."
            )
        raise NotImplementedError(
            f"ai_video provider {provider!r} is not implemented in Phase 1 "
            f"(concept_id={concept.concept_id}). Integration coming in a later wave."
        )
