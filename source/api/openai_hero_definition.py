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


DEFAULT_HERO_DEFINITION_MODEL = os.getenv("OPENAI_HERO_DEFINITION_MODEL", "gpt-4.1")


def create_hero_definition_with_openai(
    *,
    image_paths: list[Path],
    human_detail_text: str,
    hero_name: str,
    model: str | None = None,
    language: str = "ru",
    max_image_edge: int = 1024,
) -> dict[str, object]:
    if not image_paths:
        raise ValueError("At least one hero reference image is required.")

    client = _get_client()
    content: list[dict[str, object]] = [
        {
            "type": "input_text",
            "text": _build_prompt(
                hero_name=hero_name,
                human_detail_text=human_detail_text,
                language=language,
                image_count=len(image_paths),
            ),
        }
    ]
    for index, image_path in enumerate(image_paths, start=1):
        content.extend(
            [
                {
                    "type": "input_text",
                    "text": f"Reference image {index}: {image_path.name}",
                },
                {
                    "type": "input_image",
                    "image_url": _image_to_data_url(image_path, max_image_edge=max_image_edge),
                },
            ]
        )

    response = client.responses.create(
        model=model or DEFAULT_HERO_DEFINITION_MODEL,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Create a conservative visual identity profile for finding the same person "
                            "in family-video frames. Use only visible, repeatable evidence from the supplied "
                            "reference images. The text is supporting context, not proof of appearance. "
                            "Do not infer ethnicity, health, personality, or other sensitive traits. "
                            "Return strict JSON only."
                        ),
                    }
                ],
            },
            {"role": "user", "content": content},
        ],
    )
    return _extract_json_object(response.output_text)


def _build_prompt(
    *,
    hero_name: str,
    human_detail_text: str,
    language: str,
    image_count: int,
) -> str:
    language_name = "Russian" if language.casefold().startswith("ru") else language
    return (
        f"Hero name: {hero_name}\n"
        f"Reference images: {image_count}\n"
        f"All descriptive values must be in {language_name}.\n\n"
        "Supporting text supplied by the family:\n"
        f"{human_detail_text.strip()}\n\n"
        "Compare all reference images and describe only stable visual cues that remain useful across "
        "different lighting, camera angles, expressions, hairstyles, clothing, and age-adjacent footage. "
        "If a feature is uncertain or inconsistent, put it in uncertainties rather than asserting it. "
        "Do not use clothing, background, companions, activity, or image quality as identity evidence. "
        "Exclude intimate/body-development details and unrelated biography from the output.\n\n"
        "Return exactly one JSON object with this schema:\n"
        "{\n"
        '  "hero_name": string,\n'
        '  "visual_summary": string,\n'
        '  "stable_visual_features": {\n'
        '    "apparent_age_range": string,\n'
        '    "face_shape": string,\n'
        '    "hair": string,\n'
        '    "eyes": string,\n'
        '    "eyebrows": string,\n'
        '    "nose": string,\n'
        '    "mouth_and_smile": string,\n'
        '    "other_repeatable_cues": [string]\n'
        "  },\n"
        '  "appearance_variations": [string],\n'
        '  "strong_identity_evidence": [string],\n'
        '  "supporting_identity_evidence": [string],\n'
        '  "do_not_use_as_identity_evidence": [string],\n'
        '  "high_confidence_rule": string,\n'
        '  "medium_confidence_rule": string,\n'
        '  "uncertainties": [string]\n'
        "}\n"
        "The confidence rules must require agreement between several facial cues and must explicitly "
        "distinguish a near-certain match from a plausible-but-uncertain match."
    )


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
    raise ValueError(f"Hero definition response does not contain a JSON object: {raw_text[:300]}")


def _get_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("Install openai>=1.0 to create a hero definition.")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return OpenAI()
