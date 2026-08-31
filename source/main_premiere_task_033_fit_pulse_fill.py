from __future__ import annotations

import gzip
import hashlib
import json
import math
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

from utils.premiere_media_import_export import _clone_filter_component
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
from utils.premiere_project_export import (
    _ProjectObjectIdAllocator,
    clone_named_sequence,
)
from utils.premiere_sequence_delete_only import build_ffprobe_payload
from utils.premiere_sequence_motion import (
    _baseline_position,
    _baseline_scale,
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

TASK_ID = "TASK_033"
FPS = 25
FRAME_TICKS = _frame_ticks(FPS)
FW, FH = 3840, 2160
SOURCE_PROJECT = Path("input") / 'SF_26_Bd_Art_4_TASK032.prproj'
BACKUP_PROJECT = Path("input") / 'SF_26_Bd_Art_4_TASK032_before_TASK_033.prproj'
OUTPUT_PROJECT = Path(
    r"input/SF_26_Bd_Art_5_TASK033_EXTREME_MOTION_FINAL.prproj"
)
SOURCE_SEQUENCE = "SF_26_Bd_Art_7"
COLOR_SEQUENCE = "SF_26_Bd_Art_8_TASK033_COLOR_EXTREME"
FINAL_SEQUENCE = "SF_26_Bd_Art_9_TASK033_FIT_PULSE_FILL_FINAL"
COLOR_PREVIEW = Path(
    r"input/SF_26_Bd_Art_8_TASK033_COLOR_EXTREME_640_360.mp4"
)
FINAL_PREVIEW = Path(
    r"input/SF_26_Bd_Art_9_TASK033_FIT_PULSE_FILL_FINAL_640_360.mp4"
)
COMPARISON_PREVIEW = Path(
    r"input/SF_26_Bd_Art_8_vs_9_TASK033_COMPARISON_1280_360.mp4"
)
REPO_DIR = Path(__file__).resolve().parent / (
    "TASK_033_ART_AUDIT_EXTREME_COLOR_FIT_FILL_PULSE"
)
LOCAL_DIR = Path(
    r"input/TASK_033_ART_AUDIT_EXTREME_COLOR_FIT_FILL_PULSE"
)
IMAGE_EXTRA = {".jfif", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
LUMETRI_PARAM_IDS = {
    "temperature": "7",
    "tint": "8",
    "saturation": "20",
    "exposure": "11",
    "contrast": "12",
    "highlights": "13",
    "shadows": "14",
    "whites": "15",
    "blacks": "16",
    "sharpen": "29",
    "vibrance": "30",
    "vignette_amount": "51",
    "faded_film": "28",
}


from utils.premiere_art_runtime import configure_module
configure_module(globals(), "033")

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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True)))


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


def _row(item: Any) -> dict[str, Any]:
    return {
        "id": _clip_id(item),
        "track": item.track_index,
        "name": item.name,
        "start": item.start // FRAME_TICKS,
        "end": item.end // FRAME_TICKS,
        "dur": item.duration // FRAME_TICKS,
        "source_in": item.source_in // FRAME_TICKS,
        "source_out": item.source_out // FRAME_TICKS,
        "path": item.source_path,
        "kind": _kind(item.source_path),
        "online": (not item.source_path) or Path(item.source_path).is_file(),
    }


def _image_size(path: str) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def _fit_fill_scales(path: str, travel: float) -> tuple[float, float]:
    iw, ih = _image_size(path)
    fit = min(FW / iw, FH / ih) * 100.0
    fill = fit * (1.0 + travel)
    return fit, fill


def _bucket(name: str, start: int) -> str:
    lowered = name.casefold()
    if start < 390:
        return "youth"
    if any(token in lowered for token in ("20230815", "use_the_supplied", "generated_video", "keep")):
        return "computer_video"
    if start >= 2812:
        return "nuri"
    if start >= 2487:
        return "computer"
    if any(token in lowered for token in ("mattis", "levitan", "rodin", "240415", "2208012", "comfy")):
        return "art_overlay"
    if "240830" in lowered or "exec-" in lowered:
        return "digital"
    return "metal"


def _color_values(bucket: str) -> dict[str, float]:
    return {
        "youth": {
            "temperature": 10.0,
            "exposure": 0.25,
            "contrast": 35.0,
            "highlights": -35.0,
            "shadows": 28.0,
            "whites": -8.0,
            "blacks": -12.0,
            "saturation": 118.0,
            "vibrance": 12.0,
            "faded_film": 8.0,
        },
        "metal": {
            "temperature": 6.0,
            "exposure": 0.05,
            "contrast": 48.0,
            "highlights": -45.0,
            "shadows": 35.0,
            "whites": -18.0,
            "blacks": -15.0,
            "saturation": 122.0,
            "vibrance": 10.0,
        },
        "art_overlay": {
            "temperature": 4.0,
            "contrast": 38.0,
            "highlights": -30.0,
            "shadows": 22.0,
            "saturation": 114.0,
            "vibrance": 6.0,
        },
        "digital": {
            "temperature": -4.0,
            "exposure": 0.2,
            "contrast": 42.0,
            "highlights": -25.0,
            "shadows": 18.0,
            "saturation": 116.0,
        },
        "computer": {
            "temperature": -8.0,
            "exposure": 0.3,
            "contrast": 40.0,
            "highlights": -20.0,
            "shadows": 20.0,
            "saturation": 112.0,
        },
        "computer_video": {
            "temperature": -6.0,
            "exposure": 0.2,
            "contrast": 32.0,
            "highlights": -18.0,
            "shadows": 16.0,
            "saturation": 112.0,
        },
        "nuri": {
            "temperature": 8.0,
            "exposure": 0.45,
            "contrast": 28.0,
            "highlights": -40.0,
            "shadows": 40.0,
            "whites": -10.0,
            "blacks": -5.0,
            "saturation": 112.0,
            "vignette_amount": -0.18,
        },
        "background": {
            "temperature": 14.0,
            "tint": 4.0,
            "exposure": -0.4,
            "contrast": 30.0,
            "highlights": -25.0,
            "shadows": 20.0,
            "saturation": 78.0,
            "vignette_amount": -0.35,
        },
    }[bucket]


