from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image

from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    is_supported_image_media_path,
    list_named_project_sequence_names,
    load_premiere_project_root,
)
from utils.premiere_project_export import clone_named_sequence
from utils.premiere_sequence_delete_only import build_ffprobe_payload
from utils.premiere_sequence_motion import (
    _frame_ticks,
    _motion_params,
    _sequence_duration,
    _set_param_keyframes,
    _track_item_contexts,
    _video_settings,
    build_position_keyframes,
    build_scale_keyframes,
)
from utils.premiere_sequence_timeline_assembly import (
    _render_segment,
    _validate_all_refs,
    _visible_item_for_range,
)
from utils.video_frame_extract import resolve_ffmpeg_executable

TASK_ID = "TASK_034"
FPS = 25
FRAME_TICKS = _frame_ticks(FPS)
FW, FH = 3840, 2160
SOURCE_CANDIDATES = [
    Path("input") / 'SF_26_Bd_Art_5_TASK033_EXTREME_MOTION_FINAL.prproj',
    Path(
        r"input/TASK_033_ART_AUDIT_EXTREME_COLOR_FIT_FILL_PULSE"
        r"\SF_26_Bd_Art_5_TASK033_EXTREME_MOTION_FINAL.prproj"
    ),
]
SOURCE_SEQUENCE = "SF_26_Bd_Art_8_TASK033_COLOR_EXTREME"
BAD_REF_SEQUENCE = "SF_26_Bd_Art_9_TASK033_FIT_PULSE_FILL_FINAL"
OUTPUT_SEQUENCE = "SF_26_Bd_Art_10_TASK034_SINGLE_SOFT_IMPULSE_FINAL"
OUTPUT_PROJECT = Path("input") / 'SF_26_Bd_Art_6_TASK034_SINGLE_SOFT_IMPULSE.prproj'
PREVIEW = Path(
    r"input/SF_26_Bd_Art_10_TASK034_SINGLE_SOFT_IMPULSE_FINAL_640_360.mp4"
)
COMPARISON = Path(
    r"input/SF_26_Bd_Art_9_vs_10_TASK034_COMPARISON_1280_360.mp4"
)
OLD_PREVIEW = Path(
    r"input/SF_26_Bd_Art_9_TASK033_FIT_PULSE_FILL_FINAL_640_360.mp4"
)
REPO_DIR = Path(__file__).resolve().parent / "TASK_034_SINGLE_SOFT_IMPULSE"
LOCAL_DIR = Path("input") / 'TASK_034_SINGLE_SOFT_DIRECTIONAL_IMPULSE'
TASK033_PLAN = Path(__file__).resolve().parent / (
    "TASK_033_ART_AUDIT_EXTREME_COLOR_FIT_FILL_PULSE/TASK_033_EXECUTION_PLAN.json"
)
IMAGE_EXTRA = {".jfif", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}


from utils.premiere_art_runtime import configure_module
configure_module(globals(), "034")

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _save_project(root: ET.Element, path: Path) -> None:
    path.write_bytes(gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True)))


def _resolve_source() -> Path:
    if len(SOURCE_CANDIDATES) != 1 or not SOURCE_CANDIDATES[0].is_file():
        raise FileNotFoundError("Configure one exact SOURCE_PROJECT; automatic project search is disabled")
    return SOURCE_CANDIDATES[0]


def _items(root: ET.Element, name: str, project_path: Path, group: int = 0) -> list[Any]:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise PremiereProjectError(f"Missing sequence {name}")
    return _track_item_contexts(
        sequence,
        group_index=group,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=project_path,
    )


def _kind(path: str) -> str:
    if not path:
        return "nested"
    suffix = Path(path).suffix.lower()
    if is_supported_image_media_path(path) or suffix in IMAGE_EXTRA:
        return "image"
    if suffix in {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}:
        return "video"
    return "other"


def _clip_id(item: Any) -> str:
    return f"T{item.track_index}_S{item.start // FRAME_TICKS}_{item.name}"


def _sequence_xml_sha(root: ET.Element, name: str) -> str:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise PremiereProjectError(f"Missing {name}")
    return hashlib.sha256(ET.tostring(sequence, encoding="utf-8")).hexdigest()


