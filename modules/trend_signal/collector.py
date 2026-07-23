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
    missing: list[str] = []
    if not secrets.youtube_api_key:
        missing.append("YOUTUBE_API_KEY")
    if not secrets.reddit_client_id or not secrets.reddit_client_secret:
        missing.append("REDDIT_CLIENT_ID/SECRET")

    hint = (
        " Set API keys in .env, or call collect_signals(..., use_fixtures=True) "
        "/ allow_offline=True for offline fixture mode."
    )

    # Fail fast before any network I/O when both authenticated sources lack keys.
    if len(missing) == 2:
        raise ConfigurationError(
            f"Missing credentials: {', '.join(missing)}. "
            f"YouTube and Reddit API keys are required for live collection.{hint}"
        )

    collected: list[TrendSignal] = []
    errors: list[str] = []

    if secrets.youtube_api_key:
        try:
            collected.extend(await fetch_youtube_signals(settings, secrets))
        except ConfigurationError as exc:
            errors.append(str(exc))
        except Exception as exc:
            logger.exception("youtube collector failed")
            errors.append(f"YouTube: {exc}")
    else:
        errors.append("YOUTUBE_API_KEY missing — skipped YouTube")

    if secrets.reddit_client_id and secrets.reddit_client_secret:
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

    if not collected:
        detail = "; ".join(errors) if errors else "no signals returned"
        if missing:
            raise ConfigurationError(
                f"No trend signals collected; missing credentials: {', '.join(missing)}. "
                f"Details: {detail}.{hint}"
            )
        raise ConfigurationError(f"No trend signals collected. Details: {detail}.{hint}")

    demo = settings.demographic.model_dump()
    return rank_signals(
        collected,
        max_topics=settings.pipeline.max_signal_topics,
        demographic=demo,
    )
