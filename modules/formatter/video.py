"""Reels-ready H.264 export with 9:16 framing and burned-in captions."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from modules.common.models import CaptionCue, Concept


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
                f"Style: Caption,DejaVu Sans,{font_size},&H00FFFFFF,&H00FFFFFF,"
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
        has_captions = _write_ass(
            ass_path,
            caption_cues,
            width=width,
            height=height,
            max_length_sec=max_length_sec,
        )
        video_filter = f"{framing_filter},setsar=1"
        if has_captions:
            video_filter += f",subtitles=filename='{ass_path.name}'"

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