def _find_lumetri_template(root: ET.Element) -> ET.Element:
    for component in root.iter("VideoFilterComponent"):
        if (component.findtext("./MatchName") or "").strip() == "AE.ADBE Lumetri":
            return component
    raise PremiereProjectError("No Lumetri template in project")


def _find_blur_template(root: ET.Element) -> ET.Element | None:
    for component in root.iter("VideoFilterComponent"):
        if (component.findtext("./MatchName") or "").strip() == "AE.ADBE Gaussian Blur 2":
            return component
    return None


def _set_start_value(param: ET.Element, value: float) -> None:
    node = param.find("./StartKeyframe")
    if node is None:
        raise PremiereProjectError("Lumetri parameter missing StartKeyframe")
    parts = (node.text or "").split(",")
    if len(parts) < 2:
        raise PremiereProjectError("Invalid StartKeyframe")
    parts[1] = f"{value:.6f}".rstrip("0").rstrip(".")
    node.text = ",".join(parts)


def _component_chain(item: Any, ids: dict[str, ET.Element]) -> ET.Element | None:
    chain_ref = item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components")
    if chain_ref is None:
        return None
    return ids.get(chain_ref.attrib.get("ObjectRef", ""))


def _iter_match(item: Any, ids: dict[str, ET.Element], match_name: str) -> list[ET.Element]:
    chain = _component_chain(item, ids)
    if chain is None:
        return []
    found: list[ET.Element] = []
    for ref in chain.findall("./ComponentChain/Components/Component"):
        current = ids.get(ref.attrib.get("ObjectRef", ""))
        if current is not None and (current.findtext("./MatchName") or "").strip() == match_name:
            found.append(current)
    return found


def _apply_param_values(component: ET.Element, ids: dict[str, ET.Element], values: dict[str, float]) -> None:
    params: dict[str, ET.Element] = {}
    for param_ref in component.findall("./Component/Params/Param"):
        param = ids.get(param_ref.attrib.get("ObjectRef", ""))
        if param is not None:
            params[(param.findtext("./ParameterID") or "").strip()] = param
    for name, value in values.items():
        pid = LUMETRI_PARAM_IDS.get(name)
        if pid and pid in params:
            _set_start_value(params[pid], float(value))


def _append_filter(
    root: ET.Element,
    item: Any,
    template: ET.Element,
    *,
    ids: dict[str, ET.Element],
    allocator: _ProjectObjectIdAllocator,
) -> ET.Element:
    chain = _component_chain(item, ids)
    if chain is None:
        raise PremiereProjectError(f"No component chain for {item.name}")
    components = chain.find("./ComponentChain/Components")
    if components is None:
        raise PremiereProjectError(f"No Components container for {item.name}")
    cloned = _clone_filter_component(
        root,
        template,
        object_id_lookup=ids,
        template_object_id_lookup=ids,
        id_allocator=allocator,
    )
    next_index = max(
        [int(ref.attrib.get("Index", "-1")) for ref in components.findall("./Component")],
        default=-1,
    ) + 1
    ET.SubElement(components, "Component", {"Index": str(next_index), "ObjectRef": cloned.attrib["ObjectID"]})
    return cloned


def _apply_or_add_lumetri(
    root: ET.Element,
    item: Any,
    values: dict[str, float],
    *,
    ids: dict[str, ET.Element],
    allocator: _ProjectObjectIdAllocator,
    template: ET.Element,
) -> str:
    existing = _iter_match(item, ids, "AE.ADBE Lumetri")
    if existing:
        _apply_param_values(existing[-1], ids, values)
        return "adjust_existing"
    chain = _component_chain(item, ids)
    if chain is None:
        return "skip_no_chain"
    if chain.find("./ComponentChain/Components") is None:
        return "skip_no_components_container"
    cloned = _append_filter(root, item, template, ids=ids, allocator=allocator)
    _apply_param_values(cloned, ids, values)
    return "added"


def _sequence_xml_sha(root: ET.Element, name: str) -> str:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise PremiereProjectError(f"Missing {name}")
    return hashlib.sha256(ET.tostring(sequence, encoding="utf-8")).hexdigest()


