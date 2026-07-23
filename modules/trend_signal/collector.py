"""Trend signal collection entrypoint.

Collects YouTube / Reddit / Google Trends metadata, ranks into SignalsFile,
and writes JSON to ``output_path``. Metadata/text only — never downloads video.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiofiles

from modules.common.models import SignalSource, SignalsFile, TrendSignal
from modules.common.settings import ROOT_DIR, AppSettings, EnvSecrets
from modules.trend_signal.ai_web import collect_ai_web_signals_file
from modules.trend_signal.errors import ConfigurationError
from modules.trend_signal.google_trends import fetch_google_trends_signals
from modules.trend_signal.ranking import rank_signals
from modules.trend_signal.reddit import fetch_reddit_signals
from modules.trend_signal.youtube import fetch_youtube_signals

logger = logging.getLogger(__name__)


async def collect_signals(
    settings: AppSettings,
    secrets: EnvSecrets,
    output_path: Path,
    *,
    use_fixtures: bool = False,
    allow_offline: bool = False,
) -> SignalsFile:
    """Collect and rank trend signals; write signals.json; return validated model.

    Parameters
    ----------
    use_fixtures / allow_offline:
        When True, skip live APIs and load ``data/fixtures/signals.json``.
        Also used automatically when keys are missing *and* either flag is True.
        Unit tests should mock API clients rather than relying on fixtures.
    """
    offline = use_fixtures or allow_offline
    if offline:
        signals_file = await _load_fixtures(settings)
    else:
        signals_file = await _collect_live(settings, secrets)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = signals_file.model_dump_json(indent=2)
    async with aiofiles.open(output_path, "w", encoding="utf-8") as fh:
        await fh.write(payload + "\n")
    logger.info(
        "wrote %d ranked signals → %s",
        len(signals_file.signals),
        output_path,
    )
    return SignalsFile.model_validate_json(payload)


async def _load_fixtures(settings: AppSettings) -> SignalsFile:
    fixture_path = Path(settings.paths.fixtures_dir)
    if not fixture_path.is_absolute():
        fixture_path = ROOT_DIR / fixture_path
    path = fixture_path / "signals.json"
    if not path.exists():
        raise ConfigurationError(
            f"Fixture signals not found at {path}. Expected data/fixtures/signals.json."
        )
    async with aiofiles.open(path, "r", encoding="utf-8") as fh:
        raw = await fh.read()
    data = SignalsFile.model_validate_json(raw)
    # Re-stamp demographic from current settings for consistency.
    demo = settings.demographic.model_dump()
    capped = rank_signals(
        data.signals,
        max_topics=settings.pipeline.max_signal_topics,
        demographic=demo,
    )
    # Preserve fixture source labels.
    preserved = [
        s.model_copy(update={"source": s.source or SignalSource.FIXTURE})
        for s in capped.signals
    ]
    return capped.model_copy(update={"signals": preserved})


async def _collect_live(settings: AppSettings, secrets: EnvSecrets) -> SignalsFile:
    """Live collection: YouTube/Reddit if keyed, else Claude web research.

    Preferred single-key path: ``ANTHROPIC_API_KEY`` only (AI web search).
    Optional: add YouTube/Reddit keys for classic signal APIs.
    """
    has_youtube = bool(secrets.youtube_api_key)
    has_reddit = bool(secrets.reddit_client_id and secrets.reddit_client_secret)
    has_anthropic = bool(secrets.anthropic_api_key)

    hint = (
        " Set ANTHROPIC_API_KEY in .env for AI web trend research (recommended), "
        "or YOUTUBE_API_KEY + Reddit keys, or use --smoke for key-free fixtures."
    )

    # No classic API keys → AI web researcher (one Anthropic key).
    if not has_youtube and not has_reddit:
        if not has_anthropic:
            raise ConfigurationError(
                "No trend credentials configured. "
                "Add ANTHROPIC_API_KEY for AI web research (no YouTube/Reddit keys needed)."
                f"{hint}"
            )
        logger.info("Using AI web trend researcher (ANTHROPIC_API_KEY)")
        return await collect_ai_web_signals_file(settings, secrets)

    collected: list[TrendSignal] = []
    errors: list[str] = []

    if has_youtube:
        try:
            collected.extend(await fetch_youtube_signals(settings, secrets))
        except ConfigurationError as exc:
            errors.append(str(exc))
        except Exception as exc:
            logger.exception("youtube collector failed")
            errors.append(f"YouTube: {exc}")
    else:
        errors.append("YOUTUBE_API_KEY missing — skipped YouTube")

    if has_reddit:
        try:
            collected.extend(await fetch_reddit_signals(settings, secrets))
        except ConfigurationError as exc:
            errors.append(str(exc))
        except Exception as exc:
            logger.exception("reddit collector failed")
            errors.append(f"Reddit: {exc}")
    else:
        errors.append("REDDIT_CLIENT_ID/SECRET missing — skipped Reddit")

    # pytrends has no API key; only enrich when at least one auth source contributed.
    if collected:
        try:
            collected.extend(await fetch_google_trends_signals(settings))
        except Exception as exc:
            logger.warning("google trends collector failed: %s", exc)
            errors.append(f"Google Trends: {exc}")

    if not collected and has_anthropic:
        logger.warning(
            "Classic signal APIs returned nothing (%s); falling back to AI web research",
            "; ".join(errors) if errors else "empty",
        )
        return await collect_ai_web_signals_file(settings, secrets)

    if not collected:
        detail = "; ".join(errors) if errors else "no signals returned"
        raise ConfigurationError(f"No trend signals collected. Details: {detail}.{hint}")

    demo = settings.demographic.model_dump()
    return rank_signals(
        collected,
        max_topics=settings.pipeline.max_signal_topics,
        demographic=demo,
    )
