from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def resolve_ffmpeg_executable() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "ffmpeg is not available. Install imageio-ffmpeg or add ffmpeg to PATH."
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_video_frames(
    video_path: Path,
    *,
    output_dir: Path,
    timestamps_sec: list[float],
    prefix: str = "frame",
) -> list[tuple[float, Path]]:
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if video_path.suffix.lower() == ".gif":
        # GIF frames are held for their declared duration. Seeking FFmpeg past
        # the last frame's PTS can return no image while that frame is visible.
        from PIL import Image
        frames: list[tuple[float, Path]] = []
        with Image.open(video_path) as image:
            bounds: list[tuple[float, float, int]] = []
            elapsed = 0.0
            for index in range(image.n_frames):
                image.seek(index)
                duration = image.info.get("duration")
                if duration is None or duration <= 0:
                    raise ValueError(f"Unknown GIF frame duration at frame {index}: {video_path}")
                end = elapsed + duration / 1000.0
                bounds.append((elapsed, end, index))
                elapsed = end
            for index, timestamp in enumerate(timestamps_sec):
                safe_ts = max(0.0, float(timestamp))
                selected = next((n for start, end, n in bounds if start <= safe_ts < end), None)
                if selected is None:
                    raise ValueError(f"GIF timestamp {safe_ts:.3f}s outside known duration {elapsed:.3f}s")
                image.seek(selected)
                frame_path = output_dir / f"{prefix}_{index:02d}_{safe_ts:07.2f}.jpg"
                image.convert("RGB").save(frame_path, quality=95)
                frames.append((safe_ts, frame_path))
        return frames
    ffmpeg = resolve_ffmpeg_executable()
    frames: list[tuple[float, Path]] = []
    for index, timestamp in enumerate(timestamps_sec):
        safe_ts = max(0.0, float(timestamp))
        frame_path = output_dir / f"{prefix}_{index:02d}_{safe_ts:07.2f}.jpg"
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{safe_ts:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            # MJPEG requires full-range YUV, including palette-based GIF input.
            "-pix_fmt",
            "yuvj420p",
            "-q:v",
            "3",
            str(frame_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0 or not frame_path.exists() or frame_path.stat().st_size <= 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                f"Failed to extract frame at {safe_ts:.2f}s from {video_path}. {detail}"
            )
        frames.append((safe_ts, frame_path))
    return frames


def choose_sample_timestamps(duration_seconds: float, frame_count: int) -> list[float]:
    duration_seconds = max(0.1, float(duration_seconds))
    count = max(1, int(frame_count))
    if count == 1:
        return [duration_seconds * 0.5]
    # Stay away from exact edges where decoders may fail.
    start = min(0.35, duration_seconds * 0.08)
    end = max(start + 0.1, duration_seconds - min(0.35, duration_seconds * 0.08))
    if count == 2:
        return [start, end]
    step = (end - start) / (count - 1)
    return [start + step * index for index in range(count)]
