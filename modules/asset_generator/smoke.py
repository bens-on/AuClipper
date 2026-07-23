"""Offline/smoke media helpers via ffmpeg (and optional Piper).

Smoke path must work without network:
- Video: ffmpeg ``testsrc`` / ``color`` source (~5–10s) as stock stand-in
- Audio: Piper TTS when a local voice model is available; otherwise ffmpeg
  ``sine`` tone or a tiny silent WAV placeholder

Piper voice model path
----------------------
Configured via ``settings.voice.piper_voice`` (default ``en_US-lessac-medium``).
Looked up under (first hit wins):

1. ``$PIPER_DATA_DIR`` / ``$PIPER_VOICE_DIR``
2. ``data/voices/``
3. ``~/.local/share/piper/``
4. CWD

Download with::

    python -m piper.download_voices en_US-lessac-medium --download-dir data/voices

If the ``.onnx`` (+ ``.onnx.json``) pair is missing, smoke falls back to ffmpeg
audio so tests stay key-free and offline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import struct
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SMOKE_DURATION_SEC = 6.0


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


async def _run(cmd: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{err.decode(errors='replace')}"
        )


async def generate_smoke_video(
    dest: Path,
    *,
    duration_sec: float = DEFAULT_SMOKE_DURATION_SEC,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Generate a short silent color/testsrc MP4 via ffmpeg."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Prefer testsrc2; fall back to solid color if filter unavailable.
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate=30",
        "-t",
        str(duration_sec),
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(dest),
    ]
    try:
        await _run(cmd)
    except RuntimeError:
        cmd[5] = f"color=c=0x1a1a2e:s={width}x{height}:d={duration_sec}"
        # color source already has duration; drop -t duplication issues by keeping -t
        await _run(cmd)
    return dest


def resolve_piper_model(voice_name: str) -> Path | None:
    """Return path to ``{voice}.onnx`` if found locally; else None."""
    candidates: list[Path] = []
    for env_key in ("PIPER_DATA_DIR", "PIPER_VOICE_DIR"):
        raw = os.environ.get(env_key)
        if raw:
            candidates.append(Path(raw))
    candidates.extend(
        [
            Path("data/voices"),
            Path.home() / ".local" / "share" / "piper",
            Path.cwd(),
        ]
    )
    filename = f"{voice_name}.onnx"
    for base in candidates:
        path = base / filename
        if path.is_file():
            return path
        nested = base / voice_name / filename
        if nested.is_file():
            return nested
    return None


async def generate_piper_wav(
    text: str,
    dest: Path,
    *,
    voice_name: str,
) -> Path | None:
    """Synthesize WAV with Piper if the voice model is present. Returns None if unavailable."""
    model = resolve_piper_model(voice_name)
    if model is None:
        logger.info(
            "Piper voice model %s not found locally — skipping Piper "
            "(download: python -m piper.download_voices %s --download-dir data/voices)",
            voice_name,
            voice_name,
        )
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    piper_bin = shutil.which("piper")
    if piper_bin:
        proc = await asyncio.create_subprocess_exec(
            piper_bin,
            "--model",
            str(model),
            "--output_file",
            str(dest),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await proc.communicate(input=text.encode("utf-8"))
        if proc.returncode == 0 and dest.is_file():
            return dest
        logger.warning("piper CLI failed: %s", err.decode(errors="replace"))

    # Python API fallback
    try:
        await asyncio.to_thread(_piper_python_synthesize, text, model, dest)
        if dest.is_file():
            return dest
    except Exception as exc:
        logger.warning("piper Python API failed: %s", exc)
    return None


def _piper_python_synthesize(text: str, model: Path, dest: Path) -> None:
    from piper import PiperVoice

    voice = PiperVoice.load(str(model))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)


async def generate_placeholder_audio(
    dest: Path,
    *,
    duration_sec: float = DEFAULT_SMOKE_DURATION_SEC,
) -> Path:
    """ffmpeg sine tone, or a tiny silent WAV if ffmpeg audio encode fails."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix.lower() not in {".wav", ".mp3"}:
        dest = dest.with_suffix(".wav")

    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration_sec}",
        "-ac",
        "1",
        str(dest),
    ]
    try:
        await _run(cmd)
        return dest
    except RuntimeError:
        return _write_silent_wav(dest.with_suffix(".wav"), duration_sec=min(duration_sec, 2.0))


def _write_silent_wav(dest: Path, *, duration_sec: float = 1.0, rate: int = 16000) -> Path:
    n_frames = int(rate * duration_sec)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        silence = struct.pack("<h", 0)
        wf.writeframes(silence * n_frames)
    return dest


async def generate_smoke_voiceover(
    text: str,
    dest: Path,
    *,
    voice_name: str,
    duration_sec: float = DEFAULT_SMOKE_DURATION_SEC,
) -> tuple[Path, str]:
    """Prefer Piper; fall back to ffmpeg sine / silent WAV.

    Returns ``(path, source_label)``.
    """
    piper_dest = dest if dest.suffix.lower() == ".wav" else dest.with_suffix(".wav")
    piped = await generate_piper_wav(text or "Smoke test voiceover.", piper_dest, voice_name=voice_name)
    if piped is not None:
        return piped, "piper"
    fallback = await generate_placeholder_audio(dest, duration_sec=duration_sec)
    return fallback, "ffmpeg-sine-placeholder"
