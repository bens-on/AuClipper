"""Unit tests for concept_planner.planner.plan_concepts.

Live Anthropic calls are never made here: we inject a fake AsyncAnthropic-style
client (mock) or use the explicit offline fixture-mapper.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.common.models import ConceptsFile, SignalsFile
from modules.common.settings import EnvSecrets, load_yaml_settings
from modules.concept_planner.planner import plan_concepts

FIXTURE_SIGNALS = Path("data/fixtures/signals.json")


def _load_signals() -> SignalsFile:
    return SignalsFile.model_validate_json(
        FIXTURE_SIGNALS.read_text(encoding="utf-8")
    )


def _valid_concepts_json() -> str:
    return json.dumps(
        {
            "concepts": [
                {
                    "concept_id": "orig_slack_reactions",
                    "premise": (
                        "Original skit: a worker over-analyzes every emoji "
                        "reaction their manager leaves."
                    ),
                    "format": "AI-voiceover skit",
                    "target_length_sec": 20,
                    "asset_strategy": "tts+stock",
                    "stock_keywords": ["office", "laptop", "typing"],
                    "voiceover_script": "The thumbs up meant nothing. Or everything.",
                    "beat_sheet": [
                        {
                            "order": 0,
                            "description": "Cold open on a laptop",
                            "duration_sec": 6,
                            "visual_cue": "close-up typing",
                            "voiceover_line": "The thumbs up meant nothing.",
                            "on_screen_text": "9:01 AM",
                        },
                        {
                            "order": 1,
                            "description": "Spiral",
                            "duration_sec": 14,
                            "visual_cue": "confused reaction",
                            "voiceover_line": "Or everything.",
                            "on_screen_text": "or everything",
                        },
                    ],
                }
            ]
        }
    )


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessages:
    """Async .create() that returns queued responses in order."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        text = self._responses.pop(0)
        return SimpleNamespace(content=[_FakeBlock(text)])


class _FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.messages = _FakeMessages(responses)


@pytest.fixture
def settings():
    return load_yaml_settings()


@pytest.fixture
def secrets_no_key():
    return EnvSecrets(anthropic_api_key=None)


async def test_mocked_client_produces_valid_concepts(
    tmp_path, settings, secrets_no_key
):
    signals = _load_signals()
    out = tmp_path / "concepts.json"
    client = _FakeClient([_valid_concepts_json()])

    result = await plan_concepts(
        signals, settings, secrets_no_key, out, client=client
    )

    assert isinstance(result, ConceptsFile)
    assert result.concepts[0].concept_id == "orig_slack_reactions"
    assert out.exists()

    on_disk = ConceptsFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert on_disk.concepts[0].beat_sheet

    call = client.messages.calls[0]
    assert "ORIGINAL" in call["system"]
    assert "NEVER" in call["system"]


async def test_prompt_forbids_copying_specific_videos(
    tmp_path, settings, secrets_no_key
):
    signals = _load_signals()
    client = _FakeClient([_valid_concepts_json()])
    await plan_concepts(
        signals, settings, secrets_no_key, tmp_path / "c.json", client=client
    )
    system = client.messages.calls[0]["system"]
    lowered = system.lower()
    assert "reconstruct" in lowered
    assert "viral video" in lowered
    assert "original" in lowered


async def test_retry_once_on_invalid_json(tmp_path, settings, secrets_no_key):
    signals = _load_signals()
    out = tmp_path / "concepts.json"
    # First response is broken JSON, second is valid -> exactly one retry.
    client = _FakeClient(["this is not json {oops", _valid_concepts_json()])

    result = await plan_concepts(
        signals, settings, secrets_no_key, out, client=client
    )

    assert isinstance(result, ConceptsFile)
    assert len(client.messages.calls) == 2
    # The retry message must contain a corrective instruction.
    retry_messages = client.messages.calls[1]["messages"]
    assert any("valid JSON" in m["content"] for m in retry_messages)


async def test_retry_exhausted_raises(tmp_path, settings, secrets_no_key):
    signals = _load_signals()
    client = _FakeClient(["still not json", "also not json"])
    with pytest.raises(json.JSONDecodeError):
        await plan_concepts(
            signals, settings, secrets_no_key, tmp_path / "c.json", client=client
        )
    assert len(client.messages.calls) == 2


async def test_live_mode_without_key_raises(tmp_path, settings, secrets_no_key):
    signals = _load_signals()
    with pytest.raises(RuntimeError, match="Anthropic API key"):
        await plan_concepts(
            signals, settings, secrets_no_key, tmp_path / "c.json"
        )


async def test_offline_fixture_mapper(tmp_path, settings, secrets_no_key):
    signals = _load_signals()
    out = tmp_path / "concepts.json"

    result = await plan_concepts(
        signals, settings, secrets_no_key, out, offline=True
    )

    assert isinstance(result, ConceptsFile)
    assert 1 <= len(result.concepts) <= settings.pipeline.max_concepts
    for concept in result.concepts:
        assert concept.beat_sheet
        assert 0 < concept.target_length_sec <= 90
    assert out.exists()
    ConceptsFile.model_validate_json(out.read_text(encoding="utf-8"))


async def test_offline_handles_markdown_fenced_json_via_parse(
    tmp_path, settings, secrets_no_key
):
    signals = _load_signals()
    fenced = "```json\n" + _valid_concepts_json() + "\n```"
    client = _FakeClient([fenced])
    result = await plan_concepts(
        signals, settings, secrets_no_key, tmp_path / "c.json", client=client
    )
    assert result.concepts[0].concept_id == "orig_slack_reactions"
