from __future__ import annotations

import json
import os
from typing import Iterable

from config import VideoFramingMode
from models.scene_analysis import SceneAnalysis
from utils.image_analysis import ImageMetadata
from utils.prompt_builder import BackgroundPromptBundle, PromptBundle

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - runtime guard
    OpenAI = None  # type: ignore[assignment]


DEFAULT_PROMPT_MODEL = os.getenv("OPENAI_PROMPT_MODEL", "gpt-4.1-mini")


SYSTEM_PROMPT = (
    "You write cinematic prompts with strong scene specificity. "
    "Return valid JSON only. "
    "Generate one English video prompt and one Russian video prompt for the same shot. "
    "The two prompts must describe the same scene, the same continuity constraints, and the same camera plan. "
    "They must not collapse into generic boilerplate."
)

BACKGROUND_SYSTEM_PROMPT = (
    "You write production-ready background extension prompts for image generation. "
    "Return valid JSON only. "
    "The prompt must be scene-specific and suitable for generating an ideal background image from the provided source frame."
)


def synthesize_prompt_bundle_with_openai(
    *,
    metadata: ImageMetadata,
    scene_analysis: SceneAnalysis,
    stage_id: str,
    prompt_index: int,
    total_videos: int,
    initial_frame_description: str,
    motion_sequence: Iterable[str],
    framing_mode: VideoFramingMode = VideoFramingMode.IDENTITY_SAFE,
    hide_phone_in_selfie: bool = True,
    prefer_loving_kindness_tone: bool = False,
    model: str | None = None,
) -> PromptBundle:
    client = _get_client()
    payload = {
        "stage_id": stage_id,
        "prompt_index": prompt_index,
        "total_videos": total_videos,
        "initial_frame_description": initial_frame_description,
        "format_description": metadata.format_description,
        "scene_summary": metadata.scene_summary,
        "composition_label": metadata.composition_label,
        "brightness_label": metadata.brightness_label,
        "contrast_label": metadata.contrast_label,
        "palette_label": metadata.palette_label,
        "depth_label": metadata.depth_label,
        "atmosphere_label": metadata.atmosphere_label,
        "scene_analysis": scene_analysis.to_dict(),
        "motion_sequence": list(motion_sequence),
        "framing_mode": framing_mode.value,
        "hide_phone_in_selfie": hide_phone_in_selfie,
        "prefer_loving_kindness_tone": prefer_loving_kindness_tone,
    }
    response = client.responses.create(
        model=model or DEFAULT_PROMPT_MODEL,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": _synthesizer_prompt(payload, framing_mode=framing_mode)}],
            },
        ],
    )
    return _parse_prompt_bundle_response(response.output_text)


def synthesize_background_prompt_with_openai(
    *,
    metadata: ImageMetadata,
    scene_analysis: SceneAnalysis,
    stage_id: str,
    motion_sequence: Iterable[str] | None = None,
    prefer_loving_kindness_tone: bool = False,
    model: str | None = None,
) -> BackgroundPromptBundle:
    client = _get_client()
    payload = {
        "stage_id": stage_id,
        "format_description": metadata.format_description,
        "scene_summary": metadata.scene_summary,
        "composition_label": metadata.composition_label,
        "brightness_label": metadata.brightness_label,
        "contrast_label": metadata.contrast_label,
        "palette_label": metadata.palette_label,
        "depth_label": metadata.depth_label,
        "atmosphere_label": metadata.atmosphere_label,
        "scene_analysis": scene_analysis.to_dict(),
        "motion_sequence": list(motion_sequence or []),
        "prefer_loving_kindness_tone": prefer_loving_kindness_tone,
    }
    response = client.responses.create(
        model=model or DEFAULT_PROMPT_MODEL,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": BACKGROUND_SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": _background_prompt_request(payload)}],
            },
        ],
    )
    data = _extract_json_object(response.output_text)
    prompt = str(data.get("background_prompt", "")).strip()
    prompt_ru = str(data.get("background_prompt_ru", "")).strip()
    association_prompt = str(data.get("association_prompt", "")).strip()
    association_prompt_ru = str(data.get("association_prompt_ru", "")).strip()
    if not prompt or not prompt_ru or not association_prompt or not association_prompt_ru:
        raise ValueError("Background prompt synthesizer returned an empty prompt.")
    return BackgroundPromptBundle(
        background_prompt=prompt,
        background_prompt_ru=prompt_ru,
        association_prompt=association_prompt,
        association_prompt_ru=association_prompt_ru,
    )


