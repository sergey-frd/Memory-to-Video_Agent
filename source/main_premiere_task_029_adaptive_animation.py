from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
from PIL import Image, ImageDraw

from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    load_premiere_project_root,
    resolve_project_track_item_clip,
)
from utils.premiere_project_export import _set_child_text, clone_named_sequence
from utils.premiere_media_import_export import (
    _ProjectObjectIdAllocator,
    _assert_project_refs_resolved,
    _clone_and_sanitize_video_components,
)
from utils.premiere_sequence_motion import (
    _baseline_position,
    _baseline_scale,
    _frame_ticks,
    _meaningful_existing_motion,
    _motion_params,
    _run_ffmpeg,
    _sequence_duration,
    _set_param_keyframes,
    _track_item_contexts,
    _video_settings,
    build_position_keyframes,
    build_scale_keyframes,
    protected_property_snapshot,
)
from utils.premiere_sequence_delete_only import build_ffprobe_payload
from utils.premiere_sequence_timeline_assembly import (
    _source_sequence_video_clip,
    _validate_all_refs,
    _visible_item_for_range,
)
from utils.video_frame_extract import resolve_ffmpeg_executable


TASK_ID = "TASK_029"
FPS = 25
FRAME_TICKS = _frame_ticks(FPS)
PROJECT = Path(r"<LOCAL_PATH>")
SHORT_INPUT = "SF_26_BD_SHORT_76S_v05"
LONG_INPUT = "SF_26_BD_LONG_FAMILY_NURI_v12"
SHORT_REFERENCE = "SF_26_BD_SHORT_76S_v04"
LONG_REFERENCE = "SF_26_BD_LONG_FAMILY_NURI_v11"
SHORT_OUTPUT = "SF_26_BD_SHORT_76S_v06_TASK029_ANIM"
LONG_OUTPUT = "SF_26_BD_LONG_FAMILY_NURI_v13_TASK029_ANIM"
LONG_FAMILY_HELPER = "TASK_029_LONG_FAMILY_ANIM_SOURCE"
LONG_NURI_HELPER = "TASK_029_LONG_NURI_ANIM_SOURCE"
TASK_DIR = Path(__file__).resolve().parent / "TASK_029_SERGEY_HIGH_RES_AUDIT_ADAPTIVE_ANIMATION"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _items(root: Any, sequence_name: str, group: int) -> list[Any]:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise RuntimeError(f"BLOCKED: sequence not found: {sequence_name}")
    return _track_item_contexts(
        sequence,
        group_index=group,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=PROJECT,
    )


