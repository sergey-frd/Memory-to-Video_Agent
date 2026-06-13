#!/usr/bin/env python3
"""Create SF+Rita regeneration_assets, romantic + fun story HTML, composer configs."""

from __future__ import annotations

import json
import subprocess
import sys
import time
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
STAGE_STAMP = "20260613_150000"

SOURCE_FILES = [
    "Nested Sequence 05.00_00_00_00.Still001.jpg",
    "Nested Sequence 05.00_00_30_02.Still002.jpg",
    "Nested Sequence 08.00_00_06_16.Still001.jpg",
    "Nested Sequence 04.00_00_16_02.Still003.jpg",
    "Nested Sequence 06.00_00_29_22.Still002.jpg",
    "Nested Sequence 04.00_00_28_23.Still005.jpg",
    "Nested Sequence 04.00_00_05_12.Still001.jpg",
    "Nested Sequence 06.00_00_16_14.Still001.jpg",
    "Nested Sequence 03.00_00_14_13.Still001.jpg",
]

IMAGE_ASSETS: list[dict[str, str]] = [
    {
        "file": SOURCE_FILES[0],
        "tag": "@image1",
        "summary": "Молодые герой и героиня зимой: он в тёмном пальто, она в белой меховой шапке, золотой контровой свет.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Зимний пейзаж, мягкий боке",
        "framing": "Крупный парный портрет",
        "mood": "Нежный, романтичный",
    },
    {
        "file": SOURCE_FILES[1],
        "tag": "@image2",
        "summary": "Молодая героиня прижимается щекой к герою на солнце в зелени, он широко улыбается.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Сад, золотой час",
        "framing": "Крупный парный план",
        "mood": "Радостный, нежный",
    },
    {
        "file": SOURCE_FILES[2],
        "tag": "@image3",
        "summary": "Свадьба 21.07.1997: герой надевает кольцо на палец героини в белом платье с гладиолусами.",
        "people": "4 человека: герой, героиня и свидетели",
        "background": "ЗАГС, окно со шторами",
        "framing": "Средний план церемонии",
        "mood": "Торжественный, счастливый",
    },
    {
        "file": SOURCE_FILES[3],
        "tag": "@image4",
        "summary": "Зрелая пара: селфи ночью на фоне огней города, головы соприкасаются, тёплые улыбки.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Ночной город, bokeh",
        "framing": "Крупный селфи-портрет",
        "mood": "Интимный, романтичный",
    },
    {
        "file": SOURCE_FILES[4],
        "tag": "@image5",
        "summary": "Героиня в соломенной шляпе с букетами цветов, герой обнимает её за плечи, рядом молодой мужчина.",
        "people": "4 человека: героиня, герой, двое молодых мужчин",
        "background": "Светлые панели, праздник",
        "framing": "Средний групповой план",
        "mood": "Праздничный, жизнерадостный",
    },
    {
        "file": SOURCE_FILES[5],
        "tag": "@image6",
        "summary": "Зрелые герой и героиня на закате, лбы соприкасаются, золотой свет на лицах.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Холмы, закатное солнце",
        "framing": "Крупный парный портрет",
        "mood": "Лирический, умиротворённый",
    },
    {
        "file": SOURCE_FILES[6],
        "tag": "@image7",
        "summary": "Селфи у воды на закате: герой с седой бородой, героиня с короткими волосами, оба улыбаются.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Озеро, горы, golden hour",
        "framing": "Крупный селфи",
        "mood": "Тёплый, спокойный",
    },
    {
        "file": SOURCE_FILES[7],
        "tag": "@image8",
        "summary": "Семейный портрет: герой и героиня с четырьмя внуками на фоне гирлянд.",
        "people": "6 человек: пара и четверо детей",
        "background": "Светлые шторы, гирлянды",
        "framing": "Широкий семейный план",
        "mood": "Семейный, праздничный",
    },
    {
        "file": SOURCE_FILES[8],
        "tag": "@image9",
        "summary": "Застолье в ресторане: героиня и герой слева с сыновьями, все смеются за столом с бокалами.",
        "people": "4 человека за столом",
        "background": "Ресторан, бетонная стена, лампа",
        "framing": "Средний групповой план",
        "mood": "Весёлый, гостеприимный",
    },
]