def _build_plan(root: ET.Element, source_sha: str, art7_sha: str) -> dict[str, Any]:
    video = _items(root, SOURCE_SEQUENCE, SOURCE_PROJECT, 0)
    audio = _items(root, SOURCE_SEQUENCE, SOURCE_PROJECT, 1)
    duration = max(_sequence_duration(video), _sequence_duration(audio)) // FRAME_TICKS
    color_ops: list[dict[str, Any]] = []
    anim_ops: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    still_index = 0
    for item in video:
        row = _row(item)
        if not row["online"] and row["kind"] != "nested":
            raise PremiereProjectError(f"Offline media: {row['path']}")
        if row["kind"] == "nested":
            protected.append({"id": row["id"], "reason": "Nested background / sequence — не анимировать как фото"})
            color_ops.append(
                {
                    "operation_id": f"COLOR_BG_{row['id']}",
                    "target": row,
                    "bucket": "background",
                    "values": _color_values("background"),
                    "also_blur": True,
                    "reason": "Единый бронзово-охристый фон на Nested Sequence 05",
                }
            )
            continue
        bucket = _bucket(item.name, row["start"])
        color_ops.append(
            {
                "operation_id": f"COLOR_{row['id']}",
                "target": row,
                "bucket": bucket,
                "values": _color_values(bucket if bucket != "computer_video" else "computer_video"),
                "also_blur": False,
                "reason": f"Сильная поклипная коррекция bucket={bucket}",
            }
        )
        if row["kind"] != "image":
            exclusions.append({"id": row["id"], "reason": "Настоящее видео — без новой fit-pulse-fill анимации"})
            protected.append({"id": row["id"], "reason": "video"})
            continue
        if row["dur"] < 36:
            exclusions.append({"id": row["id"], "reason": f"Слишком короткий still ({row['dur']} кадров)"})
            continue
        travel = 0.12 + (still_index % 9) * 0.02  # 12..28%
        fit, fill = _fit_fill_scales(row["path"], travel)
        direction = "FIT_TO_FILL" if still_index % 2 == 0 else "FILL_TO_FIT"
        pulse = 0.02 + (still_index % 4) * 0.01  # 2..5%
        pulse_at = 0.38 + (still_index % 5) * 0.04
        pan_x = ((still_index % 5) - 2) * 0.004
        pan_y = ((still_index % 3) - 1) * 0.005
        if direction == "FIT_TO_FILL":
            start_scale, end_scale = fit, fill
        else:
            start_scale, end_scale = fill, fit
        mid_scale = (start_scale + end_scale) / 2.0
        pulse_down = mid_scale * (1.0 - pulse)
        pulse_up = mid_scale * (1.0 + pulse * 0.5)
        anim_ops.append(
            {
                "operation_id": f"ANIM_{row['id']}",
                "target": row,
                "direction": direction,
                "subject_position": [0.5 + pan_x, 0.5 + pan_y],
                "fit_scale": round(fit, 6),
                "fill_scale": round(fill, 6),
                "travel_percent": round(travel * 100, 2),
                "pulse_percent": round(pulse * 100, 2),
                "pulse_at_normalized": round(pulse_at, 3),
                "keyframes": {
                    "scale": [
                        {"t": 0.0, "v": round(start_scale, 6)},
                        {"t": pulse_at, "v": round(pulse_down, 6)},
                        {"t": min(0.95, pulse_at + 0.12), "v": round(pulse_up, 6)},
                        {"t": 1.0, "v": round(end_scale, 6)},
                    ],
                    "position_start": [0.5, 0.5],
                    "position_end": [0.5 + pan_x, 0.5 + pan_y],
                },
                "audio_sync": {
                    "mode": "musical_plausible_internal",
                    "note": "Надёжный beat-detect не подтверждён; пульс на внутренней музыкальной точке клипа",
                    "timeline_frame": row["start"] + int(row["dur"] * pulse_at),
                },
                "reason": "Индивидуальная fit-pulse-fill анимация самостоятельного фото",
            }
        )
        still_index += 1
    return {
        "task_id": TASK_ID,
        "source_project": str(SOURCE_PROJECT),
        "source_sequence": SOURCE_SEQUENCE,
        "input_fingerprint": {
            "project_sha256": source_sha,
            "project_bytes": SOURCE_PROJECT.stat().st_size,
            "sequence_xml_sha256": art7_sha,
            "duration_frames": duration,
            "fps": FPS,
            "frame_size": [FW, FH],
            "video_clips": len(video),
            "audio_clips": len(audio),
        },
        "audit_summary": {
            "narrative": "Юность → чеканка/двойные экспозиции → цифровые формы → компьютер → Нури/финал",
            "editorial_decision": "KEEP_ALL",
            "editorial_reason": (
                "SF_26_Bd_Art_7 уже сжат (3339 кадров). Чёрных дыр, offline media и явных каталожных дублей "
                "не найдено. Удаление/перестановка не улучшат драматургию; риск разрушить музыку и фон."
            ),
            "color_need": "Многие stills без сильного Lumetri — нужна экстремальная поклипная коррекция",
            "background_need": "V0 Nested Sequence 05 уже даёт единый фон; усилить бронзово-охристый look",
            "animation_need": "Большинство фото без time-varying Motion — применить fit-pulse-fill только на Art_9",
            "music": "MUZ_Beatles_Symph_260526_0959.mp3 на A1/A2 — сохранить sync, не заменять",
        },
        "protected_items": protected,
        "remove_or_trim": [],
        "add_or_reorder": [],
        "color_operations": color_ops,
        "background_operations": [
            {
                "target_sequence_track": 0,
                "source": "Nested Sequence 05",
                "method": "outer_lumetri_bronze_plus_optional_gaussian_blur",
                "palette": "bronze/ochre/umber",
            }
        ],
        "photo_animation_operations": anim_ops,
        "animation_exclusions": exclusions,
        "audio_sync_points": [
            {
                "clip": op["target"]["id"],
                "timeline_frame": op["audio_sync"]["timeline_frame"],
                "mode": op["audio_sync"]["mode"],
            }
            for op in anim_ops
        ],
        "output_sequences": {
            "color": COLOR_SEQUENCE,
            "final": FINAL_SEQUENCE,
            "output_project": str(OUTPUT_PROJECT),
            "color_preview": str(COLOR_PREVIEW),
            "final_preview": str(FINAL_PREVIEW),
        },
        "validation_rules": [
            "JSON first, mutation second",
            "reread JSON from disk before execute",
            "never mutate SF_26_Bd_Art_7",
            "no animation on genuine video",
            "unique clip identity by track+start+name",
            "Art_8 has no TASK_033 fit-pulse-fill",
            "Art_9 has fit-pulse-fill on eligible stills",
        ],
        "fallbacks": [
            "If Drive read-only: local Proj folder + WAITING_UPLOAD",
            "If short still: exclude with reason",
            "If Lumetri missing chain: skip and log",
        ],
        "stop_conditions": [
            "offline/ambiguous media",
            "output paths already exist",
            "dry-run failure",
            "Art_7 fingerprint change",
        ],
    }


