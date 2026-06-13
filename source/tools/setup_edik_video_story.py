#!/usr/bin/env python3
"""Create regeneration_assets descriptions and Edik multi-scene story HTML draft."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.video_prompt_composer import JERUSALEM_TZ
from utils.video_prompt_story import (
    StoryImageCandidate,
    build_empty_story_draft,
    build_story_references,
    default_story_output_paths,
    write_story_draft_json,
    write_story_html,
)
from video_prompt_story_config import load_video_prompt_story_config

STAGE_STAMP = "20260612_140000"
IMAGES_DIR = Path(r"<LOCAL_PATH>")
REGEN_DIR = Path(r"<LOCAL_PATH>")

IMAGE_ASSETS: list[dict[str, str]] = [
    {
        "file": "EdSn26_AN_4.00_00_55_22.Still001.jpg",
        "tag": "@image1",
        "summary": "Герой видео стоит в просторном foyer в светло-серой куртке и бежевых брюках, задумчиво смотрит в сторону.",
        "people": "1 мужчина (герой видео)",
        "background": "Холл с диваном, картиной на стене и дверью на заднем плане",
        "framing": "Средний план, вертикальный кадр в letterbox",
        "mood": "Спокойный, собранный",
    },
    {
        "file": "EdSn26_AN_4.00_01_21_13.Still002.jpg",
        "tag": "@image2",
        "summary": "Крупный профиль героя видео: тёмные кудрявые волосы, лёгкая щетина, взгляд направлен вверх с любопытством.",
        "people": "1 мужчина (герой видео)",
        "background": "Нейтральная серая стена",
        "framing": "Крупный план, профиль",
        "mood": "Задумчивый, живой",
    },
    {
        "file": "Nested Sequence 06.00_00_25_02.Still002.jpg",
        "tag": "@image3",
        "summary": "Молодой герой видео в центре кадра в тёмно-синем свитере; две женщины обнимают его за плечи.",
        "people": "3 человека: молодая женщина слева, герой видео в центре, женщина постарше справа",
        "background": "Комната с обоями и домашним растением",
        "framing": "Средний групповой портрет",
        "mood": "Тёплый, семейный",
    },
    {
        "file": "EdSn26_AN_4.00_02_38_00.Still009.jpg",
        "tag": "@image4",
        "summary": "Герой видео и его супруга сидят в лесу; она смеётся, он мягко улыбается в синей футболке.",
        "people": "2 человека: супруга слева, герой видео справа",
        "background": "Лес, стволы деревьев, дневной свет",
        "framing": "Средний план, парный портрет",
        "mood": "Радостный, романтичный",
    },
    {
        "file": "EdSn26_AN_4.00_02_31_09.Still007.jpg",
        "tag": "@image5",
        "summary": "Семейный портрет у ёлки: дочь в серебристом платье, супруга в клетчатой рубашке, герой видео в полосатом свитере.",
        "people": "3 человека: дочь, супруга, герой видео",
        "background": "Ёлка с золотыми шарами, домашний интерьер",
        "framing": "Средний групповой план",
        "mood": "Праздничный, уютный",
    },
    {
        "file": "EdSn26_AN_4.00_02_33_24.Still008.jpg",
        "tag": "@image6",
        "summary": "Четверо друзей на закате у моря; герой видео внизу справа в светлой полоске, все улыбаются.",
        "people": "4 человека: две женщины и два мужчины, герой видео — справа внизу",
        "background": "Морской горизонт, золотой час",
        "framing": "Средний групповой план",
        "mood": "Дружеский, солнечный",
    },
    {
        "file": "EdSn26_AN_4.00_02_55_18.Still011.jpg",
        "tag": "@image7",
        "summary": "Герой видео с широкой улыбкой держит на руках смеющегося младенца в домашней обстановке.",
        "people": "2 человека: герой видео и младенец (внук)",
        "background": "Светлая комната, детские игрушки",
        "framing": "Средний вертикальный план",
        "mood": "Нежный, радостный",
    },
    {
        "file": "EdSn26_AN_4.00_02_42_09.Still014.jpg",
        "tag": "@image8",
        "summary": "Герой видео в синей рубашке сидит рядом с супругой с рыжими волосами; праздничные огни и ёлка.",
        "people": "2 человека: супруга и герой видео",
        "background": "Домашний интерьер с ёлкой, гирляндами и зимними картинами",
        "framing": "Средний план",
        "mood": "Домашний, праздничный",
    },
    {
        "file": "EdSn26_AN_4.00_02_58_11.Still013.jpg",
        "tag": "@image9",
        "summary": "Семья на детском коврике: герой видео сзади, супруга с младенцем, мальчик впереди; игрушки и камин.",
        "people": "4 человека: мальчик, герой видео, супруга с младенцем",
        "background": "Гостиная с камином, телевизором и детским ковриком",
        "framing": "Широкий семейный план",
        "mood": "Счастливый, многопоколенный",
    },
]

TECHNICAL_PREAMBLE = (
    "Technical Preamble: 15-секундный поздравительный ролик ко дню рождения. "
    "Герой видео — мужчина, программист, музыкант и актёр, любящий муж, отец и дед. "
    "Хронология: зрелость → молодость и семья → праздники и друзья → домашнее тепло → внуки. "
    "Тон тёплый и добрый. Личные имена не использовать — только «герой видео», «супруга», «дочь», «внуки», «друзья». "
    "Сохранять лица, одежду и композицию по каждому @imageN. Только medium/full shots, без drone и bird's-eye."
)

SCENE_DESCRIPTIONS = [
    (
        "Камера мягко открывает @image1: герой видео в светло-серой куртке стоит задумчиво в просторном foyer; "
        "затем плавный переход к @image2 — тот же мужчина в профиль смотрит вверх с тихой улыбкой, "
        "как будто вспоминая прожитые годы."
    ),
    (
        "@image3 показывает молодого героя в центре семейного круга — две женщины обнимают его за плечи; "
        "dissolve к @image4: тот же герой и его супруга смеются, сидя среди деревьев в солнечном лесу — "
        "начало семейной жизни и первой любви."
    ),
    (
        "@image5 — праздник у ёлки: дочь в блестящем платье, супруга и герой видео рядом; "
        "камера переходит к @image6, где герой среди друзей на закате у моря — "
        "круг общения, юмор и театральная энергия жизни."
    ),
    (
        "@image8 — уютный дом: герой видео в синей рубашке рядом с супругой, праздничные огни и зимние картины; "
        "затем @image7 — тот же герой с широкой улыбкой держит на руках смеющегося младенца, "
        "радость отцовства и дедства."
    ),
    (
        "Финальный широкий план @image9: герой видео с супругой, сыном и младенцом на детском коврике среди игрушек; "
        "камера медленно приближается, завершая ролик образом семьи и благодарности за прожитую жизнь."
    ),
]


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
- Main action: персонажи позируют для камеры, естественные эмоции
- Mood: {meta["mood"]}
- Relationship dynamic: семейная и дружеская связь вокруг героя видео

Visual features:
- Palette: natural warm palette.
- Lighting: soft indoor/outdoor natural light.
- Tonality: balanced.
- Atmosphere: intimate memory photograph.

Cinematic motion logic:
- This frame works best when:
- first the scene context opens with a gentle medium shot preserving all visible faces.
- then a slow subtle push-in toward the hero while keeping the same framing anchor.
"""
    (stage_dir / f"{stage_id}_description.txt").write_text(text, encoding="utf-8")


