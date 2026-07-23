from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from modules.assembly_engine import compose
from modules.common.models import AssetItem, AssetKind, AssetsManifest, Beat, Concept


def _run_ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
    )


@pytest.mark.asyncio
async def test_assemble_roughcut_from_video_and_voiceover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video_path = tmp_path / "visual.mp4"
    voiceover_path = tmp_path / "voiceover.wav"
    _run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=royalblue:s=320x180:d=2:r=24",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    )
    _run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-c:a",
        "pcm_s16le",
        str(voiceover_path),
    )

    concept = Concept(
        concept_id="tiny-roughcut",
        premise="A tiny media pipeline test.",
        format="AI-voiceover skit",
        beat_sheet=[
            Beat(
                order=0,
                description="Blue test frame",
                duration_sec=2,
                voiceover_line="This is a tiny rough cut.",
            )
        ],
        target_length_sec=2,
        voiceover_script="This is a tiny rough cut.",
    )
    manifest = AssetsManifest(
        concept_id=concept.concept_id,
        strategy="test",
        assets=[
            AssetItem(
                kind=AssetKind.VIDEO,
                path=str(video_path),
                source="test",
                duration_sec=2,
            ),
            AssetItem(
                kind=AssetKind.AUDIO,
                path=str(voiceover_path),
                source="test-voiceover",
                duration_sec=2,
                metadata={"role": "voiceover"},
            ),
        ],
    )
    monkeypatch.setattr(
        compose,
        "_transcribe_voiceover",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline CI")),
    )

    output_path, cues = await compose.assemble_roughcut(
        concept, manifest, tmp_path / "roughcut.mp4"
    )

    assert output_path.is_file()
    assert output_path.stat().st_size > 0
    assert cues
    assert cues[0].text