def _validate_plan(plan: dict[str, Any], root: ET.Element) -> dict[str, Any]:
    errors: list[str] = []
    required = [
        "task_id",
        "source_project",
        "source_sequence",
        "input_fingerprint",
        "audit_summary",
        "protected_items",
        "remove_or_trim",
        "add_or_reorder",
        "color_operations",
        "background_operations",
        "photo_animation_operations",
        "audio_sync_points",
        "output_sequences",
        "validation_rules",
        "fallbacks",
        "stop_conditions",
    ]
    for key in required:
        if key not in plan:
            errors.append(f"missing {key}")
    if plan.get("task_id") != TASK_ID:
        errors.append("task_id mismatch")
    if plan.get("source_sequence") != SOURCE_SEQUENCE:
        errors.append("source_sequence mismatch")
    for path in (OUTPUT_PROJECT, COLOR_PREVIEW, FINAL_PREVIEW):
        if path.exists() and path != OUTPUT_PROJECT:
            errors.append(f"output exists: {path}")
    # OUTPUT_PROJECT may be rewritten from a fresh source copy if a prior run failed mid-way.
    names = list_named_project_sequence_names(root)
    for name in (COLOR_SEQUENCE, FINAL_SEQUENCE):
        if name in names:
            errors.append(f"sequence already exists: {name}")
    video = {_clip_id(item): item for item in _items(root, SOURCE_SEQUENCE, SOURCE_PROJECT, 0)}
    for op in plan.get("color_operations", []):
        target = op["target"]
        if target["id"] not in video:
            errors.append(f"color target missing: {target['id']}")
        elif target["path"] and not Path(target["path"]).is_file() and target["kind"] != "nested":
            errors.append(f"offline color target: {target['path']}")
    for op in plan.get("photo_animation_operations", []):
        target = op["target"]
        item = video.get(target["id"])
        if item is None:
            errors.append(f"anim target missing: {target['id']}")
            continue
        if _kind(item.source_path) != "image":
            errors.append(f"anim targets non-image: {target['id']}")
        if not Path(target["path"]).is_file():
            errors.append(f"anim offline: {target['path']}")
        if op["fill_scale"] <= op["fit_scale"]:
            errors.append(f"fill<=fit: {target['id']}")
        travel = (op["fill_scale"] / op["fit_scale"] - 1.0) * 100.0
        if travel < 11.5 or travel > 28.5:
            errors.append(f"travel out of 12-28%: {target['id']}={travel}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "error_count": len(errors),
        "errors": errors,
        "color_operations": len(plan.get("color_operations", [])),
        "animation_operations": len(plan.get("photo_animation_operations", [])),
        "exclusions": len(plan.get("animation_exclusions", [])),
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
        raise PremiereProjectError(f"Target not unique: {target['id']} matches={len(matches)}")
    return matches[0]


def _apply_color(root: ET.Element, plan: dict[str, Any], sequence_name: str) -> list[dict[str, Any]]:
    ids = build_project_object_id_lookup(root)
    allocator = _ProjectObjectIdAllocator(root)
    template = _find_lumetri_template(root)
    blur_template = _find_blur_template(root)
    items = _items(root, sequence_name, OUTPUT_PROJECT, 0)
    log: list[dict[str, Any]] = []
    for op in plan["color_operations"]:
        item = _find_item(items, op["target"])
        action = _apply_or_add_lumetri(
            root, item, op["values"], ids=ids, allocator=allocator, template=template
        )
        blur_action = None
        if op.get("also_blur") and blur_template is not None:
            if not _iter_match(item, ids, "AE.ADBE Gaussian Blur 2"):
                blur = _append_filter(root, item, blur_template, ids=ids, allocator=allocator)
                params: dict[str, ET.Element] = {}
                for param_ref in blur.findall("./Component/Params/Param"):
                    param = ids.get(param_ref.attrib.get("ObjectRef", ""))
                    if param is not None:
                        params[(param.findtext("./ParameterID") or "").strip()] = param
                if "1" in params:
                    _set_start_value(params["1"], 18.0)
                if "3" in params:
                    _set_start_value(params["3"], 1.0)
                blur_action = "blur_added"
            else:
                blur_action = "blur_exists"
        log.append(
            {
                "operation_id": op["operation_id"],
                "action": action,
                "blur": blur_action,
                "bucket": op["bucket"],
                "values": op["values"],
                "target": op["target"]["id"],
            }
        )
    return log


def _multi_scale_keyframes(source_in: int, source_out: int, points: list[dict[str, float]]) -> str:
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


def _apply_animation(root: ET.Element, plan: dict[str, Any], sequence_name: str) -> list[dict[str, Any]]:
    ids = build_project_object_id_lookup(root)
    items = _items(root, sequence_name, OUTPUT_PROJECT, 0)
    log: list[dict[str, Any]] = []
    for op in plan["photo_animation_operations"]:
        item = _find_item(items, op["target"])
        if _kind(item.source_path) != "image":
            raise PremiereProjectError(f"Refusing to animate video: {item.name}")
        params = _motion_params(item.track_item_node, ids)
        if params is None:
            log.append({"operation_id": op["operation_id"], "action": "skip_no_motion"})
            continue
        scale_points = op["keyframes"]["scale"]
        scale_kf = _multi_scale_keyframes(item.source_in, item.source_out, scale_points)
        _set_param_keyframes(
            params.scale,
            keyframes=scale_kf,
            current_value=f"{scale_points[-1]['v']:.6f}".rstrip("0").rstrip("."),
        )
        sx, sy = op["keyframes"]["position_start"]
        ex, ey = op["keyframes"]["position_end"]
        first = item.source_in
        last = max(first, item.source_out - FRAME_TICKS)
        _set_param_keyframes(
            params.position,
            keyframes=build_position_keyframes(first, last, sx, sy, ex, ey),
        )
        log.append(
            {
                "operation_id": op["operation_id"],
                "action": "applied",
                "direction": op["direction"],
                "fit": op["fit_scale"],
                "fill": op["fill_scale"],
                "pulse": op["pulse_percent"],
            }
        )
    return log


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
                "-b:a",
                "192k",
                "-shortest",
                "-t",
                duration_sec,
                str(output_path),
            ],
            check=True,
        )
        return
    with tempfile.TemporaryDirectory(prefix="task033_audio_") as temp_text:
        temp = Path(temp_text)
        wavs: list[tuple[Path, int]] = []
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
        filters: list[str] = []
        labels: list[str] = []
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


