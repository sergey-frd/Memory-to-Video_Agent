from __future__ import annotations

import json
import os
import re

from utils.video_prompt_composer import (
    GeneratedSeedanceJsonBundle,
    GeneratedVideoPromptBundle,
    ReferenceContext,
    ScenarioVariantSpec,
    VideoPromptRequest,
    reference_contexts_to_payload,
    scenario_variant_to_payload,
    scene_specs_to_payload,
    used_image_tags,
)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - runtime guard
    OpenAI = None  # type: ignore[assignment]


DEFAULT_VIDEO_PROMPT_MODEL = os.getenv("OPENAI_VIDEO_PROMPT_MODEL", "gpt-4.1")
ASCII_PUNCT_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)
SEEDANCE_TECHNICAL_PREAMBLE_CORE = (
    "montage, multi-shot action Hollywood movie, Don't use one camera angle or single cut, "
    "cinematic lighting, photorealistic, 35mm film quality, professional color grading, "
    "sharp focus, high detail texture, film grain, depth of field mastery, ARRI ALEXA aesthetic"
)


SYSTEM_PROMPT = (
    "You write production-ready multi-scene video generation prompts. "
    "Return valid JSON only. "
    "Create one English prompt and one Russian translation for the same video."
)

SEEDANCE_SYSTEM_PROMPT = (
    "You are a strict Seedance 2.0 prompt director JSON API. "
    "Return only a JSON array with one object in English."
)

SEEDANCE_CONTROL_RU_SYSTEM_PROMPT = (
    "You translate Seedance 2.0 prompts into Russian for human review. "
    "Return only a JSON array with one object in Russian."
)


def synthesize_multiscene_video_prompt_with_openai(
    *,
    request: VideoPromptRequest,
    reference_contexts: list[ReferenceContext],
    scenario_variant: ScenarioVariantSpec | None = None,
    model: str | None = None,
) -> GeneratedVideoPromptBundle:
    client = _get_client()
    effective_variant = scenario_variant or request.scenario_variants[0]
    payload = {
        "technical_preamble": request.technical_preamble,
        "total_duration_seconds": request.total_duration_seconds,
        "max_prompt_chars": request.max_prompt_chars,
        "aspect_ratio": request.aspect_ratio,
        "scenario_variant": scenario_variant_to_payload(effective_variant),
        "references": reference_contexts_to_payload(reference_contexts),
        "used_image_tags_in_scene_brief": used_image_tags(request),
        "scenes": scene_specs_to_payload(request),
    }
    response = client.responses.create(
        model=model or DEFAULT_VIDEO_PROMPT_MODEL,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": _composer_prompt(payload)}],
            },
        ],
    )
    bundle = _normalize_generated_prompt_bundle(
        _parse_generated_prompt_bundle(response.output_text),
        request=request,
    )
    validation_errors = _validate_generated_prompt_bundle(bundle, request)
    if validation_errors:
        repair_response = client.responses.create(
            model=model or DEFAULT_VIDEO_PROMPT_MODEL,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _repair_prompt(payload, bundle, validation_errors),
                        }
                    ],
                },
            ],
        )
        bundle = _normalize_generated_prompt_bundle(
            _parse_generated_prompt_bundle(repair_response.output_text),
            request=request,
        )
        validation_errors = _validate_generated_prompt_bundle(bundle, request)
        if validation_errors:
            raise ValueError(
                "Video prompt composer could not satisfy the required output format: "
                + "; ".join(validation_errors)
            )
    return bundle


