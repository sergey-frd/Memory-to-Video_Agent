from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path

from models.scene_analysis import PersonInFrame, SceneAnalysis

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - runtime guard
    OpenAI = None  # type: ignore[assignment]


DEFAULT_SCENE_MODEL = os.getenv("OPENAI_SCENE_MODEL", "gpt-4.1-mini")
DEFAULT_SCENE_REPAIR_MODEL = os.getenv("OPENAI_SCENE_REPAIR_MODEL", "gpt-4.1-mini")


def analyze_scene_with_openai(image_path: Path, model: str | None = None, language: str = "ru") -> SceneAnalysis:
    client = _get_client()
    raw_text = _request_scene_analysis(client, image_path, model or DEFAULT_SCENE_MODEL, language)
    try:
        return parse_scene_analysis_response(raw_text)
    except ValueError:
        repaired_text = _repair_scene_analysis_response(client, raw_text, language)
        try:
            return parse_scene_analysis_response(repaired_text)
        except ValueError:
            return _fallback_scene_analysis(raw_text)


def parse_scene_analysis_response(raw_text: str) -> SceneAnalysis:
    payload = _extract_json_object(raw_text)
    people = [
        PersonInFrame(
            label=str(person.get("label", "человек")).strip(),
            position_in_frame=str(person.get("position_in_frame", "")).strip(),
            role_in_scene=str(person.get("role_in_scene", "")).strip(),
            apparent_age_group=str(person.get("apparent_age_group", "")).strip(),
            apparent_gender_presentation=str(person.get("apparent_gender_presentation", "")).strip(),
            face_visibility=str(person.get("face_visibility", "")).strip(),
            facial_expression=str(person.get("facial_expression", "")).strip(),
            clothing=str(person.get("clothing", "")).strip(),
            pose=str(person.get("pose", "")).strip(),
        )
        for person in payload.get("people", [])
        if isinstance(person, dict)
    ]
    people_count = int(payload.get("people_count", len(people)))
    return SceneAnalysis(
        summary=str(payload.get("summary", "")).strip(),
        people_count=people_count,
        people=people,
        background=str(payload.get("background", "")).strip(),
        shot_type=str(payload.get("shot_type", "")).strip(),
        main_action=str(payload.get("main_action", "")).strip(),
        mood=_normalize_text_list(payload.get("mood", [])),
        relationships=_normalize_text_list(payload.get("relationships", [])),
    )


def format_scene_report(analysis: SceneAnalysis) -> str:
    mood_lines = analysis.mood or ["не определено"]
    relationship_lines = analysis.relationships or ["не определено"]
    lines = [
        "НАБЛЮДАЕМОЕ ОПИСАНИЕ КАДРА",
        "",
        "1. Сколько людей в кадре",
        f"{analysis.people_count}",
        "",
        "2. Кто находится в кадре",
    ]
    if analysis.people:
        for person in analysis.people:
            label = person.label or "человек"
            position = f" ({person.position_in_frame})" if person.position_in_frame else ""
            role = f" - {person.role_in_scene}" if person.role_in_scene else ""
            lines.append(f"- {label}{position}{role}")
    else:
        lines.append("- Люди не определены")

    lines.extend(["", "3. Выражение лица"])
    if analysis.people:
        for person in analysis.people:
            lines.append(f"- {person.label or 'Человек'}: {person.facial_expression or 'неразличимо'}")
    else:
        lines.append("- не определено")

    lines.extend(["", "4. Одежда"])
    if analysis.people:
        for person in analysis.people:
            lines.append(f"- {person.label or 'Человек'}: {person.clothing or 'неразличимо'}")
    else:
        lines.append("- не определено")

    lines.extend(
        [
            "",
            "5. Фон",
            analysis.background or "не определено",
            "",
            "6. План съёмки",
            analysis.shot_type or "не определено",
            "",
            "7. Поза и действие",
        ]
    )
    if analysis.main_action:
        lines.append(f"- Основное действие: {analysis.main_action}")
    if analysis.people:
        for person in analysis.people:
            lines.append(f"- {person.label or 'Человек'}: {person.pose or 'неразличимо'}")
    else:
        lines.append("- не определено")

    lines.extend(["", "8. Настроение сцены"])
    lines.extend([f"- {mood}" for mood in mood_lines])

    lines.extend(["", "9. Отношения между персонажами"])
    lines.extend([f"- {relation}" for relation in relationship_lines])

    if analysis.summary:
        lines.extend(["", "Краткое описание", analysis.summary])
    return "\n".join(lines)


