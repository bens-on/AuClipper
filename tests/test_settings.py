"""Smoke tests for Wave 0 scaffolding."""

from __future__ import annotations

from pathlib import Path

from modules.common.models import ConceptsFile, SignalsFile
from modules.common.settings import load_yaml_settings


def test_settings_load() -> None:
    settings = load_yaml_settings()
    assert settings.demographic.age_band == "18-24"
    assert settings.pipeline.output_width == 1080
    assert settings.pipeline.output_height == 1920


def test_fixture_signals_schema() -> None:
    path = Path("data/fixtures/signals.json")
    data = SignalsFile.model_validate_json(path.read_text(encoding="utf-8"))
    assert len(data.signals) >= 1
    assert data.signals[0].engagement_score > 0


def test_fixture_concepts_schema() -> None:
    path = Path("data/fixtures/concepts.json")
    data = ConceptsFile.model_validate_json(path.read_text(encoding="utf-8"))
    assert data.concepts[0].concept_id == "smoke_slack_skull"
    assert data.concepts[0].beat_sheet
