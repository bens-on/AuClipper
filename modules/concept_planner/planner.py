"""Concept planner entrypoint.

Turns a :class:`SignalsFile` of trend signals into an original :class:`ConceptsFile`
of creative briefs using the Anthropic Claude API.

Design guarantees:
- Every premise is an *original* idea inspired by observed patterns. The prompt
  explicitly forbids reproducing or reconstructing any specific creator's real
  video script/structure and forbids naming real viral videos as sources to copy.
- Model output is validated against :class:`ConceptsFile`. One corrective retry is
  attempted when the first response is not valid JSON / fails schema validation.
- Without an Anthropic API key the planner raises a clear error in live mode. An
  explicit ``offline=True`` opt-in enables a deterministic fixture-mapper for
  key-free smoke runs; tests otherwise inject a mocked client.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles
from pydantic import ValidationError

from modules.common.models import (
    Beat,
    Concept,
    ConceptFormat,
    ConceptsFile,
    SignalsFile,
    TrendSignal,
)
from modules.common.settings import AppSettings, EnvSecrets

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


SYSTEM_PROMPT = (
    "You are a short-form video concept planner for original comedy/relatable "
    "Reels and TikToks. You transform anonymized trend *signals* (topics, formats, "
    "and engagement patterns) into brand-new, ORIGINAL creative briefs.\n\n"
    "HARD CREATIVE RULES — these are non-negotiable:\n"
    "1. Every premise MUST be an original idea that you invent, merely *inspired* "
    "by the general patterns in the signals (the vibe, the topic, the format).\n"
    "2. NEVER reproduce, transcribe, paraphrase, or reconstruct any specific "
    "creator's actual video script, shot list, or beat-by-beat structure.\n"
    "3. NEVER name, cite, or reference a real viral video, real creator, or real "
    "channel as a source to copy or 'recreate'. Treat sample titles only as a "
    "loose thematic hint, not as material to imitate.\n"
    "4. Do not use real people's names, real brand names, or copyrighted "
    "characters. Keep everything generic and original.\n"
    "5. Write for an 18-24 comedy/meme audience: short, punchy, non-corporate.\n\n"
    "OUTPUT CONTRACT — respond with a SINGLE JSON object only (no prose, no "
    "markdown fences) matching exactly this shape:\n"
    "{\n"
    '  "concepts": [\n'
    "    {\n"
    '      "concept_id": "snake_case_unique_id",\n'
    '      "premise": "one or two sentences describing the ORIGINAL idea",\n'
    '      "format": one of "text-overlay meme" | "AI-voiceover skit" | '
    '"stock-footage + punchline",\n'
    '      "target_length_sec": number > 0 and <= 90,\n'
    '      "asset_strategy": "tts+stock",\n'
    '      "stock_keywords": ["generic", "b-roll", "search", "terms"],\n'
    '      "voiceover_script": "full narration text, or null for text-only memes",\n'
    '      "beat_sheet": [\n'
    "        {\n"
    '          "order": 0,\n'
    '          "description": "what happens in this beat",\n'
    '          "duration_sec": number > 0,\n'
    '          "visual_cue": "generic stock/broll description",\n'
    '          "voiceover_line": "line for this beat, or null",\n'
    '          "on_screen_text": "overlay text, or null"\n'
    "        }\n"
    "      ]\n"
    "    }\n"
    "  ]\n"
    "}\n"
    "Beat durations should roughly sum to target_length_sec. Every concept needs "
    "at least one beat."
)


def _build_user_prompt(signals: SignalsFile, settings: AppSettings) -> str:
    """Render the anonymized signals + constraints into a user prompt."""
    demo = settings.demographic
    max_concepts = settings.pipeline.max_concepts
    max_topics = settings.pipeline.max_signal_topics
    default_len = settings.pipeline.default_target_length_sec
    max_len = settings.pipeline.max_length_sec

    top_signals = sorted(
        signals.signals, key=lambda s: s.engagement_score, reverse=True
    )[:max_topics]

    signal_lines: list[str] = []
    for idx, sig in enumerate(top_signals, start=1):
        titles = "; ".join(sig.sample_titles) if sig.sample_titles else "(none)"
        signal_lines.append(
            f"{idx}. topic={sig.topic!r} | preferred_format={sig.format_type} | "
            f"engagement={sig.engagement_score} | thematic_hints=[{titles}]"
        )

    return (
        f"Audience: age_band={demo.age_band}, region={demo.region or 'n/a'}, "
        f"interests={', '.join(demo.interests) or 'general comedy'}.\n\n"
        f"Produce EXACTLY {max_concepts} original concepts (fewer only if the "
        f"signals genuinely cannot support that many). Default target length "
        f"~{default_len}s, hard max {max_len}s.\n\n"
        "Anonymized trend signals (patterns only — do NOT copy any specific "
        "video these hints came from):\n"
        + "\n".join(signal_lines)
        + "\n\nReturn the JSON object now."
    )


def _extract_text(message: Any) -> str:
    """Pull the concatenated text out of an Anthropic Messages response."""
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _strip_code_fences(raw: str) -> str:
    """Best-effort removal of markdown code fences around a JSON payload."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_concepts(raw: str, *, source_signals_path: str | None) -> ConceptsFile:
    """Parse + validate a model response into a ConceptsFile (raises on failure)."""
    payload = json.loads(_strip_code_fences(raw))
    if isinstance(payload, list):
        payload = {"concepts": payload}
    payload.setdefault("source_signals_path", source_signals_path)
    payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    return ConceptsFile.model_validate(payload)