def _render_preview(root: ET.Element, sequence_name: str, output_path: Path) -> dict[str, Any]:
    ffmpeg = resolve_ffmpeg_executable()
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    video = _items(root, sequence_name, OUTPUT_PROJECT, 0)
    audio = _items(root, sequence_name, OUTPUT_PROJECT, 1)
    frames = max(_sequence_duration(video), _sequence_duration(audio)) // FRAME_TICKS
    ranges = _visible_ranges([item for item in video if item.track_index > 0] or video)
    with tempfile.TemporaryDirectory(prefix="task033_preview_") as temp_text:
        temp = Path(temp_text)
        rendered: list[Path] = []
        for index, (start, end, item) in enumerate(ranges, 1):
            source_item = item
            sequence_in = start
            if item.source_path and Path(item.source_path).suffix.lower() in {".jfif", ".jpe"}:
                converted = temp / f"conv_{index:03d}.jpg"
                Image.open(item.source_path).convert("RGB").save(converted, "JPEG", quality=92)
                source_item = SimpleNamespace(
                    source_path=str(converted),
                    source_in=item.source_in,
                    start=item.start,
                )
            elif not item.source_path:
                nested = find_project_sequence_node(root, item.name)
                if nested is None:
                    raise PremiereProjectError(f"Nested preview missing: {item.name}")
                source_item = _visible_item_for_range(
                    nested,
                    source_in_ticks=item.source_in + (start - item.start),
                    source_out_ticks=item.source_in + (end - item.start),
                    ids=ids,
                    uids=uids,
                    project_path=OUTPUT_PROJECT,
                )
                sequence_in = item.source_in + (start - item.start)
            segment = temp / f"seg_{index:03d}.mp4"
            _render_segment(
                ffmpeg=ffmpeg,
                item=source_item,
                sequence_in_ticks=sequence_in,
                frames=(end - start) // FRAME_TICKS,
                fps=FPS,
                width=640,
                height=360,
                output_path=segment,
            )
            rendered.append(segment)
        concat = temp / "concat.txt"
        concat.write_text(
            "\n".join(f"file '{path.as_posix()}'" for path in rendered) + "\n",
            encoding="utf-8",
        )
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
        _mux_audio(ffmpeg, silent, audio, frames, output_path)
    probe = build_ffprobe_payload(output_path)
    return {"path": str(output_path), "frames": frames, "probe": probe, "segments": len(ranges)}


def _make_comparison(ffmpeg: str) -> None:
    if not COLOR_PREVIEW.is_file() or not FINAL_PREVIEW.is_file():
        return
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(COLOR_PREVIEW),
            "-i",
            str(FINAL_PREVIEW),
            "-filter_complex",
            (
                "[0:v]drawtext=text='COLOR EXTREME':x=12:y=12:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.5[v0];"
                "[1:v]drawtext=text='FIT PULSE FILL':x=12:y=12:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.5[v1];"
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
            "-b:a",
            "160k",
            "-shortest",
            str(COMPARISON_PREVIEW),
        ],
        check=False,
    )


def _copy_reports() -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    for path in REPO_DIR.glob("TASK_033_*"):
        if path.is_file():
            shutil.copy2(path, LOCAL_DIR / path.name)
    for path in (COLOR_PREVIEW, FINAL_PREVIEW, COMPARISON_PREVIEW, OUTPUT_PROJECT):
        if path.is_file() and path.stat().st_size < 120_000_000:
            shutil.copy2(path, LOCAL_DIR / path.name)


