"""SQLite persistence for clip review status (async)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from modules.common.models import ClipRecord, ClipStatus

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clips (
    concept_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    final_mp4_path TEXT NOT NULL,
    caption_path TEXT NOT NULL,
    hashtags_path TEXT NOT NULL,
    thumbnail_path TEXT,
    roughcut_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


async def init_db(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute(SCHEMA_SQL)
        await db.commit()


async def upsert_clip(db_path: str | Path, record: ClipRecord) -> None:
    now = _utcnow()
    created = record.created_at or now
    updated = now
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO clips (
                concept_id, status, final_mp4_path, caption_path, hashtags_path,
                thumbnail_path, roughcut_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(concept_id) DO UPDATE SET
                status=excluded.status,
                final_mp4_path=excluded.final_mp4_path,
                caption_path=excluded.caption_path,
                hashtags_path=excluded.hashtags_path,
                thumbnail_path=excluded.thumbnail_path,
                roughcut_path=excluded.roughcut_path,
                updated_at=excluded.updated_at
            """,
            (
                record.concept_id,
                record.status.value if isinstance(record.status, ClipStatus) else record.status,
                record.final_mp4_path,
                record.caption_path,
                record.hashtags_path,
                record.thumbnail_path,
                record.roughcut_path,
                created,
                updated,
            ),
        )
        await db.commit()


async def set_clip_status(
    db_path: str | Path,
    concept_id: str,
    status: ClipStatus | str,
) -> None:
    status_value = status.value if isinstance(status, ClipStatus) else status
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE clips SET status = ?, updated_at = ? WHERE concept_id = ?",
            (status_value, _utcnow(), concept_id),
        )
        await db.commit()


async def list_clips(db_path: str | Path) -> list[ClipRecord]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clips ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
    return [ClipRecord.model_validate(dict(row)) for row in rows]


async def get_clip(db_path: str | Path, concept_id: str) -> ClipRecord | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clips WHERE concept_id = ?",
            (concept_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return ClipRecord.model_validate(dict(row))