async def _call_model(
    client: AsyncAnthropic,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    user_prompt: str,
    correction: str | None = None,
) -> str:
    """Single Messages API round-trip; returns raw assistant text."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    if correction is not None:
        messages.append({"role": "user", "content": correction})
    message = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return _extract_text(message)


def _fixture_map(signals: SignalsFile, settings: AppSettings) -> ConceptsFile:
    """Deterministic, key-free mapping of signals -> original concepts.

    Used only for explicit offline smoke runs. Produces schema-valid concepts
    with generic, invented premises (never reconstructing any real video).
    """
    max_concepts = settings.pipeline.max_concepts
    default_len = float(settings.pipeline.default_target_length_sec)
    top = sorted(signals.signals, key=lambda s: s.engagement_score, reverse=True)
    concepts: list[Concept] = []

    for idx, sig in enumerate(top[:max_concepts]):
        fmt = _coerce_format(sig.format_type)
        slug = _slugify(sig.topic) or f"concept_{idx}"
        beats = [
            Beat(
                order=0,
                description=f"Cold open establishing the {sig.topic} setup",
                duration_sec=round(default_len * 0.3, 2),
                visual_cue=f"generic b-roll: {sig.topic}",
                voiceover_line=f"So here's the thing about {sig.topic}.",
                on_screen_text=sig.topic.upper()[:40],
            ),
            Beat(
                order=1,
                description="Escalation / relatable turn",
                duration_sec=round(default_len * 0.4, 2),
                visual_cue="reaction b-roll",
                voiceover_line="And it just keeps getting worse.",
                on_screen_text="it gets worse",
            ),
            Beat(
                order=2,
                description="Original punchline",
                duration_sec=round(default_len * 0.3, 2),
                visual_cue="closing b-roll",
                voiceover_line="Anyway, that's my villain origin story.",
                on_screen_text="the end (unfortunately)",
            ),
        ]
        voiceover = (
            " ".join(b.voiceover_line for b in beats if b.voiceover_line)
            if fmt != ConceptFormat.TEXT_OVERLAY_MEME
            else None
        )
        concepts.append(
            Concept(
                concept_id=f"offline_{idx}_{slug}"[:60],
                premise=(
                    f"Original {fmt.value}: an invented, relatable take on "
                    f"'{sig.topic}' — inspired by the trend, not copied from any "
                    "specific video."
                ),
                format=fmt,
                beat_sheet=beats,
                target_length_sec=min(default_len, 90.0),
                asset_strategy="tts+stock",
                stock_keywords=_keywords_for(sig),
                voiceover_script=voiceover,
            )
        )

    return ConceptsFile(
        concepts=concepts,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_signals_path=None,
    )


def _coerce_format(value: str) -> ConceptFormat:
    try:
        return ConceptFormat(str(value))
    except ValueError:
        return ConceptFormat.TEXT_OVERLAY_MEME


def _slugify(text: str) -> str:
    cleaned = [c.lower() if c.isalnum() else "_" for c in text.strip()]
    slug = "".join(cleaned)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _keywords_for(sig: TrendSignal) -> list[str]:
    words = [w for w in _slugify(sig.topic).split("_") if len(w) > 2]
    return words[:5] or ["lifestyle", "people"]


async def _write_concepts(concepts_file: ConceptsFile, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = concepts_file.model_dump_json(indent=2)
    async with aiofiles.open(output_path, "w", encoding="utf-8") as fh:
        await fh.write(payload)


async def plan_concepts(
    signals: SignalsFile,
    settings: AppSettings,
    secrets: EnvSecrets,
    output_path: Path,
    *,
    offline: bool = False,
    client: AsyncAnthropic | None = None,
) -> ConceptsFile:
    """Turn signals into original creative briefs; write concepts.json.

    Args:
        signals: Anonymized trend signals to draw inspiration from.
        settings: App settings (pipeline limits, llm model/params, demographic).
        secrets: Environment secrets; ``anthropic_api_key`` drives live mode.
        output_path: Destination path for the validated ``concepts.json``.
        offline: When True, use the deterministic key-free fixture-mapper
            instead of calling the Anthropic API.
        client: Optional pre-built ``AsyncAnthropic`` client (injected in tests).

    Returns:
        The validated :class:`ConceptsFile` that was written to ``output_path``.
    """
    output_path = Path(output_path)
    source_signals_path = getattr(settings.paths, "signals_dir", None)

    if offline:
        concepts_file = _fixture_map(signals, settings)
        await _write_concepts(concepts_file, output_path)
        return concepts_file

    api_key = secrets.anthropic_api_key
    if client is None:
        if not api_key:
            raise RuntimeError(
                "concept_planner.plan_concepts requires an Anthropic API key "
                "(secrets.anthropic_api_key) in live mode. Set ANTHROPIC_API_KEY, "
                "pass offline=True for a deterministic key-free run, or inject a "
                "mocked client in tests."
            )
        from anthropic import AsyncAnthropic as _AsyncAnthropic

        client = _AsyncAnthropic(api_key=api_key)

    llm = settings.llm
    user_prompt = _build_user_prompt(signals, settings)

    raw = await _call_model(
        client,
        model=llm.model,
        max_tokens=llm.max_tokens,
        temperature=llm.temperature,
        user_prompt=user_prompt,
    )

    try:
        concepts_file = _parse_concepts(raw, source_signals_path=source_signals_path)
    except (json.JSONDecodeError, ValidationError) as first_err:
        correction = (
            "Your previous reply was not valid according to the required schema. "
            f"Error: {first_err}. Reply again with ONLY a single valid JSON object "
            "matching the contract exactly — no markdown, no commentary."
        )
        raw_retry = await _call_model(
            client,
            model=llm.model,
            max_tokens=llm.max_tokens,
            temperature=llm.temperature,
            user_prompt=user_prompt,
            correction=correction,
        )
        concepts_file = _parse_concepts(
            raw_retry, source_signals_path=source_signals_path
        )

    await _write_concepts(concepts_file, output_path)
    return concepts_file
