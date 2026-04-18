from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from models.scene_analysis import SceneAnalysis
from utils.image_analysis import ImageMetadata

GROK_MULTISCENE_TOTAL_SECONDS = 6
GROK_MULTISCENE_SHOT_SECONDS = 2
DEFAULT_GROK_MULTISCENE_MAX_CHARS = 1000
DEFAULT_GROK_MULTISCENE_MAX_WORDS = 200
GROK_MULTISCENE_ASPECT_RATIO = "16:9"


@dataclass(frozen=True)
class GrokPromptJsonBundle:
    video_prompt_json_en: str
    video_prompt_json_ru: str
    video_prompt_en: str
    video_prompt_ru: str


def build_grok_multiscene_json_bundle(
    *,
    metadata: ImageMetadata,
    scene_analysis_en: SceneAnalysis | None,
    scene_analysis_ru: SceneAnalysis | None,
    motion_sequence: Iterable[str],
    hide_phone_in_selfie: bool = True,
    prefer_loving_kindness_tone: bool = True,
    max_chars: int = DEFAULT_GROK_MULTISCENE_MAX_CHARS,
    max_words: int | None = None,
) -> GrokPromptJsonBundle:
    resolved_max_words = max_words if max_words is not None else _max_words_for_chars(max_chars)
    motions = _normalize_motion_sequence(motion_sequence)
    prompt_en = _build_prompt_en(
        metadata=metadata,
        scene_analysis=scene_analysis_en,
        motions=motions,
        hide_phone_in_selfie=hide_phone_in_selfie,
        prefer_loving_kindness_tone=prefer_loving_kindness_tone,
        max_chars=max_chars,
        max_words=resolved_max_words,
    )
    prompt_ru = _build_prompt_ru(
        metadata=metadata,
        scene_analysis=scene_analysis_ru,
        motions=motions,
        hide_phone_in_selfie=hide_phone_in_selfie,
        prefer_loving_kindness_tone=prefer_loving_kindness_tone,
        max_chars=max_chars,
        max_words=resolved_max_words,
    )

    errors_en = validate_grok_prompt_json_text(
        _wrap_prompt_json(prompt_en, lang="en"),
        expected_lang="en",
        max_chars=max_chars,
        max_words=resolved_max_words,
    )
    if errors_en:
        raise ValueError("Invalid Grok multiscene EN prompt JSON: " + "; ".join(errors_en))

    errors_ru = validate_grok_prompt_json_text(
        _wrap_prompt_json(prompt_ru, lang="ru"),
        expected_lang="ru",
        max_chars=max_chars,
        max_words=resolved_max_words,
    )
    if errors_ru:
        raise ValueError("Invalid Grok multiscene RU prompt JSON: " + "; ".join(errors_ru))

    return GrokPromptJsonBundle(
        video_prompt_json_en=_wrap_prompt_json(prompt_en, lang="en"),
        video_prompt_json_ru=_wrap_prompt_json(prompt_ru, lang="ru"),
        video_prompt_en=prompt_en,
        video_prompt_ru=prompt_ru,
    )


def extract_prompt_text_from_artifact(path: Path) -> str:
    raw_text = path.read_text(encoding="utf-8").strip()
    if path.suffix.lower() != ".json":
        return raw_text
    return extract_prompt_text_from_json_text(raw_text)


def extract_prompt_text_from_json_text(raw_text: str) -> str:
    payload = _extract_json_payload(raw_text)
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        prompt_text = str(payload[0].get("prompt", "")).strip()
        if prompt_text:
            return prompt_text
    if isinstance(payload, dict):
        prompt_text = str(payload.get("prompt", "")).strip()
        if prompt_text:
            return prompt_text
    raise ValueError("Prompt JSON does not contain a non-empty 'prompt' field.")


