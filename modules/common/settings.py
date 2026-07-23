"""Load and validate config/settings.yaml + environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = ROOT_DIR / "config" / "settings.yaml"


class DemographicConfig(BaseModel):
    age_band: str
    region: str | None = None
    interests: list[str] = Field(default_factory=list)


class PipelineConfig(BaseModel):
    max_concepts: int = 3
    max_signal_topics: int = 10
    default_target_length_sec: int = 30
    max_length_sec: int = 90
    output_width: int = 1080
    output_height: int = 1920
    whisper_model: str = "base"
    voice_provider: str = "piper"
    stock_providers: list[str] = Field(default_factory=lambda: ["pexels", "pixabay"])
    ai_video_provider: str | None = None


class PathsConfig(BaseModel):
    signals_dir: str = "data/signals"
    concepts_dir: str = "data/concepts"
    assets_dir: str = "data/assets"
    fixtures_dir: str = "data/fixtures"
    output_dir: str = "output"
    sqlite_path: str = "data/aucl.db"

    def resolve(self, root: Path | None = None) -> PathsConfig:
        base = root or ROOT_DIR
        return PathsConfig(
            signals_dir=str((base / self.signals_dir).resolve()),
            concepts_dir=str((base / self.concepts_dir).resolve()),
            assets_dir=str((base / self.assets_dir).resolve()),
            fixtures_dir=str((base / self.fixtures_dir).resolve()),
            output_dir=str((base / self.output_dir).resolve()),
            sqlite_path=str((base / self.sqlite_path).resolve()),
        )


class RedditConfig(BaseModel):
    subreddits: list[str] = Field(default_factory=list)


class YouTubeConfig(BaseModel):
    region_code: str = "US"
    category_id: str = "23"
    max_results: int = 25


class VoiceConfig(BaseModel):
    elevenlabs_model: str = "eleven_monolingual_v1"
    piper_voice: str = "en_US-lessac-medium"


class LLMConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.8


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


class AppSettings(BaseModel):
    demographic: DemographicConfig
    topic_seeds: list[str] = Field(default_factory=list)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    reddit: RedditConfig = Field(default_factory=RedditConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class EnvSecrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    youtube_api_key: str | None = None
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str = "AuCl/0.1"
    anthropic_api_key: str | None = None
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    runway_api_key: str | None = None
    pika_api_key: str | None = None
    luma_api_key: str | None = None


def load_yaml_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or DEFAULT_SETTINGS_PATH
    with settings_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    return AppSettings.model_validate(raw)


def load_secrets() -> EnvSecrets:
    # Ensure .env is discoverable from project root
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        os.environ.setdefault("DOTENV_PATH", str(env_path))
    return EnvSecrets(_env_file=str(env_path) if env_path.exists() else None)


def ensure_runtime_dirs(settings: AppSettings, root: Path | None = None) -> PathsConfig:
    paths = settings.paths.resolve(root)
    for key in (
        "signals_dir",
        "concepts_dir",
        "assets_dir",
        "fixtures_dir",
        "output_dir",
    ):
        Path(getattr(paths, key)).mkdir(parents=True, exist_ok=True)
    Path(paths.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    return paths