def _write_scene_analysis(stage_dir: Path, meta: dict[str, str]) -> None:
    stage_id = stage_dir.name
    payload = {
        "summary": meta["summary"],
        "people_count": len(meta["people"].split(",")) if "," in meta["people"] else 1,
        "background": meta["background"],
        "framing": meta["framing"],
        "mood": meta["mood"],
        "hero_role": "герой видео",
        "image_tag": meta["tag"],
    }
    (stage_dir / f"{stage_id}_scene_analysis_ru.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_regeneration_assets() -> None:
    REGEN_DIR.mkdir(parents=True, exist_ok=True)
    for meta in IMAGE_ASSETS:
        source_stem = Path(meta["file"]).stem
        stage_dir = REGEN_DIR / f"{source_stem}_{STAGE_STAMP}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        _write_description(stage_dir, meta)
        _write_scene_analysis(stage_dir, meta)
        print(f"  assets: {stage_dir.name}")


def create_story_html() -> tuple[Path, Path]:
    config_path = ROOT / "video_prompt_story_config_edik.json"
    config = load_video_prompt_story_config(config_path)

    candidates = [
        StoryImageCandidate(
            source_file=meta["file"],
            image_path=IMAGES_DIR / meta["file"],
            stage_id=f"{Path(meta['file']).stem}_{STAGE_STAMP}",
        )
        for meta in IMAGE_ASSETS
    ]
    references = build_story_references(candidates)
    image_paths = tuple(candidate.image_path for candidate in candidates)

    draft = build_empty_story_draft(
        config,
        references=references,
        image_paths=image_paths,
        technical_preamble=TECHNICAL_PREAMBLE,
        scene_descriptions=list(SCENE_DESCRIPTIONS),
    )

    html_path, json_path, _ = default_story_output_paths(
        config.effective_output_dir,
        timestamp=datetime(2026, 6, 12, 14, 0, 0, tzinfo=timezone.utc),
        stem=config.story_output_stem,
    )
    latest_html = config.effective_output_dir / f"{config.story_output_stem}_latest.html"
    latest_json = config.effective_output_dir / f"{config.story_output_stem}_latest.json"

    write_story_draft_json(json_path, draft)
    write_story_html(html_path, draft)
    write_story_draft_json(latest_json, draft)
    write_story_html(latest_html, draft)

    return html_path, json_path


def main() -> int:
    print("Creating regeneration_assets descriptions...")
    create_regeneration_assets()
    print("Writing story HTML and JSON...")
    html_path, json_path = create_story_html()
    print(f"HTML:  {html_path}")
    print(f"JSON:  {json_path}")
    print(f"Latest HTML: {html_path.parent / 'video_prompt_story_edik_latest.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
