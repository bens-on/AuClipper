"""Reels-ready H.264 export with 9:16 framing and burned-in captions.

Caption burn-in prefers ffmpeg ``subtitles`` (libass) when available, and falls
back to ``drawtext`` for Homebrew/macOS builds that ship without libass.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from modules.common.models import CaptionCue, Concept

logger = logging.getLogger(__name__)


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Media command failed ({command[0]}): {detail}")


@lru_cache(maxsize=1)
def _ffmpeg_filters() -> set[str]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=False,
    )
    names: set[str] = set()
    for line in (result.stdout or "").splitlines():
        # Typical: " T.C subtitles         V->V       Render text..."
        parts = line.split()
        if len(parts) >= 2 and parts[0][0] in {".", "T", "S", "A", "V"}:
            # flags column then filter name
            for token in parts[1:3]:
                if token.isidentifier() or token.replace("_", "").isalnum():
                    if token not in {"V->V", "A->A", "N->N"}:
                        names.add(token)
                        break
    # Also accept simple containment as a fallback probe
    blob = result.stdout or ""
    for candidate in ("subtitles", "ass", "drawtext"):
        if f" {candidate} " in f" {blob} " or f" {candidate}\t" in blob:
            names.add(candidate)
    return names


def _has_filter(name: str) -> bool:
    filters = _ffmpeg_filters()
    if name in filters:
        return True
    # Cheap secondary probe for oddly formatted filter lists
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", f"filter={name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    out = (result.stdout or "") + (result.stderr or "")
    return "Unknown filter" not in out and result.returncode == 0


def _video_dimensions(path: Path) -> tuple[int, int]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    try:
        stream = json.loads(result.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"No readable video stream in {path}") from exc


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def _ass_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r\n", r"\N")
        .replace("\n", r"\N")
        .strip()
    )


def _write_ass(
    path: Path,
    cues: list[CaptionCue],
    *,
    width: int,
    height: int,
    max_length_sec: float,
) -> bool:
    events: list[str] = []
    for cue in cues:
        start = min(cue.start_sec, max_length_sec)
        end = min(cue.end_sec, max_length_sec)
        text = _ass_text(cue.text)
        if not text or end <= start:
            continue
        events.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{text}"
        )
    if not events:
        return False

    font_size = max(24, round(height * 0.045))
    outline = max(2, round(height * 0.0025))
    margin_v = max(24, round(height * 0.12))
    content = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            (
                "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
                "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,"
                "ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,"
                "MarginR,MarginV,Encoding"
            ),
            (
                f"Style: Caption,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,"
                f"&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,"
                f"70,70,{margin_v},1"
            ),
            "",
            "[Events]",
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
            *events,
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    return True


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter values."""
    cleaned = (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )
    # Keep captions short for readability
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."
    return cleaned


def _drawtext_filters(
    cues: list[CaptionCue],
    *,
    height: int,
    max_length_sec: float,
) -> list[str]:
    font_size = max(28, round(height * 0.045))
    border = max(2, round(height * 0.003))
    y_expr = f"h*{0.82:.2f}"
    filters: list[str] = []
    for cue in cues:
        start = min(cue.start_sec, max_length_sec)
        end = min(cue.end_sec, max_length_sec)
        text = _escape_drawtext(cue.text)
        if not text or end <= start:
            continue
        filters.append(
            "drawtext="
            f"text='{text}':"
            f"fontsize={font_size}:"
            "fontcolor=white:"
            f"borderw={border}:"
            "bordercolor=black:"
            "x=(w-text_w)/2:"
            f"y={y_expr}:"
            f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
        )
    return filters


