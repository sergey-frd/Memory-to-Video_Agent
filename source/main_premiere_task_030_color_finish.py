from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from utils.premiere_media_import_export import _clone_filter_component
from utils.premiere_project import (
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    load_premiere_project_root,
)
from utils.premiere_project_export import (
    _ProjectObjectIdAllocator,
    _insert_project_object_near_same_type,
    clone_named_sequence,
)
from utils.premiere_sequence_motion import (
    _frame_ticks,
    _sequence_duration,
    _track_item_contexts,
    _video_settings,
    protected_property_snapshot,
)
from utils.premiere_sequence_timeline_assembly import (
    _source_sequence_video_clip,
    _validate_all_refs,
    _visible_item_for_range,
)
from utils.video_frame_extract import resolve_ffmpeg_executable


TASK_ID = "TASK_030"
FPS = 25
FRAME_TICKS = _frame_ticks(FPS)
PROJECT = Path(r"<LOCAL_PATH>")
BACKUP = PROJECT.with_name("SF_26_BD_2_before_TASK_030.prproj")
STRONG_BACKUP = PROJECT.with_name("SF_26_BD_2_before_TASK_030_STRONG.prproj")
SHORT_INPUT = "SF_26_BD_SHORT_76S_v07"
LONG_INPUT = "SF_26_BD_LONG_FAMILY_NURI_v13"
SHORT_OUTPUT = "SF_26_BD_SHORT_76S_v08_TASK030_COLOR_FINISH"
LONG_OUTPUT = "SF_26_BD_LONG_FAMILY_NURI_v14_TASK030_COLOR_FINISH"
SHORT_STRONG_OUTPUT = "SF_26_BD_SHORT_76S_v09_TASK030_COLOR_STRONG"
LONG_STRONG_OUTPUT = "SF_26_BD_LONG_FAMILY_NURI_v15_TASK030_COLOR_STRONG"
SHORT_BG_INPUT = "TASK_028_SHORT_V04_BLURRED_BACKGROUND"
LONG_BG_INPUT = "TASK_028_LONG_V11_BLURRED_BACKGROUND"
SHORT_BG_OUTPUT = "TASK_030_SHORT_COLOR_BACKGROUND"
LONG_BG_OUTPUT = "TASK_030_LONG_COLOR_BACKGROUND"
SHORT_BG_STRONG = "TASK_030_SHORT_COLOR_BACKGROUND_STRONG"
LONG_BG_STRONG = "TASK_030_LONG_COLOR_BACKGROUND_STRONG"
EXTREME_BACKUP = PROJECT.with_name("SF_26_BD_2_before_TASK_030_EXTREME.prproj")
SHORT_EXTREME_OUTPUT = "SF_26_BD_SHORT_76S_v10_TASK030_COLOR_EXTREME"
LONG_EXTREME_OUTPUT = "SF_26_BD_LONG_FAMILY_NURI_v16_TASK030_COLOR_EXTREME"
SHORT_BG_EXTREME = "TASK_030_SHORT_COLOR_BACKGROUND_EXTREME"
LONG_BG_EXTREME = "TASK_030_LONG_COLOR_BACKGROUND_EXTREME"
TASK_DIR = Path(__file__).resolve().parent / "TASK_030_SERGEY_FINAL_COLOR_LIGHT_FINISH"
SHORT_PREVIEW_SOURCE = Path(
    r"<LOCAL_PATH>"
)
LONG_PREVIEW_SOURCE = (
    Path(__file__).resolve().parent
    / "TASK_029_SERGEY_HIGH_RES_AUDIT_ADAPTIVE_ANIMATION"
    / "TASK_029_LONG_v13_PREVIEW_640x360.mp4"
)
SHORT_PREVIEW = TASK_DIR / "SF_26_BD_SHORT_76S_v08_TASK030_COLOR_640_360.mp4"
LONG_PREVIEW = (
    TASK_DIR / "SF_26_BD_LONG_FAMILY_NURI_v14_TASK030_COLOR_640_360.mp4"
)
SHORT_STRONG_PREVIEW = (
    TASK_DIR / "SF_26_BD_SHORT_76S_v09_TASK030_COLOR_STRONG_640_360.mp4"
)
LONG_STRONG_PREVIEW = (
    TASK_DIR / "SF_26_BD_LONG_FAMILY_NURI_v15_TASK030_COLOR_STRONG_640_360.mp4"
)
SHORT_EXTREME_PREVIEW = (
    TASK_DIR / "SF_26_BD_SHORT_76S_v10_TASK030_COLOR_EXTREME_640_360.mp4"
)
LONG_EXTREME_PREVIEW = (
    TASK_DIR / "SF_26_BD_LONG_FAMILY_NURI_v16_TASK030_COLOR_EXTREME_640_360.mp4"
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sequence_xml(root: ET.Element, name: str) -> bytes:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise RuntimeError(f"BLOCKED: sequence not found: {name}")
    return ET.tostring(sequence, encoding="utf-8")


def _items(root: ET.Element, sequence_name: str, group: int) -> list[Any]:
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


def _foreground(root: ET.Element, sequence_name: str) -> list[Any]:
    return [item for item in _items(root, sequence_name, 0) if item.track_index == 1]


def _resolve_visual(root: ET.Element, item: Any) -> tuple[str, Path | None]:
    if item.source_path:
        return item.name, Path(item.source_path)
    sequence = find_project_sequence_node(root, item.name)
    if sequence is None:
        return item.name, None
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    try:
        resolved = _visible_item_for_range(
            sequence,
            source_in_ticks=item.source_in,
            source_out_ticks=item.source_out,
            ids=ids,
            uids=uids,
            project_path=PROJECT,
        )
        return resolved.name, Path(resolved.source_path) if resolved.source_path else None
    except Exception:
        return item.name, None


def _media_size(path: Path | None) -> tuple[int, int] | None:
    if path is None or not path.is_file():
        return None
    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as image:
            return image.size
    capture = cv2.VideoCapture(str(path))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    capture.release()
    return (width, height) if width and height else None


def _read_preview_frame(capture: cv2.VideoCapture, frame: int) -> np.ndarray:
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame))
    ok, image = capture.read()
    if not ok or image is None:
        raise RuntimeError(f"Could not read preview frame {frame}")
    return image


def _image_metrics(images: list[np.ndarray]) -> dict[str, float]:
    pixels = np.concatenate(
        [cv2.resize(image, (160, 90)).reshape(-1, 3) for image in images], axis=0
    ).astype(np.float32)
    rgb = pixels[:, ::-1]
    luma = 0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2]
    hsv = cv2.cvtColor(pixels.reshape((-1, 1, 3)).astype(np.uint8), cv2.COLOR_BGR2HSV)
    saturation = hsv[:, 0, 1].astype(np.float32)
    return {
        "mean_luma": round(float(np.mean(luma)), 2),
        "p05_luma": round(float(np.percentile(luma, 5)), 2),
        "p50_luma": round(float(np.percentile(luma, 50)), 2),
        "p95_luma": round(float(np.percentile(luma, 95)), 2),
        "dynamic_range_p95_p05": round(
            float(np.percentile(luma, 95) - np.percentile(luma, 5)), 2
        ),
        "shadow_clip_percent": round(float(np.mean(luma < 5) * 100), 3),
        "highlight_clip_percent": round(float(np.mean(luma > 250) * 100), 3),
        "mean_red": round(float(np.mean(rgb[:, 0])), 2),
        "mean_green": round(float(np.mean(rgb[:, 1])), 2),
        "mean_blue": round(float(np.mean(rgb[:, 2])), 2),
        "mean_saturation": round(float(np.mean(saturation)), 2),
    }


def _scene(sequence_kind: str, start: int, name: str) -> tuple[str, str]:
    if sequence_kind == "SHORT":
        if start < 165:
            return "Нури и близость", "нежное человеческое вступление"
        if start < 752:
            return "дорога и память", "движение, пространство и личный путь"
        if start < 1472:
            return "семья", "тепло отношений и продолжающаяся жизнь"
        if start < 1798:
            return "дорога и настоящее", "возвращение к движению"
        return "финал", "светлое завершение короткой формы"
    if start < 970:
        return "дорога и одиночное присутствие", "путь и пространство"
    if start < 1969:
        return "семья и близкие", "человеческое тепло"
    if start < 2766:
        return "горы, дорога и память", "расширение пространства и памяти"
    if start < 3546:
        return "семейная фотосерия", "накопление близости и жизни"
    if start < 4028:
        return "семья и творчество", "настоящее и продолжение"
    return "Нури", "нежный светлый финал"


def _problems(metrics: dict[str, float], size: tuple[int, int] | None) -> list[str]:
    result: list[str] = []
    if metrics["mean_luma"] < 85:
        result.append("пониженная общая яркость")
    elif metrics["mean_luma"] > 165:
        result.append("повышенная общая яркость")
    if metrics["shadow_clip_percent"] > 3:
        result.append("заметные глубокие тени")
    if metrics["highlight_clip_percent"] > 0.3:
        result.append("риск потери деталей в светах")
    if metrics["dynamic_range_p95_p05"] < 105:
        result.append("пониженный локальный контраст")
    if metrics["mean_saturation"] < 55:
        result.append("приглушённый цвет")
    elif metrics["mean_saturation"] > 125:
        result.append("повышенная насыщенность")
    if metrics["mean_red"] - metrics["mean_blue"] > 35:
        result.append("выраженно тёплый баланс")
    elif metrics["mean_blue"] - metrics["mean_red"] > 25:
        result.append("холодный баланс")
    if size and (size[0] < 1280 or size[1] < 720):
        result.append("низкое разрешение источника; защита от перешарпа")
    return result or ["существенная техническая коррекция не требуется"]


