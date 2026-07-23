"""Caption / hashtag text generation (LLM with template fallback).

Wave 1 (Opus): implement `generate_caption_and_hashtags`.
"""

from __future__ import annotations

from modules.common.models import Concept


async def generate_caption_and_hashtags(
    concept: Concept,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-20250514",
) -> tuple[str, str]:
    """Return (caption_text, hashtags_text). Template fallback when no API key."""
    caption = f"{concept.premise.strip()}\n\n#reels #comedy"
    hashtags = "#reels #comedy #fyp #viral #relatable"
    return caption, hashtags
