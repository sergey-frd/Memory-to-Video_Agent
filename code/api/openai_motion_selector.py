from __future__ import annotations

import json
import os

from config import VideoFramingMode
from models.scene_analysis import SceneAnalysis
from utils.image_analysis import ImageMetadata

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - runtime guard
    OpenAI = None  # type: ignore[assignment]


DEFAULT_MOTION_MODEL = os.getenv("OPENAI_MOTION_MODEL", "gpt-4.1-mini")
SYSTEM_PROMPT = (
    "You are a cinematic camera supervisor. "
    "Choose concrete camera motions that fit the exact frame content. "
    "Return valid JSON only. "
    "Use Russian wording for the motion descriptions. "
    "Do not use placeholders such as 'AI-motion 1'."
)


def select_motion_sequences_with_openai(
    *,
    metadata: ImageMetadata,
    scene_analysis: SceneAnalysis,
    video_count: int,
    camera_segments: int,
    framing_mode: VideoFramingMode = VideoFramingMode.IDENTITY_SAFE,
    model: str | None = None,
) -> list[list[str]]:
    client = _get_client()
    response = client.responses.create(
        model=model or DEFAULT_MOTION_MODEL,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Ты кинематографичный супервизор камеры. "
                            "Подбирай конкретные движения камеры на русском языке под содержимое кадра. "
                            "Если в кадре есть люди, предпочитай безопасные для сохранения лица ракурсы: издалека, сбоку, сверху, снизу, с воздуха, через мягкий полуоблет, кран или раскрытие пространства. "
                            "Избегай агрессивных наездов в лицо, экстремальных крупных планов и сильного увеличения лица. "
                            "Не используй заглушки вроде 'AI-motion 1'. "
                            "Верни только JSON."
                        ),
                    }
                ],
            },
            {
                "role": "system",
                "content": [{"type": "input_text", "text": _system_prompt_override(framing_mode)}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": _motion_prompt(
                            metadata=metadata,
                            scene_analysis=scene_analysis,
                            video_count=video_count,
                            camera_segments=camera_segments,
                            framing_mode=framing_mode,
                        ),
                    }
                ],
            },
        ],
    )
    return _parse_motion_response(
        response.output_text,
        video_count=video_count,
        camera_segments=camera_segments,
    )