ROMANTIC_PREAMBLE = (
    "Technical Preamble: 15-second tender romantic tribute — 56 years since meeting in 1970, "
    "wedding 21 July 1997. Video hero and video heroine: lifelong love through youth, marriage, "
    "family, and golden years. NOT a slideshow — characters move, smile, and gesture; soft handheld, "
    "match cuts on smile. FORBIDDEN: dissolve, crossfade, Ken Burns, static posed hold. "
    "Preserve faces and clothing per @imageN. Eye-level only; no bird's-eye or drone. "
    "No personal names — video hero, video heroine, sons, grandchildren, friends."
)

FUN_PREAMBLE = (
    "Technical Preamble: 15-second FUN energetic tribute — same love story with party energy. "
    "Video hero and video heroine laugh, dance, toast, and lead family joy. "
    "NOT a slideshow. Handheld, whip pans, snap match cuts. "
    "FORBIDDEN: dissolve, crossfade, Ken Burns, static hold. "
    "One dominant @image per scene; other tags as quick match cuts. "
    "No bird's-eye or drone."
)

ROMANTIC_SCENES = [
    (
        "@image1 — зимой молодой герой наклоняется ближе к героине в белой шапке, "
        "она мягко улыбается; золотой контровой свет оживает, камера gentle handheld вокруг их лиц."
    ),
    (
        "@image2 — героиня прижимается щекой к герою в солнечном саду, он смеётся; "
        "match cut @image3 — на свадьбе 1997 герой медленно надевает кольцо, героиня сияет, "
        "оба живут момент, а не позируют."
    ),
    (
        "@image5 — героиня с букетами в шляпе, герой обнимает её за плечи, они переглядываются и смеются; "
        "камера мягко обходит пару на уровне груди."
    ),
    (
        "@image6 — на закате герой и героиня соприкасаются лбами, тихие улыбки; "
        "snap match @image7 — у воды тот же дуэт смеётся в селфи, ветер слегка треплет волосы."
    ),
    (
        "@image8 — герой и героиня с внуками обнимают детей, гирлянды мерцают; "
        "финал @image9 — за столом в ресторане герой обнимает героиню, сыновья смеются, "
        "бокалы чокаются — камера мягко приближается к их счастливым лицам."
    ),
]

FUN_SCENES = [
    (
        "@image2 — в саду героиня внезапно смеётся громче, герой отвечает широкой улыбкой, "
        "handheld bounce; snap @image1 — зимой героиня подмигивает из-под белой шапки."
    ),
    (
        "@image3 — после кольца герой и героиня поднимают руки в радостном жесте, "
        "свидетели хлопают; match cut @image5 — героиня крутится с букетами, герой хлопает в такт."
    ),
    (
        "@image8 — внук в подтяжках делает «когти», все взрываются смехом, "
        "герой и героиня наклоняются к детям; whip pan по лицам."
    ),
    (
        "@image9 — за столом сыновья поднимают бокалы, героиня громко смеётся педагогическим голосом, "
        "герой жестом приглашает камеру; snap @image4 — ночное селфи, оба подмигивают."
    ),
    (
        "@image6 — на закате пара делает лёгкий танцевальный шаг, головы касаются; "
        "финальный match @image7 — у озера героиня указывает в камеру «56 лет вместе!», snap push-in."
    ),
]

