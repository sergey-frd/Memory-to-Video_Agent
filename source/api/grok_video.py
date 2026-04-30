from __future__ import annotations

import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

try:
    from xai_sdk import Client
except ModuleNotFoundError:
    Client = None  # type: ignore[assignment]


class GrokVideoError(RuntimeError):
    pass


@dataclass
class GrokVideoRequest:
    prompt: str
    image_path: Path
    output_path: Path
    model: str = "grok-video"
    duration_seconds: int | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    poll_interval_seconds: float = 3.0
    timeout_seconds: float = 600.0


def _require_client() -> Client:
    if Client is None:
        raise GrokVideoError("xai-sdk is not installed. Run 'pip install -r requirements.txt'.")
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise GrokVideoError("XAI_API_KEY is not set.")
    return Client(api_key=api_key)


def _image_to_data_url(image_path: Path) -> str:
    if not image_path.exists():
        raise FileNotFoundError(f"Input image was not found: {image_path}")
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type:
        mime_type = "image/png"
    image_bytes = image_path.read_bytes()
    import base64

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _download_file(url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url) as response:
        output_path.write_bytes(response.read())
    return output_path


def generate_video_with_grok(request: GrokVideoRequest) -> Path:
    client = _require_client()
    image_url = _image_to_data_url(request.image_path)

    kwargs: dict[str, object] = {
        "model": request.model,
        "prompt": request.prompt,
        "image_url": image_url,
    }
    if request.duration_seconds is not None:
        kwargs["duration_seconds"] = request.duration_seconds
    if request.aspect_ratio is not None:
        kwargs["aspect_ratio"] = request.aspect_ratio
    if request.resolution is not None:
        kwargs["resolution"] = request.resolution

    job = client.videos.generations.create(**kwargs)
    job_id = getattr(job, "id", None)
    if not job_id:
        raise GrokVideoError("xAI video API did not return a job id.")

    deadline = time.time() + request.timeout_seconds
    last_status = "unknown"
    while time.time() < deadline:
        current = client.videos.generations.retrieve(job_id)
        last_status = str(getattr(current, "status", "unknown"))
        if last_status in {"completed", "succeeded"}:
            video_url = getattr(current, "url", None) or getattr(current, "video_url", None)
            if not video_url:
                raise GrokVideoError("xAI video job completed without a download URL.")
            return _download_file(str(video_url), request.output_path)
        if last_status in {"failed", "error", "cancelled"}:
            raise GrokVideoError(f"xAI video generation failed with status: {last_status}")
        time.sleep(request.poll_interval_seconds)

    raise GrokVideoError(f"xAI video generation timed out. Last status: {last_status}")