def synthesize_seedance_json_prompt_with_openai(
    *,
    request: VideoPromptRequest,
    reference_contexts: list[ReferenceContext],
    director_requirements_text: str,
    scenario_variant: ScenarioVariantSpec | None = None,
    model: str | None = None,
) -> str:
    client = _get_client()
    effective_variant = scenario_variant or request.scenario_variants[0]
    payload = {
        "technical_preamble": request.technical_preamble,
        "total_duration_seconds": request.total_duration_seconds,
        "max_prompt_chars": request.max_prompt_chars,
        "aspect_ratio": request.aspect_ratio,
        "scene_body_mode": "structured",
        "scenario_variant": scenario_variant_to_payload(effective_variant),
        "references": reference_contexts_to_payload(reference_contexts),
        "used_image_tags_in_scene_brief": used_image_tags(request),
        "scenes": scene_specs_to_payload(request),
    }
    response = client.responses.create(
        model=model or DEFAULT_VIDEO_PROMPT_MODEL,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SEEDANCE_SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": _seedance_composer_prompt(payload, director_requirements_text),
                    }
                ],
            },
        ],
    )
    seedance_json_text = _normalize_seedance_json_text(response.output_text)
    validation_errors = _validate_seedance_json_prompt(seedance_json_text, request)
    if validation_errors:
        repair_response = client.responses.create(
            model=model or DEFAULT_VIDEO_PROMPT_MODEL,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": SEEDANCE_SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _seedance_repair_prompt(
                                payload,
                                director_requirements_text,
                                seedance_json_text,
                                validation_errors,
                            ),
                        }
                    ],
                },
            ],
        )
        seedance_json_text = _normalize_seedance_json_text(repair_response.output_text)
        validation_errors = _validate_seedance_json_prompt(seedance_json_text, request)
        if validation_errors:
            raise ValueError(
                "Seedance JSON prompt composer could not satisfy the required output format: "
                + "; ".join(validation_errors)
            )
    return seedance_json_text


def synthesize_seedance_json_bundle_with_openai(
    *,
    request: VideoPromptRequest,
    reference_contexts: list[ReferenceContext],
    director_requirements_text: str,
    scenario_variant: ScenarioVariantSpec | None = None,
    model: str | None = None,
) -> GeneratedSeedanceJsonBundle:
    seedance_json_en = synthesize_seedance_json_prompt_with_openai(
        request=request,
        reference_contexts=reference_contexts,
        director_requirements_text=director_requirements_text,
        scenario_variant=scenario_variant,
        model=model,
    )
    seedance_json_ru = synthesize_seedance_control_json_prompt_with_openai(
        seedance_json_en=seedance_json_en,
        request=request,
        model=model,
    )
    return GeneratedSeedanceJsonBundle(
        seedance_prompt_json_en=seedance_json_en,
        seedance_prompt_json_ru=seedance_json_ru,
    )


def synthesize_seedance_control_json_prompt_with_openai(
    *,
    seedance_json_en: str,
    request: VideoPromptRequest,
    model: str | None = None,
) -> str:
    client = _get_client()
    payload = _extract_json_array(seedance_json_en)
    response = client.responses.create(
        model=model or DEFAULT_VIDEO_PROMPT_MODEL,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SEEDANCE_CONTROL_RU_SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": _seedance_control_ru_prompt(payload),
                    }
                ],
            },
        ],
    )
    seedance_json_ru = _normalize_seedance_control_json_text(response.output_text)
    validation_errors = _validate_seedance_control_json_prompt(seedance_json_ru, request)
    if validation_errors:
        repair_response = client.responses.create(
            model=model or DEFAULT_VIDEO_PROMPT_MODEL,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": SEEDANCE_CONTROL_RU_SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _seedance_control_ru_repair_prompt(
                                payload,
                                seedance_json_ru,
                                validation_errors,
                            ),
                        }
                    ],
                },
            ],
        )
        seedance_json_ru = _normalize_seedance_control_json_text(repair_response.output_text)
        validation_errors = _validate_seedance_control_json_prompt(seedance_json_ru, request)
        if validation_errors:
            raise ValueError(
                "Seedance RU control JSON composer could not satisfy the required output format: "
                + "; ".join(validation_errors)
            )
    return seedance_json_ru


