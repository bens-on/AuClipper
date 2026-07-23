"""Unit tests for formatter.copy.generate_caption_and_hashtags.

Covers the Claude path (mocked client) and the key-free heuristic fallback.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from modules.common.models import Beat, Concept
from modules.formatter.copy import generate_caption_and_hashtags


def _concept(**overrides) -> Concept:
    base = dict(
        concept_id="orig_gym_mirror",
        premise=(
            "Original stock-footage + punchline: someone narrates their doomed "
            "gym mirror selfie attempts."
        ),
        format="stock-footage + punchline",
        target_length_sec=18,
        asset_strategy="tts+stock",
        stock_keywords=["gym", "mirror", "workout"],
        voiceover_script="leg day was a mistake",
        beat_sheet=[
            Beat(
                order=0,
                description="setup",
                duration_sec=6,
                visual_cue="gym b-roll",
                voiceover_line="leg day was a mistake",
                on_screen_text="leg day regret",
            ),
            Beat(
                order=1,
                description="punchline",
                duration_sec=12,
                visual_cue="mirror b-roll",
                on_screen_text="who recorded this",
            ),
        ],
    )
    base.update(overrides)
    return Concept(**base)


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    async def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return SimpleNamespace(content=[_FakeBlock(self._text)])


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.messages = _FakeMessages(text)


async def test_llm_path_with_mocked_client():
    concept = _concept()
    payload = json.dumps(
        {
            "caption": "pov: leg day filmed you back",
            "hashtags": ["gymtok", "legday", "fyp", "gym", "relatable"],
        }
    )
    client = _FakeClient(payload)

    caption, hashtags = await generate_caption_and_hashtags(
        concept, client=client, model="claude-test"
    )

    assert caption == "pov: leg day filmed you back"
    assert hashtags == "#gymtok #legday #fyp #gym #relatable"
    assert client.messages.calls[0]["model"] == "claude-test"
    assert "18-24" in client.messages.calls[0]["system"]


async def test_llm_path_strips_markdown_fences():
    concept = _concept()
    payload = "```json\n" + json.dumps(
        {"caption": "chaos", "hashtags": ["one", "two"]}
    ) + "\n```"
    client = _FakeClient(payload)

    caption, hashtags = await generate_caption_and_hashtags(concept, client=client)

    assert caption == "chaos"
    assert hashtags == "#one #two"


async def test_llm_bad_json_falls_back_to_heuristic():
    concept = _concept()
    client = _FakeClient("totally not json")

    caption, hashtags = await generate_caption_and_hashtags(concept, client=client)

    assert caption  # non-empty heuristic caption
    assert hashtags.startswith("#")


async def test_fallback_path_without_key():
    concept = _concept()

    caption, hashtags = await generate_caption_and_hashtags(concept)

    assert isinstance(caption, str) and caption.strip()
    assert isinstance(hashtags, str)
    tags = hashtags.split()
    assert 5 <= len(tags) <= 8
    assert all(t.startswith("#") for t in tags)
    # No duplicate tags.
    assert len(tags) == len(set(tags))
    # No spaces inside a tag.
    assert all(" " not in t[1:] for t in tags)


async def test_fallback_caption_drops_original_scaffold():
    concept = _concept()
    caption, _ = await generate_caption_and_hashtags(concept)
    assert not caption.lower().startswith("original")


async def test_text_overlay_meme_format_hashtags():
    concept = _concept(
        format="text-overlay meme",
        voiceover_script=None,
        beat_sheet=[
            Beat(
                order=0,
                description="single meme frame",
                duration_sec=8,
                on_screen_text="the group chat has decided",
            )
        ],
    )
    caption, hashtags = await generate_caption_and_hashtags(concept)
    assert caption.strip()
    assert "#meme" in hashtags or "#memes" in hashtags


async def test_live_failure_is_swallowed():
    concept = _concept()

    class _BoomMessages:
        async def create(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("network down")

    class _BoomClient:
        messages = _BoomMessages()

    caption, hashtags = await generate_caption_and_hashtags(
        concept, client=_BoomClient()
    )
    assert caption.strip()
    assert hashtags.startswith("#")
