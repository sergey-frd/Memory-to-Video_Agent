#!/usr/bin/env python3
"""Create Igor+Sveta regeneration_assets, lyrical + fun story HTML, composer configs."""

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
STAGE_STAMP = "20260615_120000"

SOURCE_FILES = [
    "IMG-20180409-WA0019-s.jpeg",
    "SvIg_BarMizva18_11_00.JPG",
    "IMG_8656.JPG",
    "IMG-20181124-WA0001.jpg",
    "IMG_20170618_153658_1.jpg",
    "IMG_4552-s.jpeg",
    "20211206_044850915.jpg",
    "1809_siberia_0050-s.jpeg",
    "Svt_Igr_23_7.00_01_35_22.Still023.jpg",
]

IMAGE_ASSETS: list[dict[str, str]] = [
    {
        "file": SOURCE_FILES[0],
        "tag": "@image1",
        "summary": "Домашний праздник: герой и героиня видео с бокалами, у героини розовый праздничный ободок, тёплый тост.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Диван, светлая стена, домашний интерьер",
        "framing": "Средний парный план",
        "mood": "Праздничный, нежный",
    },
    {
        "file": SOURCE_FILES[1],
        "tag": "@image2",
        "summary": "Банкет: героиня в бархатном бордовом топе и герой в белой рубашке сидят вплотную, оба широко улыбаются.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Банкетный зал, жёлтая скатерть, тёмный фон",
        "framing": "Крупный парный план",
        "mood": "Радостный, торжественный",
    },
    {
        "file": SOURCE_FILES[2],
        "tag": "@image3",
        "summary": "Домашний диван: герой откинулся с рукой за головой, героиня в чёрном платье смеётся, глядя в сторону.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Кожаный диван, полосатые шторы",
        "framing": "Средний парный план",
        "mood": "Уютный, весёлый",
    },
    {
        "file": SOURCE_FILES[3],
        "tag": "@image4",
        "summary": "У арочного окна героиня нежно держит младенца на руках, герой стоит рядом с тёплой улыбкой — рождение сына.",
        "people": "3 человека: героиня, герой и младенец",
        "background": "Светлая комната, терракотовый пол, вид на двор",
        "framing": "Полный рост, семейный план",
        "mood": "Нежный, семейный",
    },
    {
        "file": SOURCE_FILES[4],
        "tag": "@image5",
        "summary": "На катере по реке: герой и героиня под развевающимся сине-жёлтым флагом, солнечный день, золотые купола вдали.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Река, лодка, голубое небо, холмы",
        "framing": "Средний план на палубе",
        "mood": "Энергичный, патриотичный, радостный",
    },
    {
        "file": SOURCE_FILES[5],
        "tag": "@image6",
        "summary": "Герой в белой рубашке обнимает героиню за талию, она кладёт ладонь ему на грудь — пара у светлой занавески с гирляндами.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Белая занавеска, тёплые огни bokeh",
        "framing": "Средний парный план",
        "mood": "Романтичный, счастливый",
    },
    {
        "file": SOURCE_FILES[6],
        "tag": "@image7",
        "summary": "Путешествие: селфи пары на высокой точке над белыми куполами и морем, оба улыбаются в солнечный день.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Панорама города, море, голубое небо",
        "framing": "Крупный селфи-портрет",
        "mood": "Оптимистичный, путешественнический",
    },
    {
        "file": SOURCE_FILES[7],
        "tag": "@image8",
        "summary": "Дома на диване: герой обнимает героиню за плечи, оба спокойно улыбаются в камеру.",
        "people": "2 человека: герой видео и героиня видео",
        "background": "Бежевый диван, светлая стена, арка в соседнюю комнату",
        "framing": "Средний парный план",
        "mood": "Тёплый, интимный",
    },
    {
        "file": SOURCE_FILES[8],
        "tag": "@image9",
        "summary": "Семейный портрет у камина: герой и героиня с детьми и внуками в праздничных пижамах, все смеются.",
        "people": "6 человек: пара, молодые родители и двое детей",
        "background": "Камин с огнём, кирпичная облицовка",
        "framing": "Широкий семейный план",
        "mood": "Семейный, праздничный, тёплый",
    },
]

LYRICAL_PREAMBLE = (
    "Technical Preamble: 15-second tender lyrical tribute — video hero and video heroine, "
    "programmers who married 24 July 1983, journey through love, son, repatriation to Israel, "
    "travel, and grandchildren. NOT a slideshow — characters move, smile, and gesture; soft handheld, "
    "match cuts on smile. FORBIDDEN: dissolve, crossfade, Ken Burns, static posed hold. "
    "Preserve faces and clothing per @imageN. Eye-level only; no bird's-eye or drone. "
    "No personal names — video hero, video heroine, son, grandchildren, family."
)

FUN_PREAMBLE = (
    "Technical Preamble: 15-second FUN energetic tribute — same couple as active optimists: "
    "programmers, travelers, swimmers, hosts who love family parties. "
    "Characters MUST move, laugh, toast, and gesture every shot. "
    "Handheld, whip pans, snap match cuts on smile. "
    "FORBIDDEN: dissolve, crossfade, Ken Burns, static posed hold. "
    "One dominant @image per scene; other tags as quick match cuts. "
    "No bird's-eye or drone."
)