def _request_scene_analysis(client: OpenAI, image_path: Path, model: str, language: str) -> str:
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You analyze a single still frame and return JSON only. "
                            "Describe what is visibly present in the frame, count all visible people including partially visible ones, "
                            "and give grounded best-effort estimates when exact details are uncertain. "
                            "Do not leave fields empty unless the information is genuinely not visible. "
                            f"Return all descriptive values in {_language_name(language)}."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _analysis_prompt(language)},
                    {"type": "input_image", "image_url": _image_to_data_url(image_path)},
                ],
            },
        ],
    )
    return response.output_text


def _analysis_prompt(language: str) -> str:
    return (
        "Analyze the image carefully and return strict JSON only. "
        "Count every visible person, even if one is partially turned away or only partly visible. "
        "Use the following schema exactly: "
        "{"
        '"summary": string, '
        '"people_count": integer, '
        '"people": ['
        "{"
        '"label": string, '
        '"position_in_frame": string, '
        '"role_in_scene": string, '
        '"apparent_age_group": string, '
        '"apparent_gender_presentation": string, '
        '"face_visibility": string, '
        '"facial_expression": string, '
        '"clothing": string, '
        '"pose": string'
        "}"
        "], "
        '"background": string, '
        '"shot_type": string, '
        '"main_action": string, '
        '"mood": [string], '
        '"relationships": [string]'
        "}. "
        f"All values must be in {_language_name(language)}. "
        "Focus on: who is in the frame, how many people are visible, facial expression, clothing, background, shot type, pose, overall action, mood, and relationships between characters. "
        "Do not collapse multiple characters into one. "
        "If a role is inferred, say so carefully but still provide the best grounded hypothesis."
    )


def _repair_scene_analysis_response(client: OpenAI, raw_text: str, language: str) -> str:
    response = client.responses.create(
        model=DEFAULT_SCENE_REPAIR_MODEL,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Convert the provided scene analysis text into strict JSON only. "
                            f"All descriptive values must be in {_language_name(language)}."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Return JSON with schema "
                            '{"summary": string, "people_count": integer, "people": [{"label": string, "position_in_frame": string, '
                            '"role_in_scene": string, "apparent_age_group": string, "apparent_gender_presentation": string, '
                            '"face_visibility": string, "facial_expression": string, "clothing": string, "pose": string}], '
                            '"background": string, "shot_type": string, "main_action": string, "mood": [string], "relationships": [string]}.\n'
                            "Source text:\n"
                            f"{raw_text}"
                        ),
                    }
                ],
            },
        ],
    )
    return response.output_text


def _fallback_scene_analysis(raw_text: str) -> SceneAnalysis:
    cleaned = _clean_text(raw_text)
    return SceneAnalysis(
        summary=cleaned,
        people_count=_extract_people_count(cleaned),
        people=[],
        background="",
        shot_type="",
        main_action="",
        mood=[],
        relationships=[],
    )


def _extract_people_count(text: str) -> int:
    numeric_patterns = [
        r"(\d+)\s+(?:человек|людей|персонаж(?:а|ей)?)",
        r"(?:visible|there are|shows)\s+(\d+)\s+(?:people|persons)",
    ]
    for pattern in numeric_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    word_map = {
        "один": 1,
        "одна": 1,
        "два": 2,
        "две": 2,
        "три": 3,
        "четыре": 4,
        "пять": 5,
    }
    lowered = text.lower()
    for word, value in word_map.items():
        if re.search(rf"\b{word}\b\s+(?:человек|людей|персонажа|персонажей)", lowered):
            return value
    return 0


def _clean_text(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:1200]


def _get_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("Install openai>=1.0 to use scene analysis.")
    return OpenAI()


def _image_to_data_url(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(image_path.name)
    mime_type = mime_type or "image/png"
    raw_bytes = image_path.read_bytes()
    encoded = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


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
    raise ValueError("Scene analysis response does not contain a valid JSON object.")


def _normalize_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if ";" in text:
            return [part.strip() for part in text.split(";") if part.strip()]
        return [text]
    return []


def _language_name(language: str) -> str:
    normalized = language.strip().lower()
    if normalized in {"ru", "rus", "russian"}:
        return "Russian"
    return "English"