def _motion_prompt(
    *,
    metadata: ImageMetadata,
    scene_analysis: SceneAnalysis,
    video_count: int,
    camera_segments: int,
    framing_mode: VideoFramingMode = VideoFramingMode.IDENTITY_SAFE,
) -> str:
    payload = {
        "format_description": metadata.format_description,
        "scene_summary": scene_analysis.summary,
        "people_count": scene_analysis.people_count,
        "background": scene_analysis.background,
        "shot_type": scene_analysis.shot_type,
        "main_action": scene_analysis.main_action,
        "mood": scene_analysis.mood,
        "relationships": scene_analysis.relationships,
        "people": scene_analysis.to_dict().get("people", []),
        "video_count": video_count,
        "camera_segments": camera_segments,
        "framing_mode": framing_mode.value,
    }
    if framing_mode != VideoFramingMode.IDENTITY_SAFE:
        return _motion_prompt_for_mode(payload, framing_mode)
    return (
        "На основе описания кадра выбери движения камеры для каждого видеопромпта. "
        "Верни строгий JSON формата "
        '{"sequences":[{"video_index":1,"motions":["..."]}]}.\n'
        "Требования:\n"
        "1. Для каждого видео верни ровно столько движений, сколько указано в camera_segments.\n"
        "2. Формулировки движений должны быть конкретными и кинематографичными.\n"
        "3. Движения должны соответствовать персонажам, действию, плану и фону.\n"
        "4. Если видео несколько, последовательности должны различаться и раскрывать сцену разными способами.\n"
        "5. Первый элемент каждого следующего видео должен быть естественным продолжением последнего элемента предыдущего видео, без резкого скачка изображения.\n"
        "6. Не используй списки из таблиц и не возвращай шаблоны 'AI-motion N'.\n"
        "7. Если в кадре видны люди, предпочитай более безопасные для идентичности ракурсы: издалека, сверху, снизу, сбоку, с воздуха, через мягкий полуоблет, кран или пространственное раскрытие сцены.\n"
        "8. Избегай экстремальных крупных планов лица, агрессивных frontal push-in, zoom-in в лицо и других движений, которые чрезмерно увеличивают лицо.\n"
        "9. Эмоцию лучше раскрывать через силуэт, жест, позу, пространство и связь человека со средой, а не через резкое укрупнение лица.\n"
        f"Входные данные:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _system_prompt_override(framing_mode: VideoFramingMode) -> str:
    if framing_mode == VideoFramingMode.FACE_CLOSEUP:
        return (
            "Override any default identity-safe bias. "
            "Close facial framing is allowed and may be preferred when it is the strongest cinematic reading of the image."
        )
    if framing_mode == VideoFramingMode.AI_OPTIMAL:
        return (
            "Override any default identity-safe bias. "
            "Choose the most cinematic and effective camera plan for the source image, even if that includes stronger facial emphasis."
        )
    return SYSTEM_PROMPT


def _motion_prompt_for_mode(payload: dict[str, object], framing_mode: VideoFramingMode) -> str:
    return (
        "Based on the frame analysis, choose camera motions for each video prompt. "
        'Return strict JSON with schema {"sequences":[{"video_index":1,"motions":["..."]}]}. '
        "Use Russian wording for the motion descriptions.\n"
        "Requirements:\n"
        "1. For each video, return exactly the number of motions specified in camera_segments.\n"
        "2. Motion descriptions must be concrete, scene-specific, and cinematic.\n"
        "3. Motions must fit the visible people, action, framing, and environment.\n"
        "4. If there are multiple videos, each sequence must reveal the scene in a meaningfully different way.\n"
        "5. The first motion of each following video should feel like a natural continuation of the last motion from the previous video, without a jarring visual reset.\n"
        "6. Do not use motion-table placeholders or generic labels like 'AI-motion N'.\n"
        f"{_framing_mode_requirements(framing_mode)}"
        f"Input JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _framing_mode_requirements(framing_mode: VideoFramingMode) -> str:
    if framing_mode == VideoFramingMode.FACE_CLOSEUP:
        return (
            "7. If people are visible, close facial framing is allowed and may be preferred when it strengthens the shot.\n"
            "8. Medium close-ups, close-ups, and gentle push-ins toward the face are acceptable if they stay photorealistic and emotionally precise.\n"
            "9. Let the face carry emotion directly when that is the strongest reading of the source image.\n"
        )
    return (
        "7. Choose the camera plan that feels most cinematic and effective for the source image, even if that includes stronger facial emphasis or a closer portrait scale.\n"
        "8. Do not optimize for avoiding facial enlargement; optimize for the strongest scene reading, emotional clarity, and cinematic impact.\n"
        "9. The chosen motion may stay wide, medium, or close depending on what best serves the original frame.\n"
    )


def _parse_motion_response(raw_text: str, *, video_count: int, camera_segments: int) -> list[list[str]]:
    payload = _extract_json_object(raw_text)
    sequences_raw = payload.get("sequences", [])
    sequences: list[list[str]] = []
    if isinstance(sequences_raw, list):
        for item in sequences_raw:
            if not isinstance(item, dict):
                continue
            motions = item.get("motions", [])
            if not isinstance(motions, list):
                continue
            cleaned = [str(motion).strip() for motion in motions if str(motion).strip()]
            if cleaned:
                sequences.append(cleaned[:camera_segments])

    while len(sequences) < video_count:
        fallback_index = len(sequences) + 1
        sequences.append([f"Кинематографичное движение камеры {fallback_index}.{segment + 1}" for segment in range(camera_segments)])

    normalized: list[list[str]] = []
    for sequence in sequences[:video_count]:
        if len(sequence) < camera_segments:
            padded = sequence + [sequence[-1]] * (camera_segments - len(sequence))
            normalized.append(padded)
        else:
            normalized.append(sequence[:camera_segments])
    return normalized


def _extract_json_object(raw_text: str) -> dict[str, object]:
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
        if text[start] != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("Motion selector response does not contain a valid JSON object.")


def _get_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("Install openai>=1.0 to use motion selection.")
    return OpenAI()