LYRICAL_SCENES = [
    (
        "@image1 — дома герой поднимает бокал в тост, героиня в розовом ободке мягко улыбается; "
        "камера gentle handheld вокруг их лиц; "
        "match cut @image2 — на банкете после свадьбы 24.7.1983 пара сидит близко, "
        "оба сияют, живая радость, а не застывшая поза."
    ),
    (
        "@image3 — на диване героиня смеётся, герой смотрит на неё с нежностью; "
        "мягкий tracking по их лицам — домашний уют после переезда и работы программистами."
    ),
    (
        "@image4 — у окна героиня нежно прижимает младенца, герой наклоняется ближе, "
        "тёплый свет скользит по лицам; камера на уровне груди, тихая материнская радость."
    ),
    (
        "@image5 — на реке флаг развевается на ветру, герой и героиня улыбаются под солнцем; "
        "snap match @image6 — пара у занавески с гирляндами, герой обнимает героиню, "
        "она кладёт ладонь на его грудь — любовь через десятилетия."
    ),
    (
        "@image7 — селфи над белыми куполами и морем, ветер слегка треплет волосы; "
        "match cut @image8 — дома герой обнимает героиню за плечи; "
        "финал @image9 — у камина вся семья смеётся, внуки в праздничных пижамах, "
        "камера мягко приближается к счастливым лицам героя и героини."
    ),
]

FUN_SCENES = [
    (
        "@image1 — герой энергично поднимает бокал «за нас!», героиня в розовом ободке подмигивает; "
        "handheld bounce; snap @image2 — на банкете оба смеются шире, жёлтая скатерть оживает цветом."
    ),
    (
        "@image3 — героиня хлопает в ладоши от смеха, герой откидывается на диване и кивает в такт; "
        "whip pan по их лицам — душа компании дома."
    ),
    (
        "@image5 — на катере флаг хлещет на ветру, героиня поднимает руку выше, герой смеётся; "
        "stabilized tracking по палубе; match @image7 — селфи на крыше, оба машут в камеру «в путь!»."
    ),
    (
        "@image4 — героиня слегка подбрасывает младенца, все трое смеются; "
        "snap @image6 — пара делает лёгкий танцевальный шаг у гирлянд, герой крутит героиню."
    ),
    (
        "@image8 — герой обнимает героиню и показывает большой палец в камеру; "
        "финал @image9 — у камина внуки делают «когти», все взрываются смехом, "
        "герой и героиня наклоняются к детям; snap push-in, семейный beat."
    ),
]

STORY_BRANCHES = {
    "lyrical": (
        "video_prompt_story_config_igor_sv_lyrical.json",
        LYRICAL_PREAMBLE,
        LYRICAL_SCENES,
        "video_prompt_config_igor_sv_lyrical.json",
    ),
    "fun": (
        "video_prompt_story_config_igor_sv_fun.json",
        FUN_PREAMBLE,
        FUN_SCENES,
        "video_prompt_config_igor_sv_fun.json",
    ),
}


def _write_description(stage_dir: Path, meta: dict[str, str]) -> None:
    stage_id = stage_dir.name
    text = f"""Source image analysis (FRAME A)

Image format: horizontal landscape frame from source photograph.
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
        REGEN_DIR / "video_prompt_config_igor_sv_lyrical.json",
        REGEN_DIR / "video_prompt_config_igor_sv_lyrical_v2only.json",
        REGEN_DIR / "video_prompt_config_igor_sv_fun.json",
        REGEN_DIR / "video_prompt_config_igor_sv_fun_v2only.json",
    ]

    def make_v2only(src: Path, dst: Path, instruction: str) -> None:
        data = json.loads(src.read_text(encoding="utf-8"))
        data["max_prompt_chars"] = 3600
        variant = next(v for v in data["scenario_variants"] if v["variant_id"] == "Variant_2")
        variant["instruction"] = instruction
        data["scenario_variants"] = [variant]
        dst.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    make_v2only(
        REGEN_DIR / "video_prompt_config_igor_sv_lyrical.json",
        REGEN_DIR / "video_prompt_config_igor_sv_lyrical_v2only.json",
        (
            "Poetic lyrical alternative with reflective pauses. Eye-level only; "
            "never bird's-eye, drone, aerial, tiny figures, or distant specks. "
            "NOT a slideshow."
        ),
    )
    make_v2only(
        REGEN_DIR / "video_prompt_config_igor_sv_fun.json",
        REGEN_DIR / "video_prompt_config_igor_sv_fun_v2only.json",
        (
            "Travel-and-sport alternative: boat flag motion, rooftop selfie energy, "
            "fireplace family laughter. Snap cuts only. Never bird's-eye, drone, aerial, "
            "tiny, or distant specks."
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
    base = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    print("Writing lyrical story...")
    write_story_branch(*STORY_BRANCHES["lyrical"][:3], STORY_BRANCHES["lyrical"][3], timestamp=base)
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
