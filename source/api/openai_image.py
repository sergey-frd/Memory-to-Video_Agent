from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

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
PRESERVE_ASPECT_RATIO = True
GEOMETRY_POLICY = "exif-normalized-reference_native-output-v2"


def _get_client() -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        if OpenAI is None:
            raise RuntimeError("Install openai>=1.0 to use OpenAI Images.")
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is not set.")
        _CLIENT = OpenAI()
    return _CLIENT


def _prepare_uploadable(image_path: Path, model_name: str) -> tuple[Path, bool]:
    work_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".png").name)
    if model_name == "dall-e-2":
        _save_dalle2_uploadable_png(image_path, work_path)
    else:
        _save_compressed_png(image_path, work_path)
    if work_path.stat().st_size > MAX_IMAGE_BYTES:
        raise RuntimeError("Could not reduce image below 4 MB.")
    return work_path, True


def _save_compressed_png(source: Path, destination: Path) -> None:
    with Image.open(source) as img:
        base = ImageOps.exif_transpose(img).convert("RGBA")
        factor = 1.0
        while True:
            target_size = (max(1, round(base.width * factor)), max(1, round(base.height * factor)))
            resized = base if factor == 1.0 else base.resize(target_size, Image.LANCZOS)
            resized.save(destination, format="PNG", optimize=True, compress_level=9)
            if destination.stat().st_size <= MAX_IMAGE_BYTES or factor <= 0.1:
                break
            factor -= 0.05
        if destination.stat().st_size > MAX_IMAGE_BYTES:
            raise RuntimeError("Could not reduce image below 4 MB.")


def _save_dalle2_uploadable_png(source: Path, destination: Path) -> None:
    with Image.open(source) as img:
        base = ImageOps.exif_transpose(img).convert("RGBA")
        side = min(max(base.width, base.height), 1024)
        factor = 1.0
        while True:
            target_side = max(256, int(side * factor))
            squared = ImageOps.pad(
                base,
                (target_side, target_side),
                method=Image.LANCZOS,
                color=(255, 255, 255, 255),
                centering=(0.5, 0.5),
            )
            squared.save(destination, format="PNG", optimize=True, compress_level=9)
            if destination.stat().st_size <= MAX_IMAGE_BYTES or target_side <= 256:
                break
            factor -= 0.05
        if destination.stat().st_size > MAX_IMAGE_BYTES:
            raise RuntimeError("Could not reduce DALL-E 2 image below 4 MB.")


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
    model_name: Optional[str] = None,
) -> Path:
    """Call OpenAI Images edit API to create a transformed frame."""
    prompt_text = prompt_override or _default_prompt(style=style, metadata=metadata, stage_id=stage_id)
    prompt_text = _fit_prompt_length(prompt_text)
    resolved_model_name = model_name or _model_name()
    _validate_model_name(resolved_model_name)
    if output_path.exists():
        raise FileExistsError(f"Preserve existing ART; use a separate version: {output_path}")
    raw_path = output_path.with_suffix('.response.png')
    if raw_path.exists():
        raise FileExistsError(f"Saved API response exists; recover it without another paid request: {raw_path}")
    client = _get_client()
    upload_path, temp_used = _prepare_uploadable(image_path, resolved_model_name)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        reference_path = output_path.with_suffix('.reference.png')
        shutil.copyfile(upload_path, reference_path)
        with open(upload_path, "rb") as source_file:
            request_kwargs: dict[str, object] = {
                "model": resolved_model_name,
                "prompt": prompt_text,
                "image": source_file,
            }
            if resolved_model_name == "dall-e-2":
                request_kwargs["response_format"] = "b64_json"
                request_kwargs["size"] = "1024x1024"
            else:
                request_kwargs["output_format"] = "png"
                request_kwargs["size"] = "auto"
            response = client.images.edit(**request_kwargs)
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
    # Preserve the actual backend response before any encoding/normalization.
    with raw_path.open('xb') as f:
        f.write(decoded)
    with Image.open(BytesIO(decoded)) as edited:
        response_size = edited.size
        final = ImageOps.exif_transpose(edited).convert("RGBA")
        # A different artistic canvas is valid. Never squeeze it to source dimensions.
        with output_path.open('xb') as f:
            final.save(f, format="PNG")
        final_size = final.size
    def digest(path):
        with Path(path).open('rb') as f:
            return hashlib.file_digest(f, 'sha256').hexdigest()
    with Image.open(image_path) as original:
        raw_source_size = original.size
        orientation = original.getexif().get(274, 1)
        source_size = ImageOps.exif_transpose(original).size
    with Image.open(reference_path) as reference:
        reference_size = reference.size
    geometry = dict(policy=GEOMETRY_POLICY, preserve_aspect_ratio=PRESERVE_ASPECT_RATIO,
        timestamp=datetime.now(timezone.utc).isoformat(), model=resolved_model_name,
        source_path=str(image_path), source_sha256=digest(image_path),
        source_raw_size=raw_source_size, source_exif_orientation=orientation,
        source_visual_size=source_size, reference_path=str(reference_path),
        reference_size=reference_size, reference_sha256=digest(reference_path),
        requested_size=request_kwargs.get('size'), raw_response_path=str(raw_path),
        raw_response_size=response_size, raw_response_sha256=digest(raw_path),
        saved_size=final_size, output_sha256=digest(output_path), postprocess_resize=False,
        prompt=prompt_text)
    output_path.with_suffix('.geometry.json').write_text(json.dumps(geometry, ensure_ascii=False, indent=2), encoding='utf-8')
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