def main() -> dict[str, Any]:
    from utils.premiere_art_runtime import require_fresh_run
    require_fresh_run("033")
    started = datetime.now().isoformat(timespec="seconds")
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCE_PROJECT.is_file():
        raise PremiereProjectError(f"Missing source project: {SOURCE_PROJECT}")
    for path in (COLOR_PREVIEW, FINAL_PREVIEW):
        if path.exists():
            raise FileExistsError(str(path))
    # The configured launcher requires fresh output and report paths.

    source_sha = _sha256(SOURCE_PROJECT)
    root = load_premiere_project_root(SOURCE_PROJECT)
    if SOURCE_SEQUENCE not in list_named_project_sequence_names(root):
        raise PremiereProjectError(f"Missing {SOURCE_SEQUENCE}")
    art7_sha = _sequence_xml_sha(root, SOURCE_SEQUENCE)
    settings = _video_settings(
        find_project_sequence_node(root, SOURCE_SEQUENCE),
        build_project_object_id_lookup(root),
    )
    if settings.get("frame_rate") != str(FRAME_TICKS) or settings.get("frame_rect") != "0,0,3840,2160":
        raise PremiereProjectError("Unexpected frame settings")

    plan = _build_plan(root, source_sha, art7_sha)
    plan_path = REPO_DIR / "TASK_033_EXECUTION_PLAN.json"
    _write_json(plan_path, plan)
    loaded = json.loads(plan_path.read_text(encoding="utf-8"))
    validation = _validate_plan(loaded, root)
    _write_text(
        REPO_DIR / "TASK_033_JSON_VALIDATION.txt",
        "\n".join(
            [
                "TASK_033 JSON VALIDATION",
                f"Plan: {plan_path}",
                f"Status: {validation['status']}",
                f"Color ops: {validation['color_operations']}",
                f"Animation ops: {validation['animation_operations']}",
                f"Errors: {validation['errors'] or 'none'}",
                "Dry-run is in-memory only; source project not written.",
            ]
        ),
    )
    _write_json(REPO_DIR / "TASK_033_DRY_RUN.json", validation)
    if validation["status"] != "PASS":
        _write_text(REPO_DIR / "TASK_033_BLOCKED.txt", "DRY-RUN FAIL\n" + "\n".join(validation["errors"]))
        return {"status": "BLOCKED", "validation": validation}

    _write_text(
        REPO_DIR / "TASK_033_AUDIT.md",
        "\n".join(
            [
                "# TASK_033 AUDIT",
                "",
                f"- Источник: `{SOURCE_PROJECT}` / `{SOURCE_SEQUENCE}`",
                f"- Длительность: {loaded['input_fingerprint']['duration_frames']} кадров / "
                f"{loaded['input_fingerprint']['duration_frames'] / FPS:.2f} с",
                f"- SHA проекта: `{source_sha}`",
                f"- SHA sequence XML: `{art7_sha}`",
                "",
                "## Вывод",
                loaded["audit_summary"]["editorial_reason"],
                "",
                f"- Цвет: {len(loaded['color_operations'])} операций",
                f"- Анимация фото: {len(loaded['photo_animation_operations'])}",
                f"- Исключения анимации: {len(loaded['animation_exclusions'])}",
                "- Музыка сохраняется без замены",
            ]
        ),
    )

    backup = BACKUP_PROJECT
    if backup.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_PROJECT.with_name(f"{BACKUP_PROJECT.stem}_{stamp}{BACKUP_PROJECT.suffix}")
    shutil.copy2(SOURCE_PROJECT, backup)
    shutil.copy2(SOURCE_PROJECT, OUTPUT_PROJECT)

    work = load_premiere_project_root(OUTPUT_PROJECT)
    clone_named_sequence(
        work,
        source_sequence_name=SOURCE_SEQUENCE,
        new_sequence_name=COLOR_SEQUENCE,
        object_id_lookup=build_project_object_id_lookup(work),
        object_uid_lookup=build_project_object_uid_lookup(work),
    )
    color_log = _apply_color(work, loaded, COLOR_SEQUENCE)
    clone_named_sequence(
        work,
        source_sequence_name=COLOR_SEQUENCE,
        new_sequence_name=FINAL_SEQUENCE,
        object_id_lookup=build_project_object_id_lookup(work),
        object_uid_lookup=build_project_object_uid_lookup(work),
    )
    anim_log = _apply_animation(work, loaded, FINAL_SEQUENCE)
    _validate_all_refs(work)
    if _sequence_xml_sha(work, SOURCE_SEQUENCE) != art7_sha:
        raise PremiereProjectError("Art_7 changed inside output project")
    _save_project(work, OUTPUT_PROJECT)

    color_preview = _render_preview(work, COLOR_SEQUENCE, COLOR_PREVIEW)
    final_preview = _render_preview(work, FINAL_SEQUENCE, FINAL_PREVIEW)
    _make_comparison(resolve_ffmpeg_executable())

    reopened = load_premiere_project_root(OUTPUT_PROJECT)
    _validate_all_refs(reopened)
    source_re = load_premiere_project_root(SOURCE_PROJECT)
    source_unchanged = _sha256(SOURCE_PROJECT) == source_sha and _sequence_xml_sha(source_re, SOURCE_SEQUENCE) == art7_sha
    out_art7_unchanged = _sequence_xml_sha(reopened, SOURCE_SEQUENCE) == art7_sha
    color_items = _items(reopened, COLOR_SEQUENCE, OUTPUT_PROJECT, 0)
    final_items = _items(reopened, FINAL_SEQUENCE, OUTPUT_PROJECT, 0)
    color_dur = _sequence_duration(color_items) // FRAME_TICKS
    final_dur = _sequence_duration(final_items) // FRAME_TICKS

    artifacts = {
        "source": {"path": str(SOURCE_PROJECT), "sha256": source_sha, "bytes": SOURCE_PROJECT.stat().st_size},
        "backup": {"path": str(backup), "sha256": _sha256(backup), "bytes": backup.stat().st_size},
        "output": {
            "path": str(OUTPUT_PROJECT),
            "sha256": _sha256(OUTPUT_PROJECT),
            "bytes": OUTPUT_PROJECT.stat().st_size,
        },
        "color_preview": {
            "path": str(COLOR_PREVIEW),
            "sha256": _sha256(COLOR_PREVIEW),
            "bytes": COLOR_PREVIEW.stat().st_size,
        },
        "final_preview": {
            "path": str(FINAL_PREVIEW),
            "sha256": _sha256(FINAL_PREVIEW),
            "bytes": FINAL_PREVIEW.stat().st_size,
        },
    }

    _write_json(REPO_DIR / "TASK_033_COLOR_APPLY_LOG.json", color_log)
    _write_json(REPO_DIR / "TASK_033_ANIMATION_APPLY_LOG.json", anim_log)
    _write_text(
        REPO_DIR / "TASK_033_CHANGELOG.md",
        "\n".join(
            [
                "# TASK_033 CHANGELOG",
                "",
                "- Editorial: KEEP_ALL (без remove/trim/reorder/add)",
                f"- Color sequence `{COLOR_SEQUENCE}`: {len(color_log)} клипов",
                f"- Final sequence `{FINAL_SEQUENCE}`: {sum(1 for row in anim_log if row.get('action')=='applied')} анимаций",
                f"- Backup: `{backup}`",
                f"- Output project: `{OUTPUT_PROJECT}`",
            ]
        ),
    )
    _write_text(
        REPO_DIR / "TASK_033_QA_REPORT.md",
        "\n".join(
            [
                "# TASK_033 QA",
                "",
                f"- Started: {started}",
                f"- Ended: {datetime.now().isoformat(timespec='seconds')}",
                f"- Source unchanged: {source_unchanged}",
                f"- Art_7 unchanged in output: {out_art7_unchanged}",
                f"- Color duration: {color_dur}",
                f"- Final duration: {final_dur}",
                f"- Color ops applied: {len(color_log)}",
                f"- Animations applied: {sum(1 for row in anim_log if row.get('action')=='applied')}",
                f"- Animation skipped/excluded logged: {len(loaded['animation_exclusions'])}",
                f"- Color preview segments: {color_preview['segments']}",
                f"- Final preview segments: {final_preview['segments']}",
                "- Premiere Desktop GUI open-check: XML reopen + previews; full GUI playback remains for Sergey/Muza",
                "- Drive write: not available → WAITING_UPLOAD",
            ]
        ),
    )
    status = "\n".join(
        [
            "TASK_033 LOCAL COMPLETE",
            "Статус: WAITING_UPLOAD",
            "Муза preview ещё не смотрела; Drive недоступен для записи.",
            f"Проект: {OUTPUT_PROJECT}",
            f"SHA256: {artifacts['output']['sha256']} bytes={artifacts['output']['bytes']}",
            f"Sequences: {COLOR_SEQUENCE} ; {FINAL_SEQUENCE}",
            f"Color preview: {COLOR_PREVIEW}",
            f"SHA256: {artifacts['color_preview']['sha256']} bytes={artifacts['color_preview']['bytes']}",
            f"Final preview: {FINAL_PREVIEW}",
            f"SHA256: {artifacts['final_preview']['sha256']} bytes={artifacts['final_preview']['bytes']}",
            f"Backup: {backup}",
            f"Art_7 unchanged: {source_unchanged and out_art7_unchanged}",
            f"Local reports: {LOCAL_DIR}",
            f"Repo reports: {REPO_DIR}",
        ]
    )
    _write_text(REPO_DIR / "TASK_033_WAITING_UPLOAD.txt", status)
    _copy_reports()
    _write_text(LOCAL_DIR / "TASK_033_WAITING_UPLOAD.txt", status)
    return {
        "status": "WAITING_UPLOAD",
        "duration_frames": final_dur,
        "color_ops": len(color_log),
        "animations": sum(1 for row in anim_log if row.get("action") == "applied"),
        "exclusions": len(loaded["animation_exclusions"]),
        "source_unchanged": source_unchanged and out_art7_unchanged,
        "artifacts": artifacts,
    }


