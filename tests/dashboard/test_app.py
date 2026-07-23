"""Dashboard review UI tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dashboard.app import create_app, scan_output_clips
from modules.common.db import get_clip, init_db
from modules.common.models import ClipStatus
from tests.dashboard.conftest import seed_fake_clip


@pytest.mark.asyncio
async def test_index_loads_and_lists_seeded_clip(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "AuCl Review Dashboard" in body
    assert "smoke_concept" in body
    assert "Test caption for review" in body
    assert "#test #reels #aucl" in body
    assert 'src="/media/smoke_concept/final.mp4"' in body


@pytest.mark.asyncio
async def test_approve_updates_status(client: AsyncClient, temp_paths: tuple[Path, Path]) -> None:
    db_path, _ = temp_paths
    response = await client.post("/clips/smoke_concept/approve", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    record = await get_clip(db_path, "smoke_concept")
    assert record is not None
    assert record.status == ClipStatus.APPROVED

    page = await client.get("/")
    assert "approved" in page.text


@pytest.mark.asyncio
async def test_reject_and_regenerate_status(
    client: AsyncClient,
    temp_paths: tuple[Path, Path],
) -> None:
    db_path, _ = temp_paths

    reject = await client.post("/clips/smoke_concept/reject", follow_redirects=False)
    assert reject.status_code == 303
    record = await get_clip(db_path, "smoke_concept")
    assert record is not None
    assert record.status == ClipStatus.REJECTED

    regen = await client.post("/clips/smoke_concept/regenerate", follow_redirects=False)
    assert regen.status_code == 303
    record = await get_clip(db_path, "smoke_concept")
    assert record is not None
    assert record.status == ClipStatus.REGENERATE


@pytest.mark.asyncio
async def test_media_serves_final_mp4(client: AsyncClient) -> None:
    response = await client.get("/media/smoke_concept/final.mp4")
    assert response.status_code == 200
    assert response.content == b"fake-mp4-bytes"
    assert "video/mp4" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_status_unknown_clip_404(client: AsyncClient) -> None:
    response = await client.post("/clips/missing_id/approve", follow_redirects=False)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_scan_and_sync_on_startup(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.db"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    seed_fake_clip(output_dir, "fs_only_clip", caption="From disk")

    scanned = await scan_output_clips(output_dir)
    assert len(scanned) == 1
    assert scanned[0].concept_id == "fs_only_clip"

    app = create_app(db_path=db_path, output_dir=output_dir, sync_on_startup=True)
    await init_db(db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        page = await ac.get("/")
        assert page.status_code == 200
        assert "fs_only_clip" in page.text
        assert "From disk" in page.text

    record = await get_clip(db_path, "fs_only_clip")
    assert record is not None
    assert record.status == ClipStatus.PENDING


@pytest.mark.asyncio
async def test_incomplete_folder_not_synced(tmp_path: Path) -> None:
    db_path = tmp_path / "incomplete.db"
    output_dir = tmp_path / "output"
    bad = output_dir / "incomplete"
    bad.mkdir(parents=True)
    (bad / "final.mp4").write_bytes(b"x")
    # missing caption.txt / hashtags.txt

    await init_db(db_path)
    scanned = await scan_output_clips(output_dir)
    assert scanned == []
