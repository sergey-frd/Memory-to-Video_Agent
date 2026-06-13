from __future__ import annotations

import json
import os
import re

from utils.video_prompt_composer import ReferenceContext, reference_contexts_to_payload
from utils.video_prompt_story import DYNAMIC_VIDEO_STORY_PROMPT_RULES, VideoPromptStoryDraft
from video_prompt_story_config import VideoPromptStoryConfig

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - runtime guard
    OpenAI = None  # type: ignore[assignment]


DEFAULT_STORY_MODEL = os.getenv("OPENAI_VIDEO_STORY_MODEL", os.getenv("OPENAI_VIDEO_PROMPT_MODEL", "gpt-4.1"))

SYSTEM_PROMPT = (
    "You write concise multi-scene video story briefs for a later video prompt composer. "
    "Return valid JSON only."
)


def synthesize_story_draft_with_openai(
    *,
    config: VideoPromptStoryConfig,
    draft: VideoPromptStoryDraft,
    reference_contexts: list[ReferenceContext],
    model: str | None = None,
) -> VideoPromptStoryDraft:
    client = _get_client()
    payload = {
        "story_title": draft.title,
        "story_brief": config.story_brief,
        "prefer_loving_kindness_tone": config.prefer_loving_kindness_tone,
        "total_duration_seconds": draft.total_duration_seconds,
        "scene_count": len(draft.scenes),
        "scene_duration_seconds": draft.scene_duration_seconds,
        "aspect_ratio": draft.aspect_ratio,
        "references": reference_contexts_to_payload(reference_contexts),
        "required_tags": [reference.tag for reference in draft.references],
        "scenes_to_write": len(draft.scenes),
    }
    response = client.responses.create(
        model=model or DEFAULT_STORY_MODEL,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": _story_prompt(payload)}],
            },
        ],
    )
    story_payload = _parse_story_payload(response.output_text)
    return _apply_story_payload(draft, story_payload)


def _story_prompt(payload: dict[str, object]) -> str:
    schema = (
        '{"technical_preamble": string, "scene_descriptions": [string, ...]}'
    )
    return (
        "Create JSON with schema "
        f"{schema}. "
        "Hard requirements:\n"
        "1. technical_preamble must start with 'Technical Preamble:' and stay in Russian.\n"
        "2. Write exactly the requested number of scene_descriptions entries.\n"
        "3. Every scene description must be in Russian and use present tense.\n"
        "4. Use only the provided @image tags when referencing source images.\n"
        "5. Distribute all required @image tags across the story; every listed tag must appear at least once.\n"
        "6. A scene may reference one or more @image tags inline inside the sentence body, never only in trailing parentheses.\n"
        "7. Follow story_brief exactly. Do not reinterpret group portraits as a family unless story_brief explicitly says so.\n"
        "8. If story_brief forbids personal names, never write Sasha or any other personal name in technical_preamble or scene_descriptions. Use neutral Russian phrasing such as герой видео, герой ролика, мужчина, танцующий герой.\n"
        "9. Keep each scene description short and concrete; no markdown headings.\n"
        "10. Do not output markdown fences or any prose outside JSON.\n"
        f"{DYNAMIC_VIDEO_STORY_PROMPT_RULES}\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _parse_story_payload(raw_text: str) -> dict[str, object]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("Story synthesis response must be a JSON object.")
    return payload


def _apply_story_payload(
    draft: VideoPromptStoryDraft,
    payload: dict[str, object],
) -> VideoPromptStoryDraft:
    technical_preamble = str(payload.get("technical_preamble", "")).strip()
    if not technical_preamble:
        raise ValueError("Story synthesis response must include technical_preamble.")

    scene_descriptions_raw = payload.get("scene_descriptions")
    if not isinstance(scene_descriptions_raw, list):
        raise ValueError("Story synthesis response must include scene_descriptions list.")
    scene_descriptions = [str(item).strip() for item in scene_descriptions_raw]
    if len(scene_descriptions) != len(draft.scenes):
        raise ValueError(
            "Story synthesis response must include "
            f"{len(draft.scenes)} scene descriptions; got {len(scene_descriptions)}."
        )
    if any(not description for description in scene_descriptions):
        raise ValueError("Every scene description must be non-empty.")

    required_tags = {reference.tag for reference in draft.references}
    used_tags = {
        tag
        for description in scene_descriptions
        for tag in re.findall(r"@image\d+", description)
    }
    missing_tags = sorted(required_tags - used_tags, key=lambda tag: int(tag.replace("@image", "")))
    if missing_tags:
        raise ValueError(
            "Story synthesis response did not use all required image tags: "
            + ", ".join(missing_tags)
        )

    from utils.video_prompt_story import apply_story_content

    return apply_story_content(
        draft,
        technical_preamble=technical_preamble,
        scene_descriptions=scene_descriptions,
    )


def _get_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("openai package is not installed.")
    return OpenAI()