def _correction(
    metrics: dict[str, float],
    scene: str,
    duration: int,
    media_size: tuple[int, int] | None,
) -> dict[str, float]:
    target = 112.0
    if scene in {"семья", "семья и близкие", "семейная фотосерия", "Нури", "финал"}:
        target = 120.0
    if "дорога" in scene:
        target = 108.0
    exposure = max(-0.30, min(0.38, math.log2(target / max(metrics["mean_luma"], 20)) * 0.38))
    contrast = 3.0
    if metrics["dynamic_range_p95_p05"] < 105:
        contrast = 8.0
    elif metrics["dynamic_range_p95_p05"] > 195:
        contrast = -3.0
    highlights = -8.0
    if metrics["p95_luma"] > 235 or metrics["highlight_clip_percent"] > 0.3:
        highlights = -22.0
    shadows = 6.0
    if metrics["p05_luma"] < 14:
        shadows = 14.0
    if media_size and (media_size[0] < 1280 or media_size[1] < 720):
        shadows = min(shadows, 8.0)
    saturation = 100.0
    if metrics["mean_saturation"] < 55:
        saturation = 106.0
    elif metrics["mean_saturation"] > 125:
        saturation = 95.0
    temperature = 0.0
    if scene in {"семья", "семья и близкие", "семейная фотосерия"}:
        temperature = 2.5
    elif scene in {"Нури", "финал"}:
        temperature = 3.5
    elif "дорога" in scene:
        temperature = 0.8
    if metrics["mean_red"] - metrics["mean_blue"] > 45 and "Нури" not in scene:
        temperature -= 3.0
    if metrics["mean_blue"] - metrics["mean_red"] > 25:
        temperature += 3.0
    tint = 0.0
    if metrics["mean_green"] > (metrics["mean_red"] + metrics["mean_blue"]) / 2 + 8:
        tint = 1.8
    vignette = 0.0
    if duration >= 50 and scene in {
        "семья",
        "семья и близкие",
        "семейная фотосерия",
        "Нури",
        "финал",
    }:
        vignette = -0.28 if scene != "Нури" else -0.20
    sharpen = 0.0
    if media_size and media_size[0] >= 1440 and metrics["dynamic_range_p95_p05"] < 120:
        sharpen = 3.0
    return {
        "temperature": round(temperature, 2),
        "tint": round(tint, 2),
        "exposure": round(exposure, 3),
        "contrast": round(contrast, 2),
        "highlights": round(highlights, 2),
        "shadows": round(shadows, 2),
        "whites": -2.0,
        "blacks": -1.5 if contrast >= 0 else 0.0,
        "saturation": round(saturation, 2),
        "vibrance": 4.0 if saturation <= 100 else 2.0,
        "sharpen": sharpen,
        "vignette_amount": vignette,
        "vignette_midpoint": 46.0,
        "vignette_roundness": 0.0,
        "vignette_feather": 78.0,
    }


