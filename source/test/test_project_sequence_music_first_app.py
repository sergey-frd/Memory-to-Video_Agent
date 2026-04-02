from __future__ import annotations

import gzip
import json
from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import escape

from PIL import Image

import main_sequence_music_first

from models.scene_analysis import SceneAnalysis
from utils.premiere_project import parse_premiere_project_sequence_visual_clips
from utils.project_sequence_reports_from_project import (
    derive_project_sequence_music_first_bundle_paths,
    derive_project_sequence_music_first_paths,
    write_project_sequence_music_first_bundle,
)


def test_parse_premiere_project_sequence_visual_clips_reads_generic_media_paths() -> None:
    root = Path("test_runtime") / f"project_sequence_visual_parse_{uuid4().hex}"
    media_paths = _create_sample_images(root)
    project_path = _write_sample_prproj(root, sequence_name="GenericVisualSequence", media_paths=media_paths)

    selected_sequence_name, clips = parse_premiere_project_sequence_visual_clips(
        project_path,
        "GenericVisualSequence",
    )

    assert selected_sequence_name == "GenericVisualSequence"
    assert len(clips) == 3
    assert clips[0].name == media_paths[0].name
    assert clips[0].source_path == str(media_paths[0])
    assert clips[1].track_index == 1
    assert clips[2].order_index == 3


def test_write_project_sequence_music_first_bundle_builds_json_and_music_report() -> None:
    root = Path("test_runtime") / f"project_sequence_music_first_{uuid4().hex}"
    media_paths = _create_sample_images(root)
    project_path = _write_sample_prproj(root, sequence_name="GenericVisualSequence", media_paths=media_paths)
    output_json, output_music = derive_project_sequence_music_first_paths(
        project_path=project_path,
        sequence_name="GenericVisualSequence",
        output_dir=root / "reports",
    )

    def fake_analyzer(image_path: Path, _model: str | None) -> SceneAnalysis:
        lowered = image_path.name.lower()
        if "travel" in lowered:
            return SceneAnalysis(
                summary="Семья гуляет по новому месту и смотрит на панораму.",
                people_count=3,
                background="Панорамный городской вид и открытая прогулочная зона.",
                shot_type="wide shot",
                main_action="walking together",
                mood=["тепло", "радость"],
                relationships=["семья"],
            )
        if "home" in lowered:
            return SceneAnalysis(
                summary="Спокойный домашний семейный портрет.",
                people_count=2,
                background="Домашний интерьер.",
                shot_type="medium shot",
                main_action="standing together",
                mood=["уют", "спокойствие"],
                relationships=["близкие"],
            )
        return SceneAnalysis(
            summary="Один человек в мягком портретном кадре.",
            people_count=1,
            background="Нейтральный фон.",
            shot_type="close-up",
            main_action="looking at camera",
            mood=["деликатность"],
            relationships=[],
        )

    written_json, written_music, written_structure, written_transition = write_project_sequence_music_first_bundle(
        project_path=project_path,
        sequence_name="GenericVisualSequence",
        output_json=output_json,
        output_music_txt=output_music,
        max_sampled_clips=3,
        analyzer=fake_analyzer,
    )

    payload = json.loads(written_json.read_text(encoding="utf-8"))
    report_text = written_music.read_text(encoding="utf-8")

    assert written_json == output_json
    assert written_music == output_music
    assert written_structure is None
    assert written_transition is None
    assert payload["mode"] == "project_sequence_music_first"
    assert payload["selected_sequence_name"] == "GenericVisualSequence"
    assert payload["total_sequence_clip_count"] == 3
    assert payload["sampled_clip_count"] == 3
    assert "МУЗЫКАЛЬНАЯ РЕКОМЕНДАЦИЯ ДЛЯ SEQUENCE" in report_text
    assert "Главный музыкальный вектор" in report_text
    assert "Рекомендуемая музыка" in report_text


