"""AuCl pipeline orchestrator — Wave 0 ships --dry-run; Wave 2 wires real stages."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from modules.common.db import init_db
from modules.common.logging_setup import get_logger, setup_logging
from modules.common.settings import ensure_runtime_dirs, load_secrets, load_yaml_settings

logger = get_logger("orchestrator")

STAGES = (
    "trend_signal",
    "concept_planner",
    "asset_generator",
    "assembly_engine",
    "formatter",
)


async def run_dry(settings_path: Path | None = None) -> int:
    settings = load_yaml_settings(settings_path)
    setup_logging(settings.logging)
    paths = ensure_runtime_dirs(settings)
    await init_db(paths.sqlite_path)
    _ = load_secrets()  # validate .env loads without error

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


async def run_smoke(settings_path: Path | None = None) -> int:
    """Placeholder until Wave 2 wiring; fails clearly if modules are incomplete."""
    settings = load_yaml_settings(settings_path)
    setup_logging(settings.logging)
    logger.warning(
        "Smoke path not fully wired yet — complete Wave 1 modules + Wave 2 orchestrator."
    )
    logger.info("Falling back to dry-run stages for now.")
    return await run_dry(settings_path)


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
        return await run_smoke(settings_path)
    logger.error(
        "Full pipeline not wired yet. Use --dry-run or --smoke. (concept_id=%s)",
        concept_id,
    )
    return 2


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
