"""Orchestrator unit tests (dry-run + failure isolation helpers)."""

from __future__ import annotations

import pytest

from orchestrator.run_pipeline import run_dry, run_pipeline


@pytest.mark.asyncio
async def test_dry_run_exits_zero() -> None:
    assert await run_dry() == 0


@pytest.mark.asyncio
async def test_run_pipeline_dry_flag() -> None:
    assert await run_pipeline(dry_run=True) == 0