def _build_video_filter(
    framing_filter: str,
    caption_cues: list[CaptionCue],
    *,
    width: int,
    height: int,
    max_length_sec: float,
    ass_path: Path,
) -> str:
    parts = [framing_filter, "setsar=1"]
    usable = [
        c
        for c in caption_cues
        if c.text.strip() and min(c.end_sec, max_length_sec) > min(c.start_sec, max_length_sec)
    ]
    if not usable:
        return ",".join(parts)

    if _has_filter("subtitles") or _has_filter("ass"):
        if _write_ass(
            ass_path,
            usable,
            width=width,
            height=height,
            max_length_sec=max_length_sec,
        ):
            # Prefer subtitles; fall through to drawtext if somehow unavailable
            filter_name = "subtitles" if _has_filter("subtitles") else "ass"
            parts.append(f"{filter_name}=filename='{ass_path.name}'")
            return ",".join(parts)

    if _has_filter("drawtext"):
        logger.info("ffmpeg subtitles/ass unavailable; using drawtext captions")
        parts.extend(
            _drawtext_filters(usable, height=height, max_length_sec=max_length_sec)
        )
        return ",".join(parts)

    logger.warning(
        "No caption filters available in this ffmpeg build; exporting without burn-in"
    )
    return ",".join(parts)


def _format_sync(
    roughcut_path: Path,
    concept: Concept,
    output_dir: Path,
    caption_cues: list[CaptionCue],
    width: int,
    height: int,
    max_length_sec: float,
) -> dict[str, Path]:
    roughcut_path = Path(roughcut_path).expanduser().resolve()
    if not roughcut_path.is_file():
        raise FileNotFoundError(f"Rough-cut does not exist: {roughcut_path}")
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError("Output width and height must be positive even integers")
    if max_length_sec <= 0:
        raise ValueError("max_length_sec must be greater than zero")
    if not concept.concept_id or Path(concept.concept_id).name != concept.concept_id:
        raise ValueError("concept_id must be a non-empty path-safe name")

    source_width, source_height = _video_dimensions(roughcut_path)
    if source_width / source_height >= width / height:
        framing_filter = (
            f"scale=-2:{height},"
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2"
        )
    else:
        framing_filter = (
            f"scale=-2:{height},"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )

    concept_dir = Path(output_dir).expanduser().resolve() / concept.concept_id
    concept_dir.mkdir(parents=True, exist_ok=True)
    final_path = concept_dir / "final.mp4"
    token = uuid4().hex
    ass_path = concept_dir / f".captions-{token}.ass"
    temp_output = concept_dir / f".final-{token}.mp4"

    try:
        video_filter = _build_video_filter(
            framing_filter,
            caption_cues,
            width=width,
            height=height,
            max_length_sec=max_length_sec,
            ass_path=ass_path,
        )

        try:
            _run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(roughcut_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-t",
                    str(max_length_sec),
                    "-vf",
                    video_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    str(temp_output.name),
                ],
                cwd=concept_dir,
            )
        except RuntimeError as exc:
            # If subtitles path still failed (mis-detected), retry with drawtext/no captions.
            msg = str(exc).lower()
            if "subtitles" in msg or "ass" in msg:
                logger.warning("subtitles filter failed; retrying with drawtext/no-caption")
                fallback_parts = [framing_filter, "setsar=1"]
                if _has_filter("drawtext") and caption_cues:
                    fallback_parts.extend(
                        _drawtext_filters(
                            caption_cues,
                            height=height,
                            max_length_sec=max_length_sec,
                        )
                    )
                _run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(roughcut_path),
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a?",
                        "-t",
                        str(max_length_sec),
                        "-vf",
                        ",".join(fallback_parts),
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "23",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-movflags",
                        "+faststart",
                        str(temp_output.name),
                    ],
                    cwd=concept_dir,
                )
            else:
                raise

        os.replace(temp_output, final_path)
        return {"final_mp4": final_path}
    finally:
        ass_path.unlink(missing_ok=True)
        temp_output.unlink(missing_ok=True)


async def format_for_reels(
    roughcut_path: Path,
    concept: Concept,
    output_dir: Path,
    *,
    caption_cues: list[CaptionCue] | None = None,
    width: int = 1080,
    height: int = 1920,
    max_length_sec: float = 90.0,
) -> dict[str, Path]:
    """Produce a vertical final.mp4, leaving social copy writing to the caller.

    Returns paths dict with at least `final_mp4`.
    """
    return await asyncio.to_thread(
        _format_sync,
        roughcut_path,
        concept,
        output_dir,
        caption_cues or [],
        width,
        height,
        max_length_sec,
    )
