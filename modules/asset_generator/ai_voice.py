"""AI voice / TTS strategy — ElevenLabs with Piper (or ffmpeg) fallback."""

from __future__ import annotations

import logging
from pathlib import Path

import aiofiles
import httpx

from modules.asset_generator.errors import ConfigurationError
from modules.asset_generator.smoke import generate_smoke_voiceover
from modules.common.models import AssetItem, AssetKind, Concept
from modules.common.settings import AppSettings, EnvSecrets

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class AIVoiceStrategy:
    name = "ai_voice"

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
        text = (concept.full_voiceover or concept.premise or "Hello from AuCl.").strip()
        prefer_piper = smoke or not secrets.elevenlabs_api_key

        if prefer_piper:
            dest = concept_dir / "voiceover.wav"
            path, source = await generate_smoke_voiceover(
                text,
                dest,
                voice_name=settings.voice.piper_voice,
                duration_sec=min(float(concept.target_length_sec), 10.0),
            )
            return [
                AssetItem(
                    kind=AssetKind.AUDIO,
                    path=str(path),
                    source=source,
                    license="generated-local",
                    duration_sec=None,
                    metadata={"smoke": smoke, "provider": source, "text_chars": len(text)},
                )
            ]

        if not secrets.elevenlabs_voice_id:
            raise ConfigurationError(
                "ELEVENLABS_VOICE_ID missing. Set it in .env alongside ELEVENLABS_API_KEY, "
                "or use smoke=True / omit the ElevenLabs key to fall back to Piper/ffmpeg."
            )

        dest = concept_dir / "voiceover.mp3"
        await _elevenlabs_tts(
            text=text,
            dest=dest,
            api_key=secrets.elevenlabs_api_key or "",
            voice_id=secrets.elevenlabs_voice_id,
            model_id=settings.voice.elevenlabs_model,
        )
        return [
            AssetItem(
                kind=AssetKind.AUDIO,
                path=str(dest),
                source="elevenlabs",
                license="elevenlabs-generated",
                duration_sec=None,
                metadata={"model": settings.voice.elevenlabs_model, "text_chars": len(text)},
            )
        ]


async def _elevenlabs_tts(
    *,
    text: str,
    dest: Path,
    api_key: str,
    voice_id: str,
    model_id: str,
) -> Path:
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        async with aiofiles.open(dest, "wb") as fh:
            await fh.write(resp.content)
    logger.info("wrote ElevenLabs voiceover → %s", dest)
    return dest