def test_write_project_sequence_music_first_bundle_builds_structure_and_transition_reports() -> None:
    root = Path("test_runtime") / f"project_sequence_full_reports_{uuid4().hex}"
    media_paths = _create_sample_images(root)
    project_path = _write_sample_prproj(root, sequence_name="GenericVisualSequence", media_paths=media_paths)
    output_json, output_music, output_structure, output_transition = derive_project_sequence_music_first_bundle_paths(
        project_path=project_path,
        sequence_name="GenericVisualSequence",
        output_dir=root / "reports",
    )

    def fake_analyzer(image_path: Path, _model: str | None) -> SceneAnalysis:
        lowered = image_path.name.lower()
        if "travel" in lowered:
            return SceneAnalysis(
                summary="Ð¡ÐµÐ¼ÑŒÑ Ð³ÑƒÐ»ÑÐµÑ‚ Ð¿Ð¾ Ð½Ð¾Ð²Ð¾Ð¼Ñƒ Ð¼ÐµÑÑ‚Ñƒ Ð¸ ÑÐ¼Ð¾Ñ‚Ñ€Ð¸Ñ‚ Ð½Ð° Ð¿Ð°Ð½Ð¾Ñ€Ð°Ð¼Ñƒ.",
                people_count=3,
                background="ÐŸÐ°Ð½Ð¾Ñ€Ð°Ð¼Ð½Ñ‹Ð¹ Ð³Ð¾Ñ€Ð¾Ð´ÑÐºÐ¾Ð¹ Ð²Ð¸Ð´ Ð¸ Ð¾Ñ‚ÐºÑ€Ñ‹Ñ‚Ð°Ñ Ð¿Ñ€Ð¾Ð³ÑƒÐ»Ð¾Ñ‡Ð½Ð°Ñ Ð·Ð¾Ð½Ð°.",
                shot_type="wide shot",
                main_action="walking together",
                mood=["Ñ‚ÐµÐ¿Ð»Ð¾", "Ñ€Ð°Ð´Ð¾ÑÑ‚ÑŒ"],
                relationships=["ÑÐµÐ¼ÑŒÑ"],
            )
        if "home" in lowered:
            return SceneAnalysis(
                summary="Ð¡Ð¿Ð¾ÐºÐ¾Ð¹Ð½Ñ‹Ð¹ Ð´Ð¾Ð¼Ð°ÑˆÐ½Ð¸Ð¹ ÑÐµÐ¼ÐµÐ¹Ð½Ñ‹Ð¹ Ð¿Ð¾Ñ€Ñ‚Ñ€ÐµÑ‚.",
                people_count=2,
                background="Ð”Ð¾Ð¼Ð°ÑˆÐ½Ð¸Ð¹ Ð¸Ð½Ñ‚ÐµÑ€ÑŒÐµÑ€.",
                shot_type="medium shot",
                main_action="standing together",
                mood=["ÑƒÑŽÑ‚", "ÑÐ¿Ð¾ÐºÐ¾Ð¹ÑÑ‚Ð²Ð¸Ðµ"],
                relationships=["Ð±Ð»Ð¸Ð·ÐºÐ¸Ðµ"],
            )
        return SceneAnalysis(
            summary="ÐžÐ´Ð¸Ð½ Ñ‡ÐµÐ»Ð¾Ð²ÐµÐº Ð² Ð¼ÑÐ³ÐºÐ¾Ð¼ Ð¿Ð¾Ñ€Ñ‚Ñ€ÐµÑ‚Ð½Ð¾Ð¼ ÐºÐ°Ð´Ñ€Ðµ.",
            people_count=1,
            background="ÐÐµÐ¹Ñ‚Ñ€Ð°Ð»ÑŒÐ½Ñ‹Ð¹ Ñ„Ð¾Ð½.",
            shot_type="close-up",
            main_action="looking at camera",
            mood=["Ð´ÐµÐ»Ð¸ÐºÐ°Ñ‚Ð½Ð¾ÑÑ‚ÑŒ"],
            relationships=[],
        )

    written_json, written_music, written_structure, written_transition = write_project_sequence_music_first_bundle(
        project_path=project_path,
        sequence_name="GenericVisualSequence",
        output_json=output_json,
        output_music_txt=output_music,
        output_structure_txt=output_structure,
        output_transition_txt=output_transition,
        include_structure=True,
        include_transition=True,
        max_sampled_clips=3,
        analyzer=fake_analyzer,
    )

    payload = json.loads(written_json.read_text(encoding="utf-8"))
    structure_text = written_structure.read_text(encoding="utf-8")
    transition_text = written_transition.read_text(encoding="utf-8")

    assert written_music == output_music
    assert written_structure == output_structure
    assert written_transition == output_transition
    assert payload["sequence_recommendations_included"] is True
    assert len(payload["recommended_sequence_order"]) == 3
    assert "RECOMMENDED TRANSITIONS FOR THE PROPOSED SEQUENCE ORDER" in transition_text
    assert structure_text


