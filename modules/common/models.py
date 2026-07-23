"""Shared Pydantic V2 data contracts for the AuCl pipeline."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class FormatType(StrEnum):
    TEXT_OVERLAY_MEME = "text-overlay meme"
    AI_VOICEOVER_SKIT = "AI-voiceover skit"
    STOCK_PUNCHLINE = "stock-footage + punchline"
    OTHER = "other"


class SignalSource(StrEnum):
    YOUTUBE = "youtube"
    REDDIT = "reddit"
    GOOGLE_TRENDS = "google_trends"
    AI_WEB = "ai_web"
    FIXTURE = "fixture"


class TrendSignal(BaseModel):
    topic: str
    format_type: FormatType | str
    engagement_score: float = Field(ge=0.0)
    source: SignalSource | str
    sample_titles: list[str] = Field(default_factory=list)

    @field_validator("sample_titles")
    @classmethod
    def _non_empty_titles_trimmed(cls, value: list[str]) -> list[str]:
        return [t.strip() for t in value if t and t.strip()]


class SignalsFile(BaseModel):
    signals: list[TrendSignal]
    generated_at: str | None = None
    demographic: dict[str, Any] | None = None


class Beat(BaseModel):
    order: int = Field(ge=0)
    description: str
    duration_sec: float = Field(gt=0.0)
    visual_cue: str | None = None
    voiceover_line: str | None = None
    on_screen_text: str | None = None


class ConceptFormat(StrEnum):
    TEXT_OVERLAY_MEME = "text-overlay meme"
    AI_VOICEOVER_SKIT = "AI-voiceover skit"
    STOCK_PUNCHLINE = "stock-footage + punchline"


class Concept(BaseModel):
    concept_id: str
    premise: str
    format: ConceptFormat | str
    beat_sheet: list[Beat]
    target_length_sec: float = Field(gt=0.0, le=90.0)
    asset_strategy: str = "tts+stock"
    stock_keywords: list[str] = Field(default_factory=list)
    voiceover_script: str | None = None

    @model_validator(mode="after")
    def _beats_cover_length(self) -> Concept:
        if not self.beat_sheet:
            raise ValueError("beat_sheet must contain at least one beat")
        return self

    @property
    def full_voiceover(self) -> str:
        if self.voiceover_script:
            return self.voiceover_script
        lines = [b.voiceover_line for b in self.beat_sheet if b.voiceover_line]
        return " ".join(lines)


class ConceptsFile(BaseModel):
    concepts: list[Concept]
    generated_at: str | None = None
    source_signals_path: str | None = None


class AssetKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class AssetItem(BaseModel):
    kind: AssetKind
    path: str
    source: str
    license: str | None = None
    duration_sec: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetsManifest(BaseModel):
    concept_id: str
    assets: list[AssetItem]
    strategy: str
    generated_at: str | None = None


class CaptionCue(BaseModel):
    start_sec: float = Field(ge=0.0)
    end_sec: float = Field(gt=0.0)
    text: str

    @model_validator(mode="after")
    def _end_after_start(self) -> CaptionCue:
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        return self


class ClipStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REGENERATE = "regenerate"


class ClipRecord(BaseModel):
    concept_id: str
    status: ClipStatus = ClipStatus.PENDING
    final_mp4_path: str
    caption_path: str
    hashtags_path: str
    thumbnail_path: str | None = None
    roughcut_path: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