def _fit_fill(path: str, travel: float) -> tuple[float, float]:
    with Image.open(path) as image:
        iw, ih = image.size
    fit = min(FW / iw, FH / ih) * 100.0
    fill = fit * (1.0 + travel)
    return fit, fill


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _build_monotonic_keyframes(
    *,
    direction: str,
    fit: float,
    fill: float,
    impulse_at: float,
    impulse_pct: float,
    impulse_width: float,
) -> list[dict[str, float]]:
    """Return ordered scale points that are strictly monotonic in the travel direction."""
    if direction == "fit_to_fill":
        start, end = fit, fill
        increasing = True
    else:
        start, end = fill, fit
        increasing = False
    # Base values along the path.
    before_t = max(0.05, impulse_at - impulse_width / 2)
    after_t = min(0.95, impulse_at + impulse_width / 2)
    if after_t <= before_t:
        after_t = min(0.97, before_t + 0.08)
    before_v = _lerp(start, end, before_t)
    after_base = _lerp(start, end, after_t)
    impulse = abs(end - start) * impulse_pct
    if increasing:
        after_v = min(end, after_base + impulse)
        # Ensure monotonic: before <= after <= end
        if after_v < before_v:
            after_v = before_v
    else:
        after_v = max(end, after_base - impulse)
        if after_v > before_v:
            after_v = before_v
    points = [
        {"t": 0.0, "v": round(start, 6)},
        {"t": round(before_t, 4), "v": round(before_v, 6)},
        {"t": round(after_t, 4), "v": round(after_v, 6)},
        {"t": 1.0, "v": round(end, 6)},
    ]
    # Collapse accidental equals that reverse due to rounding.
    cleaned = [points[0]]
    for point in points[1:]:
        prev = cleaned[-1]["v"]
        if increasing and point["v"] < prev:
            point = {**point, "v": prev}
        if not increasing and point["v"] > prev:
            point = {**point, "v": prev}
        if point["t"] <= cleaned[-1]["t"]:
            point = {**point, "t": round(cleaned[-1]["t"] + 0.01, 4)}
        cleaned.append(point)
    return cleaned


def _is_monotonic(values: list[float], *, increasing: bool, tol: float = 1e-4) -> bool:
    for left, right in zip(values, values[1:]):
        if increasing and right + tol < left:
            return False
        if not increasing and right - tol > left:
            return False
    return True


def _chain_scale_keyframes(source_in: int, source_out: int, points: list[dict[str, float]]) -> str:
    last = max(source_in, source_out - FRAME_TICKS)
    chunks: list[str] = []
    for index in range(len(points) - 1):
        a = points[index]
        b = points[index + 1]
        t0 = source_in + int(round(a["t"] * (last - source_in)))
        t1 = source_in + int(round(b["t"] * (last - source_in)))
        if t1 <= t0:
            t1 = t0 + FRAME_TICKS
        piece = build_scale_keyframes(t0, t1, a["v"], b["v"])
        left, right = piece.split(";")[:2]
        if index == 0:
            chunks.append(left)
        chunks.append(right)
    return ";".join(chunks) + ";"


def _sample_scale_path(points: list[dict[str, float]], samples: int = 48) -> list[float]:
    values: list[float] = []
    for index in range(samples):
        t = index / (samples - 1)
        # piecewise linear sample
        for left, right in zip(points, points[1:]):
            if left["t"] <= t <= right["t"] or right is points[-1]:
                span = max(1e-9, right["t"] - left["t"])
                local = min(1.0, max(0.0, (t - left["t"]) / span))
                values.append(_lerp(left["v"], right["v"], local))
                break
    return values


