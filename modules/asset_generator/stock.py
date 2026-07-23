"""Stock footage strategy — Pexels + Pixabay (commercial-use licenses)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiofiles
import httpx

from modules.asset_generator.errors import AssetGenerationError, ConfigurationError
from modules.asset_generator.smoke import DEFAULT_SMOKE_DURATION_SEC, generate_smoke_video
from modules.common.models import AssetItem, AssetKind, Concept
from modules.common.settings import AppSettings, EnvSecrets

logger = logging.getLogger(__name__)

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"


class StockStrategy:
    name = "stock"

    async def generate(
        self,
        concept: Concept,
        settings: AppSettings,
        secrets: EnvSecrets,
        concept_dir: Path,
        *,
        smoke: bool = False,
    ) -> list[AssetItem]:
        concept_dir.mkdir(parents=True, exist_ok=True)
        if smoke:
            dest = concept_dir / "stock_smoke.mp4"
            await generate_smoke_video(
                dest,
                duration_sec=min(float(concept.target_length_sec), 10.0)
                or DEFAULT_SMOKE_DURATION_SEC,
                width=settings.pipeline.output_width,
                height=settings.pipeline.output_height,
            )
            return [
                AssetItem(
                    kind=AssetKind.VIDEO,
                    path=str(dest),
                    source="ffmpeg-smoke",
                    license="generated-local",
                    duration_sec=min(float(concept.target_length_sec), 10.0),
                    metadata={"smoke": True},
                )
            ]

        providers = [p.lower() for p in settings.pipeline.stock_providers]
        keywords = concept.stock_keywords or _keywords_from_concept(concept)
        query = " ".join(keywords[:4]) or concept.premise[:80]

        items: list[AssetItem] = []
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            if "pexels" in providers:
                if not secrets.pexels_api_key:
                    raise ConfigurationError(
                        "PEXELS_API_KEY missing. Set it in .env, or call generate_assets(..., smoke=True)."
                    )
                items.extend(
                    await self._from_pexels(
                        client, secrets.pexels_api_key, query, concept_dir
                    )
                )
            if "pixabay" in providers and not items:
                if not secrets.pixabay_api_key:
                    raise ConfigurationError(
                        "PIXABAY_API_KEY missing. Set it in .env, or call generate_assets(..., smoke=True)."
                    )
                items.extend(
                    await self._from_pixabay(
                        client, secrets.pixabay_api_key, query, concept_dir
                    )
                )

        if not items:
            raise AssetGenerationError(
                f"No commercial-use stock video found for query={query!r}"
            )
        return items

    async def _from_pexels(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        query: str,
        concept_dir: Path,
    ) -> list[AssetItem]:
        # Pexels API license: free to use commercially (attribution appreciated).
        resp = await client.get(
            PEXELS_SEARCH,
            params={"query": query, "per_page": 5, "orientation": "portrait"},
            headers={"Authorization": api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        for video in data.get("videos", []):
            file_url, width, height, duration = _pick_pexels_file(video)
            if not file_url:
                continue
            dest = concept_dir / f"pexels_{video.get('id', 'clip')}.mp4"
            await _download(client, file_url, dest)
            return [
                AssetItem(
                    kind=AssetKind.VIDEO,
                    path=str(dest),
                    source="pexels",
                    license="pexels-license-commercial",
                    duration_sec=float(duration) if duration else None,
                    metadata={
                        "pexels_id": video.get("id"),
                        "width": width,
                        "height": height,
                        "url": video.get("url"),
                    },
                )
            ]
        return []

    async def _from_pixabay(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        query: str,
        concept_dir: Path,
    ) -> list[AssetItem]:
        # Pixabay Content License allows commercial use (no identifiable people resale).
        resp = await client.get(
            PIXABAY_SEARCH,
            params={"key": api_key, "q": query, "per_page": 5, "video_type": "film"},
        )
        resp.raise_for_status()
        data = resp.json()
        for hit in data.get("hits", []):
            file_url = _pick_pixabay_file(hit)
            if not file_url:
                continue
            dest = concept_dir / f"pixabay_{hit.get('id', 'clip')}.mp4"
            await _download(client, file_url, dest)
            return [
                AssetItem(
                    kind=AssetKind.VIDEO,
                    path=str(dest),
                    source="pixabay",
                    license="pixabay-content-license-commercial",
                    duration_sec=float(hit.get("duration") or 0) or None,
                    metadata={
                        "pixabay_id": hit.get("id"),
                        "pageURL": hit.get("pageURL"),
                        "user": hit.get("user"),
                    },
                )
            ]
        return []


def _keywords_from_concept(concept: Concept) -> list[str]:
    words = [w.strip().lower() for w in concept.premise.replace(",", " ").split()]
    return [w for w in words if len(w) > 3][:6]


def _pick_pexels_file(video: dict[str, Any]) -> tuple[str | None, int | None, int | None, Any]:
    files = list(video.get("video_files") or [])
    # Prefer portrait-ish mid quality.
    files.sort(key=lambda f: abs(int(f.get("width") or 0) - 1080))
    for f in files:
        link = f.get("link")
        if link:
            return str(link), f.get("width"), f.get("height"), video.get("duration")
    return None, None, None, None


def _pick_pixabay_file(hit: dict[str, Any]) -> str | None:
    videos = hit.get("videos") or {}
    for quality in ("medium", "small", "large", "tiny"):
        entry = videos.get(quality) or {}
        url = entry.get("url")
        if url:
            return str(url)
    return None


async def _download(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    logger.info("downloading stock → %s (%s)", dest.name, urlparse(url).netloc)
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        async with aiofiles.open(dest, "wb") as fh:
            async for chunk in resp.aiter_bytes():
                await fh.write(chunk)
