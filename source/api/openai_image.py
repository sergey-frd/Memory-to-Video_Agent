from __future__ import annotations

import base64
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

try:
    from openai import OpenAI, OpenAIError
except ModuleNotFoundError:
    OpenAI = None  # type: ignore[var-annotated]
    OpenAIError = Exception

from utils.image_analysis import ImageMetadata
from utils.prompt_builder import BASE_STYLE_GUIDELINES

_CLIENT: OpenAI | None = None
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_PROMPT_CHARS = 1000
ModelName = str
SUPPORTED_EDIT_MODELS = {"dall-e-2", "gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5"}


def _get_client() -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        if OpenAI is None:
            raise RuntimeError("Install openai>=1.0 to use OpenAI Images.")
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is not set.")
        _CLIENT = OpenAI()
    return _CLIENT


def _prepare_uploadable(image_path: Path) -> tuple[Path, bool]:
    work_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".png").name)
    _save_compressed_png(image_path, work_path)
    if work_path.stat().st_size > MAX_IMAGE_BYTES:
        raise RuntimeError("Could not reduce image below 4 MB.")
    return work_path, True


def _save_compressed_png(source: Path, destination: Path) -> None:
    with Image.open(source) as img:
        base = img.convert("RGBA")
        factor = 1.0
        while True:
            target_size = (int(base.width * factor), int(base.height * factor))
            resized = base if factor == 1.0 else base.resize(target_size, Image.LANCZOS)
            resized.save(destination, format="PNG", optimize=True, compress_level=9)
            if destination.stat().st_size <= MAX_IMAGE_BYTES or factor <= 0.1:
                break
            factor -= 0.05
        if destination.stat().st_size > MAX_IMAGE_BYTES:
            raise RuntimeError("Could not reduce image below 4 MB.")


def _model_name() -> ModelName:
    return os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")


def _validate_model_name(model_name: str) -> None:
    if model_name not in SUPPORTED_EDIT_MODELS:
        allowed = ", ".join(sorted(SUPPORTED_EDIT_MODELS))
        raise ValueError(f"Model '{model_name}' is not supported. Allowed values: {allowed}.")


def edit_image_with_openai(
    image_path: Path,
    style: str,
    output_path: Path,
    metadata: ImageMetadata,
    stage_id: str,
    prompt_override: Optional[str] = None,
) -> Path:
    """Call OpenAI Images edit API to create a transformed frame."""
    prompt_text = prompt_override or _default_prompt(style=style, metadata=metadata, stage_id=stage_id)
    prompt_text = _fit_prompt_length(prompt_text)
    model_name = _model_name()
    _validate_model_name(model_name)
    client = _get_client()
    upload_path, temp_used = _prepare_uploadable(image_path)
    try:
        with open(upload_path, "rb") as source_file:
            response = client.images.edit(
                model=model_name,
                prompt=prompt_text,
                image=source_file,
                response_format="b64_json",
            )
    except OpenAIError as exc:
        raise RuntimeError("OpenAI Images API request failed.") from exc
    finally:
        if temp_used and upload_path.exists():
            try:
                upload_path.unlink()
            except OSError:
                pass

    raw_data = response.data[0].b64_json
    decoded = base64.b64decode(raw_data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(BytesIO(decoded)) as edited:
        final = edited.convert("RGBA")
        desired_size = (metadata.width, metadata.height)
        if final.size != desired_size:
            final = final.resize(desired_size, Image.LANCZOS)
        final.save(output_path, format="PNG")
    return output_path


def _fit_prompt_length(prompt_text: str) -> str:
    normalized_lines = [" ".join(line.split()) for line in prompt_text.splitlines() if line.strip()]
    compact = "\n".join(normalized_lines)
    if len(compact) <= MAX_PROMPT_CHARS:
        return compact
    return compact[: MAX_PROMPT_CHARS - 3].rstrip() + "..."


def _default_prompt(*, style: str, metadata: ImageMetadata, stage_id: str) -> str:
    return "\n".join(
        [
            f"Stage: {stage_id}",
            f"Style: {style}",
            f"Format: {metadata.format_description}",
            BASE_STYLE_GUIDELINES,
            "Scenario: stable identity, subtle motion cues only, maximum realism, cinematic visual treatment.",
            "Goal: produce the next resolved frame for the pipeline while preserving aspect ratio and subject identity.",
        ]
    )


def generate_video_from_prompt(stage_id: str, prompt_text: str, input_frame: Path, output_video: Path) -> Path:
    """TODO_STUB: generate_video_from_prompt

    Description: Placeholder for a future video generator that turns a frame and prompt into a video.
    Parameters:
      - stage_id: stage identifier for tracing
      - prompt_text: generated prompt text
      - input_frame: source frame path
      - output_video: target video path
    Expected result:
      - returns a path to a video file matching the prompt description
    Temporary implementation:
      - only ensures the output directory exists and returns the target path
    """
    output_video.parent.mkdir(parents=True, exist_ok=True)
    return output_video