def _render_still_motion_segment(
    *,
    ffmpeg: str,
    path: Path,
    frames: int,
    fit: float,
    fill: float,
    direction: str,
    pulse: float,
    pulse_at: float,
    output_path: Path,
) -> None:
    # Approximate Premiere fit/fill as relative zoom over letterboxed frame.
    start_z = 1.0 if direction == "FIT_TO_FILL" else 1.0 + (fill / fit - 1.0)
    end_z = 1.0 + (fill / fit - 1.0) if direction == "FIT_TO_FILL" else 1.0
    mid = max(1, int(frames * pulse_at))
    mid2 = min(frames - 1, mid + max(2, int(frames * 0.12)))
    # zoom expression with mid pulse
    # Linear zoom with a readable mid pulse bump via piecewise expression.
    z_expr = (
        f"if(lt(on,{mid}),"
        f"{start_z:.6f}+({end_z:.6f}-{start_z:.6f})*on/{max(1, frames - 1)},"
        f"if(lt(on,{mid2}),"
        f"{((start_z + end_z) / 2) * (1 - pulse):.6f},"
        f"{end_z:.6f}))"
    )
    vf = (
        f"scale=640:360:force_original_aspect_ratio=decrease,"
        f"pad=640:360:(ow-iw)/2:(oh-ih)/2:color=0x5A3A1E,"
        f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=640x360:fps={FPS},"
        f"format=yuv420p"
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
            str(path),
            "-vf",
            vf,
            "-frames:v",
            str(frames),
            "-r",
            str(FPS),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            str(output_path),
        ],
        check=True,
    )


