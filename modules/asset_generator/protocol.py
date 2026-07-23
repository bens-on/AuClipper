"""Strategy protocol for asset generation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from modules.common.models import AssetItem, Concept
from modules.common.settings import AppSettings, EnvSecrets


@runtime_checkable
class AssetStrategy(Protocol):
    """Pluggable asset generation strategy."""

    name: str

    async def generate(
        self,
        concept: Concept,
        settings: AppSettings,
        secrets: EnvSecrets,
        concept_dir: Path,
        *,
        smoke: bool = False,
    ) -> list[AssetItem]:
        """Produce asset items under ``concept_dir`` and return manifest entries."""
        ...
