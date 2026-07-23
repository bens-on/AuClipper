"""FastAPI review dashboard for AuCl Phase 1 clips."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from modules.common.db import get_clip, init_db, list_clips, set_clip_status, upsert_clip
from modules.common.models import ClipRecord, ClipStatus
from modules.common.settings import ROOT_DIR, ensure_runtime_dirs, load_yaml_settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
REQUIRED_CLIP_FILES = ("final.mp4", "caption.txt", "hashtags.txt")
ALLOWED_STATUSES = frozenset(
    {ClipStatus.APPROVED, ClipStatus.REJECTED, ClipStatus.REGENERATE}
)


def _is_complete_clip_dir(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in REQUIRED_CLIP_FILES)


async def scan_output_clips(output_dir: Path) -> list[ClipRecord]:
    """Scan output/ for concept folders with final.mp4, caption.txt, hashtags.txt."""

    def _scan() -> list[ClipRecord]:
        if not output_dir.exists():
            return []
        records: list[ClipRecord] = []
        for child in sorted(output_dir.iterdir()):
            if not _is_complete_clip_dir(child):
                continue
            records.append(
                ClipRecord(
                    concept_id=child.name,
                    status=ClipStatus.PENDING,
                    final_mp4_path=str(child / "final.mp4"),
                    caption_path=str(child / "caption.txt"),
                    hashtags_path=str(child / "hashtags.txt"),
                    thumbnail_path=(
                        str(child / "thumbnail.jpg")
                        if (child / "thumbnail.jpg").is_file()
                        else None
                    ),
                    roughcut_path=(
                        str(child / "roughcut.mp4")
                        if (child / "roughcut.mp4").is_file()
                        else None
                    ),
                )
            )
        return records

    return await asyncio.to_thread(_scan)


async def sync_filesystem_clips(db_path: Path, output_dir: Path) -> int:
    """Upsert filesystem clips that are not yet in SQLite as pending. Returns count added."""
    discovered = await scan_output_clips(output_dir)
    added = 0
    for record in discovered:
        existing = await get_clip(db_path, record.concept_id)
        if existing is None:
            await upsert_clip(db_path, record)
            added += 1
    return added


async def read_text_file(path: Path, *, max_chars: int = 2000) -> str:
    def _read() -> str:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + "…"
        return text

    return await asyncio.to_thread(_read)


def create_app(
    *,
    db_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    sync_on_startup: bool = True,
) -> FastAPI:
    """Application factory — tests inject temp sqlite + output paths."""

    resolved_db: Path
    resolved_output: Path

    if db_path is None or output_dir is None:
        settings = load_yaml_settings()
        paths = ensure_runtime_dirs(settings, ROOT_DIR)
        resolved_db = Path(db_path) if db_path is not None else Path(paths.sqlite_path)
        resolved_output = (
            Path(output_dir) if output_dir is not None else Path(paths.output_dir)
        )
    else:
        resolved_db = Path(db_path)
        resolved_output = Path(output_dir)

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_db(app.state.db_path)
        if app.state.sync_on_startup:
            await sync_filesystem_clips(app.state.db_path, app.state.output_dir)
        yield

    app = FastAPI(title="AuCl Review Dashboard", lifespan=lifespan)
    app.state.db_path = resolved_db
    app.state.output_dir = resolved_output
    app.state.sync_on_startup = sync_on_startup

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        db_path_state: Path = request.app.state.db_path
        output_dir_state: Path = request.app.state.output_dir

        # Keep DB in sync with any new folders dropped on disk.
        await sync_filesystem_clips(db_path_state, output_dir_state)
        clips = await list_clips(db_path_state)

        rows: list[dict[str, Any]] = []
        for clip in clips:
            caption = await read_text_file(Path(clip.caption_path))
            hashtags = await read_text_file(Path(clip.hashtags_path))
            final_path = Path(clip.final_mp4_path)
            media_url = (
                f"/media/{clip.concept_id}/final.mp4"
                if final_path.is_file()
                else None
            )
            rows.append(
                {
                    "clip": clip,
                    "caption": caption,
                    "hashtags": hashtags,
                    "media_url": media_url,
                }
            )

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "rows": rows,
                "statuses": [s.value for s in ClipStatus],
            },
        )

    @app.post("/clips/{concept_id}/approve")
    async def approve_clip(concept_id: str, request: Request) -> RedirectResponse:
        await _update_status(request, concept_id, ClipStatus.APPROVED)
        return RedirectResponse(url="/", status_code=303)

    @app.post("/clips/{concept_id}/reject")
    async def reject_clip(concept_id: str, request: Request) -> RedirectResponse:
        await _update_status(request, concept_id, ClipStatus.REJECTED)
        return RedirectResponse(url="/", status_code=303)

    @app.post("/clips/{concept_id}/regenerate")
    async def regenerate_clip(concept_id: str, request: Request) -> RedirectResponse:
        await _update_status(request, concept_id, ClipStatus.REGENERATE)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/media/{concept_id}/{filename}")
    async def media(concept_id: str, filename: str, request: Request) -> FileResponse:
        if "/" in concept_id or "\\" in concept_id or ".." in concept_id:
            raise HTTPException(status_code=400, detail="Invalid concept_id")
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        output_dir_state: Path = request.app.state.output_dir
        target = (output_dir_state / concept_id / filename).resolve()
        try:
            target.relative_to(output_dir_state.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Path escape") from exc

        if not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        media_type = "video/mp4" if filename.endswith(".mp4") else "text/plain"
        return FileResponse(target, media_type=media_type)

    async def _update_status(
        request: Request,
        concept_id: str,
        status: ClipStatus,
    ) -> None:
        if status not in ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        db_path_state: Path = request.app.state.db_path
        existing = await get_clip(db_path_state, concept_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Clip not found")
        await set_clip_status(db_path_state, concept_id, status)

    return app


app = create_app()