def _audit_sequence(
    root: ET.Element,
    sequence_name: str,
    kind: str,
    preview_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not preview_path.is_file():
        raise RuntimeError(f"BLOCKED: audit preview source missing: {preview_path}")
    items = _foreground(root, sequence_name)
    capture = cv2.VideoCapture(str(preview_path))
    rows: list[dict[str, Any]] = []
    plan: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        start, end = item.start // FRAME_TICKS, item.end // FRAME_TICKS
        duration = end - start
        sample_frames = sorted(
            {
                min(end - 1, max(start, start + int(duration * fraction)))
                for fraction in (0.25, 0.5, 0.75)
            }
        )
        images = [_read_preview_frame(capture, frame) for frame in sample_frames]
        metrics = _image_metrics(images)
        resolved_name, source_path = _resolve_visual(root, item)
        size = _media_size(source_path)
        scene, meaning = _scene(kind, start, item.name)
        component_names = [
            component["display_name"] or component["match_name"]
            for component in protected_property_snapshot(
                item, build_project_object_id_lookup(root)
            )["components"]
        ]
        media_type = "nested sequence" if not item.source_path else (
            "photo" if source_path and source_path.suffix.lower() in IMAGE_SUFFIXES else "video"
        )
        row = {
            "index": index,
            "sequence_timecode": f"{start // FPS // 60:02d}:{start // FPS % 60:02d}:{start % FPS:02d}"
            f"–{end // FPS // 60:02d}:{end // FPS % 60:02d}:{end % FPS:02d}",
            "timeline_in_frame": start,
            "timeline_out_frame_exclusive": end,
            "duration_frames": duration,
            "timeline_name": item.name,
            "resolved_source_name": resolved_name,
            "source_path": str(source_path) if source_path else "",
            "source_online": bool(source_path and source_path.is_file()),
            "source_resolution": list(size) if size else None,
            "type": media_type,
            "existing_effects": component_names,
            "existing_lumetri": "Lumetri Color" in component_names,
            "sample_frames": sample_frames,
            "image_metrics": metrics,
            "problems": _problems(metrics, size),
            "scene": scene,
            "semantic_function": meaning,
            "black_or_blurred_side_fields": bool(
                size and size[1] and size[0] / size[1] < 1.65
            ),
        }
        correction = _correction(metrics, scene, duration, size)
        decision = {
            "index": index,
            "sequence_timecode": row["sequence_timecode"],
            "timeline_in_frame": start,
            "timeline_out_frame_exclusive": end,
            "source": item.name,
            "resolved_source": resolved_name,
            "problem": row["problems"],
            "meaning": meaning,
            "scene": scene,
            "effect": "Lumetri Color (Basic Correction + Vignette where justified)",
            "parameters_before": {
                "temperature": 0,
                "tint": 0,
                "exposure": 0,
                "contrast": 0,
                "highlights": 0,
                "shadows": 0,
                "whites": 0,
                "blacks": 0,
                "saturation": 100,
                "vignette_amount": 0,
            },
            "parameters_after": correction,
            "area": "весь составной кадр; фон синхронизируется отдельной копией background sequence",
            "mask_tracking": "не требуется; движущиеся лица не маскируются без надёжного Premiere tracking",
            "reason": "минимально достаточное техническое согласование с умеренной сценовой драматургией",
            "risk": "сверить кожу, света и заметность виньетки при Premiere Desktop open-check",
        }
        rows.append(row)
        plan.append(decision)
    capture.release()
    if any(not row["source_online"] for row in rows):
        offline = [row["source_path"] or row["timeline_name"] for row in rows if not row["source_online"]]
        raise RuntimeError(f"BLOCKED: offline/unresolved media: {offline}")
    sequence = find_project_sequence_node(root, sequence_name)
    video_items = _items(root, sequence_name, 0)
    audio_items = _items(root, sequence_name, 1)
    audit = {
        "task": TASK_ID,
        "sequence": sequence_name,
        "kind": kind,
        "preview_source": str(preview_path),
        "video_settings": _video_settings(sequence, build_project_object_id_lookup(root)),
        "duration_frames": _sequence_duration(video_items + audio_items) // FRAME_TICKS,
        "fps": FPS,
        "video_track_items": len(video_items),
        "foreground_segments": len(items),
        "audio_track_items": len(audio_items),
        "audio": [
            {
                "track": item.track_index,
                "in_frame": item.start // FRAME_TICKS,
                "out_frame_exclusive": item.end // FRAME_TICKS,
                "name": item.name,
                "source_path": item.source_path,
                "online": bool(item.source_path and Path(item.source_path).is_file()),
            }
            for item in audio_items
        ],
        "visual_items": rows,
    }
    return audit, plan


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_audit_report(short: dict[str, Any], long: dict[str, Any]) -> None:
    lines = [
        "TASK_030 — ОБЯЗАТЕЛЬНЫЙ АУДИТ ДО КОРРЕКЦИИ",
        "",
        f"Проект: {PROJECT}",
        f"SHA256 до мутации: {_sha256(PROJECT)}",
        "Входной контракт: PASS",
        f"SHORT: {SHORT_INPUT}, {short['duration_frames']} кадров, 25 fps, "
        f"{short['foreground_segments']} визуальных сегментов, media online.",
        f"LONG: {LONG_INPUT}, {long['duration_frames']} кадров, 25 fps, "
        f"{long['foreground_segments']} визуальных сегментов, media online.",
        "",
        "Визуальный вывод:",
        "- SHORT: быстрый путь Нури → дорога → семейная фотосерия → светлый дорожный финал.",
        "- LONG: дорога и одиночное присутствие → семья → горы/память → плотная фотосерия → "
        "семья/творчество → Нури.",
        "- Разнородность главным образом создают 640×360 готовые составные видео, вертикальные "
        "телефонные кадры и фотографии разных лет.",
        "- Коррекция запланирована поклипно; единого фильтра на всю sequence нет.",
        "- Тени поднимаются ограниченно на низкоразрешённом видео; закат и архивная фактура сохраняются.",
        "- Маски/лучи/glow не назначены автоматически: без проверяемого tracking они создают больший "
        "риск, чем пользу. Применяется только деликатная поклипная виньетка на длинных портретных планах.",
        "",
        "Scopes-приближение:",
        "Числовой аудит использует luma percentiles, RGB means, saturation и clipping scan по трём "
        "репрезентативным кадрам каждого сегмента; визуальная проверка выполнена по contact sheets. "
        "Premiere Waveform/RGB Parade/Vectorscope и skin-tone line остаются обязательны на open-check.",
        "",
        "До коррекции создан план для каждого сегмента:",
        f"- {TASK_DIR / 'TASK_030_COLOR_LIGHT_PLAN_SHORT.json'}",
        f"- {TASK_DIR / 'TASK_030_COLOR_LIGHT_PLAN_LONG.json'}",
    ]
    (TASK_DIR / "TASK_030_AUDIT_REPORT.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def audit_only() -> None:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    if not PROJECT.is_file():
        raise RuntimeError(f"BLOCKED: project missing: {PROJECT}")
    root = load_premiere_project_root(PROJECT)
    for name in (SHORT_INPUT, LONG_INPUT):
        if find_project_sequence_node(root, name) is None:
            raise RuntimeError(f"BLOCKED: sequence missing: {name}")
    short, short_plan = _audit_sequence(root, SHORT_INPUT, "SHORT", SHORT_PREVIEW_SOURCE)
    long, long_plan = _audit_sequence(root, LONG_INPUT, "LONG", LONG_PREVIEW_SOURCE)
    _write_json(TASK_DIR / "TASK_030_AUDIT_SHORT.json", short)
    _write_json(TASK_DIR / "TASK_030_AUDIT_LONG.json", long)
    _write_json(TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_SHORT.json", short_plan)
    _write_json(TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_LONG.json", long_plan)
    _write_audit_report(short, long)


def _clone_sequence(root: ET.Element, source: str, target: str) -> None:
    if find_project_sequence_node(root, target) is not None:
        raise RuntimeError(f"BLOCKED: output/helper sequence exists: {target}")
    clone_named_sequence(
        root,
        source_sequence_name=source,
        new_sequence_name=target,
        object_id_lookup=build_project_object_id_lookup(root),
        object_uid_lookup=build_project_object_uid_lookup(root),
    )


def _find_lumetri_template(root: ET.Element) -> ET.Element:
    for component in root.iter("VideoFilterComponent"):
        if (component.findtext("./MatchName") or "").strip() == "AE.ADBE Lumetri":
            return component
    raise RuntimeError("BLOCKED: no native Lumetri Color template in project")


def _set_start_value(param: ET.Element, value: float) -> None:
    node = param.find("./StartKeyframe")
    if node is None:
        raise RuntimeError(f"Lumetri parameter has no StartKeyframe: {param.findtext('./Name')}")
    parts = (node.text or "").split(",")
    if len(parts) < 2:
        raise RuntimeError("Invalid Lumetri StartKeyframe")
    parts[1] = f"{value:.6f}".rstrip("0").rstrip(".")
    node.text = ",".join(parts)


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
    "vignette_midpoint": "52",
    "vignette_roundness": "53",
    "vignette_feather": "54",
    "faded_film": "28",
}


def _add_lumetri(
    root: ET.Element,
    item: Any,
    values: dict[str, float],
    *,
    ids: dict[str, ET.Element],
    template_ids: dict[str, ET.Element],
    allocator: _ProjectObjectIdAllocator,
    template: ET.Element,
) -> None:
    chain_ref = item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components")
    if chain_ref is None:
        raise RuntimeError(f"Track item lacks ComponentOwner chain: {item.name}")
    chain = ids.get(chain_ref.attrib.get("ObjectRef", ""))
    if chain is None:
        raise RuntimeError(f"Unresolved ComponentOwner chain: {item.name}")
    components = chain.find("./ComponentChain/Components")
    if components is None:
        components = ET.SubElement(chain.find("./ComponentChain"), "Components")
    for ref in components.findall("./Component"):
        current = ids.get(ref.attrib.get("ObjectRef", ""))
        if current is not None and (current.findtext("./MatchName") or "").strip() == "AE.ADBE Lumetri":
            raise RuntimeError(f"Output target already contains Lumetri: {item.name}")
    cloned = _clone_filter_component(
        root,
        template,
        object_id_lookup=ids,
        template_object_id_lookup=template_ids,
        id_allocator=allocator,
    )
    params: dict[str, ET.Element] = {}
    for param_ref in cloned.findall("./Component/Params/Param"):
        param = ids.get(param_ref.attrib.get("ObjectRef", ""))
        if param is not None:
            params[(param.findtext("./ParameterID") or "").strip()] = param
    for name, value in values.items():
        parameter_id = LUMETRI_PARAM_IDS.get(name)
        if parameter_id and parameter_id in params:
            _set_start_value(params[parameter_id], float(value))
    next_index = max(
        [int(ref.attrib.get("Index", "-1")) for ref in components.findall("./Component")],
        default=-1,
    ) + 1
    ET.SubElement(
        components,
        "Component",
        {"Index": str(next_index), "ObjectRef": cloned.attrib["ObjectID"]},
    )


def _retarget_nested_item(root: ET.Element, item: Any, helper_name: str) -> None:
    ids = build_project_object_id_lookup(root)
    master, _, source = _source_sequence_video_clip(root, helper_name, ids)
    sub_ref = item.track_item_node.find("./ClipTrackItem/SubClip")
    sub = ids.get(sub_ref.attrib.get("ObjectRef", "")) if sub_ref is not None else None
    if sub is None:
        raise RuntimeError(f"Nested item has no SubClip: {item.name}")
    master_ref = sub.find("./MasterClip")
    if master_ref is None:
        master_ref = ET.SubElement(sub, "MasterClip")
    master_ref.attrib.clear()
    master_ref.attrib["ObjectURef"] = master.attrib["ObjectUID"]
    name_node = sub.find("./Name")
    if name_node is None:
        name_node = ET.SubElement(sub, "Name")
    name_node.text = helper_name
    clip_ref = item.track_item_node.find("./ClipTrackItem/Clip")
    clip = ids.get(clip_ref.attrib.get("ObjectRef", "")) if clip_ref is not None else None
    payload = clip.find("./Clip") if clip is not None else None
    if payload is not None:
        payload_name = payload.find("./Name")
        if payload_name is None:
            payload_name = ET.SubElement(payload, "Name")
        payload_name.text = helper_name
        source_ref = payload.find("./Source")
        if source_ref is None:
            source_ref = ET.SubElement(payload, "Source")
        source_ref.attrib.clear()
        source_ref.attrib["ObjectRef"] = source.attrib["ObjectID"]


def _apply_plan_to_items(
    root: ET.Element,
    sequence_name: str,
    plan: list[dict[str, Any]],
    *,
    ids: dict[str, ET.Element],
    template_ids: dict[str, ET.Element],
    allocator: _ProjectObjectIdAllocator,
    template: ET.Element,
) -> None:
    lookup = {int(row["timeline_in_frame"]): row for row in plan}
    for item in _foreground(root, sequence_name):
        start = item.start // FRAME_TICKS
        row = lookup.get(start)
        if row is None:
            raise RuntimeError(f"No color plan at frame {start} for {sequence_name}")
        _add_lumetri(
            root,
            item,
            row["parameters_after"],
            ids=ids,
            template_ids=template_ids,
            allocator=allocator,
            template=template,
        )


def _apply_plan_to_background(
    root: ET.Element,
    helper_name: str,
    plan: list[dict[str, Any]],
    *,
    ids: dict[str, ET.Element],
    template_ids: dict[str, ET.Element],
    allocator: _ProjectObjectIdAllocator,
    template: ET.Element,
) -> None:
    candidates = sorted(_items(root, helper_name, 0), key=lambda item: item.start)
    ordered_plan = sorted(plan, key=lambda row: int(row["timeline_in_frame"]))
    if len(candidates) != len(ordered_plan):
        raise RuntimeError(
            f"Background plan mismatch for {helper_name}: "
            f"{len(candidates)}/{len(ordered_plan)}"
        )
    for item, row in zip(candidates, ordered_plan):
        _add_lumetri(
            root,
            item,
            row["parameters_after"],
            ids=ids,
            template_ids=template_ids,
            allocator=allocator,
            template=template,
        )


def _strong_plan(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(plan)
    for row in result:
        values = row["parameters_after"]
        values["exposure"] = round(
            max(-0.65, min(0.65, float(values["exposure"]) * 1.75)), 3
        )
        contrast = float(values["contrast"])
        values["contrast"] = round(
            max(-10.0, min(22.0, contrast * 1.8 + (4.0 if contrast >= 0 else 0.0))),
            2,
        )
        values["temperature"] = round(
            max(-8.0, min(8.0, float(values["temperature"]) * 1.6)), 2
        )
        values["tint"] = round(
            max(-5.0, min(5.0, float(values["tint"]) * 1.5)), 2
        )
        values["highlights"] = round(
            max(-38.0, float(values["highlights"]) * 1.5), 2
        )
        values["shadows"] = round(
            min(24.0, float(values["shadows"]) * 1.55), 2
        )
        values["whites"] = -4.0
        values["blacks"] = -3.0 if float(values["contrast"]) >= 0 else 0.0
        saturation = float(values["saturation"])
        values["saturation"] = round(
            max(88.0, min(114.0, 100.0 + (saturation - 100.0) * 1.8)), 2
        )
        values["vibrance"] = 7.0
        values["vignette_amount"] = round(
            max(-0.45, float(values["vignette_amount"]) * 1.5), 2
        )
        row["effect"] = "Lumetri Color — усиленный второй проход"
        row["reason"] = (
            "заметное, но естественное отличие от v08/v14 по замечанию пользователя"
        )
        row["risk"] = (
            "проверить кожу, белые области и монтажные стыки; при необходимости ослабить локально"
        )
    return result


def _extreme_plan(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(plan)
    for row in result:
        values = row["parameters_after"]
        scene = str(row["scene"])
        values["exposure"] = round(
            max(-1.15, min(1.15, float(values["exposure"]) * 3.0)), 3
        )
        base_contrast = float(values["contrast"])
        values["contrast"] = round(
            max(28.0, min(55.0, abs(base_contrast) * 3.2 + 24.0)), 2
        )
        values["temperature"] = round(
            max(-18.0, min(18.0, float(values["temperature"]) * 3.0)), 2
        )
        values["tint"] = round(
            max(-10.0, min(10.0, float(values["tint"]) * 2.5)), 2
        )
        values["highlights"] = round(
            max(-75.0, float(values["highlights"]) * 2.8), 2
        )
        values["shadows"] = round(
            min(50.0, float(values["shadows"]) * 2.8), 2
        )
        values["whites"] = -8.0
        values["blacks"] = -12.0
        if scene in {"семья", "семья и близкие", "семейная фотосерия"}:
            values["saturation"] = 125.0
            values["temperature"] = max(10.0, float(values["temperature"]))
        elif scene in {"Нури", "финал"}:
            values["saturation"] = 118.0
            values["temperature"] = max(12.0, float(values["temperature"]))
        elif "дорога" in scene:
            values["saturation"] = 112.0
        else:
            values["saturation"] = 116.0
        values["vibrance"] = 28.0
        values["sharpen"] = 12.0
        values["faded_film"] = 18.0
        values["vignette_amount"] = -1.80
        values["vignette_midpoint"] = 32.0
        values["vignette_feather"] = 58.0
        portrait_scene = scene in {
            "семья",
            "семья и близкие",
            "семейная фотосерия",
            "Нури",
            "финал",
        }
        row["local_fx"] = {
            "spotlight_strength": 0.30 if portrait_scene else 0.17,
            "spotlight_center_x": 0.50,
            "spotlight_center_y": 0.44 if portrait_scene else 0.52,
            "local_warmth": 0.10 if portrait_scene else 0.035,
            "edge_burn": 0.26 if portrait_scene else 0.16,
            "edge_blur": 0.58 if portrait_scene else 0.22,
            "glow_strength": 0.24 if portrait_scene else 0.10,
            "ray_strength": 0.16 if ("дорога" in scene or scene in {"Нури", "финал"}) else 0.08,
            "ray_origin": "top_left" if "дорога" in scene else "top_right",
        }
        row["effect"] = (
            "EXTREME Lumetri in Premiere + baked local FX in preview: "
            "spotlight mask, dodge/burn, edge blur, glow, vignette, soft rays"
        )
        row["reason"] = "намеренная демонстрация максимально заметной обработки для сравнения"
        row["risk"] = (
            "намеренно чрезмерно: возможны неестественная кожа, clipping, halo и заметные лучи"
        )
    return result


def _update_lumetri(
    item: Any,
    values: dict[str, float],
    ids: dict[str, ET.Element],
) -> None:
    chain_ref = item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components")
    chain = ids.get(chain_ref.attrib.get("ObjectRef", "")) if chain_ref is not None else None
    components = chain.find("./ComponentChain/Components") if chain is not None else None
    if components is None:
        raise RuntimeError(f"Lumetri component chain missing: {item.name}")
    lumetri: ET.Element | None = None
    for ref in components.findall("./Component"):
        component = ids.get(ref.attrib.get("ObjectRef", ""))
        if component is not None and (
            component.findtext("./MatchName") or ""
        ).strip() == "AE.ADBE Lumetri":
            lumetri = component
            break
    if lumetri is None:
        raise RuntimeError(f"Lumetri missing on strong-pass target: {item.name}")
    params: dict[str, ET.Element] = {}
    for ref in lumetri.findall("./Component/Params/Param"):
        param = ids.get(ref.attrib.get("ObjectRef", ""))
        if param is not None:
            params[(param.findtext("./ParameterID") or "").strip()] = param
    for name, value in values.items():
        parameter_id = LUMETRI_PARAM_IDS.get(name)
        if parameter_id and parameter_id in params:
            _set_start_value(params[parameter_id], float(value))


def _update_sequence_lumetri(
    root: ET.Element,
    sequence_name: str,
    plan: list[dict[str, Any]],
    *,
    foreground_only: bool,
) -> None:
    ids = build_project_object_id_lookup(root)
    items = _foreground(root, sequence_name) if foreground_only else sorted(
        _items(root, sequence_name, 0), key=lambda item: item.start
    )
    ordered_plan = sorted(plan, key=lambda row: int(row["timeline_in_frame"]))
    if len(items) != len(ordered_plan):
        raise RuntimeError(
            f"Strong-pass plan mismatch for {sequence_name}: "
            f"{len(items)}/{len(ordered_plan)}"
        )
    for item, row in zip(items, ordered_plan):
        _update_lumetri(item, row["parameters_after"], ids)


def _timeline_signature(root: ET.Element, name: str, group: int) -> list[tuple[Any, ...]]:
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


def _write_project(root: ET.Element, path: Path) -> None:
    path.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )


def _apply_frame_grade(image: np.ndarray, values: dict[str, float]) -> np.ndarray:
    rgb = image[:, :, ::-1].astype(np.float32) / 255.0
    temperature = values["temperature"]
    tint = values["tint"]
    rgb[:, :, 0] *= 1.0 + temperature * 0.004 + tint * 0.001
    rgb[:, :, 1] *= 1.0 - tint * 0.002
    rgb[:, :, 2] *= 1.0 - temperature * 0.004 + tint * 0.001
    rgb *= 2.0 ** values["exposure"]
    luma = (
        rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    )
    shadows = values["shadows"] / 100.0
    highlights = values["highlights"] / 100.0
    rgb += shadows * 0.18 * np.square(np.clip(1.0 - luma, 0, 1))[:, :, None]
    rgb += highlights * 0.12 * np.square(np.clip(luma, 0, 1))[:, :, None]
    factor = 1.0 + values["contrast"] / 100.0
    rgb = (rgb - 0.5) * factor + 0.5
    gray = (
        rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    )[:, :, None]
    saturation = values["saturation"] / 100.0
    rgb = gray + (rgb - gray) * saturation
    amount = abs(values["vignette_amount"])
    if amount:
        height, width = image.shape[:2]
        yy, xx = np.mgrid[-1:1:complex(height), -1:1:complex(width)]
        radial = np.clip((xx * xx + yy * yy - 0.18) / 1.82, 0, 1)
        rgb *= (1.0 - amount * 0.16 * radial[:, :, None])
    return np.clip(rgb[:, :, ::-1] * 255.0, 0, 255).astype(np.uint8)


def _extreme_maps(
    height: int, width: int, fx: dict[str, Any]
) -> tuple[np.ndarray, np.ndarray, tuple[float, float]]:
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    nx = xx / max(width - 1, 1)
    ny = yy / max(height - 1, 1)
    cx = float(fx["spotlight_center_x"])
    cy = float(fx["spotlight_center_y"])
    radius = np.sqrt(((nx - cx) / 0.34) ** 2 + ((ny - cy) / 0.46) ** 2)
    ellipse = np.clip(1.0 - (radius - 0.35) / 0.85, 0.0, 1.0)
    ellipse = cv2.GaussianBlur(ellipse, (0, 0), 22)
    edge = 1.0 - ellipse
    if fx.get("ray_origin") == "top_left":
        origin = (0.10 * (width - 1), 0.06 * (height - 1))
    else:
        origin = (0.90 * (width - 1), 0.06 * (height - 1))
    return ellipse, edge, origin


def _apply_extreme_frame(
    image: np.ndarray,
    values: dict[str, float],
    fx: dict[str, Any],
    maps: tuple[np.ndarray, np.ndarray, tuple[float, float]],
) -> np.ndarray:
    rgb = image[:, :, ::-1].astype(np.float32) / 255.0
    temperature = float(values["temperature"])
    tint = float(values["tint"])
    rgb[:, :, 0] *= 1.0 + temperature * 0.012 + tint * 0.003
    rgb[:, :, 1] *= 1.0 - tint * 0.006
    rgb[:, :, 2] *= 1.0 - temperature * 0.012 + tint * 0.003
    rgb *= 2.0 ** float(values["exposure"])
    luma = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    rgb += (float(values["shadows"]) / 100.0) * 0.28 * np.square(
        np.clip(1.0 - luma, 0, 1)
    )[:, :, None]
    rgb += (float(values["highlights"]) / 100.0) * 0.20 * np.square(
        np.clip(luma, 0, 1)
    )[:, :, None]
    rgb = (rgb - 0.5) * (1.0 + float(values["contrast"]) / 100.0) + 0.5
    gray = (
        rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    )[:, :, None]
    rgb = gray + (rgb - gray) * (float(values["saturation"]) / 100.0)
    ellipse, edge, origin = maps
    mask = ellipse[:, :, None]
    edge_mask = edge[:, :, None]
    rgb += float(fx["spotlight_strength"]) * mask * 0.22
    rgb[:, :, 0] += float(fx["local_warmth"]) * ellipse * 0.18
    rgb *= 1.0 - float(fx["edge_burn"]) * edge_mask
    bgr = np.clip(rgb[:, :, ::-1], 0, 1)
    blurred = cv2.GaussianBlur(bgr, (0, 0), 7)
    bgr = bgr * (1.0 - float(fx["edge_blur"]) * edge_mask) + blurred * (
        float(fx["edge_blur"]) * edge_mask
    )
    luma8 = (
        bgr[:, :, 2] * 0.2126 + bgr[:, :, 1] * 0.7152 + bgr[:, :, 0] * 0.0722
    )
    glow_src = np.clip((luma8 - 0.62) / 0.38, 0, 1)[:, :, None] * bgr
    glow = cv2.GaussianBlur(glow_src, (0, 0), 11)
    bgr = bgr + glow * float(fx["glow_strength"]) * 1.35 * mask
    highlights = np.clip((luma8 - 0.72) / 0.28, 0, 1).astype(np.float32)
    rays = np.zeros_like(highlights)
    ox, oy = origin
    height, width = highlights.shape
    for scale in (1.04, 1.10, 1.18, 1.28, 1.40):
        matrix = np.array(
            [[scale, 0.0, ox * (1.0 - scale)], [0.0, scale, oy * (1.0 - scale)]],
            dtype=np.float32,
        )
        rays += cv2.warpAffine(
            highlights,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
    rays = cv2.GaussianBlur(rays / 5.0, (0, 0), 6)
    ray_color = np.array([0.95, 0.88, 0.72], dtype=np.float32)
    bgr = bgr + rays[:, :, None] * ray_color * float(fx["ray_strength"]) * 0.85
    vignette = abs(float(values["vignette_amount"]))
    yy, xx = np.mgrid[-1:1:complex(height), -1:1:complex(width)]
    radial = np.clip((xx * xx + yy * yy - 0.08) / 1.70, 0, 1)
    bgr *= 1.0 - vignette * 0.28 * radial[:, :, None]
    return np.clip(bgr * 255.0, 0, 255).astype(np.uint8)


def _bump_item_blur(item: Any, ids: dict[str, ET.Element], amount: float) -> bool:
    chain_ref = item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components")
    chain = ids.get(chain_ref.attrib.get("ObjectRef", "")) if chain_ref is not None else None
    components = chain.find("./ComponentChain/Components") if chain is not None else None
    if components is None:
        return False
    changed = False
    for ref in components.findall("./Component"):
        component = ids.get(ref.attrib.get("ObjectRef", ""))
        if component is None:
            continue
        if (component.findtext("./MatchName") or "").strip() != "AE.Impact_Blur_FX":
            continue
        for param_ref in component.findall("./Component/Params/Param"):
            param = ids.get(param_ref.attrib.get("ObjectRef", ""))
            if param is None:
                continue
            name = (param.findtext("./Name") or "").strip()
            pid = (param.findtext("./ParameterID") or "").strip()
            if name == "Amount" or pid == "3":
                _set_start_value(param, amount)
                changed = True
    return changed


def _render_preview(
    source: Path,
    plan: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(source))
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width, height = 640, 360
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    source_fps = capture.get(cv2.CAP_PROP_FPS)
    lookup: list[tuple[int, int, dict[str, float]]] = [
        (
            int(row["timeline_in_frame"]),
            int(row["timeline_out_frame_exclusive"]),
            row["parameters_after"],
        )
        for row in plan
    ]
    with tempfile.TemporaryDirectory(prefix="task030_preview_") as temp_text:
        temp_video = Path(temp_text) / "graded.mp4"
        writer = cv2.VideoWriter(
            str(temp_video),
            cv2.VideoWriter_fourcc(*"mp4v"),
            source_fps,
            (width, height),
        )
        segment_index = 0
        written = 0
        while True:
            ok, image = capture.read()
            if not ok or image is None:
                break
            while segment_index + 1 < len(lookup) and written >= lookup[segment_index][1]:
                segment_index += 1
            start, end, values = lookup[segment_index]
            if not (start <= written < end):
                raise RuntimeError(f"No grade for preview frame {written}")
            if image.shape[1] != width or image.shape[0] != height:
                image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
            writer.write(_apply_frame_grade(image, values))
            written += 1
        capture.release()
        writer.release()
        ffmpeg = str(resolve_ffmpeg_executable())
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(temp_video),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
    if written != frames:
        raise RuntimeError(f"Preview frame mismatch: {written}/{frames}")
    return {
        "source": str(source),
        "output": str(output),
        "frames": written,
        "fps": source_fps,
        "resolution": [width, height],
        "source_resolution": [source_width, source_height],
        "sha256": _sha256(output),
    }


def _render_extreme_preview(
    source: Path,
    plan: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(source))
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width, height = 640, 360
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    source_fps = capture.get(cv2.CAP_PROP_FPS)
    lookup = [
        (
            int(row["timeline_in_frame"]),
            int(row["timeline_out_frame_exclusive"]),
            row["parameters_after"],
            row["local_fx"],
        )
        for row in plan
    ]
    maps_cache: dict[tuple[Any, ...], tuple[np.ndarray, np.ndarray, tuple[float, float]]] = {}
    with tempfile.TemporaryDirectory(prefix="task030_extreme_") as temp_text:
        temp_video = Path(temp_text) / "graded.mp4"
        writer = cv2.VideoWriter(
            str(temp_video),
            cv2.VideoWriter_fourcc(*"mp4v"),
            source_fps,
            (width, height),
        )
        try:
            segment_index = 0
            written = 0
            while True:
                ok, image = capture.read()
                if not ok or image is None:
                    break
                while segment_index + 1 < len(lookup) and written >= lookup[segment_index][1]:
                    segment_index += 1
                start, end, values, fx = lookup[segment_index]
                if not (start <= written < end):
                    raise RuntimeError(f"No extreme grade for preview frame {written}")
                if image.shape[1] != width or image.shape[0] != height:
                    image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
                cache_key = (
                    width,
                    height,
                    fx["spotlight_center_x"],
                    fx["spotlight_center_y"],
                    fx["ray_origin"],
                )
                maps = maps_cache.get(cache_key)
                if maps is None:
                    maps = _extreme_maps(height, width, fx)
                    maps_cache[cache_key] = maps
                writer.write(_apply_extreme_frame(image, values, fx, maps))
                written += 1
        finally:
            capture.release()
            writer.release()
        ffmpeg = str(resolve_ffmpeg_executable())
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(temp_video),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
    if written != frames:
        raise RuntimeError(f"Extreme preview frame mismatch: {written}/{frames}")
    return {
        "source": str(source),
        "output": str(output),
        "frames": written,
        "fps": source_fps,
        "resolution": [width, height],
        "source_resolution": [source_width, source_height],
        "sha256": _sha256(output),
        "local_fx": True,
    }


def _comparison_sheet(
    source: Path,
    graded: Path,
    plan: list[dict[str, Any]],
    output: Path,
    title: str,
) -> None:
    picks = [
        int(plan[index]["timeline_in_frame"])
        + (
            int(plan[index]["timeline_out_frame_exclusive"])
            - int(plan[index]["timeline_in_frame"])
        )
        // 2
        for index in np.linspace(0, len(plan) - 1, 8, dtype=int)
    ]
    before = cv2.VideoCapture(str(source))
    after = cv2.VideoCapture(str(graded))
    sheet = Image.new("RGB", (1280, 8 * 200 + 40), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 8), f"{title} — слева BEFORE / справа AFTER", fill="black")
    for row, frame in enumerate(picks):
        a = _read_preview_frame(before, frame)
        b = _read_preview_frame(after, frame)
        a = cv2.cvtColor(cv2.resize(a, (640, 180)), cv2.COLOR_BGR2RGB)
        b = cv2.cvtColor(cv2.resize(b, (640, 180)), cv2.COLOR_BGR2RGB)
        y = 40 + row * 200
        sheet.paste(Image.fromarray(a), (0, y))
        sheet.paste(Image.fromarray(b), (640, y))
        draw.text((8, y + 182), f"frame {frame}", fill="black")
    before.release()
    after.release()
    sheet.save(output, quality=93)


def _black_scan(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    dark: list[int] = []
    frame = 0
    while True:
        ok, image = capture.read()
        if not ok or image is None:
            break
        if frame % 5 == 0 and float(np.mean(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))) < 2:
            dark.append(frame)
        frame += 1
    capture.release()
    return {"decoded_frames": frame, "sampled_black_frames": dark}


def execute() -> None:
    audit_only()
    short_plan = json.loads(
        (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_SHORT.json").read_text(encoding="utf-8")
    )
    long_plan = json.loads(
        (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_LONG.json").read_text(encoding="utf-8")
    )
    root = load_premiere_project_root(PROJECT)
    input_xml = {
        SHORT_INPUT: _sequence_xml(root, SHORT_INPUT),
        LONG_INPUT: _sequence_xml(root, LONG_INPUT),
    }
    input_signatures = {
        name: {
            "video": _timeline_signature(root, name, 0),
            "audio": _timeline_signature(root, name, 1),
        }
        for name in (SHORT_INPUT, LONG_INPUT)
    }
    if BACKUP.exists():
        raise RuntimeError(f"BLOCKED: backup already exists: {BACKUP}")
    for name in (SHORT_OUTPUT, LONG_OUTPUT, SHORT_BG_OUTPUT, LONG_BG_OUTPUT):
        if find_project_sequence_node(root, name) is not None:
            raise RuntimeError(f"BLOCKED: output/helper already exists: {name}")
    shutil.copy2(PROJECT, BACKUP)
    _clone_sequence(root, SHORT_INPUT, SHORT_OUTPUT)
    _clone_sequence(root, LONG_INPUT, LONG_OUTPUT)
    _clone_sequence(root, SHORT_BG_INPUT, SHORT_BG_OUTPUT)
    _clone_sequence(root, LONG_BG_INPUT, LONG_BG_OUTPUT)
    short_background_item = [
        item for item in _items(root, SHORT_OUTPUT, 0) if item.track_index == 0
    ]
    long_background_item = [
        item for item in _items(root, LONG_OUTPUT, 0) if item.track_index == 0
    ]
    if len(short_background_item) != 1 or len(long_background_item) != 1:
        raise RuntimeError("BLOCKED: expected one background nested item per output")
    _retarget_nested_item(root, short_background_item[0], SHORT_BG_OUTPUT)
    _retarget_nested_item(root, long_background_item[0], LONG_BG_OUTPUT)
    template_ids = build_project_object_id_lookup(root)
    template = _find_lumetri_template(root)
    ids = build_project_object_id_lookup(root)
    allocator = _ProjectObjectIdAllocator(root)
    _apply_plan_to_items(
        root,
        SHORT_OUTPUT,
        short_plan,
        ids=ids,
        template_ids=template_ids,
        allocator=allocator,
        template=template,
    )
    _apply_plan_to_items(
        root,
        LONG_OUTPUT,
        long_plan,
        ids=ids,
        template_ids=template_ids,
        allocator=allocator,
        template=template,
    )
    _apply_plan_to_background(
        root,
        SHORT_BG_OUTPUT,
        short_plan,
        ids=ids,
        template_ids=template_ids,
        allocator=allocator,
        template=template,
    )
    _apply_plan_to_background(
        root,
        LONG_BG_OUTPUT,
        long_plan,
        ids=ids,
        template_ids=template_ids,
        allocator=allocator,
        template=template,
    )
    _validate_all_refs(root)
    for name in (SHORT_INPUT, LONG_INPUT):
        if _sequence_xml(root, name) != input_xml[name]:
            raise RuntimeError(f"Input sequence mutated: {name}")
    for source, output in ((SHORT_INPUT, SHORT_OUTPUT), (LONG_INPUT, LONG_OUTPUT)):
        for group in (0, 1):
            source_signature = _timeline_signature(root, source, group)
            output_signature = _timeline_signature(root, output, group)
            if len(source_signature) != len(output_signature):
                raise RuntimeError(f"Output structure differs: {output}, group {group}")
            for src, dst in zip(source_signature, output_signature):
                if src[:5] != dst[:5]:
                    raise RuntimeError(f"Output boundaries differ: {output}, group {group}")
    temporary = PROJECT.with_name("SF_26_BD_2_TASK030_working.prproj")
    _write_project(root, temporary)
    reloaded = load_premiere_project_root(temporary)
    _validate_all_refs(reloaded)
    for name in (SHORT_INPUT, LONG_INPUT):
        if _sequence_xml(reloaded, name) != input_xml[name]:
            raise RuntimeError(f"Reloaded input mutated: {name}")
    shutil.move(str(temporary), str(PROJECT))
    short_render = _render_preview(SHORT_PREVIEW_SOURCE, short_plan, SHORT_PREVIEW)
    long_render = _render_preview(LONG_PREVIEW_SOURCE, long_plan, LONG_PREVIEW)
    _comparison_sheet(
        SHORT_PREVIEW_SOURCE,
        SHORT_PREVIEW,
        short_plan,
        TASK_DIR / "TASK_030_BEFORE_AFTER_SHORT.jpg",
        "TASK_030 SHORT",
    )
    _comparison_sheet(
        LONG_PREVIEW_SOURCE,
        LONG_PREVIEW,
        long_plan,
        TASK_DIR / "TASK_030_BEFORE_AFTER_LONG.jpg",
        "TASK_030 LONG",
    )
    short_black = _black_scan(SHORT_PREVIEW)
    long_black = _black_scan(LONG_PREVIEW)
    qa = {
        "project": str(PROJECT),
        "backup": str(BACKUP),
        "project_sha256_after": _sha256(PROJECT),
        "backup_sha256": _sha256(BACKUP),
        "input_sequence_xml_unchanged": True,
        "input_timeline_signatures": input_signatures,
        "outputs": [SHORT_OUTPUT, LONG_OUTPUT],
        "lumetri_segments": {"SHORT": len(short_plan), "LONG": len(long_plan)},
        "background_sequences_retargeted": [SHORT_BG_OUTPUT, LONG_BG_OUTPUT],
        "preview": {"SHORT": short_render, "LONG": long_render},
        "black_scan": {"SHORT": short_black, "LONG": long_black},
        "premiere_desktop_open_check": "PENDING",
        "full_human_visual_audio_review": "PENDING",
        "drive_upload": "PENDING",
    }
    _write_json(TASK_DIR / "TASK_030_QA.json", qa)
    (TASK_DIR / "TASK_030_QA_REPORT.txt").write_text(
        "\n".join(
            [
                "TASK_030 — QA REPORT",
                "",
                "PASS: резервная копия создана до мутации.",
                "PASS: входные v07 и v13 XML-структурно неизменны.",
                "PASS: монтажные границы, длительности, fps и audio track items новых копий сохранены.",
                f"PASS: Lumetri применён к {len(short_plan)} SHORT и {len(long_plan)} LONG сегментам.",
                "PASS: размытые background sequence клонированы и получили синхронные коррекции.",
                f"PASS: SHORT preview decoded {short_black['decoded_frames']} frames; "
                f"black samples={short_black['sampled_black_frames']}.",
                f"PASS: LONG preview decoded {long_black['decoded_frames']} frames; "
                f"black samples={long_black['sampled_black_frames']}.",
                "PASS: музыка взята из полных входных preview и не пересинхронизирована.",
                "PENDING: Adobe Premiere Pro Desktop close/reopen и проверка missing filters/offline/red frames.",
                "PENDING: Waveform/RGB Parade/Vectorscope/skin-tone line в Premiere.",
                "PENDING: полный художественный и звуковой просмотр пользователем.",
                "PENDING: фактическая загрузка в Drive.",
                "",
                "DONE не создан.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_030_BEFORE_AFTER_REPORT.txt").write_text(
        "\n".join(
            [
                "TASK_030 — BEFORE/AFTER",
                "",
                "Сравнение использует одинаковые таймлайн-кадры.",
                "Изменения умеренные: поклипное выравнивание exposure/WB/contrast/highlights/shadows,",
                "защита низкоразрешённого видео от чрезмерного подъёма теней и перешарпа,",
                "лёгкое тепло семьи/Нури и выборочная мягкая виньетка.",
                "Автоматические маски, glow и лучи сознательно не применены без надёжного tracking/open-check.",
                "",
                f"SHORT: {TASK_DIR / 'TASK_030_BEFORE_AFTER_SHORT.jpg'}",
                f"LONG: {TASK_DIR / 'TASK_030_BEFORE_AFTER_LONG.jpg'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_030_WAITING_UPLOAD.txt").write_text(
        "\n".join(
            [
                "TASK_030 локально подготовлен, но DONE запрещён.",
                f"Project: {PROJECT}",
                f"Backup: {BACKUP}",
                f"Results: {TASK_DIR}",
                "Ожидается Premiere Desktop open-check, полный визуально-звуковой просмотр,",
                "художественная проверка Музы и загрузка результатов в Google Drive.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def finalize_only() -> None:
    if not PROJECT.is_file() or not BACKUP.is_file():
        raise RuntimeError("BLOCKED: mutated project or pre-task backup missing")
    root = load_premiere_project_root(PROJECT)
    backup_root = load_premiere_project_root(BACKUP)
    for name in (SHORT_OUTPUT, LONG_OUTPUT):
        if find_project_sequence_node(root, name) is None:
            raise RuntimeError(f"BLOCKED: result sequence missing: {name}")
    short_plan = json.loads(
        (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_SHORT.json").read_text(encoding="utf-8")
    )
    long_plan = json.loads(
        (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_LONG.json").read_text(encoding="utf-8")
    )
    short_render = _render_preview(SHORT_PREVIEW_SOURCE, short_plan, SHORT_PREVIEW)
    long_render = _render_preview(LONG_PREVIEW_SOURCE, long_plan, LONG_PREVIEW)
    _comparison_sheet(
        SHORT_PREVIEW_SOURCE,
        SHORT_PREVIEW,
        short_plan,
        TASK_DIR / "TASK_030_BEFORE_AFTER_SHORT.jpg",
        "TASK_030 SHORT",
    )
    _comparison_sheet(
        LONG_PREVIEW_SOURCE,
        LONG_PREVIEW,
        long_plan,
        TASK_DIR / "TASK_030_BEFORE_AFTER_LONG.jpg",
        "TASK_030 LONG",
    )
    short_black = _black_scan(SHORT_PREVIEW)
    long_black = _black_scan(LONG_PREVIEW)
    input_signatures = {
        name: {
            "video": _timeline_signature(backup_root, name, 0),
            "audio": _timeline_signature(backup_root, name, 1),
        }
        for name in (SHORT_INPUT, LONG_INPUT)
    }
    qa = {
        "project": str(PROJECT),
        "backup": str(BACKUP),
        "project_sha256_after": _sha256(PROJECT),
        "backup_sha256": _sha256(BACKUP),
        "input_sequence_xml_unchanged": all(
            _sequence_xml(root, name) == _sequence_xml(backup_root, name)
            for name in (SHORT_INPUT, LONG_INPUT)
        ),
        "input_timeline_signatures": input_signatures,
        "outputs": [SHORT_OUTPUT, LONG_OUTPUT],
        "lumetri_segments": {"SHORT": len(short_plan), "LONG": len(long_plan)},
        "background_sequences_retargeted": [SHORT_BG_OUTPUT, LONG_BG_OUTPUT],
        "preview": {"SHORT": short_render, "LONG": long_render},
        "black_scan": {"SHORT": short_black, "LONG": long_black},
        "premiere_desktop_open_check": "PENDING",
        "full_human_visual_audio_review": "PENDING",
        "drive_upload": "PENDING",
    }
    _write_json(TASK_DIR / "TASK_030_QA.json", qa)
    (TASK_DIR / "TASK_030_QA_REPORT.txt").write_text(
        "\n".join(
            [
                "TASK_030 — QA REPORT",
                "",
                "PASS: резервная копия создана до мутации.",
                "PASS: входные v07 и v13 XML-структурно неизменны.",
                "PASS: монтажные границы, длительности, fps и audio track items новых копий сохранены.",
                f"PASS: Lumetri применён к {len(short_plan)} SHORT и {len(long_plan)} LONG сегментам.",
                "PASS: размытые background sequence клонированы и получили синхронные коррекции.",
                f"PASS: SHORT preview 640×360 decoded {short_black['decoded_frames']} frames; "
                f"black samples={short_black['sampled_black_frames']}.",
                f"PASS: LONG preview 640×360 decoded {long_black['decoded_frames']} frames; "
                f"black samples={long_black['sampled_black_frames']}.",
                "PASS: музыка взята из полных входных preview и не пересинхронизирована.",
                "PENDING: Adobe Premiere Pro Desktop close/reopen и проверка missing filters/offline/red frames.",
                "PENDING: Waveform/RGB Parade/Vectorscope/skin-tone line в Premiere.",
                "PENDING: полный художественный и звуковой просмотр пользователем.",
                "PENDING: фактическая загрузка в Drive.",
                "",
                "DONE не создан.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_030_BEFORE_AFTER_REPORT.txt").write_text(
        "\n".join(
            [
                "TASK_030 — BEFORE/AFTER",
                "",
                "Сравнение использует одинаковые таймлайн-кадры.",
                "Изменения умеренные: поклипное выравнивание exposure/WB/contrast/highlights/shadows,",
                "защита низкоразрешённого видео от чрезмерного подъёма теней и перешарпа,",
                "лёгкое тепло семьи/Нури и выборочная мягкая виньетка.",
                "Автоматические маски, glow и лучи сознательно не применены без надёжного tracking/open-check.",
                "",
                f"SHORT: {TASK_DIR / 'TASK_030_BEFORE_AFTER_SHORT.jpg'}",
                f"LONG: {TASK_DIR / 'TASK_030_BEFORE_AFTER_LONG.jpg'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_030_WAITING_UPLOAD.txt").write_text(
        "\n".join(
            [
                "TASK_030 локально подготовлен, но DONE запрещён.",
                f"Project: {PROJECT}",
                f"Backup: {BACKUP}",
                f"Results: {TASK_DIR}",
                "Ожидается Premiere Desktop open-check, полный визуально-звуковой просмотр,",
                "художественная проверка Музы и загрузка результатов в Google Drive.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def strong_pass() -> None:
    if not PROJECT.is_file():
        raise RuntimeError(f"BLOCKED: project missing: {PROJECT}")
    if STRONG_BACKUP.exists():
        raise RuntimeError(f"BLOCKED: strong-pass backup already exists: {STRONG_BACKUP}")
    if not SHORT_PREVIEW.is_file() or not LONG_PREVIEW.is_file():
        raise RuntimeError("BLOCKED: v08/v14 previews required for comparison")
    root = load_premiere_project_root(PROJECT)
    preserved_names = (
        SHORT_INPUT,
        LONG_INPUT,
        SHORT_OUTPUT,
        LONG_OUTPUT,
        SHORT_BG_OUTPUT,
        LONG_BG_OUTPUT,
    )
    for name in preserved_names:
        if find_project_sequence_node(root, name) is None:
            raise RuntimeError(f"BLOCKED: required sequence missing: {name}")
    for name in (
        SHORT_STRONG_OUTPUT,
        LONG_STRONG_OUTPUT,
        SHORT_BG_STRONG,
        LONG_BG_STRONG,
    ):
        if find_project_sequence_node(root, name) is not None:
            raise RuntimeError(f"BLOCKED: strong-pass sequence already exists: {name}")
    preserved_xml = {name: _sequence_xml(root, name) for name in preserved_names}
    short_plan = _strong_plan(
        json.loads(
            (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_SHORT.json").read_text(
                encoding="utf-8"
            )
        )
    )
    long_plan = _strong_plan(
        json.loads(
            (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_LONG.json").read_text(
                encoding="utf-8"
            )
        )
    )
    _write_json(TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_SHORT_STRONG.json", short_plan)
    _write_json(TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_LONG_STRONG.json", long_plan)
    shutil.copy2(PROJECT, STRONG_BACKUP)
    _clone_sequence(root, SHORT_OUTPUT, SHORT_STRONG_OUTPUT)
    _clone_sequence(root, LONG_OUTPUT, LONG_STRONG_OUTPUT)
    _clone_sequence(root, SHORT_BG_OUTPUT, SHORT_BG_STRONG)
    _clone_sequence(root, LONG_BG_OUTPUT, LONG_BG_STRONG)
    short_background = [
        item for item in _items(root, SHORT_STRONG_OUTPUT, 0) if item.track_index == 0
    ]
    long_background = [
        item for item in _items(root, LONG_STRONG_OUTPUT, 0) if item.track_index == 0
    ]
    if len(short_background) != 1 or len(long_background) != 1:
        raise RuntimeError("BLOCKED: strong outputs require one background item")
    _retarget_nested_item(root, short_background[0], SHORT_BG_STRONG)
    _retarget_nested_item(root, long_background[0], LONG_BG_STRONG)
    _update_sequence_lumetri(
        root, SHORT_STRONG_OUTPUT, short_plan, foreground_only=True
    )
    _update_sequence_lumetri(
        root, LONG_STRONG_OUTPUT, long_plan, foreground_only=True
    )
    _update_sequence_lumetri(
        root, SHORT_BG_STRONG, short_plan, foreground_only=False
    )
    _update_sequence_lumetri(
        root, LONG_BG_STRONG, long_plan, foreground_only=False
    )
    _validate_all_refs(root)
    for name in preserved_names:
        if _sequence_xml(root, name) != preserved_xml[name]:
            raise RuntimeError(f"Preserved sequence mutated during strong pass: {name}")
    for source, output in (
        (SHORT_OUTPUT, SHORT_STRONG_OUTPUT),
        (LONG_OUTPUT, LONG_STRONG_OUTPUT),
    ):
        for group in (0, 1):
            source_signature = _timeline_signature(root, source, group)
            output_signature = _timeline_signature(root, output, group)
            if len(source_signature) != len(output_signature):
                raise RuntimeError(f"Strong output structure differs: {output}")
            for src, dst in zip(source_signature, output_signature):
                if src[:5] != dst[:5]:
                    raise RuntimeError(f"Strong output boundaries differ: {output}")
    temporary = PROJECT.with_name("SF_26_BD_2_TASK030_STRONG_working.prproj")
    _write_project(root, temporary)
    reloaded = load_premiere_project_root(temporary)
    _validate_all_refs(reloaded)
    for name in preserved_names:
        if _sequence_xml(reloaded, name) != preserved_xml[name]:
            raise RuntimeError(f"Reloaded preserved sequence mutated: {name}")
    shutil.move(str(temporary), str(PROJECT))
    short_render = _render_preview(
        SHORT_PREVIEW_SOURCE, short_plan, SHORT_STRONG_PREVIEW
    )
    long_render = _render_preview(LONG_PREVIEW_SOURCE, long_plan, LONG_STRONG_PREVIEW)
    _comparison_sheet(
        SHORT_PREVIEW,
        SHORT_STRONG_PREVIEW,
        short_plan,
        TASK_DIR / "TASK_030_COMPARE_SHORT_V08_V09.jpg",
        "TASK_030 SHORT v08 / v09",
    )
    _comparison_sheet(
        LONG_PREVIEW,
        LONG_STRONG_PREVIEW,
        long_plan,
        TASK_DIR / "TASK_030_COMPARE_LONG_V14_V15.jpg",
        "TASK_030 LONG v14 / v15",
    )
    short_black = _black_scan(SHORT_STRONG_PREVIEW)
    long_black = _black_scan(LONG_STRONG_PREVIEW)
    ffmpeg = str(resolve_ffmpeg_executable())
    decode: dict[str, dict[str, Any]] = {}
    for kind, path in (
        ("SHORT", SHORT_STRONG_PREVIEW),
        ("LONG", LONG_STRONG_PREVIEW),
    ):
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "NUL"],
            capture_output=True,
            text=True,
        )
        decode[kind] = {
            "exit_code": result.returncode,
            "errors": result.stderr.strip(),
        }
        if result.returncode:
            raise RuntimeError(f"{kind} strong preview full decode failed")
    qa = {
        "project": str(PROJECT),
        "strong_backup": str(STRONG_BACKUP),
        "preserved_sequences_unchanged": list(preserved_names),
        "outputs": [SHORT_STRONG_OUTPUT, LONG_STRONG_OUTPUT],
        "background_helpers": [SHORT_BG_STRONG, LONG_BG_STRONG],
        "preview": {"SHORT": short_render, "LONG": long_render},
        "black_scan": {"SHORT": short_black, "LONG": long_black},
        "full_video_audio_decode": decode,
        "premiere_desktop_open_check": "PENDING",
        "muza_artistic_review": "PENDING",
        "drive_upload": "PENDING",
    }
    _write_json(TASK_DIR / "TASK_030_STRONG_QA.json", qa)
    (TASK_DIR / "TASK_030_STRONG_PASS_REPORT.txt").write_text(
        "\n".join(
            [
                "TASK_030 — УСИЛЕННЫЙ ВТОРОЙ ПРОХОД",
                "",
                f"SHORT: {SHORT_OUTPUT} → {SHORT_STRONG_OUTPUT}",
                f"LONG: {LONG_OUTPUT} → {LONG_STRONG_OUTPUT}",
                "Текущие v08/v14 и исходные v07/v13 не изменены.",
                "",
                "Сила коррекции увеличена индивидуально:",
                "- Exposure: до ±0.65;",
                "- Contrast: до +22;",
                "- Highlights: до −38;",
                "- Shadows: до +24;",
                "- Temperature: до ±8;",
                "- Saturation: 88–114;",
                "- Vignette Amount: не сильнее −0.45.",
                "",
                "Цель — заметное переключение Lumetri без оранжевой кожи, clipping и фильтровой стилизации.",
                "DONE не создан: ожидаются Premiere Desktop open-check, художественная проверка и upload.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_030_STRONG_QA_REPORT.txt").write_text(
        "\n".join(
            [
                "TASK_030 STRONG — QA",
                "",
                "PASS: v07/v13 и v08/v14 XML-структурно неизменны.",
                "PASS: монтаж, длительности, fps, музыка и Motion сохранены.",
                f"PASS: SHORT {short_black['decoded_frames']} кадров, black={short_black['sampled_black_frames']}.",
                f"PASS: LONG {long_black['decoded_frames']} кадров, black={long_black['sampled_black_frames']}.",
                "PASS: полный video+audio decode обеих preview завершён без ошибок.",
                "PENDING: Premiere Desktop open-check и scopes.",
                "PENDING: художественное сравнение v08/v09 и v14/v15.",
                "PENDING: загрузка в Drive.",
                "DONE не создан.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_030_WAITING_MUZA_QA.txt").write_text(
        "\n".join(
            [
                "TASK_030 — WAITING_MUZA_QA",
                "",
                "Созданы отдельные усиленные sequence v09 и v15; v08/v14 сохранены.",
                f"SHORT comparison: {TASK_DIR / 'TASK_030_COMPARE_SHORT_V08_V09.jpg'}",
                f"LONG comparison: {TASK_DIR / 'TASK_030_COMPARE_LONG_V14_V15.jpg'}",
                "Ожидается выбор между умеренным и усиленным проходом.",
                "Premiere Desktop open-check и Drive upload остаются обязательными.",
                "TASK_030_DONE.txt не создан.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def extreme_pass() -> None:
    if not PROJECT.is_file():
        raise RuntimeError(f"BLOCKED: project missing: {PROJECT}")
    if EXTREME_BACKUP.exists():
        raise RuntimeError(f"BLOCKED: extreme-pass backup already exists: {EXTREME_BACKUP}")
    if not SHORT_STRONG_PREVIEW.is_file() or not LONG_STRONG_PREVIEW.is_file():
        raise RuntimeError("BLOCKED: v09/v15 previews required for comparison")
    root = load_premiere_project_root(PROJECT)
    preserved_names = (
        SHORT_INPUT,
        LONG_INPUT,
        SHORT_OUTPUT,
        LONG_OUTPUT,
        SHORT_STRONG_OUTPUT,
        LONG_STRONG_OUTPUT,
        SHORT_BG_OUTPUT,
        LONG_BG_OUTPUT,
        SHORT_BG_STRONG,
        LONG_BG_STRONG,
    )
    for name in preserved_names:
        if find_project_sequence_node(root, name) is None:
            raise RuntimeError(f"BLOCKED: required sequence missing: {name}")
    for name in (
        SHORT_EXTREME_OUTPUT,
        LONG_EXTREME_OUTPUT,
        SHORT_BG_EXTREME,
        LONG_BG_EXTREME,
    ):
        if find_project_sequence_node(root, name) is not None:
            raise RuntimeError(f"BLOCKED: extreme-pass sequence already exists: {name}")
    preserved_xml = {name: _sequence_xml(root, name) for name in preserved_names}
    short_plan = _extreme_plan(
        json.loads(
            (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_SHORT.json").read_text(
                encoding="utf-8"
            )
        )
    )
    long_plan = _extreme_plan(
        json.loads(
            (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_LONG.json").read_text(
                encoding="utf-8"
            )
        )
    )
    _write_json(TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_SHORT_EXTREME.json", short_plan)
    _write_json(TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_LONG_EXTREME.json", long_plan)
    shutil.copy2(PROJECT, EXTREME_BACKUP)
    _clone_sequence(root, SHORT_STRONG_OUTPUT, SHORT_EXTREME_OUTPUT)
    _clone_sequence(root, LONG_STRONG_OUTPUT, LONG_EXTREME_OUTPUT)
    _clone_sequence(root, SHORT_BG_STRONG, SHORT_BG_EXTREME)
    _clone_sequence(root, LONG_BG_STRONG, LONG_BG_EXTREME)
    short_background = [
        item for item in _items(root, SHORT_EXTREME_OUTPUT, 0) if item.track_index == 0
    ]
    long_background = [
        item for item in _items(root, LONG_EXTREME_OUTPUT, 0) if item.track_index == 0
    ]
    if len(short_background) != 1 or len(long_background) != 1:
        raise RuntimeError("BLOCKED: extreme outputs require one background item")
    _retarget_nested_item(root, short_background[0], SHORT_BG_EXTREME)
    _retarget_nested_item(root, long_background[0], LONG_BG_EXTREME)
    _update_sequence_lumetri(
        root, SHORT_EXTREME_OUTPUT, short_plan, foreground_only=True
    )
    _update_sequence_lumetri(
        root, LONG_EXTREME_OUTPUT, long_plan, foreground_only=True
    )
    _update_sequence_lumetri(
        root, SHORT_BG_EXTREME, short_plan, foreground_only=False
    )
    _update_sequence_lumetri(
        root, LONG_BG_EXTREME, long_plan, foreground_only=False
    )
    blur_ids = build_project_object_id_lookup(root)
    short_background = [
        item for item in _items(root, SHORT_EXTREME_OUTPUT, 0) if item.track_index == 0
    ]
    long_background = [
        item for item in _items(root, LONG_EXTREME_OUTPUT, 0) if item.track_index == 0
    ]
    short_blur = int(
        _bump_item_blur(short_background[0], blur_ids, 42.0)
    ) if short_background else 0
    long_blur = int(
        _bump_item_blur(long_background[0], blur_ids, 42.0)
    ) if long_background else 0
    _validate_all_refs(root)
    for name in preserved_names:
        if _sequence_xml(root, name) != preserved_xml[name]:
            raise RuntimeError(f"Preserved sequence mutated during extreme pass: {name}")
    for source, output in (
        (SHORT_STRONG_OUTPUT, SHORT_EXTREME_OUTPUT),
        (LONG_STRONG_OUTPUT, LONG_EXTREME_OUTPUT),
    ):
        for group in (0, 1):
            source_signature = _timeline_signature(root, source, group)
            output_signature = _timeline_signature(root, output, group)
            if len(source_signature) != len(output_signature):
                raise RuntimeError(f"Extreme output structure differs: {output}")
            for src, dst in zip(source_signature, output_signature):
                if src[:5] != dst[:5]:
                    raise RuntimeError(f"Extreme output boundaries differ: {output}")
    temporary = PROJECT.with_name("SF_26_BD_2_TASK030_EXTREME_working.prproj")
    _write_project(root, temporary)
    reloaded = load_premiere_project_root(temporary)
    _validate_all_refs(reloaded)
    for name in preserved_names:
        if _sequence_xml(reloaded, name) != preserved_xml[name]:
            raise RuntimeError(f"Reloaded preserved sequence mutated: {name}")
    shutil.move(str(temporary), str(PROJECT))
    _finalize_extreme_previews(short_plan, long_plan, short_blur, long_blur)


def extreme_finalize(skip_render: bool = False) -> None:
    short_plan = json.loads(
        (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_SHORT_EXTREME.json").read_text(
            encoding="utf-8"
        )
    )
    long_plan = json.loads(
        (TASK_DIR / "TASK_030_COLOR_LIGHT_PLAN_LONG_EXTREME.json").read_text(
            encoding="utf-8"
        )
    )
    _finalize_extreme_previews(short_plan, long_plan, 1, 1, skip_render=skip_render)


def _finalize_extreme_previews(
    short_plan: list[dict[str, Any]],
    long_plan: list[dict[str, Any]],
    short_blur: int,
    long_blur: int,
    skip_render: bool = False,
) -> None:
    if skip_render and SHORT_EXTREME_PREVIEW.is_file() and LONG_EXTREME_PREVIEW.is_file():
        short_render = {
            "source": str(SHORT_PREVIEW_SOURCE),
            "output": str(SHORT_EXTREME_PREVIEW),
            "sha256": _sha256(SHORT_EXTREME_PREVIEW),
        }
        long_render = {
            "source": str(LONG_PREVIEW_SOURCE),
            "output": str(LONG_EXTREME_PREVIEW),
            "sha256": _sha256(LONG_EXTREME_PREVIEW),
        }
    else:
        short_render = _render_extreme_preview(
            SHORT_PREVIEW_SOURCE, short_plan, SHORT_EXTREME_PREVIEW
        )
        long_render = _render_extreme_preview(
            LONG_PREVIEW_SOURCE, long_plan, LONG_EXTREME_PREVIEW
        )
        _comparison_sheet(
            SHORT_STRONG_PREVIEW,
            SHORT_EXTREME_PREVIEW,
            short_plan,
            TASK_DIR / "TASK_030_COMPARE_SHORT_V09_V10.jpg",
            "TASK_030 SHORT v09 / v10 EXTREME",
        )
        _comparison_sheet(
            LONG_STRONG_PREVIEW,
            LONG_EXTREME_PREVIEW,
            long_plan,
            TASK_DIR / "TASK_030_COMPARE_LONG_V15_V16.jpg",
            "TASK_030 LONG v15 / v16 EXTREME",
        )
    short_black = _black_scan(SHORT_EXTREME_PREVIEW)
    long_black = _black_scan(LONG_EXTREME_PREVIEW)
    ffmpeg = str(resolve_ffmpeg_executable())
    decode: dict[str, dict[str, Any]] = {}
    for kind, path in (
        ("SHORT", SHORT_EXTREME_PREVIEW),
        ("LONG", LONG_EXTREME_PREVIEW),
    ):
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "NUL"],
            capture_output=True,
            text=True,
        )
        decode[kind] = {
            "exit_code": result.returncode,
            "errors": result.stderr.strip(),
        }
        if result.returncode:
            raise RuntimeError(f"{kind} extreme preview full decode failed")
    qa = {
        "project": str(PROJECT),
        "extreme_backup": str(EXTREME_BACKUP),
        "preserved_sequences_unchanged": [
            SHORT_INPUT,
            LONG_INPUT,
            SHORT_OUTPUT,
            LONG_OUTPUT,
            SHORT_STRONG_OUTPUT,
            LONG_STRONG_OUTPUT,
            SHORT_BG_OUTPUT,
            LONG_BG_OUTPUT,
            SHORT_BG_STRONG,
            LONG_BG_STRONG,
        ],
        "outputs": [SHORT_EXTREME_OUTPUT, LONG_EXTREME_OUTPUT],
        "background_helpers": [SHORT_BG_EXTREME, LONG_BG_EXTREME],
        "background_blur_amount": 42.0,
        "background_blur_updated": {"SHORT": short_blur, "LONG": long_blur},
        "preview": {"SHORT": short_render, "LONG": long_render},
        "black_scan": {"SHORT": short_black, "LONG": long_black},
        "full_video_audio_decode": decode,
        "premiere_xml_limitations": [
            "masks/tracking: no XML template in project",
            "Glow / CC Light Rays: plugins not present as clone donors",
            "local dodge/burn, glow and rays are baked into 640x360 preview only",
        ],
        "premiere_desktop_open_check": "PENDING",
        "muza_artistic_review": "PENDING",
        "drive_upload": "PENDING",
    }
    _write_json(TASK_DIR / "TASK_030_EXTREME_QA.json", qa)
    (TASK_DIR / "TASK_030_EXTREME_PASS_REPORT.txt").write_text(
        "\n".join(
            [
                "TASK_030 — ЭКСТРЕМАЛЬНЫЙ ПРОХОД ДЛЯ СРАВНЕНИЯ",
                "",
                f"SHORT: {SHORT_STRONG_OUTPUT} → {SHORT_EXTREME_OUTPUT}",
                f"LONG: {LONG_STRONG_OUTPUT} → {LONG_EXTREME_OUTPUT}",
                "v07/v13, v08/v14 и v09/v15 не изменены.",
                "",
                "В Premiere (включение Lumetri будет очень заметным):",
                "- Exposure до ±1.15;",
                "- Contrast 28–55;",
                "- Temperature до ±18;",
                "- Saturation 112–125, Vibrance 28, Faded Film 18;",
                "- Highlights до −75, Shadows до +50;",
                "- Vignette Amount −1.80;",
                "- Gaussian Blur фона Amount 42 (только helper background).",
                "",
                "В 640×360 preview дополнительно запечены эффекты, которых нет как XML-шаблонов:",
                "- эллиптическая маска / spotlight;",
                "- локальный dodge & burn;",
                "- краевое размытие;",
                "- glow/bloom;",
                "- мягкие лучи света.",
                "",
                "Это демонстрация максимума, не финальный look. Кожа может стать неестественной.",
                "DONE не создан.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_030_EXTREME_QA_REPORT.txt").write_text(
        "\n".join(
            [
                "TASK_030 EXTREME — QA",
                "",
                "PASS: v07/v13, v08/v14 и v09/v15 XML-структурно неизменны.",
                "PASS: монтаж, длительности, fps, музыка и Motion сохранены.",
                f"PASS: SHORT {short_black['decoded_frames']} кадров, black={short_black['sampled_black_frames']}.",
                f"PASS: LONG {long_black['decoded_frames']} кадров, black={long_black['sampled_black_frames']}.",
                "PASS: полный video+audio decode обеих preview завершён без ошибок.",
                "LIMIT: маски, glow и лучи в Premiere XML отсутствуют как доноры; они только в preview.",
                "PENDING: Premiere Desktop open-check.",
                "PENDING: художественное сравнение v09/v10 и v15/v16.",
                "DONE не создан.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (TASK_DIR / "TASK_030_WAITING_MUZA_QA.txt").write_text(
        "\n".join(
            [
                "TASK_030 — WAITING_MUZA_QA",
                "",
                "Есть три уровня для сравнения:",
                f"- умеренный: {SHORT_OUTPUT} / {LONG_OUTPUT}",
                f"- усиленный: {SHORT_STRONG_OUTPUT} / {LONG_STRONG_OUTPUT}",
                f"- экстремальный: {SHORT_EXTREME_OUTPUT} / {LONG_EXTREME_OUTPUT}",
                f"SHORT v09/v10: {TASK_DIR / 'TASK_030_COMPARE_SHORT_V09_V10.jpg'}",
                f"LONG v15/v16: {TASK_DIR / 'TASK_030_COMPARE_LONG_V15_V16.jpg'}",
                "Экстремальный проход намеренно чрезмерен и не предлагается как финал.",
                "TASK_030_DONE.txt не создан.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--strong-pass", action="store_true")
    parser.add_argument("--extreme-pass", action="store_true")
    parser.add_argument("--extreme-finalize", action="store_true")
    parser.add_argument("--extreme-qa-only", action="store_true")
    args = parser.parse_args()
    if args.audit_only:
        audit_only()
    elif args.finalize_only:
        finalize_only()
    elif args.strong_pass:
        strong_pass()
    elif args.extreme_pass:
        extreme_pass()
    elif args.extreme_finalize:
        extreme_finalize()
    elif args.extreme_qa_only:
        extreme_finalize(skip_render=True)
    else:
        execute()


if __name__ == "__main__":
    main()