def _synthesizer_prompt(
    payload: dict[str, object],
    *,
    framing_mode: VideoFramingMode = VideoFramingMode.IDENTITY_SAFE,
) -> str:
    schema = (
        '{"video_prompt": string, "video_prompt_ru": string, '
        '"final_frame_prompt": string, "image_edit_prompt": string|null}'
    )
    return (
        "Using the structured frame analysis below, create JSON with schema "
        f"{schema}. "
        "Requirements:\n"
        "1. video_prompt must be fully in English.\n"
        "2. video_prompt_ru must be fully in Russian.\n"
        "3. Both video prompts must describe the same scene, characters, action, mood, background, and camera motions.\n"
        "4. The prompts must be specific to the exact scene and must noticeably differ for different frames with different people or actions.\n"
        "5. Tie the chosen camera motions to the content of the scene instead of listing them mechanically.\n"
        f"{_framing_mode_prompt_requirements(framing_mode)}"
        "9. If hide_phone_in_selfie is true and the scene analysis indicates a selfie or self-portrait, preserve the selfie-authored feel but avoid showing the phone, smartphone, phone reflection, or any visible recording device whenever plausible. Reconstruct occluded details naturally.\n"
        "10. If prefer_loving_kindness_tone is true, and only where it naturally fits the source image, gently bias the prompts toward loving-kindness, friendliness, benevolence, and warm goodwill through light, color, background, and environmental atmosphere. Keep this delicate and scene-appropriate, not sentimental or forced.\n"
        "11. final_frame_prompt must stay in Russian and describe only the resulting image and required changes relative to frame A, without describing camera motion.\n"
        "12. If prompt_index is first or last, image_edit_prompt may be null. Otherwise return a Russian prompt with the camera-motion section removed.\n"
        "13. Do not use markdown fences or any prose outside JSON.\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _framing_mode_prompt_requirements(framing_mode: VideoFramingMode) -> str:
    if framing_mode == VideoFramingMode.FACE_CLOSEUP:
        return (
            "6. If visible people or faces are present, close facial framing is allowed and may be used as the main emotional anchor of the shot.\n"
            "7. Medium close-ups, close-ups, and gentle push-ins toward the face are acceptable when they fit the source image and stay photorealistic.\n"
            "8. When emotion matters, the prompts may express it directly through the face as well as through gesture, pose, and environment.\n"
        )
    if framing_mode == VideoFramingMode.AI_OPTIMAL:
        return (
            "6. Choose the camera framing that feels most cinematic and effective for the source image, even if it includes stronger facial emphasis or a closer portrait scale.\n"
            "7. Do not optimize for avoiding facial enlargement; optimize for the strongest scene reading, emotional clarity, and cinematic impact.\n"
            "8. The prompts may stay wide, medium, or close depending on what best serves the original frame.\n"
        )
    return (
        "6. If visible people or faces are present, prefer identity-safe framing: medium-wide or distant observation, side angles, top or bottom view, overhead, crane, drone-like, or other oblique spatial camera plans.\n"
        "7. Avoid aggressive facial enlargement, tight frontal close-ups, extreme push-ins, or any camera plan that risks face distortion.\n"
        "8. When emotion matters, express it through gesture, silhouette, pose, body language, environment, and spatial relation rather than through an oversized facial close-up.\n"
    )


def _background_prompt_request(payload: dict[str, object]) -> str:
    schema = '{"background_prompt": string, "background_prompt_ru": string, "association_prompt": string, "association_prompt_ru": string}'
    return (
        "Using the structured frame analysis below, create JSON with schema "
        f"{schema}. "
        "Requirements:\n"
        "1. background_prompt must be fully in English.\n"
        "2. background_prompt_ru must be fully in Russian.\n"
        "3. The prompt must instruct an image generator to create a creative horizontal 16:9 image derived from the source frame.\n"
        "4. This is not a simple people-removal task. Do not reduce the result to an empty cleaned plate.\n"
        "5. Preserve the scene identity, emotional tone, key subjects, and narrative continuity, but allow a creative cinematic reinterpretation of the environment.\n"
        "6. Use the listed camera motions as inspiration for how the widened image should feel spatially and compositionally.\n"
        "7. Ask for a visually richer widescreen result with believable new environmental details, depth layers, and stronger storytelling.\n"
        "8. First derive a realistic associative environmental image from the structured source-image analysis: nature, architecture, vegetation, landscape, or another grounded motif that naturally matches the scene.\n"
        "9. Then build the background as a balanced fusion of that realistic associative image and a blurred, transformed echo of the source frame, so the result stays connected to frame A while becoming wider and more cinematic.\n"
        "10. Keep the associative image clearly readable and realistic. The transformed source echo must stay secondary, enlarged in scale, more blurred, softer in contrast, and balanced beneath the associative layer instead of overpowering it.\n"
        "11. If a near-copy risk remains, prefer a background-oriented result without the main visible people and reconstruct the hidden environment plausibly.\n"
        "12. If the scene risks staying too close to the source frame, explicitly require at least one strong visual transformation such as stronger background blur, brighter or darker lighting, a color-grading shift, wider spatial expansion, or a pronounced scale change.\n"
        "13. Do not invent unrelated fantasy or disaster elements. No dragons, wolves, monsters, storms, explosions, supernatural effects, or new story subjects that are absent from the source scene.\n"
        "14. Keep the background grounded in the same real-world place, using only plausible environmental reconstruction plus cinematic changes in lighting, color, blur, atmosphere, and spatial scale.\n"
        "15. If prefer_loving_kindness_tone is true, and only where it suits the frame, gently shift the light, palette, atmosphere, and environmental cues toward loving-kindness, friendliness, benevolence, and warm mercy without breaking realism or scene continuity.\n"
        "16. Additionally create association_prompt in English and association_prompt_ru in Russian: these must be detailed descriptors for generating a standalone realistic associative environmental image that can serve as the background world for this frame.\n"
        "17. The associative-image descriptor must be based on the source-image analysis and must describe a realistic nature, landscape, city, architecture, or interior scene that fits the frame.\n"
        "18. Do not return markdown fences or any prose outside JSON.\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _parse_prompt_bundle_response(raw_text: str) -> PromptBundle:
    data = _extract_json_object(raw_text)
    video_prompt = str(data.get("video_prompt", "")).strip()
    video_prompt_ru = str(data.get("video_prompt_ru", video_prompt)).strip()
    return PromptBundle(
        video_prompt=video_prompt,
        video_prompt_ru=video_prompt_ru,
        final_frame_prompt=str(data.get("final_frame_prompt", "")).strip(),
        image_edit_prompt=_normalize_nullable_string(data.get("image_edit_prompt")),
    )


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
    raise ValueError("Prompt synthesizer response does not contain a valid JSON object.")


def _normalize_nullable_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _get_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("Install openai>=1.0 to use prompt synthesis.")
    return OpenAI()