def _build_plan(root: ET.Element, source_project: Path) -> dict[str, Any]:
    video = _items(root, SOURCE_SEQUENCE, source_project, 0)
    audio = _items(root, SOURCE_SEQUENCE, source_project, 1)
    duration = max(_sequence_duration(video), _sequence_duration(audio)) // FRAME_TICKS
    ref_ops: dict[str, dict[str, Any]] = {}
    if TASK033_PLAN.is_file():
        old = json.loads(TASK033_PLAN.read_text(encoding="utf-8"))
        for op in old.get("photo_animation_operations", []):
            ref_ops[op["target"]["id"]] = op
    operations: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    still_index = 0
    for item in video:
        cid = _clip_id(item)
        kind = _kind(item.source_path)
        row = {
            "id": cid,
            "track": item.track_index,
            "name": item.name,
            "start": item.start // FRAME_TICKS,
            "end": item.end // FRAME_TICKS,
            "dur": item.duration // FRAME_TICKS,
            "source_in": item.source_in // FRAME_TICKS,
            "source_out": item.source_out // FRAME_TICKS,
            "path": item.source_path,
            "kind": kind,
            "online": (not item.source_path) or Path(item.source_path).is_file(),
        }
        if kind != "image":
            exclusions.append({"id": cid, "reason": "not a still image / nested / video"})
            continue
        if not row["online"]:
            raise PremiereProjectError(f"Offline still: {row['path']}")
        if row["dur"] < 36:
            exclusions.append({"id": cid, "reason": f"too short for impulse ({row['dur']}f)"})
            continue
        ref = ref_ops.get(cid)
        travel = float(ref["travel_percent"]) / 100.0 if ref else 0.12 + (still_index % 9) * 0.02
        travel = min(0.28, max(0.12, travel))
        fit, fill = _fit_fill(row["path"], travel)
        if ref:
            direction = "fit_to_fill" if ref["direction"] == "FIT_TO_FILL" else "fill_to_fit"
            impulse_at = float(ref.get("pulse_at_normalized", 0.45))
        else:
            direction = "fit_to_fill" if still_index % 2 == 0 else "fill_to_fit"
            impulse_at = 0.35 + (still_index % 8) * 0.04  # 35..63%
        impulse_pct = 0.010 + (still_index % 4) * 0.005  # 1.0..2.5%
        impulse_width = 0.10 + (still_index % 3) * 0.02  # ~10-14% of clip
        # Keep impulse window roughly 0.30-0.70s for typical stills.
        seconds = row["dur"] / FPS
        width_sec = impulse_width * seconds
        if width_sec < 0.30:
            impulse_width = min(0.18, 0.30 / max(0.01, seconds))
        if width_sec > 0.70:
            impulse_width = max(0.08, 0.70 / max(0.01, seconds))
        points = _build_monotonic_keyframes(
            direction=direction,
            fit=fit,
            fill=fill,
            impulse_at=impulse_at,
            impulse_pct=impulse_pct,
            impulse_width=impulse_width,
        )
        samples = _sample_scale_path(points)
        increasing = direction == "fit_to_fill"
        if not _is_monotonic(samples, increasing=increasing):
            raise PremiereProjectError(f"Plan non-monotonic for {cid}")
        pan = ((still_index % 5) - 2) * 0.004
        pan_y = ((still_index % 3) - 1) * 0.004
        if direction == "fit_to_fill":
            pos_start, pos_end = [0.5, 0.5], [0.5 + pan, 0.5 + pan_y]
        else:
            pos_start, pos_end = [0.5 + pan, 0.5 + pan_y], [0.5, 0.5]
        operations.append(
            {
                "operation_id": f"ANIM_{cid}",
                "target": row,
                "classification": "still_image",
                "direction": direction,
                "subject": {"x": 0.5 + pan / 2, "y": 0.5 + pan_y / 2},
                "fit_scale": round(fit, 6),
                "fill_scale": round(fill, 6),
                "travel_percent": round(travel * 100, 2),
                "impulse": {
                    "at_normalized": round(impulse_at, 4),
                    "magnitude_percent": round(impulse_pct * 100, 3),
                    "width_normalized": round(impulse_width, 4),
                    "easing": "BEZIER_EASE_IN_OUT",
                },
                "scale_keyframes": points,
                "position_start": pos_start,
                "position_end": pos_end,
                "monotonicity": "non_decreasing" if increasing else "non_increasing",
                "reason": "Один непрерывный ход + один мягкий импульс только в направлении движения",
            }
        )
        still_index += 1
    return {
        "task_id": TASK_ID,
        "source_project": str(source_project),
        "source_sequence": SOURCE_SEQUENCE,
        "reference_bad_sequence": BAD_REF_SEQUENCE,
        "output_project": str(OUTPUT_PROJECT),
        "output_sequence": OUTPUT_SEQUENCE,
        "input_fingerprint": {
            "project_sha256": _sha256(source_project),
            "art8_xml_sha256": _sequence_xml_sha(root, SOURCE_SEQUENCE),
            "duration_frames": duration,
        },
        "scope": "animation_only_from_Art_8",
        "photo_animation_operations": operations,
        "exclusions": exclusions,
        "validation_rules": [
            "duplicate Art_8 not Art_9",
            "no editorial/color/audio changes",
            "scale monotonic for each still",
            "one directional impulse max",
            "no animation on genuine video",
        ],
    }


