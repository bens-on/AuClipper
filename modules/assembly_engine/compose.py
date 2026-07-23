"""Rough-cut assembly from generated visual and voice-over assets."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Any

from modules.common.models import AssetItem, AssetKind, AssetsManifest, CaptionCue, Concept


def _existing_asset_path(asset: AssetItem) -> Path:
    path = Path(asset.path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Asset does not exist: {path}")
    return path


def _select_voiceover(assets: list[AssetItem]) -> AssetItem | None:
    audio_assets = [asset for asset in assets if asset.kind == AssetKind.AUDIO]
    if not audio_assets:
        return None
    return next(
        (
            asset
            for asset in audio_assets
            if str(asset.metadata.get("role", "")).lower() in {"voiceover", "voice-over", "tts"}
            or "voice" in asset.source.lower()
            or "tts" in asset.source.lower()
        ),
        audio_assets[0],
    )


@lru_cache(maxsize=2)
def _load_whisper_model(model_name: str) -> Any:
    """Load each requested model once per process (the import is intentionally lazy)."""
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device="cpu", compute_type="int8")


def _transcribe_voiceover(audio_path: Path, model_name: str) -> list[CaptionCue]:
    model = _load_whisper_model(model_name)
    segments, _ = model.transcribe(
        str(audio_path),
        beam_size=1,
        vad_filter=True,
        word_timestamps=True,
    )
    cues: list[CaptionCue] = []
    words: list[Any] = []

    def flush() -> None:
        if not words:
            return
        text = " ".join(str(word.word).strip() for word in words).strip()
        start = float(words[0].start)
        end = float(words[-1].end)
        if text and end > start:
            cues.append(CaptionCue(start_sec=start, end_sec=end, text=text))
        words.clear()

    for segment in segments:
        segment_words = list(segment.words or [])
        if not segment_words:
            text = str(segment.text).strip()
            if text and float(segment.end) > float(segment.start):
                cues.append(
                    CaptionCue(
                        start_sec=float(segment.start),
                        end_sec=float(segment.end),
                        text=text,
                    )
                )
            continue
        for word in segment_words:
            words.append(word)
            phrase_duration = float(words[-1].end) - float(words[0].start)
            if len(words) >= 6 or phrase_duration >= 2.5:
                flush()
        flush()
    return cues


def _fallback_cues(script: str, duration: float) -> list[CaptionCue]:
    """Split script into readable phrases, timed by their share of the word count."""
    words = script.split()
    if not words or duration <= 0:
        return []
    groups = [words[index : index + 6] for index in range(0, len(words), 6)]
    usable_duration = max(0.1, duration)
    total_words = len(words)
    elapsed = 0.0
    cues: list[CaptionCue] = []
    for index, group in enumerate(groups):
        start = elapsed
        elapsed += usable_duration * len(group) / total_words
        end = usable_duration if index == len(groups) - 1 else elapsed
        if end > start:
            cues.append(CaptionCue(start_sec=start, end_sec=end, text=" ".join(group)))
    return cues


def _assemble_sync(
    concept: Concept,
    manifest: AssetsManifest,
    output_path: Path,
    whisper_model: str,
) -> tuple[Path, list[CaptionCue]]:
    from moviepy import (
        AudioFileClip,
        ColorClip,
        ImageClip,
        VideoFileClip,
        concatenate_videoclips,
    )
    from moviepy.video.fx import Loop

    if manifest.concept_id != concept.concept_id:
        raise ValueError(
            f"Manifest concept_id {manifest.concept_id!r} does not match "
            f"concept {concept.concept_id!r}"
        )

    beats = sorted(concept.beat_sheet, key=lambda beat: beat.order)
    duration = sum(beat.duration_sec for beat in beats)
    visual_assets = [
        asset for asset in manifest.assets if asset.kind in {AssetKind.VIDEO, AssetKind.IMAGE}
    ]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    opened_clips: list[Any] = []
    beat_clips: list[Any] = []
    final_clip: Any | None = None
    try:
        for index, beat in enumerate(beats):
            if not visual_assets:
                clip = ColorClip(size=(1280, 720), color=(16, 16, 16)).with_duration(
                    beat.duration_sec
                )
                opened_clips.append(clip)
            else:
                asset = visual_assets[index % len(visual_assets)]
                asset_path = _existing_asset_path(asset)
                if asset.kind == AssetKind.IMAGE:
                    clip = ImageClip(str(asset_path)).with_duration(beat.duration_sec)
                    opened_clips.append(clip)
                else:
                    source_clip = VideoFileClip(str(asset_path), audio=False)
                    opened_clips.append(source_clip)
                    if source_clip.duration <= 0:
                        raise ValueError(f"Video asset has no duration: {asset_path}")
                    if source_clip.duration < beat.duration_sec:
                        clip = source_clip.with_effects([Loop(duration=beat.duration_sec)])
                    else:
                        clip = source_clip.subclipped(0, beat.duration_sec)
                    clip = clip.without_audio()
            beat_clips.append(clip)

        final_clip = concatenate_videoclips(beat_clips, method="compose")
        voiceover = _select_voiceover(manifest.assets)
        voiceover_path: Path | None = None
        if voiceover is not None:
            voiceover_path = _existing_asset_path(voiceover)
            audio_clip = AudioFileClip(str(voiceover_path))
            opened_clips.append(audio_clip)
            if audio_clip.duration > duration:
                audio_clip = audio_clip.subclipped(0, duration)
            final_clip = final_clip.with_audio(audio_clip)

        final_clip.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            preset="veryfast",
            threads=2,
            pixel_format="yuv420p",
            logger=None,
        )

        cues: list[CaptionCue] = []
        if voiceover_path is not None:
            try:
                cues = _transcribe_voiceover(voiceover_path, whisper_model)
            except Exception:
                # Model downloads and inference may be unavailable in offline/limited CI.
                cues = []
        if not cues:
            cues = _fallback_cues(concept.full_voiceover, duration)
        return output_path, cues
    finally:
        if final_clip is not None:
            final_clip.close()
        for clip in reversed(opened_clips):
            clip.close()


async def assemble_roughcut(
    concept: Concept,
    manifest: AssetsManifest,
    output_path: Path,
    *,
    whisper_model: str = "base",
) -> tuple[Path, list[CaptionCue]]:
    """Compose the beat sheet and assets, returning the MP4 and timed captions."""
    return await asyncio.to_thread(
        _assemble_sync,
        concept,
        manifest,
        output_path,
        whisper_model,
    )
