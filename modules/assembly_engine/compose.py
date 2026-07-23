"""Rough-cut assembly from assets + beat sheet.

Wave 1 (GPT): implement `assemble_roughcut`.
"""

from __future__ import annotations

from pathlib import Path

from modules.common.models import AssetsManifest, CaptionCue, Concept


async def assemble_roughcut(
    concept: Concept,
    manifest: AssetsManifest,
    output_path: Path,
    *,
    whisper_model: str = "base",
) -> tuple[Path, list[CaptionCue]]:
    """Compose rough-cut MP4 and return (path, caption_cues)."""
    raise NotImplementedError("assembly_engine.compose.assemble_roughcut — Wave 1 GPT")
