#!/usr/bin/env python3
"""Create Sveta regeneration_assets, lyrical + fun story HTML, and composer configs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.video_prompt_story import (
    StoryImageCandidate,
    build_empty_story_draft,
    build_story_references,
    default_story_output_paths,
    story_draft_to_video_prompt_config,
    write_story_draft_json,
    write_story_html,
    write_video_prompt_config,
)
from video_prompt_story_config import load_video_prompt_story_config

IMAGES_DIR = Path(r"<LOCAL_PATH>")
REGEN_DIR = Path(r"<LOCAL_PATH>")
STAGE_STAMP = "20260613_120000"

SOURCE_FILES = [
    "Svt_Igr_26_10.00_00_31_01.Still005.jpg",
    "Svt_Igr_26_10.00_01_06_18.Still002.jpg",
    "Svt_Igr_26_10.00_00_54_05.Still001.jpg",
    "Svt_Igr_26_10.00_01_31_12.Still003.jpg",
    "Svt_Igr_26_10.00_02_01_23.Still004.jpg",
    "Svt_Igr_26_30.00_04_07_24.Still003.jpg",
    "Svt_Igr_26_30.00_03_03_24.Still001.jpg",
    "Svt_Igr_26_30.00_02_47_24.Still002.jpg",
    "Svt_Igr_26V_1.01_08_07_18.Still002.jpg",
]

IMAGE_ASSETS: list[dict[str, str]] = [
    {
        "file": SOURCE_FILES[0],
        "tag": "@image1",
        "summary": "Маленькая героиня видео с блондинистой чёлкой и большим бантом, серьёзный нежный детский взгляд в студии.",
        "people": "1 девочка (героиня видео в детстве)",
        "background": "Тёмный студийный фон",
        "framing": "Крупный детский портрет",
        "mood": "Нежный, лирический",
    },
    {
        "file": SOURCE_FILES[1],
        "tag": "@image2",
        "summary": "Юная героиня видео с светлыми волосами и белым воротником, задумчивый спокойный взгляд.",
        "people": "1 девушка (героиня видео)",
        "background": "Тёмный текстурный фон",
        "framing": "Портрет по грудь",
        "mood": "Лирический, чистый",
    },
    {
        "file": SOURCE_FILES[2],
        "tag": "@image3",
        "summary": "Семейный детский портрет: пятеро детей, среди них будущая героиня видео с синим бантом.",
        "people": "5 детей, героиня видео — девочка с синим бантом",
        "background": "Студия, тёмный фон",
        "framing": "Групповой портрет",
        "mood": "Семейный, нostalgический",
    },
    {
        "file": SOURCE_FILES[3],
        "tag": "@image4",
        "summary": "Молодая героиня в платье в горошек на балконе рядом с супругом, широкая солнечная улыбка.",
        "people": "2 человека: героиня видео и супруг",
        "background": "Балкон, зелень, тёплый свет",
        "framing": "Средний парный план",
        "mood": "Радостный, романтичный",
    },
    {
        "file": SOURCE_FILES[4],
        "tag": "@image5",
        "summary": "Молодая героиня нежно держит младенца на руках, мягкая материнская улыбка.",
        "people": "2 человека: героиня видео и младенец (сын)",
        "background": "Домашний интерьер, тёплый свет",
        "framing": "Средний план",
        "mood": "Нежный, материнский",
    },
    {
        "file": SOURCE_FILES[5],
        "tag": "@image6",
        "summary": "Зрелая героиня в очках выходит из яркого домика с рюкзаком, уверенная улыбка путешественницы.",
        "people": "1 женщина (героиня видеo)",
        "background": "Белый домик с синими ставнями, golden hour",
        "framing": "Полный рост в дверном проёме",
        "mood": "Активный, оптимистичный",
    },
    {
        "file": SOURCE_FILES[6],
        "tag": "@image7",
        "summary": "Портрет зрелой героини в очках и футболке DISCOVER, спокойная добрая улыбка.",
        "people": "1 женщина (героиня видеo)",
        "background": "Нейтральный студийный фон",
        "framing": "Портрет по грудь",
        "mood": "Мудрый, душевный",
    },
    {
        "file": SOURCE_FILES[7],
        "tag": "@image8",
        "summary": "Героиня и супруг в зрелом возрасте, тёплые улыбки, рядом камера — крепкая пара.",
        "people": "2 человека: героиня видео и супруг",
        "background": "Тёплая ochre стена",
        "framing": "Средний парный портрет",
        "mood": "Любовь, благодарность",
    },
    {
        "file": SOURCE_FILES[8],
        "tag": "@image9",
        "summary": "Застолье дома: героиня с широкой улыбкой за столом, супруг поднимает золотой предмет, дружеская атмосфера.",
        "people": "3 человека за столом, героиня видео справа",
        "background": "Домашняя столовая, картины на стене",
        "framing": "Средний групповой план",
        "mood": "Тёплый, гостеприимный, весёлый",
    },
]

LYRICAL_PREAMBLE = (
    "Technical Preamble: 15-second tender lyrical birthday tribute to the video heroine — "
    "soulful programmer, caring mother and grandmother, kind and compassionate. "
    "Chronological life arc with gentle emotional warmth. "
    "Soft tracking and subtle push-ins; living smiles and caring gestures — NOT a static slideshow, "
    "NOT dissolve/crossfade as the main device. "
    "Preserve faces, clothing, and composition per @imageN. Eye-level framing; no bird's-eye or drone. "
    "No personal names — video heroine, spouse, son, grandchildren, family, friends."
)

FUN_PREAMBLE = (
    "Technical Preamble: 15-second FUN energetic birthday tribute — video heroine as active optimist, "
    "programmer, singer-dancer, soul of the company, loving grandmother. "
    "Characters MUST move, laugh, dance, and gesture every shot. "
    "Handheld, whip pans, snap match cuts on smile. "
    "FORBIDDEN: dissolve, crossfade, Ken Burns, static posed hold. "
    "One dominant @image per scene; other tags as quick match cuts. "
    "Preserve faces and clothing per @imageN. No bird's-eye or drone."
)

LYRICAL_SCENES = [
    (
        "@image1 — маленькая героиня видео с бантом смотрит в камеру, и в глазах тихая душевная искра; "
        "мягкий push-in; match cut @image2 — та же героиня юной, светлые волосы, белый воротник, "
        "спокойный взгляд, как начало большой жизни."
    ),
    (
        "@image3 — семейный детский портрет, героиня среди братьев и сестёр; "
        "камера медленно скользит по лицам; "
        "переход к @image4 — молодая героиня на солнечном балконе в горошек, супруг обнимает её, "
        "она сияет — первая любовь и свадебное счастье."
    ),
    (
        "@image5 — героиня нежно прижимает младенца к себе, тёплый домашний свет, "
        "материнская улыбка; камера на уровне груди, мягкое движение вокруг пары."
    ),
    (
        "@image7 — зрелая героиня в очках, спокойная добрая улыбка, футболка DISCOVER — "
        "образ мудрости и открытости; "
        "match cut @image8 — она рядом с супругом, оба тепло улыбаются, руки близко — "
        "любовь через годы и репатриацию."
    ),
    (
        "@image6 — героиня выходит из яркого домика навстречу свету, рюкзак за плечами, "
        "шаг полон надежды; "
        "финал @image9 — домашний стол, героиня с душевной улыбкой среди близких, "
        "камера мягко приближается — гостеприимство и благодарность за жизнь."
    ),
]

FUN_SCENES = [
    (
        "@image1 — маленькая героиня с бантом внезапно улыбается шире, подмигивает в камеру; "
        "snap cut @image4 — та же героиня на балконе в горошек, смеётся в объятиях супруга, "
        "камера handheld, солнечный bounce."
    ),
    (
        "@image6 — героиня энергично выскакивает из домика с рюкзаком, "
        "махает рукой «в путь!», stabilized tracking; "
        "match cut @image7 — она в футболке DISCOVER поднимает руки в жесте «открыть мир», "
        "оптимистичная улыбка."
    ),
    (
        "@image5 — героиня слегка подбрасывает младенца, оба смеются; "
        "whip pan @image3 — дети в студийном портрете хлопают в ладоши в такт воображаемой песне."
    ),
    (
        "@image8 — героиня и супруг смеются, он показывает камеру, она отвечает театральным жестом; "
        "snap cut @image9 — за столом героиня поднимает бокал, супруг поднимает золотой предмет, "
        "все смеются, handheld по кругу."
    ),
    (
        "@image2 — юная героиня делает лёгкий танцевальный поворот; "
        "финальный match cut: @image6 + @image9 energy merge — героиня указывает в камеру «с праздником!», "
        "камера snap push-in, birthday beat."
    ),
]

STORY_VARIANTS = {
    "lyrical": ("video_prompt_story_config_sveta_lyrical.json", LYRICAL_PREAMBLE, LYRICAL_SCENES, "video_prompt_config_sveta_birthday_lyrical.json"),
    "fun": ("video_prompt_story_config_sveta_fun.json", FUN_PREAMBLE, FUN_SCENES, "video_prompt_config_sveta_birthday_fun.json"),
}


def _write_description(stage_dir: Path, meta: dict[str, str]) -> None:
    stage_id = stage_dir.name
    text = f"""Source image analysis (FRAME A)