def validate_grok_prompt_json_text(
    prompt_json_text: str,
    *,
    expected_lang: str,
    max_chars: int = DEFAULT_GROK_MULTISCENE_MAX_CHARS,
    max_words: int | None = None,
) -> list[str]:
    errors: list[str] = []
    resolved_max_words = max_words if max_words is not None else _max_words_for_chars(max_chars)
    payload = _extract_json_payload(prompt_json_text)
    if not isinstance(payload, list):
        return ["Prompt JSON must be a JSON array."]
    if len(payload) != 1 or not isinstance(payload[0], dict):
        return ["Prompt JSON must contain exactly one object."]

    item = payload[0]
    if str(item.get("lang", "")).strip() != expected_lang:
        errors.append(f"Prompt JSON object must set lang to '{expected_lang}'.")

    prompt_text = str(item.get("prompt", "")).strip()
    if not prompt_text:
        return errors + ["Prompt JSON object is missing a non-empty prompt."]

    if len(prompt_text) > max_chars:
        errors.append(
            f"Prompt exceeds {max_chars} characters ({len(prompt_text)})."
        )
    word_count = len(prompt_text.split())
    if word_count > resolved_max_words:
        errors.append(f"Prompt exceeds {resolved_max_words} words ({word_count}).")
    for shot_index, marker in enumerate(("0-2s", "2-4s", "4-6s"), start=1):
        if f"Shot {shot_index}:" not in prompt_text:
            errors.append(f"Prompt is missing 'Shot {shot_index}:'.")
        if marker not in prompt_text:
            errors.append(f"Prompt is missing timing marker '{marker}'.")
    if "@image1" not in prompt_text:
        errors.append("Prompt is missing required image tag @image1.")
    total_marker = f"Total: {GROK_MULTISCENE_TOTAL_SECONDS}s / 3 shots / {GROK_MULTISCENE_ASPECT_RATIO}"
    if total_marker not in prompt_text:
        errors.append(f"Prompt is missing total footer '{total_marker}'.")
    return errors


def _build_prompt_en(
    *,
    metadata: ImageMetadata,
    scene_analysis: SceneAnalysis | None,
    motions: list[str],
    hide_phone_in_selfie: bool,
    prefer_loving_kindness_tone: bool,
    max_chars: int,
    max_words: int,
) -> str:
    background_chars, action_chars, mood_chars, summary_chars = _detail_limits(max_chars)
    subject = _subject_anchor_en(scene_analysis)
    background = _clip_clause(
        _compact_en(scene_analysis.background if scene_analysis and scene_analysis.background else metadata.composition_label),
        max_chars=background_chars,
    )
    action = _clip_clause(
        _compact_en(scene_analysis.main_action if scene_analysis and scene_analysis.main_action else metadata.scene_summary),
        max_chars=action_chars,
    )
    mood = _clip_clause(
        ", ".join(scene_analysis.mood) if scene_analysis and scene_analysis.mood else metadata.atmosphere_label
        ,
        max_chars=mood_chars,
    )
    summary = _clip_clause(
        _compact_en(scene_analysis.summary if scene_analysis and scene_analysis.summary else metadata.scene_summary),
        max_chars=summary_chars,
    )
    optional_notes: list[str] = []
    if hide_phone_in_selfie and _looks_like_selfie(metadata, scene_analysis):
        optional_notes.append("If @image1 is a selfie, do not show the phone or its reflection.")
    if prefer_loving_kindness_tone:
        optional_notes.append("Keep any warmth subtle.")
    optional_notes.append("Keep clothing, age, anatomy, and scene continuity stable across all three shots.")
    if max_chars >= 1600:
        optional_notes.append(
            f"Preserve the same environmental logic from @image1: {summary}. Avoid object invention and keep lighting physically believable."
        )

    required_sentences = [
        f"Shot 1: 0-2s. Start with the strongest current cinematic read of @image1. Keep {subject}, {background}, and {action}. Use {motions[0]}. Photorealistic, stable identity.",
        f"Shot 2: 2-4s. Shift into a clearly different optimal angle on @image1. Use {motions[1]}. Keep the same subject, clothing, and mood of {mood}.",
        f"Shot 3: 4-6s. End with a safer distant or oblique view of @image1. Use {motions[2]}. Wider frame, smaller facial scale, no new people or objects.",
        "Total: 6s / 3 shots / 16:9. No dialogue, no subtitles, no on-screen text.",
    ]
    return _fit_prompt_budget(required_sentences, optional_notes, max_chars=max_chars, max_words=max_words)


