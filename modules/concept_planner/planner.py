"""Concept planner entrypoint.

Wave 1 (Opus): implement `plan_concepts`.
"""

from __future__ import annotations

from pathlib import Path

from modules.common.models import ConceptsFile, SignalsFile
from modules.common.settings import AppSettings, EnvSecrets


async def plan_concepts(
    signals: SignalsFile,
    settings: AppSettings,
    secrets: EnvSecrets,
    output_path: Path,
) -> ConceptsFile:
    """Turn signals into original creative briefs; write concepts.json."""
    raise NotImplementedError("concept_planner.planner.plan_concepts — Wave 1 Opus")
