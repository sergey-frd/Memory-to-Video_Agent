from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


DEFAULT_TRIM_SEMANTIC_MODEL = os.getenv("OPENAI_TRIM_SEMANTIC_MODEL", os.getenv("OPENAI_SCENE_MODEL", "gpt-4.1-mini"))


def choose_keep_window_with_openai(
    *,
    frame_paths: list[tuple[float, Path]],
    clip_name: str,
    duration_seconds: float,
    keep_seconds: float,
    keep_seconds_min: float,
    keep_seconds_max: float,
    media_kind: str = "video",
    context_notes: str = "",
    model: str | None = None,
    request_timeout_seconds: float = 180.0,
) -> dict[str, object]:
    if not frame_paths:
        raise ValueError("frame_paths must not be empty.")
    client = _get_client(timeout_seconds=request_timeout_seconds)
    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": _build_prompt(
                clip_name=clip_name,
                duration_seconds=duration_seconds,
                keep_seconds=keep_seconds,
                keep_seconds_min=keep_seconds_min,
                keep_seconds_max=keep_seconds_max,
                media_kind=media_kind,
                context_notes=context_notes,
                timestamps=[timestamp for timestamp, _path in frame_paths],
            ),
        }
    ]
    for timestamp, frame_path in frame_paths:
        content.append(
            {
                "type": "text",
                "text": f"Frame at t={timestamp:.2f}s inside the clip:",
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(frame_path)},
            }
        )

    response = client.chat.completions.create(
        model=model or DEFAULT_TRIM_SEMANTIC_MODEL,
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a family-video editor. Choose the shortest meaningful keep window "
                    "that still catches the point of the take. Prefer faces, interaction, gesture, "
                    "reaction, speech-like moments, ceremony, and readable emotion. Avoid empty waiting, "
                    "blurry motion, and redundant lead-in/outro. Minimize duration. Reply with JSON only."
                ),
            },
            {"role": "user", "content": content},
        ],
    )
    raw_text = (response.choices[0].message.content or "").strip()
    payload = _extract_json_object(raw_text)
    keep_duration = float(payload.get("keep_duration_sec", keep_seconds) or keep_seconds)
    keep_duration = max(keep_seconds_min, min(keep_seconds_max, keep_duration, duration_seconds))
    keep_start = float(payload.get("keep_start_sec", 0.0) or 0.0)
    reason = str(payload.get("reason", "")).strip() or "semantic keep window"
    confidence = float(payload.get("confidence", 0.65) or 0.65)
    keep_start = max(0.0, min(max(0.0, duration_seconds - keep_duration), keep_start))
    return {
        "keep_start_sec": keep_start,
        "keep_duration_sec": keep_duration,
        "reason": reason,
        "confidence": max(0.2, min(0.95, confidence)),
        "raw": payload,
    }


def _build_prompt(
    *,
    clip_name: str,
    duration_seconds: float,
    keep_seconds: float,
    keep_seconds_min: float,
    keep_seconds_max: float,
    media_kind: str,
    context_notes: str,
    timestamps: list[float],
) -> str:
    notes = context_notes.strip() or "Family home video; keep emotionally readable moments."
    stamp_list = ", ".join(f"{value:.2f}s" for value in timestamps)
    return (
        f"Clip file: {clip_name}\n"
        f"Media kind: {media_kind}\n"
        f"Clip duration: {duration_seconds:.2f} seconds\n"
        f"Suggested keep length: {keep_seconds:.2f} seconds\n"
        f"Allowed keep length range: {keep_seconds_min:.2f}-{keep_seconds_max:.2f} seconds\n"
        f"Story context: {notes}\n"
        f"Sampled frame timestamps: {stamp_list}\n\n"
        "Choose the shortest contiguous keep window that still communicates the moment. "
        "Do not make KEEP longer than needed.\n"
        "Return JSON with keys:\n"
        '- "keep_start_sec": number (start of keep window inside the clip)\n'
        '- "keep_duration_sec": number (minimal useful keep length within the allowed range)\n'
        '- "reason": short explanation grounded in visible frame content\n'
        '- "confidence": number from 0 to 1\n'
        '- "frame_scores": optional list of {"t": number, "score": number, "note": string}\n'
    )


def _get_client(*, timeout_seconds: float = 180.0) -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("Install openai>=1.0 to use semantic trim review.")
    # Local .env support without hard dependency.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return OpenAI(timeout=max(10.0, float(timeout_seconds)))


def _image_to_data_url(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(image_path.name)
    mime_type = mime_type or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
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
    raise ValueError(f"Semantic trim response does not contain JSON object: {raw_text[:300]}")
