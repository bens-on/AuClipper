from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from modules.common.models import Beat, CaptionCue, Concept
from modules.formatter.video import format_for_reels


def _run_ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
    )


def _probe_video(path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["streams"][0]


@pytest.mark.asyncio
async def test_format_for_reels_outputs_vertical_h264_with_captions(tmp_path: Path) -> None:
    roughcut_path = tmp_path / "roughcut.mp4"
    _run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=darkgreen:s=320x180:d=2:r=24",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(roughcut_path),
    )
    concept = Concept(
        concept_id="vertical-export",
        premise="Formatter smoke test",
        format="text-overlay meme",
        beat_sheet=[Beat(order=0, description="Green frame", duration_sec=2)],
        target_length_sec=2,
    )

    paths = await format_for_reels(
        roughcut_path,
        concept,
        tmp_path / "exports",
        caption_cues=[CaptionCue(start_sec=0.1, end_sec=1.8, text="BURNED IN")],
    )

    final_path = paths["final_mp4"]
    assert final_path == tmp_path / "exports" / concept.concept_id / "final.mp4"
    assert final_path.is_file()
    assert _probe_video(final_path) == {
        "codec_name": "h264",
        "width": 1080,
        "height": 1920,
    }
    assert not list(final_path.parent.glob(".captions-*.ass"))
    assert not (final_path.parent / "caption.txt").exists()
    assert not (final_path.parent / "hashtags.txt").exists()
