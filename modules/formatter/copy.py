"""Caption / hashtag text generation (LLM with template fallback).

``generate_caption_and_hashtags`` writes the social copy for a finished clip:
a short, non-corporate caption suitable for Reels/TikTok and a de-duplicated
hashtag line. When an Anthropic API key is present it uses Claude; otherwise it
falls back to a deterministic template/heuristic so the key-free smoke path
still produces usable copy.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from modules.common.models import Concept, ConceptFormat

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


MAX_HASHTAGS = 8

SYSTEM_PROMPT = (
    "You write social copy for short-form comedy Reels/TikToks aimed at an "
    "18-24 audience. Voice: casual, punchy, a little unhinged, lowercase-friendly, "
    "NOT corporate, no hard sell, no emojispam. Given a video concept you return "
    "ONE caption (1-2 short lines, hook-first, <=150 chars) and 5-8 lowercase "
    "hashtags that fit the topic (mix broad + niche, no spaces inside a tag).\n\n"
    "Respond with a SINGLE JSON object only, no markdown:\n"
    '{"caption": "the caption text", "hashtags": ["reels", "fyp", "..."]}'
)

_BASE_HASHTAGS: tuple[str, ...] = ("reels", "fyp", "comedy", "relatable", "viral")

_FORMAT_HASHTAGS: dict[ConceptFormat, tuple[str, ...]] = {
    ConceptFormat.TEXT_OVERLAY_MEME: ("meme", "memes", "funny"),
    ConceptFormat.AI_VOICEOVER_SKIT: ("skit", "comedyskit", "voiceover"),
    ConceptFormat.STOCK_PUNCHLINE: ("funny", "lol", "humor"),
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "that", "this", "your", "you", "when", "who", "what", "it", "is", "are",
    "go", "goes", "gone", "wrong", "right",
}


def _normalize_tag(raw: str) -> str:
    """Turn arbitrary text into a bare hashtag token (no leading '#', no spaces)."""
    token = re.sub(r"[^0-9a-zA-Z]+", "", raw.lower())
    return token


def _dedupe_tags(tags: list[str], limit: int = MAX_HASHTAGS) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        token = _normalize_tag(tag)
        if token and token not in seen:
            seen.add(token)
            out.append(token)
        if len(out) >= limit:
            break
    return out


def _format_hashtag_line(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags)


def _fallback_caption(concept: Concept) -> str:
    """Heuristic caption from the concept's premise / first on-screen beat."""
    hook = ""
    for beat in concept.beat_sheet:
        if beat.on_screen_text and beat.on_screen_text.strip():
            hook = beat.on_screen_text.strip()
            break

    premise = concept.premise.strip()
    # Drop a leading "Original <format>:" scaffold that concepts sometimes carry.
    premise = re.sub(r"^original[^:]*:\s*", "", premise, flags=re.IGNORECASE)
    # First sentence only, keep it short.
    first_sentence = re.split(r"(?<=[.!?])\s+", premise, maxsplit=1)[0].strip()
    if len(first_sentence) > 140:
        first_sentence = first_sentence[:137].rstrip() + "..."

    if hook and hook.lower() not in first_sentence.lower():
        return f"{hook.lower()} — {first_sentence}"
    return first_sentence or "we need to talk about this"


def _fallback_hashtags(concept: Concept) -> list[str]:
    topic_tokens = [
        _normalize_tag(w)
        for w in re.split(r"\s+", concept.premise)
        if w and _normalize_tag(w) not in _STOPWORDS and len(_normalize_tag(w)) > 3
    ]
    fmt = _coerce_format(concept.format)
    ordered = [
        *_BASE_HASHTAGS[:3],
        *topic_tokens[:2],
        *_FORMAT_HASHTAGS.get(fmt, ()),
        *_BASE_HASHTAGS[3:],
    ]
    tags = _dedupe_tags(ordered)
    if len(tags) < 5:
        tags = _dedupe_tags([*tags, *_BASE_HASHTAGS, "trending", "foryou"])
    return tags


def _coerce_format(value: ConceptFormat | str) -> ConceptFormat:
    try:
        return ConceptFormat(str(value))
    except ValueError:
        return ConceptFormat.TEXT_OVERLAY_MEME


def _build_user_prompt(concept: Concept) -> str:
    fmt = _coerce_format(concept.format)
    keywords = ", ".join(concept.stock_keywords) or "n/a"
    return (
        f"Concept premise: {concept.premise}\n"
        f"Format: {fmt.value}\n"
        f"Target length: {concept.target_length_sec}s\n"
        f"Topic keywords: {keywords}\n\n"
        "Write the caption + hashtags JSON now."
    )


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _strip_code_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_llm_copy(
    raw: str, concept: Concept
) -> tuple[str, list[str]] | None:
    """Parse the model JSON into (caption, hashtags); None if unusable."""
    try:
        payload = json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    caption = str(payload.get("caption", "")).strip()
    raw_tags = payload.get("hashtags", [])
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.split()
    tags = _dedupe_tags([str(t) for t in raw_tags]) if isinstance(raw_tags, list) else []
    if not caption:
        return None
    if not tags:
        tags = _fallback_hashtags(concept)
    return caption, tags


async def generate_caption_and_hashtags(
    concept: Concept,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    client: AsyncAnthropic | None = None,
) -> tuple[str, str]:
    """Return (caption_text, hashtags_text).

    Uses Claude when ``api_key`` (or an injected ``client``) is available;
    otherwise falls back to a deterministic template/heuristic. If a live call
    fails or returns unusable output, the heuristic fallback is used so the
    smoke path never breaks.

    Args:
        concept: The concept whose copy is being generated.
        api_key: Anthropic API key; enables the Claude path when present.
        model: Claude model id to use for the live path.
        client: Optional pre-built ``AsyncAnthropic`` client (injected in tests).

    Returns:
        A ``(caption, hashtags)`` tuple. ``caption`` is the ``caption.txt``
        content; ``hashtags`` is a single space-separated ``#tag`` line for
        ``hashtags.txt``.
    """
    if client is None and api_key:
        from anthropic import AsyncAnthropic as _AsyncAnthropic

        client = _AsyncAnthropic(api_key=api_key)

    if client is not None:
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=512,
                temperature=0.9,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _build_user_prompt(concept)}],
            )
            parsed = _parse_llm_copy(_extract_text(message), concept)
            if parsed is not None:
                caption, tags = parsed
                return caption, _format_hashtag_line(tags)
        except Exception:  # noqa: BLE001 — never let copy break the pipeline
            pass

    caption = _fallback_caption(concept)
    hashtags = _format_hashtag_line(_fallback_hashtags(concept))
    return caption, hashtags