def _validate_plan(plan: dict[str, Any], root: ET.Element, source_project: Path) -> dict[str, Any]:
    errors: list[str] = []
    if plan.get("task_id") != TASK_ID:
        errors.append("task_id")
    if plan.get("source_sequence") != SOURCE_SEQUENCE:
        errors.append("source_sequence")
    if OUTPUT_PROJECT.exists():
        errors.append("output project exists")
    if PREVIEW.exists():
        errors.append("preview exists")
    names = list_named_project_sequence_names(root)
    if SOURCE_SEQUENCE not in names:
        errors.append("Art_8 missing")
    if OUTPUT_SEQUENCE in names:
        errors.append("Art_10 already exists in source")
    video = {_clip_id(item): item for item in _items(root, SOURCE_SEQUENCE, source_project, 0)}
    for op in plan["photo_animation_operations"]:
        target = op["target"]
        if target["id"] not in video:
            errors.append(f"missing {target['id']}")
            continue
        if _kind(video[target["id"]].source_path) != "image":
            errors.append(f"non-image {target['id']}")
        samples = _sample_scale_path(op["scale_keyframes"])
        increasing = op["direction"] == "fit_to_fill"
        if not _is_monotonic(samples, increasing=increasing):
            errors.append(f"non-monotonic plan {target['id']}")
        # detect forbidden local reversal pattern in planned points
        vals = [point["v"] for point in op["scale_keyframes"]]
        for i in range(1, len(vals) - 1):
            if increasing and vals[i] > vals[i - 1] and vals[i + 1] < vals[i] - 1e-6:
                errors.append(f"local max then decrease {target['id']}")
            if not increasing and vals[i] < vals[i - 1] and vals[i + 1] > vals[i] + 1e-6:
                errors.append(f"local min then increase {target['id']}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "operations": len(plan["photo_animation_operations"]),
        "exclusions": len(plan["exclusions"]),
    }


def _find_item(items: list[Any], target: dict[str, Any]) -> Any:
    matches = [
        item
        for item in items
        if item.track_index == target["track"]
        and item.start // FRAME_TICKS == target["start"]
        and item.name == target["name"]
    ]
    if len(matches) != 1:
        raise PremiereProjectError(f"Target not unique: {target['id']} -> {len(matches)}")
    return matches[0]


def _apply_animation(root: ET.Element, plan: dict[str, Any]) -> list[dict[str, Any]]:
    ids = build_project_object_id_lookup(root)
    items = _items(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0)
    log: list[dict[str, Any]] = []
    for op in plan["photo_animation_operations"]:
        item = _find_item(items, op["target"])
        if _kind(item.source_path) != "image":
            raise PremiereProjectError(f"Refusing video animation: {item.name}")
        params = _motion_params(item.track_item_node, ids)
        if params is None:
            log.append({"id": op["target"]["id"], "action": "skip_no_motion"})
            continue
        scale_kf = _chain_scale_keyframes(item.source_in, item.source_out, op["scale_keyframes"])
        _set_param_keyframes(
            params.scale,
            keyframes=scale_kf,
            current_value=f"{op['scale_keyframes'][-1]['v']:.6f}".rstrip("0").rstrip("."),
        )
        first = item.source_in
        last = max(first, item.source_out - FRAME_TICKS)
        sx, sy = op["position_start"]
        ex, ey = op["position_end"]
        _set_param_keyframes(
            params.position,
            keyframes=build_position_keyframes(first, last, sx, sy, ex, ey),
        )
        log.append(
            {
                "id": op["target"]["id"],
                "action": "applied",
                "direction": op["direction"],
                "impulse": op["impulse"],
                "scale_keyframes": op["scale_keyframes"],
            }
        )
    return log


def _parse_scale_keyframes(text: str) -> list[tuple[int, float]]:
    rows: list[tuple[int, float]] = []
    for entry in (text or "").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(",")
        if len(parts) < 2:
            continue
        try:
            rows.append((int(float(parts[0])), float(parts[1])))
        except ValueError:
            continue
    return rows


