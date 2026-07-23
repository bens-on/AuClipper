"""AuCl pipeline orchestrator — sequential DAG with per-concept failure isolation."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import aiofiles

from modules.assembly_engine.compose import assemble_roughcut
from modules.asset_generator.generate import generate_assets
from modules.common.db import init_db, upsert_clip
from modules.common.logging_setup import get_logger, setup_logging
from modules.common.models import ClipRecord, ClipStatus, Concept, ConceptsFile, SignalsFile
from modules.common.settings import (
    AppSettings,
    EnvSecrets,
    ensure_runtime_dirs,
    load_secrets,
    load_yaml_settings,
)
from modules.concept_planner.planner import plan_concepts
from modules.formatter.copy import generate_caption_and_hashtags
from modules.formatter.video import format_for_reels
from modules.trend_signal.collector import collect_signals

logger = get_logger("orchestrator")

STAGES = (
    "trend_signal",
    "concept_planner",
    "asset_generator",
    "assembly_engine",
    "formatter",
)


@dataclass
class ConceptResult:
    concept_id: str
    ok: bool
    error: str | None = None
    output_dir: str | None = None


@dataclass
class RunSummary:
    mode: str
    started_at: str
    finished_at: str | None = None
    signals_path: str | None = None
    concepts_path: str | None = None
    results: list[ConceptResult] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)


async def run_dry(settings_path: Path | None = None) -> int:
    settings = load_yaml_settings(settings_path)
    setup_logging(settings.logging)
    paths = ensure_runtime_dirs(settings)
    await init_db(paths.sqlite_path)
    _ = load_secrets()

    logger.info("AuCl dry-run starting (demographic age_band=%s)", settings.demographic.age_band)
    for stage in STAGES:
        logger.info("stage=%s status=noop", stage)
    logger.info(
        "dry-run complete | signals_dir=%s concepts_dir=%s assets_dir=%s output_dir=%s",
        paths.signals_dir,
        paths.concepts_dir,
        paths.assets_dir,
        paths.output_dir,
    )
    return 0


async def _process_concept(
    concept: Concept,
    *,
    settings: AppSettings,
    secrets: EnvSecrets,
    assets_root: Path,
    output_root: Path,
    sqlite_path: Path,
    smoke: bool,
) -> ConceptResult:
    concept_id = concept.concept_id
    try:
        logger.info("concept=%s stage=asset_generator", concept_id)
        manifest = await generate_assets(
            concept,
            settings,
            secrets,
            Path(assets_root),
            smoke=smoke,
        )

        roughcut_dir = Path(assets_root) / concept_id
        roughcut_path = roughcut_dir / "roughcut.mp4"
        logger.info("concept=%s stage=assembly_engine", concept_id)
        roughcut_path, cues = await assemble_roughcut(
            concept,
            manifest,
            roughcut_path,
            whisper_model=settings.pipeline.whisper_model,
        )

        logger.info("concept=%s stage=formatter.video", concept_id)
        # format_for_reels writes to output_root/{concept_id}/final.mp4
        paths = await format_for_reels(
            roughcut_path,
            concept,
            Path(output_root),
            caption_cues=cues,
            width=settings.pipeline.output_width,
            height=settings.pipeline.output_height,
            max_length_sec=float(settings.pipeline.max_length_sec),
        )
        final_mp4 = Path(paths["final_mp4"])
        concept_out = final_mp4.parent

        logger.info("concept=%s stage=formatter.copy", concept_id)
        caption, hashtags = await generate_caption_and_hashtags(
            concept,
            api_key=None if smoke else secrets.anthropic_api_key,
            model=settings.llm.model,
        )
        caption_path = concept_out / "caption.txt"
        hashtags_path = concept_out / "hashtags.txt"
        async with aiofiles.open(caption_path, "w", encoding="utf-8") as fh:
            await fh.write(caption.strip() + "\n")
        async with aiofiles.open(hashtags_path, "w", encoding="utf-8") as fh:
            await fh.write(hashtags.strip() + "\n")

        # Keep roughcut alongside finals for dashboard optional preview
        dest_rough = concept_out / "roughcut.mp4"
        if roughcut_path.is_file() and roughcut_path.resolve() != dest_rough.resolve():
            await asyncio.to_thread(dest_rough.write_bytes, roughcut_path.read_bytes())

        await upsert_clip(
            sqlite_path,
            ClipRecord(
                concept_id=concept_id,
                status=ClipStatus.PENDING,
                final_mp4_path=str(final_mp4),
                caption_path=str(caption_path),
                hashtags_path=str(hashtags_path),
                roughcut_path=str(dest_rough) if dest_rough.is_file() else str(roughcut_path),
            ),
        )
        logger.info("concept=%s status=ok output=%s", concept_id, concept_out)
        return ConceptResult(concept_id=concept_id, ok=True, output_dir=str(concept_out))
    except Exception as exc:  # noqa: BLE001 — isolate per-concept failures
        logger.exception("concept=%s status=failed error=%s", concept_id, exc)
        return ConceptResult(concept_id=concept_id, ok=False, error=str(exc))


async def run_full(
    *,
    smoke: bool = False,
    concept_id: str | None = None,
    settings_path: Path | None = None,
) -> int:
    settings = load_yaml_settings(settings_path)
    setup_logging(settings.logging)
    paths = ensure_runtime_dirs(settings)
    # Refresh settings.paths to absolute resolved paths for downstream modules
    settings = settings.model_copy(update={"paths": paths})
    await init_db(paths.sqlite_path)
    secrets = load_secrets()

    summary = RunSummary(
        mode="smoke" if smoke else "live",
        started_at=datetime.now(UTC).isoformat(),
    )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    signals_path = Path(paths.signals_dir) / f"signals_{stamp}.json"
    concepts_path = Path(paths.concepts_dir) / f"concepts_{stamp}.json"

    logger.info("AuCl pipeline starting mode=%s", summary.mode)

    logger.info("stage=trend_signal")
    signals: SignalsFile = await collect_signals(
        settings,
        secrets,
        signals_path,
        use_fixtures=smoke,
        allow_offline=smoke,
    )
    summary.signals_path = str(signals_path)
    logger.info("stage=trend_signal done count=%d", len(signals.signals))

    logger.info("stage=concept_planner")
    if smoke:
        # Deterministic smoke: load fixture concepts (planner offline remains available
        # for unit tests / key-free concept generation without fixtures).
        fixture_concepts = Path(paths.fixtures_dir) / "concepts.json"
        if fixture_concepts.is_file():
            async with aiofiles.open(fixture_concepts, "r", encoding="utf-8") as fh:
                raw = await fh.read()
            concepts = ConceptsFile.model_validate_json(raw)
            async with aiofiles.open(concepts_path, "w", encoding="utf-8") as fh:
                await fh.write(concepts.model_dump_json(indent=2) + "\n")
            logger.info("stage=concept_planner source=fixture path=%s", fixture_concepts)
        else:
            concepts = await plan_concepts(
                signals,
                settings,
                secrets,
                concepts_path,
                offline=True,
            )
    else:
        concepts = await plan_concepts(
            signals,
            settings,
            secrets,
            concepts_path,
            offline=False,
        )
    summary.concepts_path = str(concepts_path)
    logger.info("stage=concept_planner done count=%d", len(concepts.concepts))

    selected = concepts.concepts
    if concept_id:
        selected = [c for c in selected if c.concept_id == concept_id]
        if not selected:
            logger.error("No concept matched concept_id=%s", concept_id)
            return 1
    else:
        selected = selected[: settings.pipeline.max_concepts]

    for concept in selected:
        result = await _process_concept(
            concept,
            settings=settings,
            secrets=secrets,
            assets_root=Path(paths.assets_dir),
            output_root=Path(paths.output_dir),
            sqlite_path=Path(paths.sqlite_path),
            smoke=smoke,
        )
        summary.results.append(result)

    summary.finished_at = datetime.now(UTC).isoformat()
    logger.info(
        "pipeline complete mode=%s succeeded=%d failed=%d signals=%s concepts=%s",
        summary.mode,
        summary.succeeded,
        summary.failed,
        summary.signals_path,
        summary.concepts_path,
    )
    for r in summary.results:
        if r.ok:
            logger.info("result concept=%s ok output=%s", r.concept_id, r.output_dir)
        else:
            logger.error("result concept=%s failed error=%s", r.concept_id, r.error)

    if not summary.results:
        return 1
    # Partial success is still exit 0 so batch jobs don't look fully broken
    return 0 if summary.succeeded > 0 else 1


async def run_pipeline(
    *,
    dry_run: bool = False,
    smoke: bool = False,
    concept_id: str | None = None,
    settings_path: Path | None = None,
) -> int:
    if dry_run:
        return await run_dry(settings_path)
    if smoke:
        return await run_full(smoke=True, concept_id=concept_id, settings_path=settings_path)
    return await run_full(smoke=False, concept_id=concept_id, settings_path=settings_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AuCl Auto-Clipper pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Log each stage as a no-op")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Key-free end-to-end run using fixtures + local TTS/ffmpeg",
    )
    parser.add_argument("--concept-id", type=str, default=None, help="Run a single concept")
    parser.add_argument(
        "--settings",
        type=Path,
        default=None,
        help="Optional path to settings.yaml",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(
            run_pipeline(
                dry_run=args.dry_run,
                smoke=args.smoke,
                concept_id=args.concept_id,
                settings_path=args.settings,
            )
        )
    except KeyboardInterrupt:
        logging.getLogger("orchestrator").warning("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
