"""9:16 export, crop/pad, burned-in captions.

Wave 1 (GPT): implement `format_for_reels`.
"""

from __future__ import annotations

from pathlib import Path

from modules.common.models import CaptionCue, Concept


async def format_for_reels(
    roughcut_path: Path,
    concept: Concept,
    output_dir: Path,
    *,
    caption_cues: list[CaptionCue] | None = None,
    width: int = 1080,
    height: int = 1920,
    max_length_sec: float = 90.0,
) -> dict[str, Path]:
    """Produce final.mp4 (+ optionally leave caption/hashtag writing to caller).

    Returns paths dict with at least `final_mp4`.
    """
    raise NotImplementedError("formatter.video.format_for_reels — Wave 1 GPT")