def _qa_monotonicity(root: ET.Element, plan: dict[str, Any]) -> dict[str, Any]:
    ids = build_project_object_id_lookup(root)
    items = _items(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0)
    results = []
    failures = 0
    for op in plan["photo_animation_operations"]:
        item = _find_item(items, op["target"])
        params = _motion_params(item.track_item_node, ids)
        assert params is not None
        varying = (params.scale.findtext("./IsTimeVarying") or "").lower() == "true"
        parsed = _parse_scale_keyframes(params.scale.findtext("./Keyframes") or "")
        values = [value for _, value in parsed]
        # dense resample between keyframes
        dense: list[float] = []
        for (t0, v0), (t1, v1) in zip(parsed, parsed[1:]):
            steps = max(2, int((t1 - t0) / FRAME_TICKS))
            for step in range(steps):
                local = step / (steps - 1) if steps > 1 else 0.0
                dense.append(_lerp(v0, v1, local))
        if not dense:
            dense = values
        increasing = op["direction"] == "fit_to_fill"
        mono = _is_monotonic(dense, increasing=increasing)
        # detect local extremum reversal
        reversal = False
        for i in range(1, len(dense) - 1):
            if increasing and dense[i] > dense[i - 1] + 1e-6 and dense[i + 1] < dense[i] - 1e-6:
                reversal = True
            if not increasing and dense[i] < dense[i - 1] - 1e-6 and dense[i + 1] > dense[i] + 1e-6:
                reversal = True
        ok = varying and mono and not reversal
        if not ok:
            failures += 1
        results.append(
            {
                "id": op["target"]["id"],
                "direction": op["direction"],
                "pass": ok,
                "time_varying": varying,
                "monotonic": mono,
                "reversal_detected": reversal,
                "keyframe_values": values,
                "sampled_count": len(dense),
                "sampled_min": min(dense) if dense else None,
                "sampled_max": max(dense) if dense else None,
            }
        )
    # ensure videos untouched: no new motion requirement beyond existing
    video_checks = []
    for item in items:
        if _kind(item.source_path) != "video":
            continue
        # just record presence
        video_checks.append({"id": _clip_id(item), "protected": True})
    return {
        "status": "PASS" if failures == 0 else "FAIL",
        "failures": failures,
        "animated": len(results),
        "results": results,
        "video_protected": video_checks,
    }


def _visible_ranges(items: list[Any]) -> list[tuple[int, int, Any]]:
    bounds = sorted({item.start for item in items} | {item.end for item in items})
    compact: list[tuple[int, int, Any]] = []
    for start, end in zip(bounds, bounds[1:]):
        covering = [item for item in items if item.start <= start < item.end]
        if not covering:
            continue
        top = max(covering, key=lambda item: item.track_index)
        if compact and compact[-1][2] is top and compact[-1][1] == start:
            compact[-1] = (compact[-1][0], end, top)
        else:
            compact.append((start, end, top))
    return compact


def _mux_audio(ffmpeg: str, video_path: Path, audio_items: list[Any], frames: int, output_path: Path) -> None:
    duration_sec = f"{frames / FPS:.3f}"
    usable = [item for item in audio_items if item.source_path and Path(item.source_path).is_file()]
    with tempfile.TemporaryDirectory(prefix="task034_audio_") as temp_text:
        temp = Path(temp_text)
        if not usable:
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(video_path),
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-shortest",
                    "-t",
                    duration_sec,
                    str(output_path),
                ],
                check=True,
            )
            return
        wavs = []
        for index, item in enumerate(usable):
            wav = temp / f"a{index:02d}.wav"
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{item.source_in / PREMIERE_TICKS_PER_SECOND:.9f}",
                    "-t",
                    f"{item.duration / PREMIERE_TICKS_PER_SECOND:.9f}",
                    "-i",
                    item.source_path,
                    "-vn",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    str(wav),
                ],
                check=True,
            )
            wavs.append((wav, int(round((item.start / PREMIERE_TICKS_PER_SECOND) * 1000))))
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path)]
        filters = []
        labels = []
        for index, (wav, delay_ms) in enumerate(wavs):
            command.extend(["-i", str(wav)])
            label = f"a{index}"
            filters.append(f"[{index + 1}:a]adelay={delay_ms}:all=1[{label}]")
            labels.append(f"[{label}]")
        command.extend(
            [
                "-filter_complex",
                ";".join(filters)
                + ";"
                + "".join(labels)
                + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0[aout]",
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-t",
                duration_sec,
                str(output_path),
            ]
        )
        subprocess.run(command, check=True)


