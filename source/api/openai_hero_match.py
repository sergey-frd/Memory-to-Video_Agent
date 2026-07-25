from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

from PIL import Image, ImageOps

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - runtime guard
    OpenAI = None  # type: ignore[assignment]


DEFAULT_HERO_MATCH_MODEL = os.getenv("OPENAI_HERO_MATCH_MODEL", "gpt-4.1")
VALID_MATCH_LEVELS = {"high", "medium", "absent", "uncertain"}


def match_hero_in_frames_with_openai(
    *,
    frame_paths: list[tuple[float, Path]],
    reference_image_paths: list[Path],
    hero_definition: dict[str, object],
    clip_name: str,
    model: str | None = None,
    high_confidence_threshold: float = 0.85,
    medium_confidence_threshold: float = 0.55,
    max_image_edge: int = 1024,
    request_timeout_seconds: float = 180.0,
) -> list[dict[str, object]]:
    if not frame_paths:
        return []
    if not reference_image_paths:
        raise ValueError("At least one hero reference image is required.")

    content: list[dict[str, object]] = [
        {
            "type": "input_text",
            "text": _build_prompt(
                clip_name=clip_name,
                hero_definition=hero_definition,
                frame_paths=frame_paths,
                high_confidence_threshold=high_confidence_threshold,
                medium_confidence_threshold=medium_confidence_threshold,
            ),
        },
        {"type": "input_text", "text": "Эталонные изображения героя:"},
    ]
    for index, reference_path in enumerate(reference_image_paths, start=1):
        content.extend(
            [
                {"type": "input_text", "text": f"Эталон {index}: {reference_path.name}"},
                {
                    "type": "input_image",
                    "image_url": _image_to_data_url(reference_path, max_image_edge=max_image_edge),
                },
            ]
        )
    content.append({"type": "input_text", "text": "Проверяемые кадры видео:"})
    for index, (timestamp, frame_path) in enumerate(frame_paths):
        content.extend(
            [
                {
                    "type": "input_text",
                    "text": f"Кадр index={index}, local_time_sec={timestamp:.3f}",
                },
                {
                    "type": "input_image",
                    "image_url": _image_to_data_url(frame_path, max_image_edge=max_image_edge),
                },
            ]
        )

    response = _get_client(timeout_seconds=request_timeout_seconds).responses.create(
        model=model or DEFAULT_HERO_MATCH_MODEL,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You conservatively compare a known person in reference images with people "
                            "visible in family-video frames. A false high-confidence match is worse than "
                            "an uncertain result. Compare multiple facial proportions and stable features; "
                            "never identify by clothing, background, companions, activity, or hairstyle alone. "
                            "Return strict JSON only, with Russian reasons."
                        ),
                    }
                ],
            },
            {"role": "user", "content": content},
        ],
    )
    payload = _extract_json_object(response.output_text)
    raw_matches = payload.get("frames")
    if not isinstance(raw_matches, list):
        raise ValueError("Hero match response must contain a frames array.")

    by_index = {
        int(item["index"]): item
        for item in raw_matches
        if isinstance(item, dict) and str(item.get("index", "")).lstrip("-").isdigit()
    }
    normalized: list[dict[str, object]] = []
    for index, (timestamp, _path) in enumerate(frame_paths):
        item = by_index.get(index, {})
        confidence = _clamp_confidence(item.get("confidence", 0.0))
        level = _normalize_level(
            str(item.get("match_level") or "uncertain"),
            confidence=confidence,
            high_threshold=high_confidence_threshold,
            medium_threshold=medium_confidence_threshold,
        )
        normalized.append(
            {
                "index": index,
                "timestamp_sec": round(timestamp, 3),
                "match_level": level,
                "confidence": round(confidence, 3),
                "reason": str(item.get("reason") or "Модель не предоставила объяснение.").strip(),
                "visible_cues": [
                    str(value).strip()
                    for value in item.get("visible_cues", [])
                    if str(value).strip()
                ]
                if isinstance(item.get("visible_cues"), list)
                else [],
            }
        )
    return normalized


def _build_prompt(
    *,
    clip_name: str,
    hero_definition: dict[str, object],
    frame_paths: list[tuple[float, Path]],
    high_confidence_threshold: float,
    medium_confidence_threshold: float,
) -> str:
    profile = hero_definition.get("definition", hero_definition)
    frame_list = ", ".join(f"{index}:{timestamp:.3f}s" for index, (timestamp, _path) in enumerate(frame_paths))
    return (
        f"Видео: {clip_name}\n"
        f"Проверяемые кадры index:time: {frame_list}\n"
        f"Порог high: {high_confidence_threshold:.2f}; порог medium: {medium_confidence_threshold:.2f}\n"
        "Профиль героя:\n"
        f"{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        "Для каждого проверяемого кадра реши, присутствует ли тот же человек, что на эталонах. "
        "high допустим только при чётко видимом лице и одновременном совпадении нескольких устойчивых "
        "черт. medium означает правдоподобное сходство, но ракурс, резкость, перекрытие или недостаток "
        "признаков не позволяют утверждать точно. absent означает, что герой не найден; uncertain — "
        "качество кадра не позволяет решить. Наличие брекетов может усиливать совпадение, но их отсутствие "
        "не опровергает личность.\n"
        'Верни {"frames": [{"index": integer, "match_level": "high|medium|absent|uncertain", '
        '"confidence": number, "reason": string, "visible_cues": [string]}]}. '
        "Верни ровно одну запись для каждого index."
    )


def _normalize_level(
    raw_level: str,
    *,
    confidence: float,
    high_threshold: float,
    medium_threshold: float,
) -> str:
    level = raw_level.strip().casefold()
    if level not in VALID_MATCH_LEVELS:
        level = "uncertain"
    if level == "high" and confidence < high_threshold:
        return "medium" if confidence >= medium_threshold else "uncertain"
    if level == "medium" and confidence < medium_threshold:
        return "uncertain"
    return level


def _clamp_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _image_to_data_url(image_path: Path, *, max_image_edge: int) -> str:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_image_edge, max_image_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


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
    raise ValueError(f"Hero match response does not contain a JSON object: {raw_text[:300]}")


def _get_client(*, timeout_seconds: float = 180.0) -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("Install openai>=1.0 to use hero matching.")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return OpenAI(timeout=max(10.0, float(timeout_seconds)))