def _build_prompt_ru(
    *,
    metadata: ImageMetadata,
    scene_analysis: SceneAnalysis | None,
    motions: list[str],
    hide_phone_in_selfie: bool,
    prefer_loving_kindness_tone: bool,
    max_chars: int,
    max_words: int,
) -> str:
    background_chars, action_chars, mood_chars, summary_chars = _detail_limits(max_chars)
    subject = _subject_anchor_ru(scene_analysis)
    background = _clip_clause(
        _compact_ru(scene_analysis.background if scene_analysis and scene_analysis.background else metadata.composition_label),
        max_chars=background_chars,
    )
    action = _clip_clause(
        _compact_ru(scene_analysis.main_action if scene_analysis and scene_analysis.main_action else metadata.scene_summary),
        max_chars=action_chars,
    )
    mood = _clip_clause(
        ", ".join(scene_analysis.mood) if scene_analysis and scene_analysis.mood else metadata.atmosphere_label
        ,
        max_chars=mood_chars,
    )
    summary = _clip_clause(
        _compact_ru(scene_analysis.summary if scene_analysis and scene_analysis.summary else metadata.scene_summary),
        max_chars=summary_chars,
    )
    optional_notes: list[str] = []
    if hide_phone_in_selfie and _looks_like_selfie(metadata, scene_analysis):
        optional_notes.append("Если @image1 выглядит как селфи, не показывать телефон или его отражение.")
    if prefer_loving_kindness_tone:
        optional_notes.append("Теплоту оставлять только мягкой.")

    required_sentences = [
        f"Shot 1: 0-2s. Начать с самого сильного на сегодня кинематографичного варианта @image1. Сохранить {subject}, {background} и {action}. Использовать {motions[0]}. Фотореализм и стабильная идентичность.",
        f"Shot 2: 2-4s. Перейти к явно другому оптимальному ракурсу для @image1. Использовать {motions[1]}. Сохранить того же героя, одежду и настроение {mood}.",
        f"Shot 3: 4-6s. Завершить более безопасным дальним или косым ракурсом для @image1. Использовать {motions[2]}. Более широкий кадр, меньший масштаб лица, без новых людей и объектов.",
        "Total: 6s / 3 shots / 16:9. Без диалогов, без субтитров, без текста в кадре.",
    ]
    return _fit_prompt_budget(required_sentences, optional_notes, max_chars=max_chars, max_words=max_words)


def _fit_prompt_budget(
    required_sentences: list[str],
    optional_sentences: list[str],
    *,
    max_chars: int,
    max_words: int,
) -> str:
    prompt = _normalize_prompt_text(" ".join(required_sentences))
    if _within_budget(prompt, max_chars=max_chars, max_words=max_words):
        for sentence in optional_sentences:
            candidate = _normalize_prompt_text(f"{prompt} {sentence}")
            if _within_budget(candidate, max_chars=max_chars, max_words=max_words):
                prompt = candidate
        return prompt
    compact_prompt = _normalize_prompt_text(" ".join(required_sentences[:-1] + [required_sentences[-1]]))
    if _within_budget(compact_prompt, max_chars=max_chars, max_words=max_words):
        return compact_prompt
    words = compact_prompt.split()
    return " ".join(words[:max_words])


def _within_budget(text: str, *, max_chars: int, max_words: int) -> bool:
    return len(text) <= max_chars and len(text.split()) <= max_words