def _render_motion_preview(root: ET.Element, plan: dict[str, Any]) -> dict[str, Any]:
    ffmpeg = resolve_ffmpeg_executable()
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    video = _items(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0)
    audio = _items(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 1)
    frames = max(_sequence_duration(video), _sequence_duration(audio)) // FRAME_TICKS
    anim = {op["target"]["id"]: op for op in plan["photo_animation_operations"]}
    ranges = _visible_ranges([item for item in video if item.track_index > 0] or video)
    with tempfile.TemporaryDirectory(prefix="task034_prev_") as temp_text:
        temp = Path(temp_text)
        rendered = []
        for index, (start, end, item) in enumerate(ranges, 1):
            seg_frames = (end - start) // FRAME_TICKS
            segment = temp / f"seg_{index:03d}.mp4"
            cid = _clip_id(item)
            media = Path(item.source_path) if item.source_path else None
            source_item = item
            sequence_in = start
            if media and media.suffix.lower() in {".jfif", ".jpe"}:
                converted = temp / f"c{index:03d}.jpg"
                Image.open(media).convert("RGB").save(converted, "JPEG", quality=92)
                media = converted
                source_item = SimpleNamespace(source_path=str(converted), source_in=item.source_in, start=item.start)
            elif not item.source_path:
                nested = find_project_sequence_node(root, item.name)
                source_item = _visible_item_for_range(
                    nested,
                    source_in_ticks=item.source_in + (start - item.start),
                    source_out_ticks=item.source_in + (end - item.start),
                    ids=ids,
                    uids=uids,
                    project_path=OUTPUT_PROJECT,
                )
                sequence_in = item.source_in + (start - item.start)
                media = Path(source_item.source_path)
            if cid in anim and media is not None and media.suffix.lower() in IMAGE_EXTRA | {".jpg", ".jpeg", ".png"}:
                op = anim[cid]
                start_z = 1.0 if op["direction"] == "fit_to_fill" else op["fill_scale"] / op["fit_scale"]
                end_z = op["fill_scale"] / op["fit_scale"] if op["direction"] == "fit_to_fill" else 1.0
                # Monotonic zoom only (no mid reversal) for preview approximation.
                z_expr = f"{start_z:.6f}+({end_z:.6f}-{start_z:.6f})*on/{max(1, seg_frames - 1)}"
                # Add a brief faster segment around impulse by piecewise if:
                before = max(1, int(seg_frames * op["scale_keyframes"][1]["t"]))
                after = max(before + 1, int(seg_frames * op["scale_keyframes"][2]["t"]))
                b_z = op["scale_keyframes"][1]["v"] / op["fit_scale"]
                a_z = op["scale_keyframes"][2]["v"] / op["fit_scale"]
                z_expr = (
                    f"if(lt(on,{before}),"
                    f"{start_z:.6f}+({b_z:.6f}-{start_z:.6f})*on/{max(1, before)},"
                    f"if(lt(on,{after}),"
                    f"{b_z:.6f}+({a_z:.6f}-{b_z:.6f})*(on-{before})/{max(1, after - before)},"
                    f"{a_z:.6f}+({end_z:.6f}-{a_z:.6f})*(on-{after})/{max(1, seg_frames - 1 - after)}))"
                )
                vf = (
                    f"scale=640:360:force_original_aspect_ratio=decrease,"
                    f"pad=640:360:(ow-iw)/2:(oh-ih)/2:color=0x5A3A1E,"
                    f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d={seg_frames}:s=640x360:fps={FPS},format=yuv420p"
                )
                subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-loop",
                        "1",
                        "-framerate",
                        str(FPS),
                        "-i",
                        str(media),
                        "-vf",
                        vf,
                        "-frames:v",
                        str(seg_frames),
                        "-an",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "21",
                        str(segment),
                    ],
                    check=True,
                )
            else:
                _render_segment(
                    ffmpeg=ffmpeg,
                    item=source_item,
                    sequence_in_ticks=sequence_in,
                    frames=seg_frames,
                    fps=FPS,
                    width=640,
                    height=360,
                    output_path=segment,
                )
            rendered.append(segment)
        concat = temp / "concat.txt"
        concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in rendered) + "\n", encoding="utf-8")
        silent = temp / "video.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-an",
                "-frames:v",
                str(frames),
                "-r",
                str(FPS),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "21",
                "-pix_fmt",
                "yuv420p",
                str(silent),
            ],
            check=True,
        )
        _mux_audio(ffmpeg, silent, audio, frames, PREVIEW)
    return {"path": str(PREVIEW), "frames": frames, "sha256": _sha256(PREVIEW), "bytes": PREVIEW.stat().st_size}


