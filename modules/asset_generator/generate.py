"""Asset generation entrypoint with pluggable strategies.

Wave 1 (Grok): implement strategies + `generate_assets`.
"""

from __future__ import annotations

from pathlib import Path

from modules.common.models import AssetsManifest, Concept
from modules.common.settings import AppSettings, EnvSecrets


async def generate_assets(
    concept: Concept,
    settings: AppSettings,
    secrets: EnvSecrets,
    assets_root: Path,
    *,
    smoke: bool = False,
) -> AssetsManifest:
    """Generate assets for one concept into assets_root/{concept_id}/."""
    raise NotImplementedError("asset_generator.generate.generate_assets — Wave 1 Grok")