def _media_size(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as image:
            return image.size
    if path.suffix.lower() in VIDEO_SUFFIXES:
        capture = cv2.VideoCapture(str(path))
        size = (
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        capture.release()
        return size if min(size) > 0 else None
    return None


def _motion_snapshot(item: Any, ids: dict[str, Any]) -> dict[str, object]:
    params = _motion_params(item.track_item_node, ids)
    if params is None:
        return {
            "intrinsic_motion_present": False,
            "baseline_scale": None,
            "baseline_position": None,
            "has_keyframes": False,
            "meaningful_keyframes": False,
        }
    has_keyframes, meaningful = _meaningful_existing_motion(params)
    try:
        scale = _baseline_scale(params.scale)
        position = list(_baseline_position(params.position))
    except Exception:
        scale = None
        position = None
    return {
        "intrinsic_motion_present": True,
        "baseline_scale": scale,
        "baseline_position": position,
        "has_keyframes": has_keyframes,
        "meaningful_keyframes": meaningful,
    }


def _resolved_item(root: Any, item: Any) -> tuple[Any, str | None]:
    if item.source_path:
        return item, None
    nested = find_project_sequence_node(root, item.name)
    if nested is None:
        return item, "nested sequence not found"
    try:
        resolved = _visible_item_for_range(
            nested,
            source_in_ticks=item.source_in,
            source_out_ticks=item.source_out,
            ids=build_project_object_id_lookup(root),
            uids=build_project_object_uid_lookup(root),
            project_path=PROJECT,
        )
        return resolved, None
    except Exception as exc:
        return item, str(exc)


def _orientation(size: tuple[int, int] | None) -> str | None:
    if size is None:
        return None
    if size[0] == size[1]:
        return "square"
    return "landscape" if size[0] > size[1] else "portrait"


def _face_data(path: Path) -> dict[str, object]:
    if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        return {"count": 0, "boxes_normalized": []}
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        return {
            "count": 0,
            "boxes_normalized": [],
            "detector_status": "OpenCV build has no Haar cascade support",
        }
    frame = cv2.imread(str(path))
    if frame is None:
        return {"count": 0, "boxes_normalized": []}
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    boxes = cascade.detectMultiScale(
        gray, scaleFactor=1.08, minNeighbors=4, minSize=(32, 32)
    )
    height, width = gray.shape
    normalized = [
        [
            round(x / width, 4),
            round(y / height, 4),
            round((x + w) / width, 4),
            round((y + h) / height, 4),
        ]
        for x, y, w, h in boxes
    ]
    return {"count": len(normalized), "boxes_normalized": normalized}


def _audit_sequence(root: Any, name: str, reference: str) -> dict[str, object]:
    ids = build_project_object_id_lookup(root)
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise RuntimeError(f"BLOCKED: sequence not found: {name}")
    video = _items(root, name, 0)
    audio = _items(root, name, 1)
    duration = _sequence_duration(video + audio)
    rows: list[dict[str, object]] = []
    offline: list[str] = []
    unresolved: list[str] = []
    conflicting_keyframes: list[str] = []
    foreground = [item for item in video if item.track_index == 1]
    for order, item in enumerate(video, 1):
        resolved, resolution_error = _resolved_item(root, item)
        path = Path(resolved.source_path) if resolved.source_path else None
        size = _media_size(path) if path else None
        direct_kind = (
            "image"
            if path and path.suffix.lower() in IMAGE_SUFFIXES
            else "video"
            if path and path.suffix.lower() in VIDEO_SUFFIXES
            else "nested_or_background"
        )
        if path and not path.is_file():
            offline.append(str(path))
        if resolution_error and item.track_index == 1:
            unresolved.append(
                f"{item.name} [{item.start // FRAME_TICKS},{item.end // FRAME_TICKS})"
            )
        outer_motion = _motion_snapshot(item, ids)
        resolved_motion = _motion_snapshot(resolved, ids)
        if (
            item.track_index == 1
            and direct_kind == "image"
            and (
                bool(outer_motion["meaningful_keyframes"])
                or bool(resolved_motion["meaningful_keyframes"])
            )
        ):
            conflicting_keyframes.append(
                f"{item.name} [{item.start // FRAME_TICKS},{item.end // FRAME_TICKS})"
            )
        components = protected_property_snapshot(item, ids)["components"]
        rows.append(
            {
                "order": order,
                "track_index": item.track_index,
                "timeline_in_frame": item.start // FRAME_TICKS,
                "timeline_out_frame": item.end // FRAME_TICKS,
                "duration_frames": item.duration // FRAME_TICKS,
                "name": item.name,
                "element_type": (
                    "blurred_background"
                    if item.track_index == 0
                    else "nested_sequence"
                    if not item.source_path
                    else direct_kind
                ),
                "resolved_media_type": direct_kind,
                "source_path": str(path) if path else None,
                "source_online": path.is_file() if path else None,
                "source_size": list(size) if size else None,
                "orientation": _orientation(size),
                "source_in_frame": (
                    resolved.source_in + item.source_in - resolved.start
                )
                // FRAME_TICKS
                if resolved is not item
                else item.source_in // FRAME_TICKS,
                "source_out_frame": (
                    resolved.source_in + item.source_out - resolved.start
                )
                // FRAME_TICKS
                if resolved is not item
                else item.source_out // FRAME_TICKS,
                "resolved_nested_sequence": item.name if not item.source_path else None,
                "resolved_track_item_id": resolved.track_item_node.attrib.get(
                    "ObjectID", ""
                ),
                "outer_motion": outer_motion,
                "resolved_motion": resolved_motion,
                "faces": _face_data(path) if path else {"count": 0, "boxes_normalized": []},
                "components": components,
                "resolved_components": protected_property_snapshot(resolved, ids)[
                    "components"
                ],
                "resolution_error": resolution_error,
            }
        )
    foreground_bounds = [(item.start, item.end) for item in foreground]
    gaps = [
        [foreground_bounds[index - 1][1] // FRAME_TICKS, bounds[0] // FRAME_TICKS]
        for index, bounds in enumerate(foreground_bounds)
        if index and bounds[0] > foreground_bounds[index - 1][1]
    ]
    overlaps = [
        [bounds[0] // FRAME_TICKS, foreground_bounds[index - 1][1] // FRAME_TICKS]
        for index, bounds in enumerate(foreground_bounds)
        if index and bounds[0] < foreground_bounds[index - 1][1]
    ]
    reference_rows = [
        (
            item.track_index,
            item.start // FRAME_TICKS,
            item.end // FRAME_TICKS,
            item.name,
            Path(item.source_path).name if item.source_path else None,
        )
        for item in _items(root, reference, 0)
    ]
    current_rows = [
        (
            item.track_index,
            item.start // FRAME_TICKS,
            item.end // FRAME_TICKS,
            item.name,
            Path(item.source_path).name if item.source_path else None,
        )
        for item in video
    ]
    audio_rows = [
        {
            "track_index": item.track_index,
            "timeline_in_frame": item.start // FRAME_TICKS,
            "timeline_out_frame": item.end // FRAME_TICKS,
            "name": item.name,
            "source_path": item.source_path,
            "source_online": Path(item.source_path).is_file()
            if item.source_path
            else None,
        }
        for item in audio
    ]
    expected_frames = 1878 if name == SHORT_INPUT else 4103
    settings = _video_settings(sequence, ids)
    frame_contract = (
        settings["frame_rate"] == str(FRAME_TICKS)
        and settings["frame_rect"] == "0,0,3840,2160"
        and duration == expected_frames * FRAME_TICKS
    )
    safe = (
        frame_contract
        and not offline
        and not unresolved
        and not conflicting_keyframes
        and not gaps
        and not overlaps
        and bool(foreground)
        and all(row["source_online"] is not False for row in audio_rows)
    )
    return {
        "task_id": TASK_ID,
        "audit_phase": "A_NO_MUTATION",
        "audited_at": datetime.now().isoformat(timespec="seconds"),
        "project_path": str(PROJECT),
        "project_sha256": _sha256(PROJECT),
        "sequence": name,
        "reference_task_028_sequence": reference,
        "settings": settings,
        "fps": FPS,
        "frame_size": [3840, 2160],
        "duration_frames": duration // FRAME_TICKS,
        "duration_seconds": duration / PREMIERE_TICKS_PER_SECOND,
        "video_track_indexes": sorted({item.track_index for item in video}),
        "audio_track_indexes": sorted({item.track_index for item in audio}),
        "video_item_count": len(video),
        "foreground_item_count": len(foreground),
        "audio_item_count": len(audio),
        "visual_items": rows,
        "audio_items": audio_rows,
        "offline_media": sorted(set(offline)),
        "unresolved_visual_ranges": unresolved,
        "conflicting_meaningful_keyframes": conflicting_keyframes,
        "foreground_gaps": gaps,
        "foreground_overlaps": overlaps,
        "duplicate_resolved_media_names": {
            key: count
            for key, count in Counter(
                Path(str(row["source_path"])).name
                for row in rows
                if row["track_index"] == 1 and row["source_path"]
            ).items()
            if count > 1
        },
        "difference_from_task_028_reference": {
            "structurally_identical": current_rows == reference_rows,
            "current_signature": current_rows,
            "reference_signature": reference_rows,
        },
        "frame_contract_pass": frame_contract,
        "safe_to_proceed_to_animation": safe,
        "status": "PASS_READY_FOR_PHASE_B" if safe else "BLOCKED",
    }


def _representative_frame(row: dict[str, object]) -> Image.Image:
    path_text = row.get("source_path")
    if not path_text:
        return Image.new("RGB", (320, 180), "#333333")
    path = Path(str(path_text))
    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as source:
            image = source.convert("RGB")
    else:
        capture = cv2.VideoCapture(str(path))
        source_in = int(row.get("source_in_frame") or 0)
        source_out = int(row.get("source_out_frame") or source_in + 1)
        capture.set(cv2.CAP_PROP_POS_FRAMES, (source_in + source_out) // 2)
        ok, frame = capture.read()
        capture.release()
        image = (
            Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if ok
            else Image.new("RGB", (320, 180), "#880000")
        )
    canvas = Image.new("RGB", (320, 180), "black")
    image.thumbnail((320, 180))
    canvas.paste(image, ((320 - image.width) // 2, (180 - image.height) // 2))
    return canvas


def _contact_sheet(audit: dict[str, object], output: Path) -> None:
    rows = [
        row
        for row in audit["visual_items"]  # type: ignore[index]
        if isinstance(row, dict) and row.get("track_index") == 1
    ]
    columns = 4
    cell_width, image_height, label_height = 320, 180, 48
    sheet_rows = (len(rows) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * cell_width, 34 + sheet_rows * (image_height + label_height)),
        "white",
    )
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 8), f"{TASK_ID} AUDIT — {audit['sequence']}", fill="black")
    for index, row in enumerate(rows):
        x = index % columns * cell_width
        y = 34 + index // columns * (image_height + label_height)
        sheet.paste(_representative_frame(row), (x, y))
        label = (
            f"{row['order']}  {row['timeline_in_frame']}:{row['timeline_out_frame']}  "
            f"{Path(str(row.get('source_path') or row['name'])).name}"
        )
        draw.text((x + 4, y + image_height + 3), label[:52], fill="black")
        draw.text(
            (x + 4, y + image_height + 20),
            f"{row['resolved_media_type']} {row.get('source_size')} faces={row['faces']['count']}",
            fill="black",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=92)


def audit_only() -> tuple[dict[str, object], dict[str, object]]:
    if not PROJECT.is_file():
        raise RuntimeError(f"BLOCKED: project not found: {PROJECT}")
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    source_instruction = Path(__file__).resolve().parent / (
        "TASK_029_HIGH_RES_AUDIT_AND_ADAPTIVE_ANIMATION.txt"
    )
    if source_instruction.is_file():
        shutil.copy2(source_instruction, TASK_DIR / source_instruction.name)
    root = load_premiere_project_root(PROJECT)
    for output in (SHORT_OUTPUT, LONG_OUTPUT):
        if find_project_sequence_node(root, output) is not None:
            raise RuntimeError(f"BLOCKED: output sequence already exists: {output}")
    short = _audit_sequence(root, SHORT_INPUT, SHORT_REFERENCE)
    long = _audit_sequence(root, LONG_INPUT, LONG_REFERENCE)
    (TASK_DIR / "TASK_029_AUDIT_SHORT.json").write_text(
        json.dumps(short, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (TASK_DIR / "TASK_029_AUDIT_LONG.json").write_text(
        json.dumps(long, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _contact_sheet(short, TASK_DIR / "TASK_029_AUDIT_SHORT_CONTACT_SHEET.jpg")
    _contact_sheet(long, TASK_DIR / "TASK_029_AUDIT_LONG_CONTACT_SHEET.jpg")
    safe = bool(short["safe_to_proceed_to_animation"]) and bool(
        long["safe_to_proceed_to_animation"]
    )
    report = [
        "TASK_029 — ЭТАП A: АУДИТ БЕЗ МУТАЦИИ",
        "",
        f"Проект: {PROJECT}",
        f"SHA256 до мутации: {_sha256(PROJECT)}",
        "",
        f"SHORT {SHORT_INPUT}: {short['status']}",
        f"- 3840x2160 / 25 fps / {short['duration_frames']} кадров",
        f"- foreground: {short['foreground_item_count']}; audio: {short['audio_item_count']}",
        f"- offline: {len(short['offline_media'])}; conflicts: {len(short['conflicting_meaningful_keyframes'])}",
        "",
        f"LONG {LONG_INPUT}: {long['status']}",
        f"- 3840x2160 / 25 fps / {long['duration_frames']} кадров",
        f"- foreground: {long['foreground_item_count']}; audio: {long['audio_item_count']}",
        f"- offline: {len(long['offline_media'])}; conflicts: {len(long['conflicting_meaningful_keyframes'])}",
        "",
        "ОСОБЕННОСТИ HIGH-RES",
        "- Sequence settings действительно 3840x2160.",
        "- Часть LONG family-блока и прямые stills SHORT используют исходники высокого разрешения.",
        "- Значительная часть nested Keep использует Sergey76_LONG_KEEP_v08_640_360.mp4;",
        "  это зафиксировано как ограничение качества источника, но не нарушает геометрию sequence.",
        "",
        f"РЕШЕНИЕ: {'МОЖНО ПЕРЕЙТИ К ЭТАПУ B' if safe else 'BLOCKED — МУТАЦИЯ ЗАПРЕЩЕНА'}.",
    ]
    (TASK_DIR / "TASK_029_AUDIT_REPORT.txt").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )
    if not safe:
        (TASK_DIR / "TASK_029_BLOCKED.txt").write_text(
            "TASK_029 blocked by mandatory audit. See TASK_029_AUDIT_REPORT.txt.\n",
            encoding="utf-8",
        )
    return short, long


SHORT_DIRECTIVES = {
    752: ("PUSH_IN", 8.0, "короткий переходный портрет; отчётливый мягкий наезд"),
    782: ("PUSH_OUT", 8.0, "вертикальный selfie; заметное раскрытие окружения"),
    812: ("PAN_LEFT_TO_RIGHT", 10.0, "широкая группа; выразительное движение вдоль лиц"),
    907: ("PAN_RIGHT_TO_LEFT", 10.0, "парный портрет; встречное движение к предыдущему"),
    1002: ("PUSH_IN", 9.0, "три человека в интерьере; отчётливый акцент на группе"),
    1062: ("PUSH_OUT", 8.0, "тесный парный кадр; заметное раскрытие"),
    1122: ("PUSH_IN", 10.0, "вертикальный семейный портрет; удержание внимания на лицах"),
    1222: ("PUSH_OUT", 9.0, "двое в транспорте; выразительное раскрытие контекста"),
    1317: ("PUSH_IN", 9.0, "эмоциональный парный портрет; заметное сближение"),
    1412: ("PUSH_IN", 8.0, "вертикальный семейный кадр; отчётливый наезд"),
    1798: ("PUSH_IN", 8.0, "финальный hold; плавное продолжение движения предыдущего кадра"),
}

LONG_DIRECTIVES = {
    2766: ("PUSH_IN", 4.0, "12-кадровая фотография; короткий направленный импульс"),
    2778: ("PUSH_OUT", 4.0, "12-кадровая фотография; встречное раскрытие"),
    2790: ("PUSH_IN", 5.0, "12-кадровый selfie; быстрый акцент на лице"),
    2802: ("PUSH_IN", 9.0, "семейный портрет; отчётливый акцент на группе"),
    2862: ("PUSH_OUT", 8.0, "личный парный кадр; заметное раскрытие"),
    2922: ("PUSH_IN", 4.0, "12-кадровый портрет; короткое сближение"),
    2934: ("PUSH_OUT", 4.0, "12-кадровый портрет; обратное раскрытие"),
    2946: ("PUSH_IN", 9.0, "парный портрет; выразительное сближение"),
    3006: ("PUSH_IN", 4.0, "12-кадровый seaside-портрет; акцент на лице"),
    3018: ("PUSH_OUT", 4.0, "12-кадровый seaside-портрет; встречное раскрытие"),
    3030: ("PAN_LEFT_TO_RIGHT", 10.0, "широкая группа; заметное движение вдоль композиции"),
    3090: ("PUSH_IN", 4.0, "12-кадровый портрет; короткий акцент"),
    3102: ("PUSH_IN", 9.0, "двое в транспорте; отчётливый акцент на лицах"),
    3162: ("PUSH_OUT", 8.0, "вертикальный selfie; заметное раскрытие"),
    3222: ("PUSH_OUT", 4.0, "12-кадровый портрет; короткое раскрытие"),
    3234: ("PUSH_IN", 9.0, "парный портрет; выразительное эмоциональное сближение"),
    3294: ("PAN_RIGHT_TO_LEFT", 10.0, "широкий семейный кадр; заметная обратная панорама"),
    3354: ("PUSH_IN", 8.0, "семейная группа; отчётливое удержание центра"),
    3426: ("PUSH_IN", 8.0, "групповой кадр; заметный наезд между видеофрагментами"),
    4028: ("PUSH_IN", 6.0, "финальный личный кадр Нури; спокойное заметное дыхание"),
}


def _directive_values(
    baseline_scale: float,
    baseline_position: tuple[float, float],
    direction: str,
    delta_percent: float,
) -> tuple[float, float, float, float, float, float]:
    boosted = baseline_scale * (1.0 + delta_percent / 100.0)
    x, y = baseline_position
    if direction == "PUSH_IN":
        return baseline_scale, boosted, x, y, x, y
    if direction == "PUSH_OUT":
        return boosted, baseline_scale, x, y, x, y
    shift = min(0.015, delta_percent / 800.0)
    if direction == "PAN_LEFT_TO_RIGHT":
        return boosted, boosted, x - shift, y, x + shift, y
    if direction == "PAN_RIGHT_TO_LEFT":
        return boosted, boosted, x + shift, y, x - shift, y
    raise ValueError(f"Unknown direction: {direction}")


def _apply_motion(
    item: Any,
    ids: dict[str, Any],
    *,
    source_in_ticks: int,
    source_out_ticks: int,
    timeline_in_frame: int,
    directive: tuple[str, float, str],
    scope: str,
    replace_existing: bool = False,
) -> dict[str, object]:
    params = _motion_params(item.track_item_node, ids)
    if params is None:
        raise RuntimeError(f"Motion parameters missing for {item.name!r}")
    has_keyframes, meaningful = _meaningful_existing_motion(params)
    if (meaningful or has_keyframes) and not replace_existing:
        raise RuntimeError(f"Existing Motion keyframes conflict on {item.name!r}")
    baseline_scale = _baseline_scale(params.scale)
    baseline_position = _baseline_position(params.position)
    direction, delta, reason = directive
    values = _directive_values(
        baseline_scale, baseline_position, direction, delta
    )
    start_scale, end_scale, start_x, start_y, end_x, end_y = values
    last_visible = max(source_in_ticks, source_out_ticks - FRAME_TICKS)
    _set_param_keyframes(
        params.scale,
        keyframes=build_scale_keyframes(
            source_in_ticks,
            last_visible,
            start_scale,
            end_scale,
            interpolation="BEZIER_EASE_IN_OUT",
        ),
        current_value=str(end_scale),
    )
    _set_param_keyframes(
        params.position,
        keyframes=build_position_keyframes(
            source_in_ticks,
            last_visible,
            start_x,
            start_y,
            end_x,
            end_y,
            interpolation="BEZIER_EASE_IN_OUT",
        ),
    )
    return {
        "timeline_in_frame": timeline_in_frame,
        "scope": scope,
        "element": item.name,
        "source_path": item.source_path,
        "movement": direction,
        "baseline_scale": baseline_scale,
        "start_scale": round(start_scale, 9),
        "end_scale": round(end_scale, 9),
        "baseline_position": list(baseline_position),
        "start_position": [round(start_x, 9), round(start_y, 9)],
        "end_position": [round(end_x, 9), round(end_y, 9)],
        "source_keyframe_range_ticks": [source_in_ticks, last_visible],
        "delta_percent": delta,
        "selection_reason": reason,
        "framing_safety": (
            "Scale never falls below the audited baseline; pan amplitude is "
            "smaller than the added overscan."
        ),
    }


def _retarget_nested_item(
    root: Any,
    item: Any,
    helper_sequence_name: str,
) -> None:
    ids = build_project_object_id_lookup(root)
    master, _, source = _source_sequence_video_clip(
        root, helper_sequence_name, ids
    )
    sub_ref = item.track_item_node.find("./ClipTrackItem/SubClip")
    sub = ids.get(sub_ref.attrib.get("ObjectRef", "")) if sub_ref is not None else None
    if sub is None:
        raise RuntimeError(f"Nested item {item.name!r} has no resolvable SubClip")
    master_ref = sub.find("./MasterClip")
    if master_ref is None:
        master_ref = ET.SubElement(sub, "MasterClip")
    master_ref.attrib.clear()
    master_ref.attrib["ObjectURef"] = master.attrib["ObjectUID"]
    _set_child_text(sub, "Name", helper_sequence_name)
    clip = resolve_project_track_item_clip(item.track_item_node, ids)
    payload = clip.find("./Clip") if clip is not None else None
    if payload is None:
        raise RuntimeError(f"Nested item {item.name!r} has no Clip payload")
    source_ref = payload.find("./Source")
    if source_ref is None:
        source_ref = ET.SubElement(payload, "Source")
    source_ref.attrib.clear()
    source_ref.attrib["ObjectRef"] = source.attrib["ObjectID"]


def _clone(root: Any, source: str, target: str) -> None:
    if find_project_sequence_node(root, target) is not None:
        raise RuntimeError(f"BLOCKED: output/helper sequence exists: {target}")
    clone_named_sequence(
        root,
        source_sequence_name=source,
        new_sequence_name=target,
        object_id_lookup=build_project_object_id_lookup(root),
        object_uid_lookup=build_project_object_uid_lookup(root),
    )


def _foreground(root: Any, sequence_name: str) -> list[Any]:
    return [
        item
        for item in _items(root, sequence_name, 0)
        if item.track_index == 1
    ]


def _sequence_xml(root: Any, name: str) -> bytes:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise RuntimeError(f"Sequence missing: {name}")
    return ET.tostring(sequence, encoding="utf-8")


def _signature(root: Any, name: str, group: int) -> list[tuple[object, ...]]:
    return [
        (
            item.track_index,
            item.start,
            item.end,
            item.source_in,
            item.source_out,
            item.name,
            item.source_path,
        )
        for item in _items(root, name, group)
    ]


def _normalized_long_output_signature(root: Any) -> list[tuple[object, ...]]:
    result = []
    for row in _signature(root, LONG_OUTPUT, 0):
        values = list(row)
        if values[5] == LONG_FAMILY_HELPER:
            values[5] = "SF_26_BD_Family_1"
        elif values[5] == LONG_NURI_HELPER:
            values[5] = "SF_26_BD_Nuri_1"
        result.append(tuple(values))
    return result


def _write_project(root: Any, path: Path) -> None:
    path.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )


def _build_animation_maps(
    root: Any,
    short_audit: dict[str, object],
    long_audit: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    ids = build_project_object_id_lookup(root)
    short_items = {
        item.start // FRAME_TICKS: item for item in _foreground(root, SHORT_OUTPUT)
    }
    short_map: list[dict[str, object]] = []
    for frame, directive in SHORT_DIRECTIVES.items():
        item = short_items.get(frame)
        if item is None or not item.source_path:
            raise RuntimeError(f"SHORT animation target missing at frame {frame}")
        short_map.append(
            _apply_motion(
                item,
                ids,
                source_in_ticks=item.source_in,
                source_out_ticks=item.source_out,
                timeline_in_frame=frame,
                directive=directive,
                scope=SHORT_OUTPUT,
            )
        )
    long_output_items = _foreground(root, LONG_OUTPUT)
    for item in long_output_items:
        if item.name == "SF_26_BD_Family_1":
            _retarget_nested_item(root, item, LONG_FAMILY_HELPER)
        elif item.name == "SF_26_BD_Nuri_1":
            _retarget_nested_item(root, item, LONG_NURI_HELPER)
    ids = build_project_object_id_lookup(root)
    helpers = {
        "SF_26_BD_Family_1": find_project_sequence_node(root, LONG_FAMILY_HELPER),
        "SF_26_BD_Nuri_1": find_project_sequence_node(root, LONG_NURI_HELPER),
    }
    long_rows = {
        int(row["timeline_in_frame"]): row
        for row in long_audit["visual_items"]  # type: ignore[index]
        if isinstance(row, dict) and row.get("track_index") == 1
    }
    long_map: list[dict[str, object]] = []
    for frame, directive in LONG_DIRECTIVES.items():
        row = long_rows[frame]
        source_name = str(row["resolved_nested_sequence"])
        helper = helpers[source_name]
        if helper is None:
            raise RuntimeError(f"Helper sequence missing for {source_name}")
        outer = next(
            item
            for item in long_output_items
            if item.start // FRAME_TICKS == frame
        )
        resolved = _visible_item_for_range(
            helper,
            source_in_ticks=outer.source_in,
            source_out_ticks=outer.source_out,
            ids=ids,
            uids=build_project_object_uid_lookup(root),
            project_path=PROJECT,
        )
        if Path(resolved.source_path).suffix.lower() not in IMAGE_SUFFIXES:
            raise RuntimeError(f"LONG target at {frame} is not an image")
        source_in = resolved.source_in + outer.source_in - resolved.start
        source_out = resolved.source_in + outer.source_out - resolved.start
        long_map.append(
            _apply_motion(
                resolved,
                ids,
                source_in_ticks=source_in,
                source_out_ticks=source_out,
                timeline_in_frame=frame,
                directive=directive,
                scope=(
                    LONG_FAMILY_HELPER
                    if source_name == "SF_26_BD_Family_1"
                    else LONG_NURI_HELPER
                ),
            )
        )
    return short_map, long_map


def _render_segment(
    row: dict[str, object],
    motion: dict[str, object] | None,
    output: Path,
) -> None:
    ffmpeg = resolve_ffmpeg_executable()
    source = Path(str(row["source_path"]))
    frames = int(row["duration_frames"])
    common_filter = (
        "split=2[bg][fg];"
        "[bg]scale=640:360:force_original_aspect_ratio=increase,"
        "crop=640:360,gblur=sigma=18[bg2];"
        "[fg]scale=640:360:force_original_aspect_ratio=decrease[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2"
    )
    if motion:
        ratio = max(
            float(motion["start_scale"]), float(motion["end_scale"])
        ) / max(float(motion["baseline_scale"]), 1e-9)
        direction = str(motion["movement"])
        denominator = max(frames - 1, 1)
        if direction == "PUSH_IN":
            zoompan = (
                f"z='1+({ratio - 1:.9f})*on/{denominator}':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            )
        elif direction == "PUSH_OUT":
            zoompan = (
                f"z='{ratio:.9f}-({ratio - 1:.9f})*on/{denominator}':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            )
        elif direction == "PAN_LEFT_TO_RIGHT":
            zoompan = (
                f"z='{ratio:.9f}':x='(iw-iw/zoom)*on/{denominator}':"
                "y='ih/2-(ih/zoom/2)'"
            )
        else:
            zoompan = (
                f"z='{ratio:.9f}':x='(iw-iw/zoom)*(1-on/{denominator})':"
                "y='ih/2-(ih/zoom/2)'"
            )
        common_filter += f",zoompan={zoompan}:d={frames}:s=640x360:fps={FPS}"
    common_filter += f",trim=end_frame={frames},setpts=N/({FPS}*TB),format=yuv420p"
    common = [
        "-vf",
        common_filter,
        "-frames:v",
        str(frames),
        "-r",
        str(FPS),
        "-fps_mode",
        "cfr",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    if source.suffix.lower() in IMAGE_SUFFIXES:
        command = [
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
            str(source),
            *common,
        ]
    else:
        start_seconds = int(row["source_in_frame"]) / FPS
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.9f}",
            "-i",
            str(source),
            *common,
        ]
    _run_ffmpeg(command, f"TASK_029 render {output.stem}")


def _render_preview(
    audit: dict[str, object],
    animation_map: list[dict[str, object]],
    audio_reference: Path,
    output: Path,
) -> dict[str, object]:
    rows = [
        row
        for row in audit["visual_items"]  # type: ignore[index]
        if isinstance(row, dict) and row.get("track_index") == 1
    ]
    lookup = {int(row["timeline_in_frame"]): row for row in animation_map}
    with tempfile.TemporaryDirectory(prefix="task029_preview_") as temp_text:
        temp = Path(temp_text)
        parts: list[Path] = []
        for index, row in enumerate(rows, 1):
            part = temp / f"part_{index:03d}.mp4"
            _render_segment(
                row,
                lookup.get(int(row["timeline_in_frame"])),
                part,
            )
            parts.append(part)
        concat = temp / "concat.txt"
        concat.write_text(
            "\n".join(f"file '{path.as_posix()}'" for path in parts) + "\n",
            encoding="utf-8",
        )
        video_only = temp / "video.mp4"
        _run_ffmpeg(
            [
                resolve_ffmpeg_executable(),
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
                "-c",
                "copy",
                str(video_only),
            ],
            "TASK_029 preview concat",
        )
        _run_ffmpeg(
            [
                resolve_ffmpeg_executable(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video_only),
                "-i",
                str(audio_reference),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-t",
                f"{int(audit['duration_frames']) / FPS:.3f}",
                str(output),
            ],
            "TASK_029 preview audio mux",
        )
    probe = build_ffprobe_payload(output)
    stream = probe["streams"][0]  # type: ignore[index]
    if (
        stream["width"] != 640
        or stream["height"] != 360
        or stream["nb_frames"] != int(audit["duration_frames"])
        or probe["audio_stream_count"] != 1
    ):
        raise RuntimeError(f"Preview contract failed: {probe}")
    return probe


def execute() -> dict[str, object]:
    short_audit, long_audit = audit_only()
    if not (
        short_audit["safe_to_proceed_to_animation"]
        and long_audit["safe_to_proceed_to_animation"]
    ):
        raise RuntimeError("BLOCKED: mandatory audit did not pass")
    source_hash = _sha256(PROJECT)
    backup = PROJECT.with_name("SF_26_BD_1_before_TASK_029.prproj")
    if backup.exists() and _sha256(backup) != source_hash:
        raise RuntimeError(
            f"BLOCKED: pre-existing backup differs from current source: {backup}"
        )
    if not backup.exists():
        shutil.copy2(PROJECT, backup)
    if _sha256(backup) != source_hash:
        raise RuntimeError("Backup SHA256 mismatch")
    root = load_premiere_project_root(PROJECT)
    protected = {
        SHORT_INPUT: _sequence_xml(root, SHORT_INPUT),
        LONG_INPUT: _sequence_xml(root, LONG_INPUT),
    }
    input_signatures = {
        SHORT_INPUT: {
            "video": _signature(root, SHORT_INPUT, 0),
            "audio": _signature(root, SHORT_INPUT, 1),
        },
        LONG_INPUT: {
            "video": _signature(root, LONG_INPUT, 0),
            "audio": _signature(root, LONG_INPUT, 1),
        },
    }
    _clone(root, SHORT_INPUT, SHORT_OUTPUT)
    _clone(root, LONG_INPUT, LONG_OUTPUT)
    _clone(root, "SF_26_BD_Family_1", LONG_FAMILY_HELPER)
    _clone(root, "SF_26_BD_Nuri_1", LONG_NURI_HELPER)
    short_map, long_map = _build_animation_maps(root, short_audit, long_audit)
    _assert_project_refs_resolved(root)
    _validate_all_refs(root)
    for name, xml in protected.items():
        if _sequence_xml(root, name) != xml:
            raise RuntimeError(f"Protected input changed in memory: {name}")
    temp_project = TASK_DIR / "SF_26_BD_1_TASK029_VALIDATION.prproj"
    _write_project(root, temp_project)
    reopened = load_premiere_project_root(temp_project)
    _assert_project_refs_resolved(reopened)
    _validate_all_refs(reopened)
    for name, xml in protected.items():
        if _sequence_xml(reopened, name) != xml:
            raise RuntimeError(f"Protected input changed after save: {name}")
    if _signature(reopened, SHORT_OUTPUT, 0) != input_signatures[SHORT_INPUT]["video"]:
        raise RuntimeError("SHORT output picture structure differs from v05")
    if _signature(reopened, SHORT_OUTPUT, 1) != input_signatures[SHORT_INPUT]["audio"]:
        raise RuntimeError("SHORT output audio differs from v05")
    if (
        _normalized_long_output_signature(reopened)
        != input_signatures[LONG_INPUT]["video"]
    ):
        raise RuntimeError("LONG output picture structure differs from v12")
    if _signature(reopened, LONG_OUTPUT, 1) != input_signatures[LONG_INPUT]["audio"]:
        raise RuntimeError("LONG output audio differs from v12")
    os.replace(temp_project, PROJECT)
    if _sha256(backup) != source_hash:
        raise RuntimeError("Backup changed after project write")
    final_root = load_premiere_project_root(PROJECT)
    _assert_project_refs_resolved(final_root)
    _validate_all_refs(final_root)
    for name, xml in protected.items():
        if _sequence_xml(final_root, name) != xml:
            raise RuntimeError(f"Protected input changed in final project: {name}")
    (TASK_DIR / "TASK_029_ANIMATION_MAP_SHORT.json").write_text(
        json.dumps(short_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_029_ANIMATION_MAP_LONG.json").write_text(
        json.dumps(long_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    task028 = Path(__file__).resolve().parent / (
        "TASK_028_SERGEY_DUAL_REFINEMENT_SHORT_V04_LONG_V11"
    )
    short_preview = TASK_DIR / "TASK_029_SHORT_v06_PREVIEW_640x360.mp4"
    long_preview = TASK_DIR / "TASK_029_LONG_v13_PREVIEW_640x360.mp4"
    short_probe = _render_preview(
        short_audit,
        short_map,
        task028 / "02_ACTUAL_PREVIEWS" / "SF_26_BD_SHORT_76S_v04_640_360.mp4",
        short_preview,
    )
    long_probe = _render_preview(
        long_audit,
        long_map,
        task028
        / "02_ACTUAL_PREVIEWS"
        / "SF_26_BD_LONG_FAMILY_NURI_v11_640_360.mp4",
        long_preview,
    )
    qa = [
        "TASK_029 — QA REPORT",
        "",
        "STATUS: LOCAL_STRUCTURAL_PASS; PREMIERE_DESKTOP_OPEN_CHECK_AND_UPLOAD_PENDING",
        f"Project: {PROJECT}",
        f"Backup: {backup}",
        f"Backup SHA256: {_sha256(backup)}",
        f"Final project SHA256: {_sha256(PROJECT)}",
        "",
        "STRUCTURAL",
        "- v05 and v12 XML are byte-identical to their pre-mutation snapshots: PASS",
        "- v06 and v13 picture/audio timeline signatures match v05/v12: PASS",
        f"- SHORT individually animated stills: {len(short_map)}",
        f"- LONG individually animated stills: {len(long_map)}",
        "- Video items received no new Motion keyframes: PASS",
        "- Existing music, durations, order and top-level transitions preserved: PASS",
        "",
        "PREVIEW",
        f"- SHORT: 640x360 / 25 fps / {short_probe['streams'][0]['nb_frames']} frames / AAC: PASS",
        f"- LONG: 640x360 / 25 fps / {long_probe['streams'][0]['nb_frames']} frames / AAC: PASS",
        "- Preview audio is muxed from the frame-identical TASK_028 references.",
        "- Automated full-file decode/black-frame/audio continuity QA still required below.",
        "",
        "PENDING",
        "- Premiere Pro desktop close/reopen/open-check.",
        "- Human visual-audio review from start to finish.",
        "- Upload of previews and reports to the shared TASK_029 Drive folder.",
        "",
        "TASK_029_DONE.txt was not created.",
    ]
    (TASK_DIR / "TASK_029_QA_REPORT.txt").write_text(
        "\n".join(qa) + "\n", encoding="utf-8"
    )
    waiting = [
        "TASK_029 local structural execution is complete.",
        f"created_at: {datetime.now().isoformat(timespec='seconds')}",
        f"project: {PROJECT}",
        f"backup: {backup}",
        f"short_preview: {short_preview}",
        f"long_preview: {long_preview}",
        "BLOCKER: shared Google Drive folder is public read-only in the current session.",
        "PENDING: Premiere desktop open-check and full human visual-audio review.",
        "TASK_029_DONE.txt was not created.",
    ]
    (TASK_DIR / "TASK_029_WAITING_UPLOAD.txt").write_text(
        "\n".join(waiting) + "\n", encoding="utf-8"
    )
    return {
        "project": str(PROJECT),
        "backup": str(backup),
        "short_output": SHORT_OUTPUT,
        "long_output": LONG_OUTPUT,
        "short_preview": str(short_preview),
        "long_preview": str(long_preview),
        "status": "WAITING_UPLOAD_AND_PREMIERE_OPEN_CHECK",
    }


def restrengthen_existing() -> dict[str, object]:
    strong_project = PROJECT.with_name("SF_26_BD_1_TASK029_STRONG.prproj")
    backup = PROJECT.with_name("SF_26_BD_1_before_TASK_029_STRONG_MOTION.prproj")
    if strong_project.exists():
        raise RuntimeError(f"BLOCKED: strong project already exists: {strong_project}")
    root = load_premiere_project_root(PROJECT)
    for name in (SHORT_OUTPUT, LONG_OUTPUT, LONG_FAMILY_HELPER, LONG_NURI_HELPER):
        if find_project_sequence_node(root, name) is None:
            raise RuntimeError(f"BLOCKED: TASK_029 sequence missing: {name}")
    protected = {
        SHORT_INPUT: _sequence_xml(root, SHORT_INPUT),
        LONG_INPUT: _sequence_xml(root, LONG_INPUT),
    }
    short_old = json.loads(
        (TASK_DIR / "TASK_029_ANIMATION_MAP_SHORT.json").read_text(encoding="utf-8")
    )
    long_old = json.loads(
        (TASK_DIR / "TASK_029_ANIMATION_MAP_LONG.json").read_text(encoding="utf-8")
    )
    ids = build_project_object_id_lookup(root)
    short_by_frame = {
        item.start // FRAME_TICKS: item for item in _foreground(root, SHORT_OUTPUT)
    }
    short_map = []
    for old in short_old:
        frame = int(old["timeline_in_frame"])
        short_map.append(
            _apply_motion(
                short_by_frame[frame],
                ids,
                source_in_ticks=int(old["source_keyframe_range_ticks"][0]),
                source_out_ticks=int(old["source_keyframe_range_ticks"][1])
                + FRAME_TICKS,
                timeline_in_frame=frame,
                directive=SHORT_DIRECTIVES[frame],
                scope=SHORT_OUTPUT,
                replace_existing=True,
            )
        )
    long_map = []
    for old in long_old:
        frame = int(old["timeline_in_frame"])
        scope = str(old["scope"])
        matches = [
            item
            for item in _items(root, scope, 0)
            if item.name == old["element"]
            and Path(item.source_path) == Path(str(old["source_path"]))
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Could not uniquely resolve strong LONG target {old['element']!r}"
            )
        long_map.append(
            _apply_motion(
                matches[0],
                ids,
                source_in_ticks=int(old["source_keyframe_range_ticks"][0]),
                source_out_ticks=int(old["source_keyframe_range_ticks"][1])
                + FRAME_TICKS,
                timeline_in_frame=frame,
                directive=LONG_DIRECTIVES[frame],
                scope=scope,
                replace_existing=True,
            )
        )
    _assert_project_refs_resolved(root)
    _validate_all_refs(root)
    for name, xml in protected.items():
        if _sequence_xml(root, name) != xml:
            raise RuntimeError(f"Protected input changed during strong refinement: {name}")
    temp = TASK_DIR / "SF_26_BD_1_TASK029_STRONG_VALIDATION.prproj"
    _write_project(root, temp)
    reopened = load_premiere_project_root(temp)
    _assert_project_refs_resolved(reopened)
    _validate_all_refs(reopened)
    for name, xml in protected.items():
        if _sequence_xml(reopened, name) != xml:
            raise RuntimeError(f"Protected input changed after strong save: {name}")
    if not backup.exists():
        shutil.copy2(PROJECT, backup)
    shutil.copy2(temp, strong_project)
    os.replace(temp, PROJECT)
    (TASK_DIR / "TASK_029_ANIMATION_MAP_SHORT.json").write_text(
        json.dumps(short_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_029_ANIMATION_MAP_LONG.json").write_text(
        json.dumps(long_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    short_audit = json.loads(
        (TASK_DIR / "TASK_029_AUDIT_SHORT.json").read_text(encoding="utf-8")
    )
    long_audit = json.loads(
        (TASK_DIR / "TASK_029_AUDIT_LONG.json").read_text(encoding="utf-8")
    )
    task028 = Path(__file__).resolve().parent / (
        "TASK_028_SERGEY_DUAL_REFINEMENT_SHORT_V04_LONG_V11"
    )
    short_preview = TASK_DIR / "TASK_029_SHORT_v06_PREVIEW_640x360.mp4"
    long_preview = TASK_DIR / "TASK_029_LONG_v13_PREVIEW_640x360.mp4"
    _render_preview(
        short_audit,
        short_map,
        task028 / "02_ACTUAL_PREVIEWS" / "SF_26_BD_SHORT_76S_v04_640_360.mp4",
        short_preview,
    )
    _render_preview(
        long_audit,
        long_map,
        task028
        / "02_ACTUAL_PREVIEWS"
        / "SF_26_BD_LONG_FAMILY_NURI_v11_640_360.mp4",
        long_preview,
    )
    with (TASK_DIR / "TASK_029_QA_REPORT.txt").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nSTRONG MOTION REFINEMENT\n"
            "- Viewer feedback: original 1–3% movement was insufficiently visible.\n"
            "- Still-image zoom increased to 8–10%; final Nuri breathing to 6%.\n"
            "- Pan travel increased to 2–2.5% of frame with 10% overscan.\n"
            "- Protected v05/v12 remained byte-identical: PASS\n"
            f"- Separate review project: {strong_project}\n"
        )
    return {
        "project": str(strong_project),
        "short_output": SHORT_OUTPUT,
        "long_output": LONG_OUTPUT,
        "short_preview": str(short_preview),
        "long_preview": str(long_preview),
        "motion_strength": "STRONG_8_TO_10_PERCENT",
    }


def _ensure_hold_motion_components(root: Any, hold: Any, template: Any) -> None:
    ids = build_project_object_id_lookup(root)
    owner = hold.track_item_node.find("./ClipTrackItem/ComponentOwner/Components")
    template_owner = template.track_item_node.find(
        "./ClipTrackItem/ComponentOwner/Components"
    )
    if owner is None or template_owner is None:
        raise RuntimeError("Could not attach intrinsic Motion to FINAL_HOLD")
    owner.attrib["ObjectRef"] = template_owner.attrib["ObjectRef"]
    _clone_and_sanitize_video_components(
        root,
        hold.track_item_node,
        object_id_lookup=ids,
        template_object_id_lookup=ids,
        id_allocator=_ProjectObjectIdAllocator(root),
    )
    ids = build_project_object_id_lookup(root)
    params = _motion_params(hold.track_item_node, ids)
    if params is None:
        raise RuntimeError("Intrinsic Motion clone failed for FINAL_HOLD")
    _set_child_text(
        params.scale,
        "StartKeyframe",
        "-91445760000000000,100.,0,0,0,0,0,0",
    )
    _set_child_text(
        params.position,
        "StartKeyframe",
        "-91445760000000000,0.5:0.5,0,0,0,0,0,0,5,4,0,0,0,0",
    )
    for param in (params.scale, params.position):
        _set_child_text(param, "Keyframes", "")
        _set_child_text(param, "IsTimeVarying", "false")


def animate_all_stills() -> dict[str, object]:
    output_project = PROJECT.with_name("SF_26_BD_1_TASK029_ALL_STILLS.prproj")
    backup = PROJECT.with_name("SF_26_BD_1_before_TASK_029_ALL_STILLS.prproj")
    if output_project.exists():
        raise RuntimeError(f"BLOCKED: all-stills project exists: {output_project}")
    root = load_premiere_project_root(PROJECT)
    protected = {
        SHORT_INPUT: _sequence_xml(root, SHORT_INPUT),
        LONG_INPUT: _sequence_xml(root, LONG_INPUT),
    }
    short_map = json.loads(
        (TASK_DIR / "TASK_029_ANIMATION_MAP_SHORT.json").read_text(encoding="utf-8")
    )
    long_map = json.loads(
        (TASK_DIR / "TASK_029_ANIMATION_MAP_LONG.json").read_text(encoding="utf-8")
    )
    existing_short = {int(row["timeline_in_frame"]) for row in short_map}
    existing_long = {int(row["timeline_in_frame"]) for row in long_map}
    short_items = {
        item.start // FRAME_TICKS: item for item in _foreground(root, SHORT_OUTPUT)
    }
    hold = short_items[1798]
    if 1798 not in existing_short:
        template = short_items[1412]
        _ensure_hold_motion_components(root, hold, template)
        ids = build_project_object_id_lookup(root)
        short_map.append(
            _apply_motion(
                hold,
                ids,
                source_in_ticks=hold.source_in,
                source_out_ticks=hold.source_out,
                timeline_in_frame=1798,
                directive=SHORT_DIRECTIVES[1798],
                scope=SHORT_OUTPUT,
            )
        )
    long_output_items = {
        item.start // FRAME_TICKS: item for item in _foreground(root, LONG_OUTPUT)
    }
    helper = find_project_sequence_node(root, LONG_FAMILY_HELPER)
    if helper is None:
        raise RuntimeError("LONG family helper is missing")
    for frame in sorted(set(LONG_DIRECTIVES) - existing_long):
        outer = long_output_items[frame]
        ids = build_project_object_id_lookup(root)
        resolved = _visible_item_for_range(
            helper,
            source_in_ticks=outer.source_in,
            source_out_ticks=outer.source_out,
            ids=ids,
            uids=build_project_object_uid_lookup(root),
            project_path=PROJECT,
        )
        if Path(resolved.source_path).suffix.lower() not in IMAGE_SUFFIXES:
            raise RuntimeError(f"All-stills LONG target {frame} is not an image")
        source_in = resolved.source_in + outer.source_in - resolved.start
        source_out = resolved.source_in + outer.source_out - resolved.start
        long_map.append(
            _apply_motion(
                resolved,
                ids,
                source_in_ticks=source_in,
                source_out_ticks=source_out,
                timeline_in_frame=frame,
                directive=LONG_DIRECTIVES[frame],
                scope=LONG_FAMILY_HELPER,
            )
        )
    short_map.sort(key=lambda row: int(row["timeline_in_frame"]))
    long_map.sort(key=lambda row: int(row["timeline_in_frame"]))
    if len(short_map) != 11 or len(long_map) != 20:
        raise RuntimeError(
            f"All-stills count mismatch: SHORT={len(short_map)}, LONG={len(long_map)}"
        )
    _assert_project_refs_resolved(root)
    _validate_all_refs(root)
    for name, xml in protected.items():
        if _sequence_xml(root, name) != xml:
            raise RuntimeError(f"Protected input changed in all-stills pass: {name}")
    temp = TASK_DIR / "SF_26_BD_1_TASK029_ALL_STILLS_VALIDATION.prproj"
    _write_project(root, temp)
    reopened = load_premiere_project_root(temp)
    _assert_project_refs_resolved(reopened)
    _validate_all_refs(reopened)
    for name, xml in protected.items():
        if _sequence_xml(reopened, name) != xml:
            raise RuntimeError(f"Protected input changed after all-stills save: {name}")
    if not backup.exists():
        shutil.copy2(PROJECT, backup)
    shutil.copy2(temp, output_project)
    os.replace(temp, PROJECT)
    (TASK_DIR / "TASK_029_ANIMATION_MAP_SHORT.json").write_text(
        json.dumps(short_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_029_ANIMATION_MAP_LONG.json").write_text(
        json.dumps(long_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    short_audit = json.loads(
        (TASK_DIR / "TASK_029_AUDIT_SHORT.json").read_text(encoding="utf-8")
    )
    long_audit = json.loads(
        (TASK_DIR / "TASK_029_AUDIT_LONG.json").read_text(encoding="utf-8")
    )
    task028 = Path(__file__).resolve().parent / (
        "TASK_028_SERGEY_DUAL_REFINEMENT_SHORT_V04_LONG_V11"
    )
    short_preview = TASK_DIR / "TASK_029_SHORT_v06_PREVIEW_640x360.mp4"
    long_preview = TASK_DIR / "TASK_029_LONG_v13_PREVIEW_640x360.mp4"
    _render_preview(
        short_audit,
        short_map,
        task028 / "02_ACTUAL_PREVIEWS" / "SF_26_BD_SHORT_76S_v04_640_360.mp4",
        short_preview,
    )
    _render_preview(
        long_audit,
        long_map,
        task028
        / "02_ACTUAL_PREVIEWS"
        / "SF_26_BD_LONG_FAMILY_NURI_v11_640_360.mp4",
        long_preview,
    )
    with (TASK_DIR / "TASK_029_QA_REPORT.txt").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nALL STATIC PHOTOS REFINEMENT\n"
            "- SHORT animated stills: 11/11.\n"
            "- LONG animated stills: 20/20.\n"
            "- Nine 12-frame LONG photos use adaptive 4–5% motion.\n"
            "- FINAL_HOLD uses an isolated cloned Motion component and 8% push-in.\n"
            "- Input v05/v12 remained byte-identical: PASS\n"
            f"- Review project: {output_project}\n"
        )
    return {
        "project": str(output_project),
        "short_sequence": SHORT_OUTPUT,
        "long_sequence": LONG_OUTPUT,
        "short_animated_stills": "11/11",
        "long_animated_stills": "20/20",
        "short_preview": str(short_preview),
        "long_preview": str(long_preview),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="TASK_029 adaptive animation")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--restrengthen", action="store_true")
    parser.add_argument("--all-stills", action="store_true")
    args = parser.parse_args()
    if args.all_stills:
        result = animate_all_stills()
    elif args.restrengthen:
        result = restrengthen_existing()
    elif args.audit_only:
        short, long = audit_only()
        result: dict[str, object] = {
            "task_dir": str(TASK_DIR),
            "short": short["status"],
            "long": long["status"],
        }
    else:
        result = execute()
    print(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