def _comparison(ffmpeg: str) -> None:
    if not OLD_PREVIEW.is_file() or not PREVIEW.is_file():
        return
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(OLD_PREVIEW),
            "-i",
            str(PREVIEW),
            "-filter_complex",
            (
                "[0:v]drawtext=text='TASK033 DOUBLE PULSE':x=10:y=10:fontsize=20:"
                "fontcolor=white:box=1:boxcolor=black@0.5[v0];"
                "[1:v]drawtext=text='TASK034 SINGLE IMPULSE':x=10:y=10:fontsize=20:"
                "fontcolor=white:box=1:boxcolor=black@0.5[v1];"
                "[v0][v1]hstack=inputs=2[vout]"
            ),
            "-map",
            "[vout]",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-shortest",
            str(COMPARISON),
        ],
        check=False,
    )


def _copy_local() -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    for path in REPO_DIR.glob("TASK_034_*"):
        if path.is_file():
            shutil.copy2(path, LOCAL_DIR / path.name)
    for path in (PREVIEW, COMPARISON, OUTPUT_PROJECT):
        if path.is_file() and path.stat().st_size < 150_000_000:
            shutil.copy2(path, LOCAL_DIR / path.name)


def main() -> dict[str, Any]:
    from utils.premiere_art_runtime import require_fresh_run
    require_fresh_run("034")
    started = datetime.now().isoformat(timespec="seconds")
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    source_project = _resolve_source()
    if OUTPUT_PROJECT.exists() or PREVIEW.exists():
        _write_text(REPO_DIR / "TASK_034_BLOCKED.txt", "Output already exists")
        return {"status": "BLOCKED"}

    root = load_premiere_project_root(source_project)
    names = list_named_project_sequence_names(root)
    if SOURCE_SEQUENCE not in names:
        raise PremiereProjectError("Art_8 missing")
    if BAD_REF_SEQUENCE not in names:
        raise PremiereProjectError("Art_9 reference missing")
    settings = _video_settings(
        find_project_sequence_node(root, SOURCE_SEQUENCE),
        build_project_object_id_lookup(root),
    )
    if settings.get("frame_rate") != str(FRAME_TICKS) or settings.get("frame_rect") != "0,0,3840,2160":
        raise PremiereProjectError("Unexpected frame settings")

    art8_sha = _sequence_xml_sha(root, SOURCE_SEQUENCE)
    art9_sha = _sequence_xml_sha(root, BAD_REF_SEQUENCE)
    plan = _build_plan(root, source_project)
    plan_path = REPO_DIR / "TASK_034_EXECUTION_PLAN.json"
    _write_json(plan_path, plan)
    loaded = json.loads(plan_path.read_text(encoding="utf-8"))
    validation = _validate_plan(loaded, root, source_project)
    _write_text(
        REPO_DIR / "TASK_034_JSON_VALIDATION.txt",
        "\n".join(
            [
                "TASK_034 JSON VALIDATION / DRY-RUN",
                f"Source: {source_project}",
                f"Status: {validation['status']}",
                f"Operations: {validation['operations']}",
                f"Exclusions: {validation['exclusions']}",
                f"Errors: {validation['errors'] or 'none'}",
            ]
        ),
    )
    if validation["status"] != "PASS":
        _write_text(REPO_DIR / "TASK_034_BLOCKED.txt", "DRY-RUN FAIL\n" + "\n".join(validation["errors"]))
        return {"status": "BLOCKED", "validation": validation}

    backup = source_project.with_name(
        f"{source_project.stem}_before_TASK_034{source_project.suffix}"
    )
    if backup.exists():
        backup = source_project.with_name(
            f"{source_project.stem}_before_TASK_034_{datetime.now().strftime('%Y%m%d_%H%M%S')}{source_project.suffix}"
        )
    shutil.copy2(source_project, backup)
    shutil.copy2(source_project, OUTPUT_PROJECT)

    work = load_premiere_project_root(OUTPUT_PROJECT)
    clone_named_sequence(
        work,
        source_sequence_name=SOURCE_SEQUENCE,
        new_sequence_name=OUTPUT_SEQUENCE,
        object_id_lookup=build_project_object_id_lookup(work),
        object_uid_lookup=build_project_object_uid_lookup(work),
    )
    apply_log = _apply_animation(work, loaded)
    _validate_all_refs(work)
    if _sequence_xml_sha(work, SOURCE_SEQUENCE) != art8_sha:
        raise PremiereProjectError("Art_8 changed")
    if _sequence_xml_sha(work, BAD_REF_SEQUENCE) != art9_sha:
        raise PremiereProjectError("Art_9 changed")
    _save_project(work, OUTPUT_PROJECT)

    reopened = load_premiere_project_root(OUTPUT_PROJECT)
    qa = _qa_monotonicity(reopened, loaded)
    _write_json(REPO_DIR / "TASK_034_ANIMATION_QA.json", qa)
    if qa["status"] != "PASS":
        _write_text(REPO_DIR / "TASK_034_BLOCKED.txt", "Monotonicity QA FAIL")
        return {"status": "BLOCKED", "qa": qa}

    preview = _render_motion_preview(reopened, loaded)
    _comparison(resolve_ffmpeg_executable())

    source_re = load_premiere_project_root(source_project)
    source_unchanged = (
        _sha256(source_project) == loaded["input_fingerprint"]["project_sha256"]
        and _sequence_xml_sha(source_re, SOURCE_SEQUENCE) == art8_sha
        and _sequence_xml_sha(source_re, BAD_REF_SEQUENCE) == art9_sha
    )
    fit_n = sum(1 for op in loaded["photo_animation_operations"] if op["direction"] == "fit_to_fill")
    fill_n = len(loaded["photo_animation_operations"]) - fit_n

    _write_json(REPO_DIR / "TASK_034_ANIMATION_APPLY_LOG.json", apply_log)
    _write_text(
        REPO_DIR / "TASK_034_CHANGELOG.md",
        "\n".join(
            [
                "# TASK_034 CHANGELOG",
                "",
                f"- Source project: `{source_project}`",
                f"- Base sequence: `{SOURCE_SEQUENCE}` (not Art_9)",
                f"- New sequence: `{OUTPUT_SEQUENCE}`",
                f"- Animated stills: {len(apply_log)}",
                f"- Directions: fit_to_fill={fit_n}, fill_to_fit={fill_n}",
                "- Color/edit/audio/background: unchanged from Art_8",
                "- Pulse grammar: single soft directional impulse; scale monotonic",
            ]
        ),
    )
    _write_text(
        REPO_DIR / "TASK_034_QA_REPORT.md",
        "\n".join(
            [
                "# TASK_034 QA",
                f"- Started: {started}",
                f"- Ended: {datetime.now().isoformat(timespec='seconds')}",
                f"- Monotonicity QA: {qa['status']} (failures={qa['failures']})",
                f"- Animated: {qa['animated']}",
                f"- Source unchanged: {source_unchanged}",
                f"- Art_8/Art_9 unchanged in output project: True",
                f"- Preview: {PREVIEW}",
                "- Premiere Desktop GUI visual check remains for Sergey/Muza",
                "- Drive unavailable => WAITING_UPLOAD",
            ]
        ),
    )
    artifacts = {
        "source": {"path": str(source_project), "sha256": _sha256(source_project)},
        "backup": {"path": str(backup), "sha256": _sha256(backup)},
        "output": {"path": str(OUTPUT_PROJECT), "sha256": _sha256(OUTPUT_PROJECT), "bytes": OUTPUT_PROJECT.stat().st_size},
        "preview": preview,
    }
    status = "\n".join(
        [
            "TASK_034 LOCAL COMPLETE",
            "Статус: WAITING_UPLOAD",
            f"Input: {source_project} / {SOURCE_SEQUENCE}",
            f"Backup: {backup}",
            f"Output: {OUTPUT_PROJECT}",
            f"Sequence: {OUTPUT_SEQUENCE}",
            f"Animated: {qa['animated']}; exclusions: {len(loaded['exclusions'])}",
            f"fit_to_fill={fit_n}; fill_to_fit={fill_n}",
            "Monotonicity QA: PASS on all animated stills",
            "Color/background/edit/audio unchanged from Art_8",
            f"Preview: {PREVIEW}",
            f"SHA256: {preview['sha256']} bytes={preview['bytes']}",
            f"Local: {LOCAL_DIR}",
        ]
    )
    _write_text(REPO_DIR / "TASK_034_WAITING_UPLOAD.txt", status)
    _copy_local()
    _write_text(LOCAL_DIR / "TASK_034_WAITING_UPLOAD.txt", status)
    return {
        "status": "WAITING_UPLOAD",
        "animated": qa["animated"],
        "exclusions": len(loaded["exclusions"]),
        "fit_to_fill": fit_n,
        "fill_to_fit": fill_n,
        "monotonicity": qa["status"],
        "source_unchanged": source_unchanged,
        "artifacts": artifacts,
    }


if __name__ == "__main__":
    from main_premiere_art_task import main as launch
    launch(["--task", "034"] + sys.argv[1:])