def _composer_prompt(payload: dict[str, object]) -> str:
    schema = '{"video_prompt_en": string, "video_prompt_ru": string}'
    return (
        "Create JSON with schema "
        f"{schema}. "
        "Hard requirements:\n"
        "1. video_prompt_en must be fully in English.\n"
        "2. video_prompt_ru must be a faithful Russian translation of the same prompt.\n"
        "3. Use explicit Shot N: labels in both prompts, one label per scene, in the same order as the input scenes.\n"
        "4. No other metadata headers. Do not write Theme:, Style:, Beat:, or similar headings.\n"
        "5. Include the total video duration, each scene duration, and the 16:9 aspect ratio inline inside the prompt body.\n"
        "6. Present tense, active voice. Vivid but economical. No poetic padding.\n"
        "7. Keep character names consistent. If the brief names Slava, keep Slava. Unnamed figures should stay functional.\n"
        "8. No dialogue or subtitles unless explicitly requested.\n"
        "9. Every mention of a character or subject derived from a reference image must include its @image tag inline every time it appears.\n"
        "10. Use the regeneration_assets reference context to ground age, clothing, environment, action, background, mood, and identity cues.\n"
        "11. Favor the strongest cinematic framing, but avoid significant facial enlargement, facial distortion, or risky close framing.\n"
        f"12. Keep each prompt at or under {payload['max_prompt_chars']} characters.\n"
        "13. Write concrete direction suitable for direct video generation.\n"
        "14. Do not output markdown fences or any prose outside JSON.\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _seedance_composer_prompt(payload: dict[str, object], director_requirements_text: str) -> str:
    return (
        "Follow the Seedance 2.0 director specification exactly. "
        "Return ONLY a JSON array with one object: "
        '[{"lang":"en","prompt":"..."}]. '
        "This input is a multi-scene timed brief, so use STRUCTURED mode with explicit Shot N: labels, "
        "one shot per input scene in the same order. "
        f"Keep the final prompt at or under {payload['max_prompt_chars']} characters. "
        "Variant rule: Variant 1 must be the most likely, most suitable, most coherent cinematic interpretation. "
        "Variant 2 must be a clearly alternative interpretation that is fully distinct in visual logic, camera plan, "
        "transitions, and dramatic emphasis while preserving the same scene order, durations, and core story facts. "
        "Include the exact Seedance technical preamble core string below once at the start of the prompt, "
        "then append the specific fishing/location context.\n"
        f"Required technical preamble core:\n{SEEDANCE_TECHNICAL_PREAMBLE_CORE}\n\n"
        "Director specification:\n"
        f"{director_requirements_text}\n\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _seedance_control_ru_prompt(seedance_payload_en: list[object]) -> str:
    return (
        "Translate the English Seedance JSON below into a Russian control JSON for human review only. "
        "Return ONLY a JSON array with one object: "
        '[{"lang":"ru","prompt":"..."}]. '
        "Preserve every @image tag exactly. Preserve every Shot N: label exactly in English. "
        "Preserve the Total: footer exactly in English. Preserve timings, numbers, aspect ratio, and scene order. "
        "Translate the prose into natural Russian, but do not translate @image tags, Shot labels, or Total:. "
        "Do not add or remove scenes. Do not output markdown fences.\n"
        "Input JSON:\n"
        f"{json.dumps(seedance_payload_en, ensure_ascii=False, indent=2)}"
    )


def _repair_prompt(
    payload: dict[str, object],
    bundle: GeneratedVideoPromptBundle,
    validation_errors: list[str],
) -> str:
    schema = '{"video_prompt_en": string, "video_prompt_ru": string}'
    current_bundle = {
        "video_prompt_en": bundle.video_prompt_en,
        "video_prompt_ru": bundle.video_prompt_ru,
    }
    return (
        "Repair the JSON prompt bundle below and return JSON only with schema "
        f"{schema}. "
        "Fix every listed issue while keeping the same story, scene order, reference tags, durations, and aspect ratio.\n"
        "Issues:\n"
        f"{json.dumps(validation_errors, ensure_ascii=False, indent=2)}\n"
        "Original input:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "Current bundle:\n"
        f"{json.dumps(current_bundle, ensure_ascii=False, indent=2)}"
    )


def _seedance_repair_prompt(
    payload: dict[str, object],
    director_requirements_text: str,
    current_seedance_json_text: str,
    validation_errors: list[str],
) -> str:
    return (
        "Repair the Seedance JSON output below. Return ONLY a JSON array with one object "
        '[{"lang":"en","prompt":"..."}]. '
        "Fix every listed issue while preserving the same story, scene order, durations, aspect ratio, "
        "and @image tags.\n"
        "Issues:\n"
        f"{json.dumps(validation_errors, ensure_ascii=False, indent=2)}\n"
        "Required technical preamble core:\n"
        f"{SEEDANCE_TECHNICAL_PREAMBLE_CORE}\n\n"
        "Director specification:\n"
        f"{director_requirements_text}\n\n"
        "Original input:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "Current output:\n"
        f"{current_seedance_json_text}"
    )


def _seedance_control_ru_repair_prompt(
    seedance_payload_en: list[object],
    current_seedance_json_ru: str,
    validation_errors: list[str],
) -> str:
    return (
        "Repair the Russian control JSON below. Return ONLY a JSON array with one object "
        '[{"lang":"ru","prompt":"..."}]. '
        "Preserve every @image tag, every Shot N: label, and the Total: footer exactly. "
        "Fix every listed issue without changing scene order.\n"
        "Issues:\n"
        f"{json.dumps(validation_errors, ensure_ascii=False, indent=2)}\n"
        "Original English JSON:\n"
        f"{json.dumps(seedance_payload_en, ensure_ascii=False, indent=2)}\n"
        "Current Russian JSON:\n"
        f"{current_seedance_json_ru}"
    )


def _parse_generated_prompt_bundle(raw_text: str) -> GeneratedVideoPromptBundle:
    payload = _extract_json_object(raw_text)
    video_prompt_en = str(payload.get("video_prompt_en", "")).strip()
    video_prompt_ru = str(payload.get("video_prompt_ru", "")).strip()
    if not video_prompt_en or not video_prompt_ru:
        raise ValueError("Video prompt composer returned an empty prompt.")
    return GeneratedVideoPromptBundle(
        video_prompt_en=video_prompt_en,
        video_prompt_ru=video_prompt_ru,
    )


def _validate_seedance_json_prompt(seedance_json_text: str, request: VideoPromptRequest) -> list[str]:
    errors: list[str] = []
    if "```" in seedance_json_text:
        errors.append("Seedance JSON output contains markdown fences.")
    try:
        payload = _extract_json_array(seedance_json_text)
    except ValueError as exc:
        return [str(exc)]

    if len(payload) != 1:
        errors.append(f"Seedance JSON output must contain exactly one object, got {len(payload)}.")
        return errors

    item = payload[0]
    if not isinstance(item, dict):
        errors.append("Seedance JSON array item must be an object.")
        return errors

    if str(item.get("lang", "")).strip() != "en":
        errors.append("Seedance JSON object must set lang to 'en'.")

    prompt_text = str(item.get("prompt", "")).strip()
    if not prompt_text:
        errors.append("Seedance JSON object is missing a non-empty prompt.")
        return errors

    if len(prompt_text) > request.max_prompt_chars:
        errors.append(
            f"Seedance prompt exceeds request max_prompt_chars {request.max_prompt_chars} ({len(prompt_text)})."
        )
    if SEEDANCE_TECHNICAL_PREAMBLE_CORE not in prompt_text:
        errors.append("Seedance prompt is missing the required technical preamble core.")
    for scene in request.scenes:
        if f"Shot {scene.index}:" not in prompt_text:
            errors.append(f"Seedance prompt is missing 'Shot {scene.index}:'.")
    total_marker = f"Total: {_format_seconds(request.total_duration_seconds)}s /"
    if total_marker not in prompt_text:
        errors.append(f"Seedance prompt is missing total footer marker '{total_marker}'.")
    if request.aspect_ratio not in prompt_text:
        errors.append(f"Seedance prompt is missing aspect ratio {request.aspect_ratio}.")
    for tag in used_image_tags(request):
        if tag not in prompt_text:
            errors.append(f"Seedance prompt is missing required image tag {tag}.")
    return errors


def _validate_seedance_control_json_prompt(seedance_json_text: str, request: VideoPromptRequest) -> list[str]:
    errors: list[str] = []
    if "```" in seedance_json_text:
        errors.append("Seedance RU control JSON output contains markdown fences.")
    try:
        payload = _extract_json_array(seedance_json_text)
    except ValueError as exc:
        return [str(exc)]

    if len(payload) != 1:
        errors.append(f"Seedance RU control JSON output must contain exactly one object, got {len(payload)}.")
        return errors

    item = payload[0]
    if not isinstance(item, dict):
        errors.append("Seedance RU control JSON array item must be an object.")
        return errors

    if str(item.get("lang", "")).strip() != "ru":
        errors.append("Seedance RU control JSON object must set lang to 'ru'.")

    prompt_text = str(item.get("prompt", "")).strip()
    if not prompt_text:
        errors.append("Seedance RU control JSON object is missing a non-empty prompt.")
        return errors

    for scene in request.scenes:
        if f"Shot {scene.index}:" not in prompt_text:
            errors.append(f"Seedance RU control prompt is missing 'Shot {scene.index}:'.")
    total_marker = f"Total: {_format_seconds(request.total_duration_seconds)}s /"
    if total_marker not in prompt_text:
        errors.append(f"Seedance RU control prompt is missing total footer marker '{total_marker}'.")
    if request.aspect_ratio not in prompt_text:
        errors.append(f"Seedance RU control prompt is missing aspect ratio {request.aspect_ratio}.")
    for tag in used_image_tags(request):
        if tag not in prompt_text:
            errors.append(f"Seedance RU control prompt is missing required image tag {tag}.")
    return errors


def _validate_generated_prompt_bundle(
    bundle: GeneratedVideoPromptBundle,
    request: VideoPromptRequest,
) -> list[str]:
    errors: list[str] = []
    used_tags = used_image_tags(request)
    for label, prompt_text in (("video_prompt_en", bundle.video_prompt_en), ("video_prompt_ru", bundle.video_prompt_ru)):
        if len(prompt_text) > request.max_prompt_chars:
            errors.append(
                f"{label} exceeds request max_prompt_chars {request.max_prompt_chars} ({len(prompt_text)})."
            )
        for scene in request.scenes:
            if f"Shot {scene.index}:" not in prompt_text:
                errors.append(f"{label} is missing 'Shot {scene.index}:'.")
        if request.aspect_ratio not in prompt_text:
            errors.append(f"{label} is missing aspect ratio {request.aspect_ratio}.")
        total_marker = f"{_format_seconds(request.total_duration_seconds)}s total"
        if total_marker not in prompt_text:
            errors.append(f"{label} is missing total duration marker '{total_marker}'.")
        for tag in used_tags:
            if tag not in prompt_text:
                errors.append(f"{label} is missing required image tag {tag}.")
    return errors


def _normalize_generated_prompt_bundle(
    bundle: GeneratedVideoPromptBundle,
    *,
    request: VideoPromptRequest,
) -> GeneratedVideoPromptBundle:
    return GeneratedVideoPromptBundle(
        video_prompt_en=_normalize_prompt_text(bundle.video_prompt_en, request=request),
        video_prompt_ru=_normalize_prompt_text(bundle.video_prompt_ru, request=request),
    )


def _normalize_seedance_json_text(raw_text: str) -> str:
    payload = _extract_json_array(raw_text)
    if len(payload) == 1 and isinstance(payload[0], dict):
        payload[0]["lang"] = str(payload[0].get("lang", "")).strip()
        payload[0]["prompt"] = _sanitize_prompt_text(str(payload[0].get("prompt", "")).strip())
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _normalize_seedance_control_json_text(raw_text: str) -> str:
    payload = _extract_json_array(raw_text)
    if len(payload) == 1 and isinstance(payload[0], dict):
        payload[0]["lang"] = str(payload[0].get("lang", "")).strip()
        payload[0]["prompt"] = _sanitize_control_prompt_text(str(payload[0].get("prompt", "")).strip())
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _normalize_prompt_text(prompt_text: str, *, request: VideoPromptRequest) -> str:
    text = prompt_text.replace("\r\n", "\n").strip()
    for scene in request.scenes:
        text = re.sub(rf"\bКадр\s+{scene.index}\s*:", f"Shot {scene.index}:", text)
    normalized_lines: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for scene in request.scenes:
        matching_line = next((line for line in lines if line.startswith(f"Shot {scene.index}:")), None)
        if matching_line is None:
            continue
        body = matching_line.split(":", 1)[1].strip()
        body = re.sub(r"^\([^)]*\)\s*", "", body)
        timing = (
            f"Shot {scene.index}: "
            f"({_format_seconds(scene.start_seconds)}-{_format_seconds(scene.end_seconds)}s / "
            f"{_format_seconds(scene.duration_seconds)}s, "
            f"{_format_seconds(request.total_duration_seconds)}s total, "
            f"{request.aspect_ratio}) "
        )
        normalized_lines.append(f"{timing}{body}")
    if normalized_lines:
        return "\n\n".join(normalized_lines)
    return text


def _get_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("Install openai>=1.0 to use the video prompt composer.")
    return OpenAI()


def _extract_json_array(raw_text: str) -> list[object]:
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
        if text[start] != "[":
            continue
        try:
            payload, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            return payload
    raise ValueError("Seedance prompt composer response does not contain a valid JSON array.")


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
    raise ValueError("Video prompt composer response does not contain a valid JSON object.")


def _format_seconds(value: float) -> str:
    rounded = int(value)
    if abs(value - rounded) < 1e-9:
        return str(rounded)
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _sanitize_prompt_text(text: str) -> str:
    sanitized = text.translate(ASCII_PUNCT_TRANSLATION)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized


def _sanitize_control_prompt_text(text: str) -> str:
    sanitized = text.translate(ASCII_PUNCT_TRANSLATION)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized
