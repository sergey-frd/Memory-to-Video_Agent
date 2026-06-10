from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from urllib.request import urlopen

try:
    from xai_sdk import Client
except ModuleNotFoundError:
    Client = None  # type: ignore[assignment]

from api.grok_video import (
    GrokVideoError,
    GrokVideoRequest,
    generate_video_with_grok,
)
from api.grok_web import GrokWebConfig, GrokWebError


DEFAULT_VIDEO_MODEL = os.getenv("XAI_VIDEO_MODEL", "grok-imagine-video")
DEFAULT_IMAGE_MODEL = os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image-quality")
DEFAULT_VIDEO_RESOLUTION = os.getenv("XAI_VIDEO_RESOLUTION", "720p")
DEFAULT_VIDEO_DURATION_SECONDS = int(os.getenv("XAI_VIDEO_DURATION_SECONDS", "10"))
DEFAULT_VIDEO_ASPECT_RATIO = os.getenv("XAI_VIDEO_ASPECT_RATIO", "")


def _log(message: str) -> None:
    print(message, flush=True)


def _image_to_data_url(image_path: Path) -> str:
    if not image_path.exists():
        raise FileNotFoundError(f"Input image was not found: {image_path}")
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _save_image_response(response: object, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    image_bytes = getattr(response, "image_bytes", None) or getattr(response, "bytes", None)
    if isinstance(image_bytes, (bytes, bytearray)):
        output_path.write_bytes(bytes(image_bytes))
        return output_path

    url_candidates: list[str] = []
    for attr in ("url", "image_url"):
        value = getattr(response, attr, None)
        if value:
            url_candidates.append(str(value))
    image_attr = getattr(response, "image", None)
    if image_attr is not None:
        for attr in ("url", "image_url"):
            value = getattr(image_attr, attr, None)
            if value:
                url_candidates.append(str(value))

    for candidate in url_candidates:
        if not candidate:
            continue
        if candidate.startswith("data:"):
            _, _, payload = candidate.partition(",")
            output_path.write_bytes(base64.b64decode(payload))
            return output_path
        with urlopen(candidate) as response_io:
            output_path.write_bytes(response_io.read())
        return output_path

    raise GrokVideoError("xAI image API response did not contain image bytes or a URL.")


def _generate_image_via_api(config: GrokWebConfig) -> Path:
    if Client is None:
        raise GrokVideoError(
            "xai-sdk is not installed. Run 'pip install -U xai-sdk' or update requirements.txt."
        )
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise GrokVideoError("XAI_API_KEY is not set. Add it to .env or the environment.")

    timeout_seconds = max(60.0, config.result_timeout_ms / 1000.0)
    client = Client(api_key=api_key, timeout=timeout_seconds + 60.0)

    kwargs: dict[str, object] = {
        "model": DEFAULT_IMAGE_MODEL,
        "prompt": config.prompt_text,
    }
    if config.image_path is not None:
        kwargs["image_url"] = _image_to_data_url(config.image_path)
    if config.aspect_ratio:
        kwargs["aspect_ratio"] = config.aspect_ratio

    try:
        response = client.image.sample(**kwargs)
    except Exception as exc:  # noqa: BLE001 - re-raise wrapped
        raise GrokVideoError(f"xAI image generation failed: {exc}") from exc

    return _save_image_response(response, config.output_path)


def grok_video_api_runner(config: GrokWebConfig) -> Path:
    """AgentRunner-compatible adapter that calls the xAI Imagine API instead of the Grok web UI.

    Honors the same GrokWebConfig fields used by the browser-based runner:
    prompt_text, image_path, output_path, generation_mode ("video"/"image"),
    aspect_ratio, result_timeout_ms, submit.
    """

    if not config.submit:
        _log(f"[xAI API] --no-submit set, skipping actual generation for {config.output_path.name}")
        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        return config.output_path

    mode = (config.generation_mode or "video").strip().lower()
    if mode == "image":
        _log(f"[xAI API] image -> {config.output_path.name}")
        try:
            return _generate_image_via_api(config)
        except GrokVideoError as exc:
            raise GrokWebError(str(exc)) from exc

    timeout_seconds = max(60.0, config.result_timeout_ms / 1000.0)
    aspect_ratio = config.aspect_ratio or DEFAULT_VIDEO_ASPECT_RATIO or None
    duration_seconds = config.duration_seconds if config.duration_seconds else DEFAULT_VIDEO_DURATION_SECONDS
    _log(
        "[xAI API] video -> {name} | model={model} dur={dur}s ratio={ratio} res={res}".format(
            name=config.output_path.name,
            model=DEFAULT_VIDEO_MODEL,
            dur=duration_seconds,
            ratio=aspect_ratio or "auto",
            res=DEFAULT_VIDEO_RESOLUTION,
        )
    )
    request = GrokVideoRequest(
        prompt=config.prompt_text,
        image_path=config.image_path,
        output_path=config.output_path,
        model=DEFAULT_VIDEO_MODEL,
        duration_seconds=duration_seconds,
        aspect_ratio=aspect_ratio,
        resolution=DEFAULT_VIDEO_RESOLUTION,
        timeout_seconds=timeout_seconds,
    )
    try:
        return generate_video_with_grok(request)
    except GrokVideoError as exc:
        raise GrokWebError(str(exc)) from exc


class GrokVideoAPISessionRunner:
    """Mimics GrokWebSessionRunner interface but routes through the xAI API.

    Stateless from xAI's side, but keeps the same shape so it can be a drop-in
    replacement for GrokWebSessionRunner in main_full_pipeline.py and
    main_grok_batch.py.
    """

    def run(self, config: GrokWebConfig) -> Path:
        return grok_video_api_runner(config)

    def close_stage_session(self) -> None:
        return None

    def close(self) -> None:
        return None
