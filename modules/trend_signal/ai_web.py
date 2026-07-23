"""AI web trend researcher — Claude + Anthropic web_search (one API key).

Replaces YouTube/Reddit keys for Phase 1 when you only want ANTHROPIC_API_KEY.
Uses Anthropic's hosted web search tool (metadata/text only — no video download).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from modules.common.models import FormatType, SignalSource, SignalsFile, TrendSignal
from modules.common.settings import AppSettings, EnvSecrets
from modules.trend_signal.errors import ConfigurationError
from modules.trend_signal.ranking import rank_signals

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a short-form comedy trend researcher for Instagram Reels / TikTok "
    "aimed at Gen Z. Use web search to find what formats and topics are landing "
    "right now among the target demographic.\n\n"
    "Rules:\n"
    "- Metadata and pattern research ONLY. Do not download or reconstruct any "
    "specific creator's video.\n"
    "- Prefer general cultural/meme patterns, comedy formats, and topic clusters "
    "over naming a single viral video to copy.\n"
    "- Sample titles must be ORIGINAL example hooks you invent that match the "
    "pattern — not titles of real videos to remake.\n"
    "- Return ONLY a JSON object (no markdown) with shape:\n"
    '{"signals":[{"topic":"...","format_type":"AI-voiceover skit|text-overlay meme|'
    'stock-footage + punchline","engagement_score":0-100,"sample_titles":["...","..."]}]}'
)


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


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts).strip()


def _coerce_format(value: str) -> str:
    raw = (value or "").strip().lower()
    mapping = {
        "text-overlay meme": FormatType.TEXT_OVERLAY_MEME.value,
        "text overlay meme": FormatType.TEXT_OVERLAY_MEME.value,
        "meme": FormatType.TEXT_OVERLAY_MEME.value,
        "ai-voiceover skit": FormatType.AI_VOICEOVER_SKIT.value,
        "ai voiceover skit": FormatType.AI_VOICEOVER_SKIT.value,
        "voiceover": FormatType.AI_VOICEOVER_SKIT.value,
        "stock-footage + punchline": FormatType.STOCK_PUNCHLINE.value,
        "stock footage + punchline": FormatType.STOCK_PUNCHLINE.value,
        "stock": FormatType.STOCK_PUNCHLINE.value,
    }
    return mapping.get(raw, value if value else FormatType.AI_VOICEOVER_SKIT.value)


def _parse_signals(raw: str) -> list[TrendSignal]:
    payload = json.loads(_strip_code_fences(raw))
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("signals") or payload.get("topics") or []
    else:
        raise ValueError("Model response is not a JSON object/list")
    if not isinstance(items, list) or not items:
        raise ValueError("No signals array in model response")

    signals: list[TrendSignal] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        if not topic:
            continue
        score = item.get("engagement_score", 50)
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            score_f = 50.0
        titles = item.get("sample_titles") or item.get("hooks") or []
        if not isinstance(titles, list):
            titles = [str(titles)]
        signals.append(
            TrendSignal.model_validate(
                {
                    "topic": topic,
                    "format_type": _coerce_format(str(item.get("format_type") or "")),
                    "engagement_score": max(0.0, min(100.0, score_f)),
                    "source": SignalSource.AI_WEB,
                    "sample_titles": [str(t) for t in titles][:5],
                }
            )
        )
    if not signals:
        raise ValueError("Parsed zero valid signals from model response")
    return signals


def _find_json_blob(text: str) -> str:
    """If prose surrounds JSON, take the outermost object/array."""
    text = text.strip()
    try:
        json.loads(_strip_code_fences(text))
        return _strip_code_fences(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if not match:
        raise ValueError("No JSON object found in model response")
    return match.group(1)


async def fetch_ai_web_signals(
    settings: AppSettings,
    secrets: EnvSecrets,
    *,
    client: Any | None = None,
) -> list[TrendSignal]:
    """Research current short-form comedy trends via Claude web_search."""
    api_key = secrets.anthropic_api_key
    if client is None and not api_key:
        raise ConfigurationError(
            "ANTHROPIC_API_KEY is required for AI web trend research. "
            "Add it to .env, or use --smoke for key-free fixtures."
        )

    if client is None:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)

    demo = settings.demographic
    seeds = ", ".join(settings.topic_seeds) or "relatable comedy, memes, student life"
    max_topics = settings.pipeline.max_signal_topics
    user_prompt = (
        f"Research what short-form comedy / meme formats are trending NOW for "
        f"ages {demo.age_band}"
        + (f" in {demo.region}" if demo.region else "")
        + f". Interests: {', '.join(demo.interests) or 'comedy'}. "
        f"Topic seeds to explore: {seeds}.\n\n"
        f"Return up to {max_topics} ranked signals as JSON only."
    )

    tools: list[dict[str, Any]] = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        }
    ]

    logger.info("AI web trend research starting (model=%s)", settings.llm.model)
    try:
        message = await client.messages.create(
            model=settings.llm.model,
            max_tokens=min(settings.llm.max_tokens, 4096),
            temperature=min(settings.llm.temperature, 0.7),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tools=tools,
        )
    except Exception as exc:
        # Some accounts/models may not allow web_search; fall back to no-tools research.
        logger.warning("web_search tool failed (%s); retrying without tools", exc)
        message = await client.messages.create(
            model=settings.llm.model,
            max_tokens=min(settings.llm.max_tokens, 4096),
            temperature=min(settings.llm.temperature, 0.7),
            system=SYSTEM_PROMPT
            + "\nWeb search is unavailable — use best current cultural knowledge.",
            messages=[{"role": "user", "content": user_prompt}],
        )

    raw = _extract_text(message)
    if not raw:
        raise ConfigurationError("Claude returned empty trend research response")

    try:
        signals = _parse_signals(_find_json_blob(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        # One corrective retry without tools
        logger.warning("Invalid AI web JSON (%s); requesting correction", exc)
        correction = (
            "Your previous reply was not valid JSON matching the schema. "
            "Reply with ONLY the JSON object of signals, no prose."
        )
        retry = await client.messages.create(
            model=settings.llm.model,
            max_tokens=2048,
            temperature=0.3,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": raw},
                {"role": "user", "content": correction},
            ],
        )
        signals = _parse_signals(_find_json_blob(_extract_text(retry)))

    logger.info("AI web trend research returned %d signals", len(signals))
    return signals


async def collect_ai_web_signals_file(
    settings: AppSettings,
    secrets: EnvSecrets,
    *,
    client: Any | None = None,
) -> SignalsFile:
    signals = await fetch_ai_web_signals(settings, secrets, client=client)
    ranked = rank_signals(
        signals,
        max_topics=settings.pipeline.max_signal_topics,
        demographic=settings.demographic.model_dump(),
    )
    return ranked.model_copy(
        update={"generated_at": datetime.now(UTC).isoformat()}
    )