def _max_words_for_chars(max_chars: int) -> int:
    return max(1, max_chars // 5)


def _detail_limits(max_chars: int) -> tuple[int, int, int, int]:
    scale = max(1.0, max_chars / DEFAULT_GROK_MULTISCENE_MAX_CHARS)
    background_chars = max(56, int(round(56 * scale)))
    action_chars = max(64, int(round(64 * scale)))
    mood_chars = max(36, int(round(36 * scale)))
    summary_chars = max(72, int(round(72 * scale)))
    return background_chars, action_chars, mood_chars, summary_chars


def _normalize_motion_sequence(motion_sequence: Iterable[str]) -> list[str]:
    cleaned = [_compact_motion_label(str(motion)) for motion in motion_sequence if str(motion).strip()]
    if not cleaned:
        cleaned = [
            "a cinematic reveal",
            "an alternate camera drift",
            "a slow pullback to a wider view",
        ]
    while len(cleaned) < 3:
        cleaned.append(cleaned[-1])
    cleaned[2] = "a slow pullback to a wider view"
    return cleaned[:3]


def _wrap_prompt_json(prompt_text: str, *, lang: str) -> str:
    payload = [
        {
            "lang": lang,
            "prompt_mode": "grok_multiscene_three_shot",
            "duration_seconds": GROK_MULTISCENE_TOTAL_SECONDS,
            "aspect_ratio": GROK_MULTISCENE_ASPECT_RATIO,
            "prompt": prompt_text,
        }
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _extract_json_payload(raw_text: str) -> object:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    for start in range(len(text)):
        if text[start] not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        return payload
    raise ValueError("Prompt artifact does not contain valid JSON.")


def _subject_anchor_en(scene_analysis: SceneAnalysis | None) -> str:
    if scene_analysis and scene_analysis.people_count > 1:
        return "the same visible people and their relations from @image1"
    if scene_analysis and scene_analysis.people_count == 1:
        return "the same main subject from @image1"
    return "the same key subject and setting from @image1"


def _subject_anchor_ru(scene_analysis: SceneAnalysis | None) -> str:
    if scene_analysis and scene_analysis.people_count > 1:
        return "тех же видимых людей и их связи из @image1"
    if scene_analysis and scene_analysis.people_count == 1:
        return "того же главного героя из @image1"
    return "тот же главный объект и ту же среду из @image1"


def _looks_like_selfie(metadata: ImageMetadata, scene_analysis: SceneAnalysis | None) -> bool:
    haystack = " ".join(
        [
            metadata.scene_summary,
            metadata.composition_label,
            scene_analysis.summary if scene_analysis else "",
            scene_analysis.shot_type if scene_analysis else "",
            scene_analysis.main_action if scene_analysis else "",
        ]
    ).lower()
    return any(token in haystack for token in ("selfie", "self-portrait", "селфи", "автопортрет"))


def _compact_en(text: str) -> str:
    compact = _normalize_prompt_text(text)
    compact = re.sub(r"[.;:]+$", "", compact)
    return compact


def _compact_ru(text: str) -> str:
    compact = _normalize_prompt_text(text)
    compact = re.sub(r"[.;:]+$", "", compact)
    return compact


def _clip_clause(text: str, *, max_chars: int) -> str:
    compact = _normalize_prompt_text(text)
    if len(compact) <= max_chars:
        return compact
    clipped = compact[: max_chars - 3].rstrip(" ,.;:")
    return f"{clipped}..."


def _compact_motion_label(text: str) -> str:
    compact = _normalize_prompt_text(text).lower()
    if any(token in compact for token in ("push in", "dolly in", "move closer", "zoom in", "наезд", "приближ")):
        return "a gentle push in"
    if any(token in compact for token in ("orbit", "arc", "side", "панорам", "сбоку", "облет")):
        return "a soft side arc"
    if any(token in compact for token in ("pull", "zoom out", "move back", "wider", "отдал", "шире")):
        return "a slow pullback"
    if any(token in compact for token in ("rise", "crane", "jib", "boom", "подъем")):
        return "a light rise"
    if any(token in compact for token in ("tilt", "наклон")):
        return "a soft tilt"
    return _clip_clause(compact, max_chars=28)


def _normalize_prompt_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r", " ").replace("\n", " ")).strip()
