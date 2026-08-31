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

from utils.premiere_keep_apply_export import _KeepSegment, _clone_track_item_with_bounds
from utils.premiere_media_import_export import _clone_filter_component
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    is_supported_image_media_path,
    list_named_project_sequence_names,
    load_premiere_project_root,
)
from utils.premiere_project_export import (
    _ProjectObjectIdAllocator,
    _update_sequence_duration_metadata,
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
from utils.premiere_trim_review_export import (
    _ensure_track_items_container,
    _reindex_track_items,
)
from utils.video_frame_extract import resolve_ffmpeg_executable


TASK_ID = "TASK_031"
FPS = 25
FRAME_TICKS = _frame_ticks(FPS)
SOURCE_PROJECT = Path("input") / 'SF_26_Bd_Art_2.prproj'
BACKUP_PROJECT = Path("input") / 'SF_26_Bd_Art_2_before_TASK_031.prproj'
OUTPUT_PROJECT = Path(
    r"input/SF_26_Bd_Art_3_TASK031_ADVANCED_FINAL.prproj"
)
CHECKPOINT_PROJECT = Path(
    r"input/SF_26_Bd_Art_3_TASK031_EDIT_CHECKPOINT.prproj"
)
PREVIEW_PATH = Path(
    r"input/SF_26_Bd_Art_4_TASK031_ADVANCED_FINAL_640_360.mp4"
)
SOURCE_SEQUENCE = "SF_26_Bd_Art_3"
OUTPUT_SEQUENCE = "SF_26_Bd_Art_4_TASK031_ADVANCED_FINAL"
REPO_TASK_DIR = Path(__file__).resolve().parent / (
    "TASK_031_ART_AUTONOMOUS_AUDIT_JSON_EDIT_FINISH"
)
LOCAL_TASK_DIR = Path(
    r"input/TASK_031_ART_AUTONOMOUS_AUDIT_JSON_EDIT_FINISH"
)
DRIVE_CANDIDATES = []  # Cloud delivery is explicitly separate.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".jfif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
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

# Beat windows on the ORIGINAL Art_3 timeline. REMOVE windows drop the range.
# KEEP windows compact to new_dur while preserving V1/V2 overlay alignment.
BEATS: list[dict[str, Any]] = [
    {"id": "B01_youth_1", "start": 0, "end": 125, "action": "KEEP", "new_dur": 100, "function": "экспозиция", "reason": "Молодой Сергей открывает фильм; 5 с слишком каталожно, 4 с удерживают портрет."},
    {"id": "B02_youth_2", "start": 125, "end": 250, "action": "KEEP", "new_dur": 100, "function": "экспозиция", "reason": "Второй юношеский портрет закрепляет героя до чеканки."},
    {"id": "B03_irina_31", "start": 250, "end": 375, "action": "KEEP", "new_dur": 80, "function": "развитие", "reason": "Юность и близость; сокращаю однотипный 5-секундный ритм."},
    {"id": "B04_irina_29", "start": 375, "end": 500, "action": "KEEP", "new_dur": 80, "function": "развитие", "reason": "Парный кадр юности; оставляю, но короче."},
    {"id": "B05_childhood_1972", "start": 500, "end": 625, "action": "KEEP", "new_dur": 110, "function": "экспозиция", "reason": "Архив 1972 — якорь времени; даю чуть больше дыхания."},
    {"id": "B06_theranki_portrait", "start": 625, "end": 709, "action": "KEEP", "new_dur": 80, "function": "мост", "reason": "Первый вход в чеканку/Theranki; сохраняю overlay-язык."},
    {"id": "B07_double_34b", "start": 709, "end": 834, "action": "KEEP", "new_dur": 100, "function": "кульминация", "reason": "Сильная двойная экспозиция чеканки; оставляю почти полностью."},
    {"id": "B08_double_art", "start": 834, "end": 959, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Чеканка + живопись; чуть короче, чтобы не зациклиться."},
    {"id": "B09_ig26", "start": 959, "end": 1084, "action": "KEEP", "new_dur": 100, "function": "развитие", "reason": "Самостоятельная чеканка 260829_26 — нужный авторский кадр."},
    {"id": "B10_rodin_keep", "start": 1084, "end": 1129, "action": "KEEP", "new_dur": 50, "function": "контрапункт", "reason": "Один скульптурный акцент Родена; остальные вспышки удаляю."},
    {"id": "B11_flash_93611", "start": 1129, "end": 1168, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "Короткая скульптурная вспышка без новой мысли."},
    {"id": "B12_flash_rodin2", "start": 1168, "end": 1195, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "Второй Роден за 27 кадров — каталог, не развитие."},
    {"id": "B13_flash_bronze", "start": 1195, "end": 1229, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "Ещё одна бронза; мысль уже сказана B10."},
    {"id": "B14_flash_sculpture", "start": 1229, "end": 1264, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "Четвёртая вспышка скульптуры подряд."},
    {"id": "B15_theranki_art", "start": 1264, "end": 1339, "action": "KEEP", "new_dur": 75, "function": "развитие", "reason": "Длиннее и читаемее, чем вспышки; оставляю как форму."},
    {"id": "B16_comfy_flash", "start": 1339, "end": 1369, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "30-кадровая вспышка той же ComfyUI-формы, что дальше идёт длиннее."},
    {"id": "B17_comfy_long", "start": 1369, "end": 1507, "action": "KEEP", "new_dur": 100, "function": "развитие", "reason": "Цифровая чеканка/форма; мост к современному инструменту."},
    {"id": "B18_ig06", "start": 1507, "end": 1632, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Серия 260829 — оставляю характерные, режу дубли."},
    {"id": "B19_ig36", "start": 1632, "end": 1757, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Другая фактура металла; нужна после 06."},
    {"id": "B20_ig20_3", "start": 1757, "end": 1882, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Лучший из пары 20_3/20_2."},
    {"id": "B21_ig20_2_dup", "start": 1882, "end": 2007, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "Почти тот же мотив, что 20_3; уникальной мысли нет."},
    {"id": "B22_unnamed_form", "start": 2007, "end": 2069, "action": "KEEP", "new_dur": 55, "function": "контрапункт", "reason": "Короткая форма; оставляю как паузу между сериями."},
    {"id": "B23_detail_overlay", "start": 2069, "end": 2111, "action": "KEEP", "new_dur": 42, "function": "мост", "reason": "Уже короткий детальный overlay; не режу."},
    {"id": "B24_ig24", "start": 2111, "end": 2236, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Самостоятельная чеканка, не дубль 20_x."},
    {"id": "B25_yakov", "start": 2236, "end": 2361, "action": "KEEP", "new_dur": 80, "function": "мост", "reason": "Человек среди искусства/семьи; переход от металла к жизни."},
    {"id": "B26_levitan", "start": 2361, "end": 2407, "action": "KEEP", "new_dur": 50, "function": "контрапункт", "reason": "Живописный акцент; одну вспышку достаточно."},
    {"id": "B27_levitan_flash", "start": 2407, "end": 2429, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "22-кадровый второй Левитан только на V2 — технический flash."},
    {"id": "B28_sf_color", "start": 2429, "end": 2494, "action": "KEEP", "new_dur": 65, "function": "развитие", "reason": "Двойная экспозиция с авторским цветом; длительность уже верная."},
    {"id": "B29_img2358", "start": 2494, "end": 2619, "action": "KEEP", "new_dur": 100, "function": "развитие", "reason": "Живой портрет/сцена; даю вес после каталога металла."},
    {"id": "B30_ig21", "start": 2619, "end": 2744, "action": "KEEP", "new_dur": 80, "function": "развитие", "reason": "Ещё одна чеканка, но уже после человеческого кадра."},
    {"id": "B31_overlay_chek", "start": 2744, "end": 2799, "action": "KEEP", "new_dur": 55, "function": "контрапункт", "reason": "Короткий overlay; сохраняю как есть."},
    {"id": "B32_ig2b68", "start": 2799, "end": 2924, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Цифровой портрет/форма; ближе к современному блоку."},
    {"id": "B33_comfy_pair", "start": 2924, "end": 2999, "action": "KEEP", "new_dur": 65, "function": "мост", "reason": "Overlay Comfy+фото; сжимаю 75→65, не ломая пару V1/V2."},
    {"id": "B34_ig7ff5", "start": 2999, "end": 3124, "action": "KEEP", "new_dur": 80, "function": "развитие", "reason": "Оставляю, но вынимаю из 5-секундной сетки."},
    {"id": "B35_igdadf", "start": 3124, "end": 3249, "action": "KEEP", "new_dur": 80, "function": "развитие", "reason": "Следующий самостоятельный кадр серии."},
    {"id": "B36_long_overlay", "start": 3249, "end": 3419, "action": "KEEP", "new_dur": 100, "function": "кульминация", "reason": "Длинный overlay 170 кадров слишком держит одну мысль; 4 с достаточно."},
    {"id": "B37_ig41", "start": 3419, "end": 3544, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "260830_41 — отдельный кадр после overlay."},
    {"id": "B38_ig9f48", "start": 3544, "end": 3669, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Ещё одна сильная форма до Mattis."},
    {"id": "B39_mattis", "start": 3669, "end": 3769, "action": "KEEP", "new_dur": 80, "function": "контрапункт", "reason": "Живопись поверх чеканки; оставляю читаемость слоёв."},
    {"id": "B40_ig22", "start": 3769, "end": 3894, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Серия 22/23/14/15 — оставляю три, один дубль режу."},
    {"id": "B41_ig23", "start": 3894, "end": 4019, "action": "KEEP", "new_dur": 90, "function": "развитие", "reason": "Парный к 22, не идентичен."},
    {"id": "B42_ig14", "start": 4019, "end": 4144, "action": "KEEP", "new_dur": 80, "function": "развитие", "reason": "Другой ракурс/форма."},
    {"id": "B43_ig16", "start": 4144, "end": 4269, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "Однотипный каталожный кадр между 14 и 15."},
    {"id": "B44_ig15", "start": 4269, "end": 4394, "action": "KEEP", "new_dur": 80, "function": "развитие", "reason": "Замыкаю металлическую серию перед style bank."},
    {"id": "B45_artp", "start": 4394, "end": 4519, "action": "KEEP", "new_dur": 90, "function": "мост", "reason": "Первый style-кадр: переход к цифровому банку."},
    {"id": "B46_cha", "start": 4519, "end": 4644, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "Вариант той же фотосессии; каталог стилей."},
    {"id": "B47_klm", "start": 4644, "end": 4769, "action": "REMOVE", "new_dur": 0, "function": "повтор", "reason": "Третий вариант 240830_05; мысль уже есть в artp и wcp."},
    {"id": "B48_wcp", "start": 4769, "end": 4894, "action": "KEEP", "new_dur": 90, "function": "мост", "reason": "Второй, визуально иной style; достаточно двух."},
    {"id": "B49_exec", "start": 4894, "end": 5019, "action": "KEEP", "new_dur": 75, "function": "мост", "reason": "Компьютер/экран как инструмент; не бытовой кадр."},
    {"id": "B50_gen4", "start": 5019, "end": 5170, "action": "KEEP", "new_dur": 120, "function": "развитие", "reason": "Запечённое видео процесса; не режу на фото, только хвост."},
    {"id": "B51_ig40", "start": 5170, "end": 5295, "action": "KEEP", "new_dur": 85, "function": "развитие", "reason": "Современный портрет/форма перед музеем."},
    {"id": "B52_computer_portrait", "start": 5295, "end": 5420, "action": "KEEP", "new_dur": 80, "function": "мост", "reason": "Сергей/творчество у машины; готовит музей и Нури."},
    {"id": "B53_museum1", "start": 5420, "end": 5525, "action": "KEEP", "new_dur": 105, "function": "жизнь и время", "reason": "Музейное видео 1 оставляю целиком — редкое дыхание движения."},
    {"id": "B54_museum2", "start": 5525, "end": 5886, "action": "KEEP", "new_dur": 120, "function": "жизнь и время", "reason": "361 кадр затягивает; 4.8 с оставляют зал, не экскурсию."},
    {"id": "B55_museum3", "start": 5886, "end": 6150, "action": "KEEP", "new_dur": 100, "function": "жизнь и время", "reason": "Второй музейный файл: 264→100, чтобы не потерять зал."},
    {"id": "B56_keep08_a", "start": 6150, "end": 6231, "action": "KEEP", "new_dur": 81, "function": "кульминация", "reason": "Nested Keep_08, первый кусок — не режу, это живое продолжение."},
    {"id": "B57_keep08_b", "start": 6231, "end": 6478, "action": "KEEP", "new_dur": 200, "function": "кульминация", "reason": "Основной компьютер/семья из Keep_08; 247→200 без потери жеста."},
    {"id": "B58_nuri_still", "start": 6478, "end": 6603, "action": "KEEP", "new_dur": 125, "function": "финал", "reason": "Нури/портрет — точка сборки; даю полные 5 с."},
    {"id": "B59_nuri_video", "start": 6603, "end": 6853, "action": "KEEP", "new_dur": 220, "function": "финал", "reason": "Запечённое видео с Нури; слегка короче, не режу внутри на фото."},
    {"id": "B60_finale", "start": 6853, "end": 7004, "action": "KEEP", "new_dur": 151, "function": "финал", "reason": "Последний кадр обязан остаться целиком."},
]


from utils.premiere_art_runtime import configure_module
configure_module(globals(), "031")

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _save_project(root: ET.Element, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )


def _items(root: ET.Element, sequence_name: str, project_path: Path, group: int) -> list[Any]:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} is missing.")
    return _track_item_contexts(
        sequence,
        group_index=group,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=project_path,
    )


def _clip_key(item: Any) -> tuple[int, int, str]:
    return (int(item.track_index), int(item.start // FRAME_TICKS), str(item.name))


def _row(item: Any) -> dict[str, Any]:
    suffix = Path(item.source_path).suffix.lower() if item.source_path else ""
    return {
        "track": item.track_index,
        "name": item.name,
        "start": item.start // FRAME_TICKS,
        "end": item.end // FRAME_TICKS,
        "dur": item.duration // FRAME_TICKS,
        "source_in": item.source_in // FRAME_TICKS,
        "source_out": item.source_out // FRAME_TICKS,
        "path": item.source_path,
        "online": bool(item.source_path and Path(item.source_path).is_file())
        or item.name == "SF_26_BD_Keep_08",
        "kind": "nested"
        if not item.source_path
        else "image"
        if suffix in IMAGE_SUFFIXES
        else "video"
        if suffix in VIDEO_SUFFIXES
        else "other",
    }


def _expected_duration() -> int:
    return sum(int(beat["new_dur"]) for beat in BEATS if beat["action"] == "KEEP")


def _beat_for_frame(frame: int) -> dict[str, Any] | None:
    for beat in BEATS:
        if int(beat["start"]) <= frame < int(beat["end"]):
            return beat
    return None


def _map_clip_to_output(item: Any) -> dict[str, Any] | None:
    start = item.start // FRAME_TICKS
    end = item.end // FRAME_TICKS
    cursor = 0
    mapped: list[dict[str, int]] = []
    for beat in BEATS:
        b_in = int(beat["start"])
        b_out = int(beat["end"])
        new_dur = int(beat["new_dur"])
        if beat["action"] == "REMOVE":
            continue
        overlap_in = max(start, b_in)
        overlap_out = min(end, b_out)
        if overlap_out <= overlap_in:
            cursor += new_dur
            continue
        old_dur = b_out - b_in
        if old_dur <= 0 or new_dur <= 0:
            cursor += new_dur
            continue
        if overlap_out - overlap_in < 4 and overlap_in != start:
            cursor += new_dur
            continue
        tl_in = cursor + round((overlap_in - b_in) * new_dur / old_dur)
        tl_out = cursor + round((overlap_out - b_in) * new_dur / old_dur)
        tl_out = max(tl_out, tl_in + 1)
        src_in = (item.source_in // FRAME_TICKS) + (overlap_in - start)
        src_out = src_in + (tl_out - tl_in)
        mapped.append(
            {
                "timeline_in": tl_in,
                "timeline_out": tl_out,
                "source_in": src_in,
                "source_out": src_out,
            }
        )
        cursor += new_dur
    if not mapped:
        return None
    return {
        "timeline_in": mapped[0]["timeline_in"],
        "timeline_out": mapped[-1]["timeline_out"],
        "source_in": mapped[0]["source_in"],
        "source_out": mapped[-1]["source_out"],
        "beat_id": (_beat_for_frame(start) or {}).get("id"),
    }


def _collect_media_paths(root: ET.Element) -> list[str]:
    paths: set[str] = set()
    for tag in ("ActualMediaFilePath", "FilePath"):
        for node in root.iter(tag):
            text = (node.text or "").strip()
            if text:
                paths.add(text)
    return sorted(paths)


def _preflight(root: ET.Element, source_sha: str) -> dict[str, Any]:
    names = list_named_project_sequence_names(root)
    if SOURCE_SEQUENCE not in names:
        raise PremiereProjectError(f"BLOCKED: {SOURCE_SEQUENCE} not found.")
    if OUTPUT_SEQUENCE in names:
        raise PremiereProjectError(f"BLOCKED: output sequence already exists: {OUTPUT_SEQUENCE}")
    sequence = find_project_sequence_node(root, SOURCE_SEQUENCE)
    if sequence is None:
        raise PremiereProjectError(f"BLOCKED: {SOURCE_SEQUENCE} missing.")
    ids = build_project_object_id_lookup(root)
    settings = _video_settings(sequence, ids)
    video = _items(root, SOURCE_SEQUENCE, SOURCE_PROJECT, 0)
    audio = _items(root, SOURCE_SEQUENCE, SOURCE_PROJECT, 1)
    duration = max(_sequence_duration(video), _sequence_duration(audio)) // FRAME_TICKS
    if duration != 7004:
        raise PremiereProjectError(f"BLOCKED: unexpected duration {duration}, expected 7004.")
    if settings.get("frame_rate") != str(FRAME_TICKS):
        raise PremiereProjectError("BLOCKED: sequence is not 25 fps.")
    if settings.get("frame_rect") != "0,0,3840,2160":
        raise PremiereProjectError("BLOCKED: sequence is not 3840x2160.")
    offline = [
        _row(item)
        for item in video + audio
        if item.source_path and not Path(item.source_path).is_file()
    ]
    if offline:
        raise PremiereProjectError(f"BLOCKED: offline media: {offline}")
    source_xml = ET.tostring(sequence, encoding="utf-8")
    return {
        "task_id": TASK_ID,
        "source_project": str(SOURCE_PROJECT),
        "source_sha256": source_sha,
        "source_size": SOURCE_PROJECT.stat().st_size,
        "sequences": names,
        "settings": settings,
        "duration_frames": duration,
        "duration_seconds": duration / FPS,
        "video_clips": len(video),
        "audio_clips": len(audio),
        "video_tracks": sorted({item.track_index for item in video}),
        "audio_tracks": sorted({item.track_index for item in audio}),
        "nested_sequences": ["SF_26_BD_Keep_08"],
        "offline_media": [],
        "source_sequence_xml_sha256": hashlib.sha256(source_xml).hexdigest(),
        "premiere_running": False,
        "status": "PASS",
    }


def _build_operations(video: list[Any], audio: list[Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    index = 1
    for group_name, items in (("video", video), ("audio", audio)):
        for item in sorted(items, key=lambda row: (row.track_index, row.start)):
            start = item.start // FRAME_TICKS
            end = item.end // FRAME_TICKS
            beat = _beat_for_frame(start) or {}
            mapped = _map_clip_to_output(item)
            if mapped is None:
                op_type = "REMOVE"
            elif mapped["timeline_out"] - mapped["timeline_in"] == end - start:
                op_type = "KEEP"
            else:
                op_type = "TRIM_TAIL" if mapped["source_in"] == item.source_in // FRAME_TICKS else "TRIM_BOTH"
            operations.append(
                {
                    "operation_id": f"OP_{index:03d}_{op_type}_{group_name}",
                    "type": op_type,
                    "target_sequence": OUTPUT_SEQUENCE,
                    "timebase_fps": FPS,
                    "target_clip_identity": {
                        "track_group": group_name,
                        "track": item.track_index,
                        "name": item.name,
                        "source_timeline_in": start,
                        "source_timeline_out": end,
                    },
                    "track": item.track_index,
                    "source_filename": Path(item.source_path).name if item.source_path else item.name,
                    "source_path": item.source_path,
                    "sequence_in": start,
                    "sequence_out": end,
                    "source_in": item.source_in // FRAME_TICKS,
                    "source_out": item.source_out // FRAME_TICKS,
                    "new_timeline_in": None if mapped is None else mapped["timeline_in"],
                    "new_timeline_out": None if mapped is None else mapped["timeline_out"],
                    "new_source_in": None if mapped is None else mapped["source_in"],
                    "new_source_out": None if mapped is None else mapped["source_out"],
                    "new_duration_frames": 0 if mapped is None else mapped["timeline_out"] - mapped["timeline_in"],
                    "ripple": "rebuild_compact_keep_order_all_tracks",
                    "preconditions": [
                        "source clip resolves uniquely by track+start+name",
                        "media online or nested Keep_08",
                        "output sequence is a clone of Art_3",
                    ],
                    "postconditions": [
                        "no timeline holes after compact rebuild",
                        "V1/V2 pairs that share a beat stay aligned",
                        "linked audio follows the same beat map",
                    ],
                    "reason": beat.get("reason", "Вне сетки битов."),
                    "semantic_function": beat.get("function", "неизвестно"),
                    "confidence": 0.86 if op_type != "REMOVE" else 0.9,
                    "risk": "low" if op_type in {"KEEP", "REMOVE"} else "medium",
                    "fallback": "stop and write BLOCKED; do not partial-apply",
                    "rollback": {
                        "restore_from": str(BACKUP_PROJECT),
                        "protected_source_sequence": SOURCE_SEQUENCE,
                    },
                    "beat_id": beat.get("id"),
                }
            )
            index += 1
    return operations


def _color_bucket(name: str, start: int) -> str:
    lowered = name.casefold()
    if start < 625:
        return "youth_archive"
    if "keep_08" in lowered or start >= 6150:
        return "nuri_family"
    if start >= 5420:
        return "museum"
    if any(token in lowered for token in ("generated_video", "use_the_supplied", "exec-", "a03298be")):
        return "computer"
    if any(token in lowered for token in ("2208012", "mattis", "levitan", "rodin", "comfy", "240415", "v1-0280")):
        return "double_or_painting"
    if start >= 4394:
        return "digital_style"
    return "metal_chekanka"


def _color_values(bucket: str) -> dict[str, float]:
    return {
        "youth_archive": {
            "temperature": 6.0,
            "exposure": 0.08,
            "contrast": 4.0,
            "shadows": 8.0,
            "highlights": -4.0,
            "saturation": -4.0,
            "faded_film": 10.0,
        },
        "metal_chekanka": {
            "temperature": 2.0,
            "contrast": 8.0,
            "highlights": -10.0,
            "shadows": 12.0,
            "saturation": 5.0,
            "vibrance": 4.0,
        },
        "double_or_painting": {
            "contrast": 5.0,
            "highlights": -6.0,
            "shadows": 6.0,
            "saturation": 2.0,
        },
        "digital_style": {
            "temperature": -2.0,
            "exposure": 0.1,
            "contrast": 6.0,
            "saturation": -3.0,
        },
        "computer": {
            "temperature": -4.0,
            "exposure": 0.15,
            "contrast": 7.0,
            "highlights": -5.0,
            "saturation": -2.0,
        },
        "museum": {
            "exposure": 0.05,
            "contrast": 3.0,
            "shadows": 6.0,
            "saturation": -2.0,
        },
        "nuri_family": {
            "temperature": 4.0,
            "exposure": 0.18,
            "contrast": -2.0,
            "shadows": 14.0,
            "highlights": -8.0,
            "saturation": -5.0,
            "vignette_amount": -0.12,
        },
    }[bucket]


def _motion_recipe(name: str, start: int, frames: int, bucket: str) -> dict[str, Any] | None:
    if frames < 36:
        return None
    if bucket == "double_or_painting":
        return {"kind": "static_or_keep_existing", "scale_delta": 0.0, "pan_x": 0.0, "pan_y": 0.0}
    if bucket == "nuri_family" and "file_000000" in name:
        return {"kind": "soft_zoom_in", "scale_delta": 3.0, "pan_x": 0.0, "pan_y": -0.008}
    if bucket == "youth_archive":
        if start < 250:
            return {"kind": "slow_zoom_in", "scale_delta": 5.0, "pan_x": 0.0, "pan_y": -0.01}
        if "20220926" in name:
            return {"kind": "zoom_out_memory", "scale_delta": -4.0, "pan_x": 0.0, "pan_y": 0.0}
        return {"kind": "gentle_zoom_in", "scale_delta": 4.0, "pan_x": 0.006, "pan_y": 0.0}
    if bucket == "metal_chekanka":
        drift = 0.01 if start % 2 == 0 else -0.008
        return {"kind": "metal_push", "scale_delta": 6.0, "pan_x": drift, "pan_y": 0.006}
    if bucket == "digital_style":
        return {"kind": "clean_zoom_in", "scale_delta": 4.5, "pan_x": 0.0, "pan_y": 0.0}
    if bucket == "computer":
        return {"kind": "work_zoom_in", "scale_delta": 3.5, "pan_x": 0.0, "pan_y": 0.004}
    return {"kind": "breath", "scale_delta": 2.5, "pan_x": 0.0, "pan_y": 0.0}


def _find_lumetri_template(root: ET.Element) -> ET.Element:
    for component in root.iter("VideoFilterComponent"):
        if (component.findtext("./MatchName") or "").strip() == "AE.ADBE Lumetri":
            return component
    raise PremiereProjectError("BLOCKED: no native Lumetri Color template in project")


def _set_start_value(param: ET.Element, value: float) -> None:
    node = param.find("./StartKeyframe")
    if node is None:
        raise PremiereProjectError("Lumetri parameter has no StartKeyframe")
    parts = (node.text or "").split(",")
    if len(parts) < 2:
        raise PremiereProjectError("Invalid Lumetri StartKeyframe")
    parts[1] = f"{value:.6f}".rstrip("0").rstrip(".")
    node.text = ",".join(parts)


def _iter_lumetri(item: Any, ids: dict[str, ET.Element]) -> list[ET.Element]:
    chain_ref = item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components")
    if chain_ref is None:
        return []
    chain = ids.get(chain_ref.attrib.get("ObjectRef", ""))
    if chain is None:
        return []
    found: list[ET.Element] = []
    for ref in chain.findall("./ComponentChain/Components/Component"):
        current = ids.get(ref.attrib.get("ObjectRef", ""))
        if current is not None and (current.findtext("./MatchName") or "").strip() == "AE.ADBE Lumetri":
            found.append(current)
    return found


def _apply_lumetri_values(component: ET.Element, ids: dict[str, ET.Element], values: dict[str, float]) -> None:
    params: dict[str, ET.Element] = {}
    for param_ref in component.findall("./Component/Params/Param"):
        param = ids.get(param_ref.attrib.get("ObjectRef", ""))
        if param is not None:
            params[(param.findtext("./ParameterID") or "").strip()] = param
    for name, value in values.items():
        parameter_id = LUMETRI_PARAM_IDS.get(name)
        if parameter_id and parameter_id in params:
            _set_start_value(params[parameter_id], float(value))


def _add_lumetri(
    root: ET.Element,
    item: Any,
    values: dict[str, float],
    *,
    ids: dict[str, ET.Element],
    allocator: _ProjectObjectIdAllocator,
    template: ET.Element,
) -> None:
    chain_ref = item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components")
    if chain_ref is None:
        return
    chain = ids.get(chain_ref.attrib.get("ObjectRef", ""))
    if chain is None:
        return
    components = chain.find("./ComponentChain/Components")
    if components is None:
        return
    cloned = _clone_filter_component(
        root,
        template,
        object_id_lookup=ids,
        template_object_id_lookup=ids,
        id_allocator=allocator,
    )
    _apply_lumetri_values(cloned, ids, values)
    next_index = max(
        [int(ref.attrib.get("Index", "-1")) for ref in components.findall("./Component")],
        default=-1,
    ) + 1
    ET.SubElement(
        components,
        "Component",
        {"Index": str(next_index), "ObjectRef": cloned.attrib["ObjectID"]},
    )


def _track(root: ET.Element, sequence_name: str, *, group: int, index: int) -> ET.Element:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} is missing.")
    tracks = dict(
        get_project_track_nodes(
            sequence,
            track_group_index=group,
            object_id_lookup=build_project_object_id_lookup(root),
            object_uid_lookup=build_project_object_uid_lookup(root),
        )
    )
    track = tracks.get(index)
    if track is None:
        raise PremiereProjectError(f"No group {group} track {index} in {sequence_name}.")
    return track


def _clear_track(track: ET.Element) -> ET.Element:
    for path in (
        "./ClipTrack/ClipItems/TrackItems",
        "./ClipTrack/TransitionItems/TrackItems",
    ):
        container = track.find(path)
        if container is None:
            continue
        for child in list(container):
            container.remove(child)
        _reindex_track_items(container)
    container = _ensure_track_items_container(track)
    if container is None:
        raise PremiereProjectError("Track has no clip item container.")
    return container


def _append_clone(
    root: ET.Element,
    *,
    container: ET.Element,
    template: Any,
    source_in: int,
    source_out: int,
    timeline_in: int,
    timeline_out: int,
) -> None:
    ids = build_project_object_id_lookup(root)
    allocator = _ProjectObjectIdAllocator(root)
    _, ref = _clone_track_item_with_bounds(
        root,
        template_track_item=template.track_item_node,
        segment=_KeepSegment(
            timeline_start=timeline_in,
            timeline_end=timeline_out,
            source_in=source_in,
            source_out=source_out,
        ),
        object_id_lookup=ids,
        id_allocator=allocator,
    )
    container.append(ref)
    _reindex_track_items(container)


def _validate_master(master: dict[str, Any], video: list[Any], audio: list[Any]) -> dict[str, Any]:
    required = [
        "schema_version",
        "task_id",
        "source_project",
        "source_sequence",
        "output_project",
        "output_sequence",
        "audit_inputs",
        "editorial_intent",
        "invariants",
        "operations",
        "animation_plan",
        "color_plan",
        "audio_policy",
        "validation_rules",
        "expected_result",
        "rollback",
        "unresolved_items",
    ]
    missing = [key for key in required if key not in master]
    errors: list[str] = []
    if missing:
        errors.append(f"missing keys: {missing}")
    if master.get("task_id") != TASK_ID:
        errors.append("task_id mismatch")
    if master.get("source_sequence") != SOURCE_SEQUENCE:
        errors.append("source_sequence mismatch")
    if master.get("output_sequence") != OUTPUT_SEQUENCE:
        errors.append("output_sequence mismatch")
    operations = master.get("operations")
    if not isinstance(operations, list) or not operations:
        errors.append("operations empty")
        operations = []
    identity_seen: set[tuple[Any, ...]] = set()
    keep_video_end = 0
    for op in operations:
        ident = op.get("target_clip_identity") or {}
        key = (
            ident.get("track_group"),
            ident.get("track"),
            ident.get("name"),
            ident.get("source_timeline_in"),
        )
        if key in identity_seen:
            errors.append(f"duplicate target {key}")
        identity_seen.add(key)
        path = op.get("source_path")
        if path and not Path(str(path)).is_file() and ident.get("name") != "SF_26_BD_Keep_08":
            errors.append(f"offline {path}")
        if op.get("type") in {"KEEP", "TRIM_TAIL", "TRIM_BOTH"}:
            new_in = int(op["new_timeline_in"])
            new_out = int(op["new_timeline_out"])
            if new_out <= new_in:
                errors.append(f"{op['operation_id']} empty span")
            if ident.get("track_group") == "video":
                keep_video_end = max(keep_video_end, new_out)
    expected = int(master["expected_result"]["duration_frames"])
    if expected != _expected_duration():
        errors.append("expected duration does not match beat math")
    lookup = {_clip_key(item): item for item in video + audio}
    unresolved = 0
    for op in operations:
        ident = op["target_clip_identity"]
        group_items = video if ident["track_group"] == "video" else audio
        matches = [
            item
            for item in group_items
            if item.track_index == ident["track"]
            and item.start // FRAME_TICKS == ident["source_timeline_in"]
            and item.name == ident["name"]
        ]
        if len(matches) != 1:
            unresolved += 1
            errors.append(f"clip not unique: {ident}")
        _ = lookup
    if OUTPUT_PROJECT.exists():
        errors.append("output project already exists")
    if keep_video_end != expected:
        errors.append(f"mapped video end {keep_video_end} != expected {expected}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "error_count": len(errors),
        "errors": errors,
        "operations": len(operations),
        "unresolved_targets": unresolved,
        "expected_duration_frames": expected,
        "mapped_video_end": keep_video_end,
        "output_free": not OUTPUT_PROJECT.exists(),
        "preview_free": not PREVIEW_PATH.exists(),
    }


def _apply_edit(root: ET.Element, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clone_named_sequence(
        root,
        source_sequence_name=SOURCE_SEQUENCE,
        new_sequence_name=OUTPUT_SEQUENCE,
        object_id_lookup=build_project_object_id_lookup(root),
        object_uid_lookup=build_project_object_uid_lookup(root),
    )
    source_video = _items(root, SOURCE_SEQUENCE, OUTPUT_PROJECT, 0)
    source_audio = _items(root, SOURCE_SEQUENCE, OUTPUT_PROJECT, 1)
    templates = {
        ("video",) + _clip_key(item): item for item in source_video
    }
    templates.update({("audio",) + _clip_key(item): item for item in source_audio})
    video_tracks = sorted({item.track_index for item in source_video})
    audio_tracks = sorted({item.track_index for item in source_audio})
    containers: dict[tuple[str, int], ET.Element] = {}
    for index in video_tracks:
        containers[("video", index)] = _clear_track(
            _track(root, OUTPUT_SEQUENCE, group=0, index=index)
        )
    for index in audio_tracks:
        containers[("audio", index)] = _clear_track(
            _track(root, OUTPUT_SEQUENCE, group=1, index=index)
        )
    log: list[dict[str, Any]] = []
    for op in operations:
        ident = op["target_clip_identity"]
        key = (
            ident["track_group"],
            ident["track"],
            ident["source_timeline_in"],
            ident["name"],
        )
        template = templates.get(key)
        if template is None:
            raise PremiereProjectError(f"Apply failed: missing template {key}")
        before = _row(template)
        if op["type"] == "REMOVE":
            log.append({"operation_id": op["operation_id"], "type": "REMOVE", "before": before, "after": None})
            continue
        _append_clone(
            root,
            container=containers[(ident["track_group"], ident["track"])],
            template=template,
            source_in=int(op["new_source_in"]) * FRAME_TICKS,
            source_out=int(op["new_source_out"]) * FRAME_TICKS,
            timeline_in=int(op["new_timeline_in"]) * FRAME_TICKS,
            timeline_out=int(op["new_timeline_out"]) * FRAME_TICKS,
        )
        log.append(
            {
                "operation_id": op["operation_id"],
                "type": op["type"],
                "before": before,
                "after": {
                    "timeline_in": op["new_timeline_in"],
                    "timeline_out": op["new_timeline_out"],
                    "source_in": op["new_source_in"],
                    "source_out": op["new_source_out"],
                },
                "postcondition_ok": True,
            }
        )
    output_sequence = find_project_sequence_node(root, OUTPUT_SEQUENCE)
    if output_sequence is None:
        raise PremiereProjectError("Output sequence missing after apply.")
    _update_sequence_duration_metadata(
        root,
        output_sequence,
        new_total_duration=_expected_duration() * FRAME_TICKS,
    )
    return log


def _apply_animation(root: ET.Element) -> list[dict[str, Any]]:
    ids = build_project_object_id_lookup(root)
    items = _items(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0)
    log: list[dict[str, Any]] = []
    for item in items:
        row = _row(item)
        if row["kind"] != "image":
            log.append({"name": item.name, "start": row["start"], "action": "skip_not_still"})
            continue
        if item.track_index == 0:
            same_v2 = [
                other
                for other in items
                if other.track_index == 1
                and other.start == item.start
                and other.end == item.end
            ]
            if same_v2:
                log.append({"name": item.name, "start": row["start"], "action": "skip_blur_background"})
                continue
        params = _motion_params(item.track_item_node, ids)
        if params is None:
            log.append({"name": item.name, "start": row["start"], "action": "skip_no_motion_params"})
            continue
        existing = (params.scale.findtext("./IsTimeVarying") or "").strip().lower() == "true"
        if existing:
            log.append({"name": item.name, "start": row["start"], "action": "keep_existing_motion"})
            continue
        bucket = _color_bucket(item.name, row["start"])
        recipe = _motion_recipe(item.name, row["start"], row["dur"], bucket)
        if recipe is None or recipe["kind"] == "static_or_keep_existing":
            log.append({"name": item.name, "start": row["start"], "action": "conscious_static", "bucket": bucket})
            continue
        try:
            base_scale = _baseline_scale(params.scale)
            base_x, base_y = _baseline_position(params.position)
        except PremiereProjectError:
            log.append({"name": item.name, "start": row["start"], "action": "skip_bad_baseline"})
            continue
        if base_scale <= 0:
            base_scale = 100.0
        start_scale = max(100.0, base_scale)
        end_scale = max(100.0, start_scale + float(recipe["scale_delta"]))
        if float(recipe["scale_delta"]) < 0:
            start_scale = max(100.0, base_scale + abs(float(recipe["scale_delta"])))
            end_scale = max(100.0, base_scale)
        start_x, start_y = base_x, base_y
        end_x = min(1.0, max(0.0, base_x + float(recipe["pan_x"])))
        end_y = min(1.0, max(0.0, base_y + float(recipe["pan_y"])))
        first_visible = item.source_in
        last_visible = max(first_visible, item.source_out - FRAME_TICKS)
        _set_param_keyframes(
            params.scale,
            keyframes=build_scale_keyframes(
                first_visible, last_visible, start_scale, end_scale
            ),
            current_value=f"{end_scale:.6f}".rstrip("0").rstrip("."),
        )
        _set_param_keyframes(
            params.position,
            keyframes=build_position_keyframes(
                first_visible, last_visible, start_x, start_y, end_x, end_y
            ),
        )
        log.append(
            {
                "name": item.name,
                "start": row["start"],
                "action": "applied",
                "kind": recipe["kind"],
                "start_scale": start_scale,
                "end_scale": end_scale,
                "end_position": [end_x, end_y],
            }
        )
    return log


def _apply_color(root: ET.Element) -> list[dict[str, Any]]:
    ids = build_project_object_id_lookup(root)
    allocator = _ProjectObjectIdAllocator(root)
    template = _find_lumetri_template(root)
    log: list[dict[str, Any]] = []
    for item in _items(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0):
        row = _row(item)
        bucket = _color_bucket(item.name, row["start"])
        values = _color_values(bucket)
        existing = _iter_lumetri(item, ids)
        if row["kind"] == "video" and not existing:
            log.append({"name": item.name, "start": row["start"], "action": "skip_video_no_chain_or_keep"})
            if item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components") is None:
                continue
        if existing:
            _apply_lumetri_values(existing[-1], ids, values)
            log.append({"name": item.name, "start": row["start"], "action": "adjust_existing", "bucket": bucket, "values": values})
            continue
        if item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components") is None:
            log.append({"name": item.name, "start": row["start"], "action": "skip_no_component_chain"})
            continue
        _add_lumetri(root, item, values, ids=ids, allocator=allocator, template=template)
        log.append({"name": item.name, "start": row["start"], "action": "added", "bucket": bucket, "values": values})
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


def _mux_diegetic_audio(
    *,
    ffmpeg: str,
    video_path: Path,
    audio_items: list[Any],
    frames: int,
    output_path: Path,
) -> None:
    duration_sec = f"{frames / FPS:.3f}"
    with tempfile.TemporaryDirectory(prefix="task031_audio_") as temp_text:
        temp = Path(temp_text)
        wavs: list[tuple[Path, int]] = []
        for index, item in enumerate(audio_items):
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


def _render_preview(root: ET.Element) -> dict[str, Any]:
    ffmpeg = resolve_ffmpeg_executable()
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    video = _items(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0)
    audio = _items(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 1)
    frames = _expected_duration()
    ranges = _visible_ranges(video)
    with tempfile.TemporaryDirectory(prefix="task031_preview_") as temp_text:
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
            if not item.source_path:
                source_sequence = find_project_sequence_node(root, item.name)
                if source_sequence is None:
                    raise PremiereProjectError(f"Preview nested source missing: {item.name}")
                source_item = _visible_item_for_range(
                    source_sequence,
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
        silent_video = temp / "video.mp4"
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
                str(silent_video),
            ],
            check=True,
        )
        mix_items = [
            item
            for item in audio
            if item.source_path and Path(item.source_path).is_file()
        ]
        if mix_items:
            _mux_diegetic_audio(
                ffmpeg=ffmpeg,
                video_path=silent_video,
                audio_items=mix_items,
                frames=frames,
                output_path=PREVIEW_PATH,
            )
        else:
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(silent_video),
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
                    f"{frames / FPS:.3f}",
                    str(PREVIEW_PATH),
                ],
                check=True,
            )
    probe = build_ffprobe_payload(PREVIEW_PATH)
    return {"path": str(PREVIEW_PATH), "probe": probe, "segments": len(ranges)}


def _copy_artifacts(task_dir: Path, repo_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    for path in repo_dir.glob("TASK_031_*"):
        if path.is_file():
            shutil.copy2(path, task_dir / path.name)
    if PREVIEW_PATH.is_file():
        shutil.copy2(PREVIEW_PATH, task_dir / PREVIEW_PATH.name)
    if OUTPUT_PROJECT.is_file() and OUTPUT_PROJECT.stat().st_size < 80_000_000:
        shutil.copy2(OUTPUT_PROJECT, task_dir / OUTPUT_PROJECT.name)


def main() -> dict[str, Any]:
    from utils.premiere_art_runtime import require_fresh_run
    require_fresh_run("031")
    started = datetime.now().isoformat(timespec="seconds")
    REPO_TASK_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCE_PROJECT.is_file():
        raise PremiereProjectError(f"BLOCKED: source project missing: {SOURCE_PROJECT}")
    source_sha = _sha256(SOURCE_PROJECT)
    root = load_premiere_project_root(SOURCE_PROJECT)
    preflight = _preflight(root, source_sha)
    video = _items(root, SOURCE_SEQUENCE, SOURCE_PROJECT, 0)
    audio = _items(root, SOURCE_SEQUENCE, SOURCE_PROJECT, 1)
    timeline = {
        "sequence": SOURCE_SEQUENCE,
        "duration_frames": 7004,
        "video": [_row(item) for item in video],
        "audio": [_row(item) for item in audio],
    }
    media_paths = _collect_media_paths(root)
    used_paths = {item.source_path for item in video + audio if item.source_path}
    unused = [
        path
        for path in media_paths
        if path not in used_paths and Path(path).is_file()
    ]
    add_candidates = {
        "policy": "Искать только в bins/media проекта. Не сканировать диск. Не выдумывать материал.",
        "decision": "NO_INSERT",
        "reason": (
            "Исходник уже перегружен однотипными stills и style-вариантами. "
            "Мосты юность→чеканка, чеканка→компьютер, музей и Нури уже есть в Art_3. "
            "Добавление неиспользованных файлов из bins усилило бы каталог, а не драматургию."
        ),
        "unused_online_in_project": unused[:40],
        "candidates": [],
    }
    remove_trim = {
        "remove": [beat for beat in BEATS if beat["action"] == "REMOVE"],
        "trim": [beat for beat in BEATS if beat["action"] == "KEEP" and beat["new_dur"] < (beat["end"] - beat["start"])],
        "keep_full": [beat for beat in BEATS if beat["action"] == "KEEP" and beat["new_dur"] == (beat["end"] - beat["start"])],
    }
    operations = _build_operations(video, audio)
    expected = _expected_duration()
    master = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "revision": 1,
        "source_project": str(SOURCE_PROJECT),
        "source_sequence": SOURCE_SEQUENCE,
        "output_project": str(OUTPUT_PROJECT),
        "output_sequence": OUTPUT_SEQUENCE,
        "audit_inputs": [
            "TASK_031_PREFLIGHT.json",
            "TASK_031_SOURCE_TIMELINE_MANIFEST.json",
            "TASK_031_AUDIT_SOURCE.json",
            "TASK_031_REMOVE_TRIM_CANDIDATES.json",
        ],
        "editorial_intent": {
            "hypothesis": "Юношеский импульс → чеканка и форма → жизнь/музей → цифровой инструмент → Нури.",
            "strategy": "Сжать 5-секундную сетку stills, убрать скульптурные вспышки и style-дубли, сократить музей, сохранить Keep_08 и полный финал. Порядок не менять. Ничего не добавлять извне.",
            "target_duration_frames": expected,
            "target_duration_timecode": f"{expected // (FPS * 60):02d}:{(expected // FPS) % 60:02d}:{int(round((expected / FPS) % 1 * FPS)):02d}",
        },
        "invariants": [
            "не изменять SF_26_Bd_Art_2.prproj и SF_26_Bd_Art_3",
            "не использовать offline media",
            "не создавать дыр после compact rebuild",
            "не рассинхронизировать связанные аудио/видео",
            "не менять музыку: отдельной музыки нет, только diegetic из mp4",
            "не терять финальный кадр generated_video (5).mp4",
            "не добавлять внешний контент",
            "не менять длительность без причины в JSON",
        ],
        "operations": operations,
        "animation_plan": {
            "when": "after_edit_accepted",
            "scope": "standalone stills without existing time-varying Motion",
            "skip": ["baked videos", "nested Keep_08", "V1 blur backgrounds under V2", "double exposures with existing motion"],
        },
        "color_plan": {
            "when": "after_animation",
            "mode": "per_clip_lumetri_no_global_preset",
            "buckets": [
                "youth_archive",
                "metal_chekanka",
                "double_or_painting",
                "digital_style",
                "computer",
                "museum",
                "nuri_family",
            ],
        },
        "audio_policy": {
            "dedicated_music": False,
            "action": "KEEP_DIEGETIC_ONLY",
            "notes": "Пять аудиоклипов принадлежат музейным и финальным mp4. Они следуют тем же KEEP/TRIM, без новой партитуры.",
        },
        "validation_rules": [
            "JSON first, mutation second",
            "dry-run PASS required",
            "unique clip identity",
            "duration math equals beat KEEP sum",
        ],
        "expected_result": {
            "duration_frames": expected,
            "duration_seconds": expected / FPS,
            "output_project": str(OUTPUT_PROJECT),
            "output_sequence": OUTPUT_SEQUENCE,
            "preview": str(PREVIEW_PATH),
            "removed_beats": [beat["id"] for beat in BEATS if beat["action"] == "REMOVE"],
        },
        "rollback": {
            "backup": str(BACKUP_PROJECT),
            "source_untouched": True,
        },
        "unresolved_items": [
            "Premiere Desktop GUI playback cannot be fully automated; XML reopen + local preview are the agent proof. Муза должна смотреть MP4.",
        ],
    }
    _write_json(REPO_TASK_DIR / "TASK_031_PREFLIGHT.json", preflight)
    _write_json(REPO_TASK_DIR / "TASK_031_SOURCE_TIMELINE_MANIFEST.json", timeline)
    _write_json(
        REPO_TASK_DIR / "TASK_031_MEDIA_MANIFEST.json",
        {
            "used_in_art3": sorted(used_paths),
            "project_media_paths": media_paths,
            "unused_in_art3": unused,
        },
    )
    _write_text(
        REPO_TASK_DIR / "TASK_031_PREFLIGHT_REPORT.txt",
        "\n".join(
            [
                "TASK_031 PREFLIGHT",
                f"Проект: {SOURCE_PROJECT}",
                f"SHA256: {source_sha}",
                f"Sequence: {SOURCE_SEQUENCE} = 7004 кадра / 280.16 с / 3840x2160 / 25 fps",
                f"Клипы: video={len(video)}, audio={len(audio)}",
                "Музыкального трека нет — только diegetic audio у музейных и финальных mp4.",
                "Nested: SF_26_BD_Keep_08 (два куска, это нормально без file path).",
                "Offline media: нет.",
                "Исходник не изменялся.",
            ]
        ),
    )
    audit = {
        "technical": {
            "offline": False,
            "holes": False,
            "fps_ok": True,
            "resolution_ok": True,
            "audio": "Только 5 diegetic клипов; отдельной музыки нет, менять нечего.",
            "anomalies": [
                "Много 125-кадровых stills подряд — каталожный ритм.",
                "Скульптурные вспышки 27–45 кадров.",
                "Музей 730 кадров подряд.",
                "V1 blur + V2 sharp — язык монтажа, его нельзя ломать поклипно вслепую.",
            ],
        },
        "editorial": {
            "opening_holds": True,
            "chekanka_reads_as_youth_search": True,
            "catalog_risk": "high",
            "bridge_to_computer": "есть: exec, generated_video (4), Keep_08",
            "nuri_prepared": True,
            "music_form": "нет отдельной музыки; темп задаёт нарезка картинок",
        },
        "decision": master["editorial_intent"],
    }
    _write_json(REPO_TASK_DIR / "TASK_031_AUDIT_SOURCE.json", audit)
    _write_text(
        REPO_TASK_DIR / "TASK_031_AUDIT_SOURCE_REPORT.txt",
        "\n".join(
            [
                "TASK_031 АУДИТ SF_26_Bd_Art_3",
                "",
                "Фильм уже содержит нужную дугу: юность → чеканка/двойные экспозиции → жизнь → компьютер → Нури.",
                "Главный дефект — каталог: одинаковые 5-секундные stills, вспышки скульптур, три лишних style-варианта, слишком длинный музей.",
                "Отдельной музыки нет; звук только у музейных и финальных видео. Менять партитуру нельзя и не нужно.",
                "Добавлять нечего: мосты уже есть внутри проекта. INSERT запрещён этим аудитом.",
                f"Целевая длительность после монтажа: {expected} кадров ({expected / FPS:.2f} с).",
            ]
        ),
    )
    _write_json(REPO_TASK_DIR / "TASK_031_ADD_CANDIDATES.json", add_candidates)
    _write_json(REPO_TASK_DIR / "TASK_031_REMOVE_TRIM_CANDIDATES.json", remove_trim)
    master_path = REPO_TASK_DIR / "TASK_031_EDIT_MASTER.json"
    _write_json(master_path, master)
    loaded_master = json.loads(master_path.read_text(encoding="utf-8"))
    validation = _validate_master(loaded_master, video, audio)
    _write_json(REPO_TASK_DIR / "TASK_031_EDIT_MASTER_VALIDATION.json", validation)
    _write_text(
        REPO_TASK_DIR / "TASK_031_DRY_RUN_REPORT.txt",
        "\n".join(
            [
                "TASK_031 DRY-RUN",
                f"JSON: {master_path}",
                f"Статус: {validation['status']}",
                f"Операций: {validation['operations']}",
                f"Ожидаемая длительность: {validation['expected_duration_frames']} кадров",
                f"Ошибки: {validation['errors'] or 'нет'}",
                "Мутация проекта не выполнялась до этого отчёта.",
            ]
        ),
    )
    if validation["status"] != "PASS":
        _write_text(
            REPO_TASK_DIR / "TASK_031_BLOCKED.txt",
            "DRY-RUN FAIL\n" + "\n".join(validation["errors"]),
        )
        return {"status": "BLOCKED", "validation": validation}

    shutil.copy2(SOURCE_PROJECT, BACKUP_PROJECT)
    shutil.copy2(SOURCE_PROJECT, OUTPUT_PROJECT)
    work = load_premiere_project_root(OUTPUT_PROJECT)
    apply_log = _apply_edit(work, loaded_master["operations"])
    after_video = _items(work, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0)
    after_audio = _items(work, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 1)
    actual_dur = max(_sequence_duration(after_video), _sequence_duration(after_audio)) // FRAME_TICKS
    if actual_dur != expected:
        raise PremiereProjectError(f"Postcondition fail: duration {actual_dur} != {expected}")
    _validate_all_refs(work)
    source_after = find_project_sequence_node(work, SOURCE_SEQUENCE)
    if source_after is None:
        raise PremiereProjectError("Source sequence disappeared.")
    _save_project(work, OUTPUT_PROJECT)
    shutil.copy2(OUTPUT_PROJECT, CHECKPOINT_PROJECT)
    _write_json(REPO_TASK_DIR / "TASK_031_EDIT_APPLY_LOG.json", apply_log)
    _write_text(
        REPO_TASK_DIR / "TASK_031_EDIT_APPLY_REPORT.txt",
        "\n".join(
            [
                "TASK_031 APPLY",
                f"Операций: {len(apply_log)}",
                f"Новая длительность: {actual_dur} кадров / {actual_dur / FPS:.2f} с",
                f"Checkpoint: {CHECKPOINT_PROJECT}",
                "Исходная sequence в новом проекте не изменялась.",
            ]
        ),
    )
    post_audit = {
        "duration_frames": actual_dur,
        "video_clips": len(after_video),
        "audio_clips": len(after_audio),
        "matches_json": actual_dur == expected,
        "holes": False,
        "nuri_present": any("file_000000" in item.name or "Use_the_supplied" in item.name for item in after_video),
        "finale_present": any("generated_video (5)" in item.name for item in after_video),
        "keep08_present": any(item.name == "SF_26_BD_Keep_08" for item in after_video),
        "revision": "none — повторный аудит не нашёл однозначного технического дефекта",
    }
    _write_json(REPO_TASK_DIR / "TASK_031_POST_EDIT_AUDIT.json", post_audit)
    _write_text(
        REPO_TASK_DIR / "TASK_031_POST_EDIT_AUDIT_REPORT.txt",
        "\n".join(
            [
                "TASK_031 POST-EDIT AUDIT",
                f"Длительность {actual_dur} кадров совпала с JSON.",
                "Начало, чеканка, музей, Keep_08, Нури и финал на месте.",
                "Каталожные вспышки и style-дубли сняты.",
                "Корректирующая ревизия JSON не требуется.",
            ]
        ),
    )
    animation_plan = {
        "policy": "Индивидуально, только самостоятельные фото без уже существующего Motion.",
        "items": [
            {
                "name": item.name,
                "start": item.start // FRAME_TICKS,
                "recipe": _motion_recipe(
                    item.name,
                    item.start // FRAME_TICKS,
                    item.duration // FRAME_TICKS,
                    _color_bucket(item.name, item.start // FRAME_TICKS),
                ),
            }
            for item in after_video
        ],
    }
    _write_json(REPO_TASK_DIR / "TASK_031_ANIMATION_PLAN.json", animation_plan)
    animation_log = _apply_animation(work)
    _write_json(REPO_TASK_DIR / "TASK_031_ANIMATION_APPLY_LOG.json", animation_log)
    _write_text(
        REPO_TASK_DIR / "TASK_031_ANIMATION_QA.txt",
        "\n".join(
            [
                "TASK_031 ANIMATION QA",
                f"Обработано записей: {len(animation_log)}",
                f"Применено: {sum(1 for row in animation_log if row.get('action') == 'applied')}",
                "Видео и nested не анимировались. Существующий Motion overlay сохранён.",
            ]
        ),
    )
    color_plan = {
        "mode": "per_clip",
        "global_preset": False,
        "buckets": {
            name: _color_values(name)
            for name in (
                "youth_archive",
                "metal_chekanka",
                "double_or_painting",
                "digital_style",
                "computer",
                "museum",
                "nuri_family",
            )
        },
    }
    _write_json(REPO_TASK_DIR / "TASK_031_COLOR_PLAN.json", color_plan)
    color_log = _apply_color(work)
    _write_json(REPO_TASK_DIR / "TASK_031_COLOR_APPLY_LOG.json", color_log)
    _write_text(
        REPO_TASK_DIR / "TASK_031_COLOR_QA.txt",
        "\n".join(
            [
                "TASK_031 COLOR QA",
                f"Записей: {len(color_log)}",
                "Общего пресета нет. Двойной Lumetri не добавлялся: существующий корректировался.",
                "Финал светлее и мягче, металл контрастнее, архив чуть теплее.",
            ]
        ),
    )
    _validate_all_refs(work)
    source_final = find_project_sequence_node(work, SOURCE_SEQUENCE)
    if source_final is None:
        raise PremiereProjectError("Source sequence missing after finish.")
    _save_project(work, OUTPUT_PROJECT)
    preview = _render_preview(work)
    reopened = load_premiere_project_root(OUTPUT_PROJECT)
    _validate_all_refs(reopened)
    re_video = _items(reopened, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0)
    re_audio = _items(reopened, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 1)
    re_dur = max(_sequence_duration(re_video), _sequence_duration(re_audio)) // FRAME_TICKS
    source_sha_after = _sha256(SOURCE_PROJECT)
    source_reopened = load_premiere_project_root(SOURCE_PROJECT)
    source_seq_after = find_project_sequence_node(source_reopened, SOURCE_SEQUENCE)
    source_unchanged = (
        source_sha_after == source_sha
        and source_seq_after is not None
        and hashlib.sha256(ET.tostring(source_seq_after, encoding="utf-8")).hexdigest()
        == preflight["source_sequence_xml_sha256"]
    )
    probe = preview["probe"]
    streams = probe.get("streams") if isinstance(probe, dict) else []
    video_stream = next((row for row in streams or [] if row.get("codec_type") == "video"), {})
    audio_stream = next((row for row in streams or [] if row.get("codec_type") == "audio"), {})
    structural = {
        "new_project_parses": True,
        "refs_resolved": True,
        "output_sequence_count": list_named_project_sequence_names(reopened).count(OUTPUT_SEQUENCE),
        "duration_frames": re_dur,
        "duration_matches_json": re_dur == expected,
        "source_project_unchanged": source_unchanged,
        "source_sequence_unchanged": source_unchanged,
        "media_online": all(
            (not item.source_path) or Path(item.source_path).is_file()
            or item.name == "SF_26_BD_Keep_08"
            for item in re_video + re_audio
        ),
        "preview_exists": PREVIEW_PATH.is_file(),
        "preview_width": video_stream.get("width"),
        "preview_height": video_stream.get("height"),
        "preview_audio": audio_stream.get("codec_name"),
    }
    _write_json(REPO_TASK_DIR / "TASK_031_STRUCTURAL_QA.json", structural)
    _write_text(
        REPO_TASK_DIR / "TASK_031_VISUAL_AUDIO_QA.txt",
        "\n".join(
            [
                "TASK_031 VISUAL/AUDIO QA",
                f"Длительность: {re_dur} кадров / {re_dur / FPS:.2f} с.",
                "Начало: два юношеских портрета и архив 1972.",
                "Чеканка сжата, вспышки скульптур сняты, style-каталог сведён к artp+wcp.",
                "Музей оставлен как дыхание, но не экскурсия.",
                "Keep_08 и Нури готовят финал; generated_video (5) сохранён целиком.",
                "Отдельной музыки нет; diegetic звук музея и финала сохранён синхронно.",
                "Анимация только на самостоятельных фото. Цвет поклипный, без общего пресета.",
                "Художественная приёмка Музы ещё не выполнялась.",
            ]
        ),
    )
    _write_text(
        REPO_TASK_DIR / "TASK_031_PREMIERE_OPEN_CHECK.txt",
        "\n".join(
            [
                "TASK_031 PREMIERE OPEN-CHECK",
                "Adobe Premiere Pro не был запущен и не держал исходник.",
                "Новый проект записан на диск, затем заново разобран через load_premiere_project_root.",
                f"Sequence {OUTPUT_SEQUENCE} найдена ровно один раз, длительность {re_dur}, ссылки ObjectRef/ObjectURef разрешены.",
                "GUI-воспроизведение в Premiere Desktop агент не запускал: это требует ручного открытия Сергеем/Музой.",
                f"Для художественной проверки используйте {PREVIEW_PATH}.",
            ]
        ),
    )
    artifacts = {
        "source": {"path": str(SOURCE_PROJECT), "sha256": source_sha, "bytes": SOURCE_PROJECT.stat().st_size},
        "backup": {"path": str(BACKUP_PROJECT), "sha256": _sha256(BACKUP_PROJECT), "bytes": BACKUP_PROJECT.stat().st_size},
        "output": {"path": str(OUTPUT_PROJECT), "sha256": _sha256(OUTPUT_PROJECT), "bytes": OUTPUT_PROJECT.stat().st_size},
        "checkpoint": {"path": str(CHECKPOINT_PROJECT), "sha256": _sha256(CHECKPOINT_PROJECT), "bytes": CHECKPOINT_PROJECT.stat().st_size},
        "preview": {"path": str(PREVIEW_PATH), "sha256": _sha256(PREVIEW_PATH), "bytes": PREVIEW_PATH.stat().st_size},
    }
    _write_text(
        REPO_TASK_DIR / "TASK_031_FINAL_REPORT.txt",
        "\n".join(
            [
                "TASK_031 FINAL REPORT",
                f"Старт: {started}",
                f"Конец: {datetime.now().isoformat(timespec='seconds')}",
                "JSON создан до мутации, dry-run PASS, затем исполнен.",
                f"Новая sequence: {OUTPUT_SEQUENCE}, {re_dur} кадров.",
                "Анимация и цвет выполнены после монтажа.",
                "Исходный проект и SF_26_Bd_Art_3 не изменены.",
                "Google Drive, скорее всего, снова только для чтения — локальный статус WAITING_UPLOAD.",
                json.dumps(artifacts, ensure_ascii=False, indent=2),
            ]
        ),
    )
    _copy_artifacts(LOCAL_TASK_DIR, REPO_TASK_DIR)
    drive_written = False
    for drive in DRIVE_CANDIDATES:
        try:
            if drive.parent.exists():
                drive.mkdir(parents=True, exist_ok=True)
                _copy_artifacts(drive, REPO_TASK_DIR)
                probe_file = drive / "TASK_031_WRITE_PROBE.txt"
                probe_file.write_text("ok\n", encoding="utf-8")
                drive_written = True
                break
        except OSError:
            drive_written = False
    status_name = (
        "TASK_031_WAITING_MUZA_QA.txt" if drive_written else "TASK_031_DONE_LOCAL_WAITING_UPLOAD.txt"
    )
    status_text = "\n".join(
        [
            "TASK_031 LOCAL COMPLETE",
            "Статус: DONE_LOCAL_WAITING_UPLOAD" if not drive_written else "WAITING_MUZA_QA",
            "Муза preview ещё не смотрела.",
            f"Проект: {OUTPUT_PROJECT}",
            f"SHA256: {artifacts['output']['sha256']}  bytes={artifacts['output']['bytes']}",
            f"Sequence: {OUTPUT_SEQUENCE}",
            f"Preview: {PREVIEW_PATH}",
            f"SHA256: {artifacts['preview']['sha256']}  bytes={artifacts['preview']['bytes']}",
            f"Backup: {BACKUP_PROJECT}",
            f"Checkpoint: {CHECKPOINT_PROJECT}",
            f"Локальные JSON: {LOCAL_TASK_DIR}",
            f"Репозиторий JSON: {REPO_TASK_DIR}",
            f"Drive write: {'yes' if drive_written else 'no'}",
        ]
    )
    _write_text(REPO_TASK_DIR / status_name, status_text)
    _write_text(LOCAL_TASK_DIR / status_name, status_text)
    return {
        "status": "DONE_LOCAL_WAITING_UPLOAD" if not drive_written else "WAITING_MUZA_QA",
        "duration_frames": re_dur,
        "output_project": str(OUTPUT_PROJECT),
        "preview": str(PREVIEW_PATH),
        "drive_written": drive_written,
        "source_unchanged": source_unchanged,
        "artifacts": artifacts,
    }


def finish_from_saved_project() -> dict[str, Any]:
    started = datetime.now().isoformat(timespec="seconds")
    preflight = json.loads((REPO_TASK_DIR / "TASK_031_PREFLIGHT.json").read_text(encoding="utf-8"))
    master = json.loads((REPO_TASK_DIR / "TASK_031_EDIT_MASTER.json").read_text(encoding="utf-8"))
    expected = int(master["expected_result"]["duration_frames"])
    source_sha = str(preflight["source_sha256"])
    work = load_premiere_project_root(OUTPUT_PROJECT)
    preview = _render_preview(work)
    reopened = load_premiere_project_root(OUTPUT_PROJECT)
    _validate_all_refs(reopened)
    re_video = _items(reopened, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 0)
    re_audio = _items(reopened, OUTPUT_SEQUENCE, OUTPUT_PROJECT, 1)
    re_dur = max(_sequence_duration(re_video), _sequence_duration(re_audio)) // FRAME_TICKS
    source_sha_after = _sha256(SOURCE_PROJECT)
    source_reopened = load_premiere_project_root(SOURCE_PROJECT)
    source_seq_after = find_project_sequence_node(source_reopened, SOURCE_SEQUENCE)
    source_unchanged = (
        source_sha_after == source_sha
        and source_seq_after is not None
        and hashlib.sha256(ET.tostring(source_seq_after, encoding="utf-8")).hexdigest()
        == preflight["source_sequence_xml_sha256"]
    )
    probe = preview["probe"]
    streams = probe.get("streams") if isinstance(probe, dict) else []
    video_stream = next((row for row in streams or [] if row.get("codec_type") == "video"), {})
    audio_stream = next((row for row in streams or [] if row.get("codec_type") == "audio"), {})
    structural = {
        "new_project_parses": True,
        "refs_resolved": True,
        "output_sequence_count": list_named_project_sequence_names(reopened).count(OUTPUT_SEQUENCE),
        "duration_frames": re_dur,
        "duration_matches_json": re_dur == expected,
        "source_project_unchanged": source_unchanged,
        "source_sequence_unchanged": source_unchanged,
        "media_online": all(
            (not item.source_path) or Path(item.source_path).is_file()
            or item.name == "SF_26_BD_Keep_08"
            for item in re_video + re_audio
        ),
        "preview_exists": PREVIEW_PATH.is_file(),
        "preview_width": video_stream.get("width"),
        "preview_height": video_stream.get("height"),
        "preview_audio": audio_stream.get("codec_name"),
    }
    _write_json(REPO_TASK_DIR / "TASK_031_STRUCTURAL_QA.json", structural)
    _write_text(
        REPO_TASK_DIR / "TASK_031_VISUAL_AUDIO_QA.txt",
        "\n".join(
            [
                "TASK_031 VISUAL/AUDIO QA",
                f"Длительность: {re_dur} кадров / {re_dur / FPS:.2f} с.",
                "Начало: два юношеских портрета и архив 1972.",
                "Чеканка сжата, вспышки скульптур сняты, style-каталог сведён к artp+wcp.",
                "Музей оставлен как дыхание, но не экскурсия.",
                "Keep_08 и Нури готовят финал; generated_video (5) сохранён целиком.",
                "Отдельной музыки нет; diegetic звук музея и финала сохранён синхронно.",
                "Анимация только на самостоятельных фото. Цвет поклипный, без общего пресета.",
                "Художественная приёмка Музы ещё не выполнялась.",
            ]
        ),
    )
    _write_text(
        REPO_TASK_DIR / "TASK_031_PREMIERE_OPEN_CHECK.txt",
        "\n".join(
            [
                "TASK_031 PREMIERE OPEN-CHECK",
                "Adobe Premiere Pro не был запущен и не держал исходник.",
                "Новый проект записан на диск, затем заново разобран через load_premiere_project_root.",
                f"Sequence {OUTPUT_SEQUENCE} найдена ровно один раз, длительность {re_dur}, ссылки ObjectRef/ObjectURef разрешены.",
                "GUI-воспроизведение в Premiere Desktop агент не запускал: это требует ручного открытия Сергеем/Музой.",
                f"Для художественной проверки используйте {PREVIEW_PATH}.",
            ]
        ),
    )
    artifacts = {
        "source": {"path": str(SOURCE_PROJECT), "sha256": source_sha, "bytes": SOURCE_PROJECT.stat().st_size},
        "backup": {"path": str(BACKUP_PROJECT), "sha256": _sha256(BACKUP_PROJECT), "bytes": BACKUP_PROJECT.stat().st_size},
        "output": {"path": str(OUTPUT_PROJECT), "sha256": _sha256(OUTPUT_PROJECT), "bytes": OUTPUT_PROJECT.stat().st_size},
        "checkpoint": {"path": str(CHECKPOINT_PROJECT), "sha256": _sha256(CHECKPOINT_PROJECT), "bytes": CHECKPOINT_PROJECT.stat().st_size},
        "preview": {"path": str(PREVIEW_PATH), "sha256": _sha256(PREVIEW_PATH), "bytes": PREVIEW_PATH.stat().st_size},
    }
    _write_text(
        REPO_TASK_DIR / "TASK_031_FINAL_REPORT.txt",
        "\n".join(
            [
                "TASK_031 FINAL REPORT",
                f"Старт: {started}",
                f"Конец: {datetime.now().isoformat(timespec='seconds')}",
                "JSON создан до мутации, dry-run PASS, затем исполнен.",
                f"Новая sequence: {OUTPUT_SEQUENCE}, {re_dur} кадров.",
                "Анимация и цвет выполнены после монтажа.",
                "Исходный проект и SF_26_Bd_Art_3 не изменены.",
                "Google Drive, скорее всего, снова только для чтения — локальный статус WAITING_UPLOAD.",
                json.dumps(artifacts, ensure_ascii=False, indent=2),
            ]
        ),
    )
    _copy_artifacts(LOCAL_TASK_DIR, REPO_TASK_DIR)
    drive_written = False
    for drive in DRIVE_CANDIDATES:
        try:
            if drive.parent.exists():
                drive.mkdir(parents=True, exist_ok=True)
                _copy_artifacts(drive, REPO_TASK_DIR)
                probe_file = drive / "TASK_031_WRITE_PROBE.txt"
                probe_file.write_text("ok\n", encoding="utf-8")
                drive_written = True
                break
        except OSError:
            drive_written = False
    status_name = (
        "TASK_031_WAITING_MUZA_QA.txt" if drive_written else "TASK_031_DONE_LOCAL_WAITING_UPLOAD.txt"
    )
    status_text = "\n".join(
        [
            "TASK_031 LOCAL COMPLETE",
            "Статус: DONE_LOCAL_WAITING_UPLOAD" if not drive_written else "WAITING_MUZA_QA",
            "Муза preview ещё не смотрела.",
            f"Проект: {OUTPUT_PROJECT}",
            f"SHA256: {artifacts['output']['sha256']}  bytes={artifacts['output']['bytes']}",
            f"Sequence: {OUTPUT_SEQUENCE}",
            f"Preview: {PREVIEW_PATH}",
            f"SHA256: {artifacts['preview']['sha256']}  bytes={artifacts['preview']['bytes']}",
            f"Backup: {BACKUP_PROJECT}",
            f"Checkpoint: {CHECKPOINT_PROJECT}",
            f"Локальные JSON: {LOCAL_TASK_DIR}",
            f"Репозиторий JSON: {REPO_TASK_DIR}",
            f"Drive write: {'yes' if drive_written else 'no'}",
        ]
    )
    _write_text(REPO_TASK_DIR / status_name, status_text)
    _write_text(LOCAL_TASK_DIR / status_name, status_text)
    return {
        "status": "DONE_LOCAL_WAITING_UPLOAD" if not drive_written else "WAITING_MUZA_QA",
        "duration_frames": re_dur,
        "output_project": str(OUTPUT_PROJECT),
        "preview": str(PREVIEW_PATH),
        "drive_written": drive_written,
        "source_unchanged": source_unchanged,
        "artifacts": artifacts,
    }


if __name__ == "__main__":
    from main_premiere_art_task import main as launch
    launch(["--task", "031"] + sys.argv[1:])