def test_main_sequence_music_first_cli_prints_output_paths(monkeypatch, capsys) -> None:
    root = Path("test_runtime") / f"project_sequence_music_first_cli_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    output_json = root / "music_first.json"
    output_music = root / "music_first.txt"
    output_structure = root / "music_first_structure.txt"
    output_transition = root / "music_first_transition.txt"

    monkeypatch.setattr(
        "sys.argv",
        [
            "main_sequence_music_first.py",
            "--prproj",
            "project.prproj",
            "--sequence-name",
            "My Sequence",
        ],
    )
    monkeypatch.setattr(
        main_sequence_music_first,
        "derive_project_sequence_music_first_bundle_paths",
        lambda **_kwargs: (output_json, output_music, output_structure, output_transition),
    )

    def fake_write_bundle(**_kwargs):
        output_json.write_text("{}", encoding="utf-8")
        output_music.write_text("music", encoding="utf-8")
        return output_json, output_music, None, None

    monkeypatch.setattr(main_sequence_music_first, "write_project_sequence_music_first_bundle", fake_write_bundle)

    main_sequence_music_first.main()

    output = capsys.readouterr().out
    assert "Project-sequence JSON saved to:" in output
    assert "Music-first recommendation report saved to:" in output


def test_main_sequence_music_first_cli_prints_full_report_paths(monkeypatch, capsys) -> None:
    root = Path("test_runtime") / f"project_sequence_music_first_full_cli_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    output_json = root / "music_first.json"
    output_music = root / "music_first.txt"
    output_structure = root / "music_first_structure.txt"
    output_transition = root / "music_first_transition.txt"

    monkeypatch.setattr(
        "sys.argv",
        [
            "main_sequence_music_first.py",
            "--prproj",
            "project.prproj",
            "--sequence-name",
            "My Sequence",
            "--full-recommendations",
        ],
    )
    monkeypatch.setattr(
        main_sequence_music_first,
        "derive_project_sequence_music_first_bundle_paths",
        lambda **_kwargs: (output_json, output_music, output_structure, output_transition),
    )

    def fake_write_bundle(**_kwargs):
        output_json.write_text("{}", encoding="utf-8")
        output_music.write_text("music", encoding="utf-8")
        output_structure.write_text("structure", encoding="utf-8")
        output_transition.write_text("transition", encoding="utf-8")
        return output_json, output_music, output_structure, output_transition

    monkeypatch.setattr(main_sequence_music_first, "write_project_sequence_music_first_bundle", fake_write_bundle)

    main_sequence_music_first.main()

    output = capsys.readouterr().out
    assert "Recommended sequence/order report saved to:" in output
    assert "Transition recommendations report saved to:" in output