STORY_BRANCHES = {
    "romantic": (
        "video_prompt_story_config_sf_rita_romantic.json",
        ROMANTIC_PREAMBLE,
        ROMANTIC_SCENES,
        "video_prompt_config_sf_rita_romantic.json",
    ),
    "fun": (
        "video_prompt_story_config_sf_rita_fun.json",
        FUN_PREAMBLE,
        FUN_SCENES,
        "video_prompt_config_sf_rita_fun.json",
    ),
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
- Main action: персонажи живут эмоцию, не застывают в позе
- Mood: {meta["mood"]}
- Relationship dynamic: любовь и семья вокруг героя и героини видео

Visual features:
- Palette: natural warm palette.
- Lighting: soft or golden natural light.
- Tonality: balanced.
- Atmosphere: memory photograph with motion potential.

Cinematic motion logic:
- Characters should move, smile, or gesture within the frame.
- Use eye-level medium or close framing; avoid static Ken Burns only.
"""
    (stage_dir / f"{stage_id}_description.txt").write_text(text, encoding="utf-8")


def _write_scene_analysis(stage_dir: Path, meta: dict[str, str]) -> None:
    stage_id = stage_dir.name
    payload = {
        "summary": meta["summary"],
        "people_count": 2,
        "background": meta["background"],
        "framing": meta["framing"],
        "mood": meta["mood"],
        "hero_role": "герой видео / героиня видео",
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
) -> None:
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
    write_video_prompt_config(
        composer_path,
        story_draft_to_video_prompt_config(
            draft,
            model=config.model,
            output_dir=config.effective_output_dir,
            seedance_json=config.seedance_json,
            seedance_json_only=config.seedance_json_only,
            seedance_director_file=config.seedance_director_file,
        ),
    )
    print(f"  HTML:     {latest_html}")
    print(f"  Composer: {composer_path}")


def run_composers() -> None:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    main = ROOT / "main_video_prompt_composer.py"
    jobs = [
        REGEN_DIR / "video_prompt_config_sf_rita_romantic.json",
        REGEN_DIR / "video_prompt_config_sf_rita_romantic_v2only.json",
        REGEN_DIR / "video_prompt_config_sf_rita_fun.json",
        REGEN_DIR / "video_prompt_config_sf_rita_fun_v2only.json",
    ]

    def make_v2only(src: Path, dst: Path, instruction: str) -> None:
        data = json.loads(src.read_text(encoding="utf-8"))
        data["max_prompt_chars"] = 3600
        variant = next(v for v in data["scenario_variants"] if v["variant_id"] == "Variant_2")
        variant["instruction"] = instruction
        data["scenario_variants"] = [variant]
        dst.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    make_v2only(
        REGEN_DIR / "video_prompt_config_sf_rita_romantic.json",
        REGEN_DIR / "video_prompt_config_sf_rita_romantic_v2only.json",
        (
            "Poetic romantic alternative with reflective pauses. Eye-level only; "
            "never bird's-eye, drone, aerial, tiny figures, or distant specks. "
            "NOT a slideshow."
        ),
    )
    make_v2only(
        REGEN_DIR / "video_prompt_config_sf_rita_fun.json",
        REGEN_DIR / "video_prompt_config_sf_rita_fun_v2only.json",
        (
            "Music-and-joy alternative: piano-lesson rhythm, singing, table toast energy. "
            "Snap cuts only. Never bird's-eye, drone, aerial, tiny, or distant specks."
        ),
    )

    for index, job in enumerate(jobs):
        print(f"=== Composer: {job.name} ===")
        for attempt in range(1, 7):
            if attempt > 1:
                time.sleep(20)
            print(f"attempt {attempt}")
            result = subprocess.run(
                [str(py), "-u", str(main), "--config-file", str(job)],
                cwd=ROOT,
            )
            if result.returncode == 0:
                break
        else:
            raise SystemExit(f"Composer failed: {job}")
        if index + 1 < len(jobs):
            time.sleep(15)


def main() -> int:
    print("Creating regeneration_assets...")
    create_regeneration_assets()
    base = datetime(2026, 6, 13, 15, 0, 0, tzinfo=timezone.utc)
    print("Writing romantic story...")
    write_story_branch(*STORY_BRANCHES["romantic"][:3], STORY_BRANCHES["romantic"][3], timestamp=base)
    print("Writing fun story...")
    write_story_branch(
        *STORY_BRANCHES["fun"][:3],
        STORY_BRANCHES["fun"][3],
        timestamp=base.replace(minute=30),
    )
    print("Running Seedance composers...")
    run_composers()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
