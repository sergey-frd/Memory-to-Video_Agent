from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.request import urlopen

try:
    from xai_sdk import Client
except ModuleNotFoundError:
    Client = None  # type: ignore[assignment]

try:
    from xai_sdk.video import VideoGenerationError  # type: ignore[import-not-found]
except (ModuleNotFoundError, ImportError):
    class VideoGenerationError(Exception):
        code: str = ""
        message: str = ""


class GrokVideoError(RuntimeError):
    pass


@dataclass
class GrokVideoRequest:
    prompt: str
    image_path: Path | None
    output_path: Path
    model: str = "grok-imagine-video"
    duration_seconds: int | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 600.0


def _require_client(timeout_seconds: float) -> Client:
    if Client is None:
        raise GrokVideoError(
            "xai-sdk is not installed. Run 'pip install -U xai-sdk' or update requirements.txt."
        )
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise GrokVideoError(
            "XAI_API_KEY is not set. Add it to .env or the environment."
        )
    return Client(api_key=api_key, timeout=max(60.0, float(timeout_seconds) + 60.0))


def _image_to_data_url(image_path: Path) -> str:
    if not image_path.exists():
        raise FileNotFoundError(f"Input image was not found: {image_path}")
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _download_file(url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url) as response:
        output_path.write_bytes(response.read())
    return output_path


def _extract_video_url(response: object) -> str | None:
    candidates: list[object] = []
    for attr in ("url", "video_url"):
        value = getattr(response, attr, None)
        if value:
            candidates.append(value)
    nested = getattr(response, "video", None)
    if nested is not None:
        for attr in ("url", "video_url"):
            value = getattr(nested, attr, None)
            if value:
                candidates.append(value)
    for candidate in candidates:
        text = str(candidate).strip()
        if text:
            return text
    return None


def generate_video_with_grok(request: GrokVideoRequest) -> Path:
    client = _require_client(request.timeout_seconds)

    kwargs: dict[str, object] = {
        "model": request.model,
        "prompt": request.prompt,
    }
    if request.image_path is not None:
        kwargs["image_url"] = _image_to_data_url(request.image_path)
    if request.duration_seconds is not None:
        kwargs["duration"] = int(request.duration_seconds)
    if request.aspect_ratio is not None:
        kwargs["aspect_ratio"] = request.aspect_ratio
    if request.resolution is not None:
        kwargs["resolution"] = request.resolution
    kwargs["timeout"] = timedelta(seconds=max(60.0, float(request.timeout_seconds)))
    kwargs["interval"] = timedelta(seconds=max(1.0, float(request.poll_interval_seconds)))

    try:
        response = client.video.generate(**kwargs)
    except VideoGenerationError as exc:
        code = getattr(exc, "code", "") or ""
        message = getattr(exc, "message", "") or str(exc)
        raise GrokVideoError(f"xAI video generation failed [{code}]: {message}") from exc
    except TimeoutError as exc:
        raise GrokVideoError(
            f"xAI video generation timed out after {request.timeout_seconds}s"
        ) from exc

    video_url = _extract_video_url(response)
    if not video_url:
        raise GrokVideoError("xAI video API did not return a download URL.")
    return _download_file(video_url, request.output_path)