def _create_sample_images(root: Path) -> list[Path]:
    media_dir = root / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    assets = [
        ("travel_panorama.jpg", (220, 180, 120)),
        ("home_family.jpg", (160, 200, 210)),
        ("portrait_close.jpg", (180, 140, 190)),
    ]
    paths: list[Path] = []
    for name, color in assets:
        path = (media_dir / name).resolve()
        Image.new("RGB", (320, 240), color).save(path, format="JPEG")
        paths.append(path)
    return paths


def _write_sample_prproj(root: Path, *, sequence_name: str, media_paths: list[Path]) -> Path:
    sequence_refs: list[str] = []
    object_blocks: list[str] = []
    for index, media_path in enumerate(media_paths, start=1):
        track_item_id = 2000 + index
        subclip_id = 3000 + index
        clip_id = 4000 + index
        source_id = 5000 + index
        media_uid = f"media-{index}"
        start = (index - 1) * 100
        end = index * 100
        out_point = 50 + (index * 10)
        sequence_refs.append(f'<TrackItem ObjectRef="{track_item_id}" />')
        object_blocks.extend(
            [
                (
                    f'<VideoClipTrackItem ObjectID="{track_item_id}" ClassID="track" Version="1">'
                    f'<ClipTrackItem Version="1"><TrackItem Version="1"><Start>{start}</Start><End>{end}</End></TrackItem>'
                    f'<SubClip ObjectRef="{subclip_id}" /></ClipTrackItem></VideoClipTrackItem>'
                ),
                (
                    f'<SubClip ObjectID="{subclip_id}" ClassID="subclip" Version="1">'
                    f'<Clip ObjectRef="{clip_id}" /><Name>{escape(media_path.name)}</Name></SubClip>'
                ),
                (
                    f'<VideoClip ObjectID="{clip_id}" ClassID="clip" Version="1">'
                    f'<Clip Version="1"><Source ObjectRef="{source_id}" /><InPoint>0</InPoint><OutPoint>{out_point}</OutPoint></Clip></VideoClip>'
                ),
                (
                    f'<VideoMediaSource ObjectID="{source_id}" ClassID="source" Version="1">'
                    f'<MediaSource Version="1"><Media ObjectURef="{media_uid}" /></MediaSource></VideoMediaSource>'
                ),
                (
                    f'<Media ObjectUID="{media_uid}" ClassID="media" Version="1">'
                    f'<FilePath>{escape(str(media_path))}</FilePath>'
                    f'<ActualMediaFilePath>{escape(str(media_path))}</ActualMediaFilePath>'
                    f'<Title>{escape(media_path.name)}</Title></Media>'
                ),
            ]
        )

    xml_text = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<PremiereData Version="3">'
        f'<Sequence ObjectUID="sequence-1" ClassID="sequence" Version="1"><Name>{escape(sequence_name)}</Name>'
        '<TrackGroups>'
        '<TrackGroup Index="0"><Second ObjectRef="1000" /></TrackGroup>'
        '<TrackGroup Index="1"><Second ObjectRef="1001" /></TrackGroup>'
        '</TrackGroups></Sequence>'
        '<VideoTrackGroup ObjectID="1000" ClassID="video-group" Version="1">'
        '<TrackGroup Version="1"><Tracks Version="1"><Track Index="0" ObjectURef="video-track-1" /></Tracks></TrackGroup>'
        '</VideoTrackGroup>'
        '<AudioTrackGroup ObjectID="1001" ClassID="audio-group" Version="1">'
        '<TrackGroup Version="1"><Tracks Version="1"></Tracks></TrackGroup>'
        '</AudioTrackGroup>'
        '<Track ObjectUID="video-track-1" ClassID="track" Version="1">'
        '<ClipTrack Version="1"><ClipItems Version="1"><TrackItems Version="1">'
        + "".join(sequence_refs)
        + "</TrackItems></ClipItems></ClipTrack></Track>"
        + "".join(object_blocks)
        + "</PremiereData>"
    )

    project_path = root / "generic_sequence.prproj"
    project_path.write_bytes(gzip.compress(xml_text.encode("utf-8")))
    return project_path
