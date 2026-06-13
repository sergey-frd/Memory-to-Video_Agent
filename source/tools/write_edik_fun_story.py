#!/usr/bin/env python3
"""Write fun birthday story HTML/JSON and export video_prompt_config."""

from __future__ import annotations

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
STAGE_STAMP = "20260612_140000"

SOURCE_FILES = [
    "EdSn26_AN_4.00_00_55_22.Still001.jpg",
    "EdSn26_AN_4.00_01_21_13.Still002.jpg",
    "Nested Sequence 06.00_00_25_02.Still002.jpg",
    "EdSn26_AN_4.00_02_38_00.Still009.jpg",
    "EdSn26_AN_4.00_02_31_09.Still007.jpg",
    "EdSn26_AN_4.00_02_33_24.Still008.jpg",
    "EdSn26_AN_4.00_02_55_18.Still011.jpg",
    "EdSn26_AN_4.00_02_42_09.Still014.jpg",
    "EdSn26_AN_4.00_02_58_11.Still013.jpg",
]

TECHNICAL_PREAMBLE = (
    "Technical Preamble: 15-second FUN birthday tribute — party energy, not a slideshow. "
    "Video hero: programmer, musician, actor, KVN humor, soul of the company. "
    "Characters MUST move, laugh, and gesture in every shot; handheld tracking, whip pans, match cuts on laughter. "
    "FORBIDDEN: dissolve, crossfade, fade, Ken Burns, static posed hold, slow dolly only. "
    "One dominant @image per scene with action inside the frame; other tags only as quick match cuts. "
    "Preserve faces, clothing, and composition per @imageN. Eye-level and chest-level only; no bird's-eye or drone."
)

SCENE_DESCRIPTIONS = [
    (
        "@image1 — герой видео в светло-серой куртке в foyer резко поворачивается к камере, "
        "раскрывает широкую birthday-улыбку и делает шаг навстречу, камера handheld следует за ним; "
        "match cut на смех @image2 — тот же герой в профиле поднимает взгляд и смеётся, как услышав старую шутку."
    ),
    (
        "@image4 — герой и супруга в солнечном лесу: он делает театральную «серьёзную» мину, "
        "она взрывается смехом и наклоняется к нему, камера whip-pan по их лицам; "
        "snap cut @image3 — молодой герой в центре, две женщины хлопают его по плечам в том же ритме смеха."
    ),
    (
        "@image6 — герой среди друзей у моря на закате жестикулирует как ведущий КВN, "
        "друзья отвечают громким смехом, камера stabilized tracking по кругу; "
        "match cut on clap @image5 — у ёлки дочь крутится в серебристом платье, герой в полоску хлопает в ладоши в такт."
    ),
    (
        "@image7 — герой с широкой улыбкой слегка подбрасывает смеющегося младенца на руках, "
        "камера следует за bounce; "
        "snap cut @image8 — герой в синей рубашке у праздничных огней имитирует дирижёра, супруга подхватывает жест и смеётся."
    ),
    (
        "@image9 — на детском коврике сын подбегает к герою, герой подхватывает его, "
        "супруга с младенцем поднимается в общий кадр, все пружинят в такт воображаемой birthday-песне; "
        "финальный snap push-in: герой указывает пальцем в камеру — «с праздником!», камера handheld."
    ),
]


def main() -> int:
    config_path = ROOT / "video_prompt_story_config_edik_fun.json"
    config = load_video_prompt_story_config(config_path)

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
        technical_preamble=TECHNICAL_PREAMBLE,
        scene_descriptions=list(SCENE_DESCRIPTIONS),
    )

    html_path, json_path, _ = default_story_output_paths(
        config.effective_output_dir,
        timestamp=datetime(2026, 6, 12, 15, 30, 0, tzinfo=timezone.utc),
        stem=config.story_output_stem,
    )
    latest_html = REGEN_DIR / f"{config.story_output_stem}_latest.html"
    latest_json = REGEN_DIR / f"{config.story_output_stem}_latest.json"

    write_story_draft_json(json_path, draft)
    write_story_html(html_path, draft)
    write_story_draft_json(latest_json, draft)
    write_story_html(latest_html, draft)

    composer_path = REGEN_DIR / "video_prompt_config_edik_birthday_fun.json"
    payload = story_draft_to_video_prompt_config(
        draft,
        model=config.model,
        output_dir=config.effective_output_dir,
        seedance_json=config.seedance_json,
        seedance_json_only=config.seedance_json_only,
        seedance_director_file=config.seedance_director_file,
    )
    write_video_prompt_config(composer_path, payload)

    print(f"HTML:     {latest_html}")
    print(f"JSON:     {latest_json}")
    print(f"Composer: {composer_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