Image format: horizontal landscape frame from video still.
This format must be preserved in video and final frames.

Scene composition:
- Input anchor: frame A (source frame).
- Narrative summary: {meta["summary"]}
- Visible people count: {meta["people"]}.
- Background: {meta["background"]}
- Framing: {meta["framing"]}
- Main action: естественные эмоции и жесты вокруг героини видео
- Mood: {meta["mood"]}
- Relationship dynamic: семейная и дружеская связь

Visual features:
- Palette: natural warm palette.
- Lighting: soft natural or studio light.
- Tonality: balanced.
- Atmosphere: memory photograph with living emotion potential.

Cinematic motion logic:
- This frame works best when characters show visible motion or expression change.
- Use medium eye-level framing; gentle or energetic camera follow depending on story tone.
"""
    (stage_dir / f"{stage_id}_description.txt").write_text(text, encoding="utf-8")


def _write_scene_analysis(stage_dir: Path, meta: dict[str, str]) -> None:
    stage_id = stage_dir.name
    payload = {
        "summary": meta["summary"],
        "people_count": 1,
        "background": meta["background"],
        "framing": meta["framing"],
        "mood": meta["mood"],
        "hero_role": "героиня видео",
        "image_tag": meta["tag"],
    }
    (stage_dir / f"{stage_id}_scene_analysis_ru.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_regeneration_assets() -> None:
    REGEN_DIR.mkdir(parents=True, exist_ok=True)
    for meta in IMAGE_ASSETS:
        stage_dir = REGEN_DIR / f"{Path(meta['file']).stem}_{STAGE_STAMP}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        _write_description(stage_dir, meta)
        _write_scene_analysis(stage_dir, meta)
        print(f"  assets: {stage_dir.name}")


def write_story_branch(
    config_rel: str,
    preamble: str,
    scenes: list[str],
    composer_name: str,
    *,
    timestamp: datetime,
) -> tuple[Path, Path]:
    config = load_video_prompt_story_config(ROOT / config_rel)
    candidates = [
        StoryImageCandidate(
            source_file=name,
            image_path=IMAGES_DIR / name,
            stage_id=f"{Path(name).stem}_{STAGE_STAMP}",
        )
        for name in SOURCE_FILES
    ]
    references = build_story_references(candidates)
    image_paths = tuple(c.image_path for c in candidates)
    draft = build_empty_story_draft(
        config,
        references=references,
        image_paths=image_paths,
        technical_preamble=preamble,
        scene_descriptions=list(scenes),
    )
    html_path, json_path, _ = default_story_output_paths(
        config.effective_output_dir,
        timestamp=timestamp,
        stem=config.story_output_stem,
    )
    latest_html = REGEN_DIR / f"{config.story_output_stem}_latest.html"
    latest_json = REGEN_DIR / f"{config.story_output_stem}_latest.json"
    write_story_draft_json(json_path, draft)
    write_story_html(html_path, draft)
    write_story_draft_json(latest_json, draft)
    write_story_html(latest_html, draft)
    composer_path = REGEN_DIR / composer_name
    payload = story_draft_to_video_prompt_config(
        draft,
        model=config.model,
        output_dir=config.effective_output_dir,
        seedance_json=config.seedance_json,
        seedance_json_only=config.seedance_json_only,
        seedance_director_file=config.seedance_director_file,
    )
    write_video_prompt_config(composer_path, payload)
    print(f"  HTML:     {latest_html}")
    print(f"  Composer: {composer_path}")
    return latest_html, composer_path


def main() -> int:
    print("Creating regeneration_assets...")
    create_regeneration_assets()
    base_ts = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)
    print("Writing lyrical story...")
    write_story_branch(*STORY_VARIANTS["lyrical"][:3], STORY_VARIANTS["lyrical"][3], timestamp=base_ts)
    print("Writing fun story...")
    write_story_branch(
        *STORY_VARIANTS["fun"][:3],
        STORY_VARIANTS["fun"][3],
        timestamp=base_ts.replace(minute=30),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