def _render_colorish_segment(
    *,
    ffmpeg: str,
    item: Any,
    sequence_in_ticks: int,
    frames: int,
    bucket: str,
    output_path: Path,
) -> None:
    values = _color_values(bucket if bucket != "background" else "metal")
    sat = max(0.7, min(1.5, float(values.get("saturation", 100.0)) / 100.0))
    contrast = 1.0 + float(values.get("contrast", 0.0)) / 200.0
    brightness = float(values.get("exposure", 0.0)) * 0.08
    _render_segment(
        ffmpeg=ffmpeg,
        item=item,
        sequence_in_ticks=sequence_in_ticks,
        frames=frames,
        fps=FPS,
        width=640,
        height=360,
        output_path=output_path,
    )
    # post color grade approximation on the segment
    graded = output_path.with_name(output_path.stem + "_graded.mp4")
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(output_path),
            "-vf",
            f"eq=saturation={sat:.3f}:contrast={contrast:.3f}:brightness={brightness:.3f}",
            "-c:a",
            "copy",
            str(graded),
        ],
        check=True,
    )
    graded.replace(output_path)


def resume_distinct_previews() -> dict[str, Any]:
    plan = json.loads((REPO_DIR / "TASK_033_EXECUTION_PLAN.json").read_text(encoding="utf-8"))
    anim_by_id = {op["target"]["id"]: op for op in plan["photo_animation_operations"]}
    root = load_premiere_project_root(OUTPUT_PROJECT)
    ffmpeg = resolve_ffmpeg_executable()
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)

    def render_one(sequence_name: str, output_path: Path, *, motion: bool) -> dict[str, Any]:
        video = _items(root, sequence_name, OUTPUT_PROJECT, 0)
        audio = _items(root, sequence_name, OUTPUT_PROJECT, 1)
        frames = max(_sequence_duration(video), _sequence_duration(audio)) // FRAME_TICKS
        ranges = _visible_ranges([item for item in video if item.track_index > 0] or video)
        with tempfile.TemporaryDirectory(prefix="task033_prev2_") as temp_text:
            temp = Path(temp_text)
            rendered: list[Path] = []
            for index, (start, end, item) in enumerate(ranges, 1):
                seg_frames = (end - start) // FRAME_TICKS
                segment = temp / f"seg_{index:03d}.mp4"
                clip_id = _clip_id(item)
                source_item = item
                sequence_in = start
                media_path = Path(item.source_path) if item.source_path else None
                if media_path and media_path.suffix.lower() in {".jfif", ".jpe"}:
                    converted = temp / f"conv_{index:03d}.jpg"
                    Image.open(media_path).convert("RGB").save(converted, "JPEG", quality=92)
                    media_path = converted
                    source_item = SimpleNamespace(
                        source_path=str(converted),
                        source_in=item.source_in,
                        start=item.start,
                    )
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
                    media_path = Path(source_item.source_path)
                if (
                    motion
                    and clip_id in anim_by_id
                    and media_path is not None
                    and media_path.suffix.lower() in IMAGE_EXTRA | {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".jfif"}
                ):
                    op = anim_by_id[clip_id]
                    _render_still_motion_segment(
                        ffmpeg=ffmpeg,
                        path=media_path,
                        frames=seg_frames,
                        fit=float(op["fit_scale"]),
                        fill=float(op["fill_scale"]),
                        direction=op["direction"],
                        pulse=float(op["pulse_percent"]) / 100.0,
                        pulse_at=float(op["pulse_at_normalized"]),
                        output_path=segment,
                    )
                else:
                    bucket = _bucket(item.name, item.start // FRAME_TICKS)
                    _render_colorish_segment(
                        ffmpeg=ffmpeg,
                        item=source_item,
                        sequence_in_ticks=sequence_in,
                        frames=seg_frames,
                        bucket=bucket,
                        output_path=segment,
                    )
                rendered.append(segment)
            concat = temp / "concat.txt"
            concat.write_text(
                "\n".join(f"file '{path.as_posix()}'" for path in rendered) + "\n",
                encoding="utf-8",
            )
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
            _mux_audio(ffmpeg, silent, audio, frames, output_path)
        return {"path": str(output_path), "frames": frames, "sha256": _sha256(output_path), "bytes": output_path.stat().st_size}

    color = render_one(COLOR_SEQUENCE, COLOR_PREVIEW, motion=False)
    final = render_one(FINAL_SEQUENCE, FINAL_PREVIEW, motion=True)
    _make_comparison(ffmpeg)
    status = "\n".join(
        [
            "TASK_033 LOCAL COMPLETE",
            "Статус: WAITING_UPLOAD",
            "Preview пересобраны с видимой цветовой аппроксимацией и fit-pulse-fill zoom для Art_9.",
            f"Color preview SHA256: {color['sha256']} bytes={color['bytes']}",
            f"Final preview SHA256: {final['sha256']} bytes={final['bytes']}",
            f"Project: {OUTPUT_PROJECT}",
            f"Local reports: {LOCAL_DIR}",
        ]
    )
    _write_text(REPO_DIR / "TASK_033_WAITING_UPLOAD.txt", status)
    _copy_reports()
    _write_text(LOCAL_DIR / "TASK_033_WAITING_UPLOAD.txt", status)
    return {"status": "WAITING_UPLOAD", "color": color, "final": final}


if __name__ == "__main__":
    from main_premiere_art_task import main as launch
    launch(["--task", "033"] + sys.argv[1:])
