"""Fixtures and helpers for dashboard tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dashboard.app import create_app, sync_filesystem_clips
from modules.common.db import init_db


def seed_fake_clip(
    output_dir: Path,
    concept_id: str = "test_clip",
    *,
    caption: str = "Test caption for review",
    hashtags: str = "#test #reels #aucl",
) -> Path:
    """Create a minimal concept folder under output/ for listing tests.

    Writes a tiny placeholder final.mp4 (not a real video) plus caption/hashtags.
    """
    clip_dir = output_dir / concept_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    (clip_dir / "final.mp4").write_bytes(b"fake-mp4-bytes")
    (clip_dir / "caption.txt").write_text(caption, encoding="utf-8")
    (clip_dir / "hashtags.txt").write_text(hashtags, encoding="utf-8")
    return clip_dir


@pytest.fixture
def temp_paths(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    db_path = tmp_path / "review.db"
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    yield db_path, output_dir


@pytest.fixture
def seeded_output(temp_paths: tuple[Path, Path]) -> Path:
    _, output_dir = temp_paths
    seed_fake_clip(output_dir, "smoke_concept")
    return output_dir


@pytest.fixture
async def client(
    temp_paths: tuple[Path, Path],
    seeded_output: Path,
) -> AsyncIterator[AsyncClient]:
    db_path, output_dir = temp_paths
    assert seeded_output == output_dir
    app = create_app(db_path=db_path, output_dir=output_dir, sync_on_startup=True)
    # httpx ASGITransport in this env does not run lifespan; bootstrap explicitly.
    await init_db(db_path)
    await sync_filesystem_clips(db_path, output_dir)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
