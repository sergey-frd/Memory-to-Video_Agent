import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4
import gzip
import xml.etree.ElementTree as ET

import pytest

from main_sequence_optimizer import Settings, run_project_sequence_optimizer, run_sequence_optimizer, run_sequence_optimizer_batch
from models.video_sequence import (
    ClipAssetBundle,
    PremiereSequenceClip,
    SequenceCandidate,
    SequenceOptimizationResult,
    SequenceRecommendationEntry,
)
from utils.current_sequence_reports import write_current_sequence_reports
from utils.fcp_translation_results import parse_fcp_translation_results
from utils.human_profile_sequence_report import (
    _describe_human_adjusted_soundtrack,
    build_human_profile_sequence_report,
    extract_human_profile_overlay,
    write_human_profile_sequence_report_from_json,
)
from utils.premiere_project import parse_premiere_project_sequence_clips, resolve_project_track_item_stage_id
from utils.project_sequence_batch import run_project_sequence_batch_from_config
from utils.premiere_xml import parse_premiere_sequence_clips
from utils.sequence_optimizer import optimize_sequence
from utils.sequence_optimizer_runtime import load_clip_asset_bundle
from utils.sequence_structure_report import (
    _build_profile_context,
    _count_fragment_hits,
    _derive_music_profile_tags,
    _select_soundtrack_references,
    _top_tokens,
    build_sequence_structure_report,
)
from utils.transition_recommendations import _select_recommended_transition_type, build_transition_recommendations_report


def test_parse_premiere_sequence_clips_selects_sequence_with_most_mp4() -> None:
    root, xml_path, regeneration_assets_dir = _build_sample_project()

    selected_sequence_name, clips = parse_premiere_sequence_clips(xml_path)

    assert root.exists()
    assert regeneration_assets_dir.exists()
    assert selected_sequence_name == "MainSequence"
    assert [clip.order_index for clip in clips] == [1, 2, 3]
    assert [clip.name for clip in clips] == [
        "party close_20260322_100001_video_1.mp4",
        "park morning_20260322_100002_video_1.mp4",
        "park smile_20260322_100003_video_1.mp4",
    ]


def test_optimize_sequence_prefers_establishing_then_related_clip() -> None:
    _root, xml_path, regeneration_assets_dir = _build_sample_project()
    selected_sequence_name, clips = parse_premiere_sequence_clips(xml_path)

    result = optimize_sequence(
        source_xml=xml_path,
        selected_sequence_name=selected_sequence_name,
        clips=clips,
        regeneration_assets_dir=regeneration_assets_dir,
    )

    assert [entry.original_index for entry in result.entries] == [2, 3, 1]
    assert "establishing" in result.entries[0].reason
    assert "shared context" in result.entries[1].reason


def test_run_sequence_optimizer_writes_json_and_text_report() -> None:
    _root, xml_path, regeneration_assets_dir = _build_sample_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_output_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)

    json_path, txt_path = run_sequence_optimizer(
        xml_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
    )

    json_payload = json.loads(json_path.read_text(encoding="utf-8"))
    txt_payload = txt_path.read_text(encoding="utf-8")

    assert json_payload["selected_sequence_name"] == "MainSequence"
    assert [entry["original_index"] for entry in json_payload["entries"]] == [2, 3, 1]
    assert "Recommended order" in txt_payload
    assert "1. Original V2: park morning_20260322_100002_video_1.mp4" in txt_payload


def test_load_clip_asset_bundle_falls_back_to_sibling_regeneration_assets_dir() -> None:
    root = Path("test_runtime") / f"sequence_optimizer_alt_assets_{uuid4().hex}"
    primary_assets_dir = root / "regeneration_assets"
    secondary_assets_dir = root / "regeneration_assets_2"
    primary_assets_dir.mkdir(parents=True, exist_ok=True)
    secondary_assets_dir.mkdir(parents=True, exist_ok=True)

    stage_id = "family_trip_20260322_100001"
    bundle_dir = secondary_assets_dir / stage_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / f"{stage_id}_scene_analysis.json").write_text(
        json.dumps(
            {
                "summary": "Семья гуляет вместе по японской деревне.",
                "background": "Традиционные дома и прогулочная улица.",
                "main_action": "walking together",
                "mood": ["тепло"],
                "relationships": ["семья"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (bundle_dir / f"{stage_id}_api_pipeline_manifest.json").write_text("{}", encoding="utf-8")
    (bundle_dir / f"{stage_id}_v_prompt_1.txt").write_text("family trip prompt", encoding="utf-8")

    clip = PremiereSequenceClip(
        sequence_name="MainSequence",
        order_index=1,
        track_index=1,
        clipitem_id="clip-1",
        name=f"{stage_id}_video_1.mp4",
        source_path=str(root / f"{stage_id}_video_1.mp4"),
        start=0,
        end=100,
        in_point=0,
        out_point=100,
        duration=100,
        stage_id=stage_id,
        video_index=1,
    )

    bundle = load_clip_asset_bundle(primary_assets_dir, clip)

    assert bundle.bundle_dir == str(bundle_dir)
    assert bundle.scene_analysis["summary"] == "Семья гуляет вместе по японской деревне."
    assert bundle.prompt_text == "family trip prompt"
    assert bundle.missing_files == []


def test_parse_fcp_translation_results_filters_selected_sequence_and_effects() -> None:
    _root, xml_path, _regeneration_assets_dir = _build_sample_project()
    report_path = _write_translation_results_report(
        xml_path,
        [
            "Synthetic Item (Black Video) not translated, Slug used as a placeholder.",
            "Sequence <MainSequence> at , video track 1: Effect <Gaussian Blur> on Clip <park morning_20260322_100002_bg_image_16x9.jpg> not translated.",
            "Sequence <MainSequence> at , video track 1: Effect <Grow> on Clip <park morning_20260322_100002_bg_image_16x9.jpg> not translated.",
            "Sequence <SecondarySequence> at , video track 2: Effect <Shrink> on Clip <party close_20260322_100001_bg_image_16x9.jpg> not translated.",
        ],
    )

    issues, warnings = parse_fcp_translation_results(report_path, selected_sequence_name="MainSequence")

    assert len(issues) == 2
    assert {issue.effect_name for issue in issues} == {"Gaussian Blur", "Grow"}
    assert all(issue.sequence_name == "MainSequence" for issue in issues)
    assert issues[0].stage_id == "park morning_20260322_100002"
    assert warnings == ["Synthetic Item (Black Video) not translated, Slug used as a placeholder."]


def test_parse_fcp_translation_results_rejects_binary_prin_path() -> None:
    root, xml_path, _regeneration_assets_dir = _build_sample_project()
    prin_path = root / f"{xml_path.stem}.prin"
    prin_path.write_bytes(b"\x1f\x8b\x08\x00binary prin payload")

    with pytest.raises(ValueError, match=r"FCP Translation Results\*\.txt"):
        parse_fcp_translation_results(prin_path, selected_sequence_name="MainSequence")


def test_run_sequence_optimizer_includes_lost_effect_clips_from_translation_report() -> None:
    _root, xml_path, regeneration_assets_dir = _build_sample_project()
    report_path = _write_translation_results_report(
        xml_path,
        [
            "Sequence <MainSequence> at , video track 1: Effect <Gaussian Blur> on Clip <park morning_20260322_100002_bg_image_16x9.jpg> not translated.",
            "Sequence <MainSequence> at , video track 1: Effect <Grow> on Clip <park morning_20260322_100002_bg_image_16x9.jpg> not translated.",
            "Sequence <MainSequence> at , video track 2: Effect <Shrink> on Clip <park morning_20260322_100002_bg_image_16x9.jpg> not translated.",
            "Synthetic Item (Black Video) not translated, Slug used as a placeholder.",
        ],
    )
    output_root = Path("test_runtime") / f"sequence_optimizer_translation_results_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)

    json_path, txt_path = run_sequence_optimizer(
        xml_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
    )

    json_payload = json.loads(json_path.read_text(encoding="utf-8"))
    txt_payload = txt_path.read_text(encoding="utf-8")
    summary = json_payload["clips_with_lost_effects"][0]

    assert json_payload["translation_report_path"] == str(report_path)
    assert summary["clip_name"] == "park morning_20260322_100002_bg_image_16x9.jpg"
    assert summary["stage_id"] == "park morning_20260322_100002"
    assert summary["original_index"] == 2
    assert summary["recommended_index"] == 1
    assert summary["effect_names"] == ["Gaussian Blur", "Grow", "Shrink"]
    assert summary["track_locations"] == ["video 1", "video 2"]
    assert json_payload["translation_warnings"] == ["Synthetic Item (Black Video) not translated, Slug used as a placeholder."]
    assert "Clips with lost effects" in txt_payload
    assert "park morning_20260322_100002_bg_image_16x9.jpg" in txt_payload
    assert "Gaussian Blur, Grow, Shrink" in txt_payload


def test_run_sequence_optimizer_batch_writes_summary_with_success_and_error() -> None:
    root, _xml_path, regeneration_assets_dir = _build_sample_project()
    invalid_xml_path = root / "invalid_only_images.xml"
    invalid_xml_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="4">
  <sequence id="sequence-invalid">
    <name>ImagesOnly</name>
    <media>
      <video>
        <track>
          <clipitem id="clip-1">
            <name>still_frame.jpg</name>
            <start>0</start>
            <end>10</end>
            <in>0</in>
            <out>10</out>
            <duration>10</duration>
            <file id="file-1">
              <name>still_frame.jpg</name>
              <pathurl>file://localhost/E%3a/temp/still_frame.jpg</pathurl>
            </file>
          </clipitem>
        </track>
      </video>
    </media>
  </sequence>
</xmeml>
""",
        encoding="utf-8",
    )

    output_root = Path("test_runtime") / f"sequence_optimizer_batch_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)

    json_path, txt_path = run_sequence_optimizer_batch(
        root,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "batch.json",
        output_txt=output_root / "batch.txt",
    )

    json_payload = json.loads(json_path.read_text(encoding="utf-8"))
    txt_payload = txt_path.read_text(encoding="utf-8")

    assert json_payload["processed"] == 2
    assert json_payload["succeeded"] == 1
    assert json_payload["failed"] == 1
    assert any(item["status"] == "ok" for item in json_payload["items"])
    assert any(item["status"] == "error" for item in json_payload["items"])
    assert "Successful runs" in txt_payload
    assert "Failed runs" in txt_payload


def test_optimize_sequence_prioritizes_youngest_main_character_for_opening() -> None:
    _root, xml_path, regeneration_assets_dir = _build_main_character_priority_project()
    selected_sequence_name, clips = parse_premiere_sequence_clips(xml_path)

    result = optimize_sequence(
        source_xml=xml_path,
        selected_sequence_name=selected_sequence_name,
        clips=clips,
        regeneration_assets_dir=regeneration_assets_dir,
    )

    assert [entry.original_index for entry in result.entries[:2]] == [2, 1]
    assert "youngest child" in result.entries[0].reason


def test_run_sequence_optimizer_can_export_reordered_premiere_xml() -> None:
    _root, xml_path, regeneration_assets_dir = _build_sample_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_xml_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_xml = output_root / "optimized_sequence.xml"

    run_sequence_optimizer(
        xml_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_xml=output_xml,
    )

    tree = ET.parse(output_xml)
    root = tree.getroot()
    sequence_node = None
    for node in root.findall(".//sequence"):
        if node.findtext("./name") == "MainSequence_optimized":
            sequence_node = node
            break

    assert sequence_node is not None
    video_tracks = sequence_node.findall("./media/video/track")
    assert [clip.findtext("./name") for clip in video_tracks[1].findall("./clipitem")] == [
        "park morning_20260322_100002_video_1.mp4",
        "park smile_20260322_100003_video_1.mp4",
        "party close_20260322_100001_video_1.mp4",
    ]
    assert [clip.findtext("./name") for clip in video_tracks[0].findall("./clipitem")] == [
        "park morning_20260322_100002_bg_image_16x9.jpg",
        "park smile_20260322_100003_bg_image_16x9.jpg",
        "party close_20260322_100001_bg_image_16x9.jpg",
    ]


def test_optimize_sequence_prefers_forward_age_progression_over_age_regression() -> None:
    _root, xml_path, regeneration_assets_dir = _build_age_progression_project()
    selected_sequence_name, clips = parse_premiere_sequence_clips(xml_path)

    result = optimize_sequence(
        source_xml=xml_path,
        selected_sequence_name=selected_sequence_name,
        clips=clips,
        regeneration_assets_dir=regeneration_assets_dir,
    )

    assert [entry.original_index for entry in result.entries] == [1, 3, 4, 2]
    assert "keeps age progression moving forward" in result.entries[1].reason


def test_optimize_sequence_subject_series_grouping_is_temporarily_disabled() -> None:
    _root, xml_path, regeneration_assets_dir = _build_subject_series_project()
    selected_sequence_name, clips = parse_premiere_sequence_clips(xml_path)

    result = optimize_sequence(
        source_xml=xml_path,
        selected_sequence_name=selected_sequence_name,
        clips=clips,
        regeneration_assets_dir=regeneration_assets_dir,
    )

    assert sorted(entry.original_index for entry in result.entries) == [1, 2, 3]
    assert all(
        "same subject" not in entry.reason and "same person or object" not in entry.reason
        for entry in result.entries
    )


def test_optimize_sequence_can_enable_subject_series_grouping() -> None:
    _root, xml_path, regeneration_assets_dir = _build_subject_series_project()
    selected_sequence_name, clips = parse_premiere_sequence_clips(xml_path)

    result = optimize_sequence(
        source_xml=xml_path,
        selected_sequence_name=selected_sequence_name,
        clips=clips,
        regeneration_assets_dir=regeneration_assets_dir,
        enable_subject_series_grouping=True,
    )

    assert [entry.original_index for entry in result.entries] == [1, 3, 2]
    assert (
        "same subject" in result.entries[1].reason
        or "same person or object" in result.entries[1].reason
    )


def test_run_sequence_optimizer_preserves_background_filter_payloads() -> None:
    _root, xml_path, regeneration_assets_dir = _build_sample_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_filter_fidelity_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_xml = output_root / "optimized_sequence.xml"

    run_sequence_optimizer(
        xml_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_xml=output_xml,
    )

    clip_name = "park morning_20260322_100002_bg_image_16x9.jpg"
    source_filters = _serialized_filters(xml_path, "MainSequence", 0, clip_name)
    output_filters = _serialized_filters(output_xml, "MainSequence_optimized", 0, clip_name)

    assert source_filters == output_filters


def test_run_sequence_optimizer_hydrates_external_file_definitions_in_exported_sequence() -> None:
    _root, xml_path, regeneration_assets_dir = _build_sample_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_hydrated_files_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_xml = output_root / "optimized_sequence.xml"

    run_sequence_optimizer(
        xml_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_xml=output_xml,
    )

    root = ET.parse(output_xml).getroot()
    sequence_node = next(
        node for node in root.findall(".//sequence") if node.findtext("./name") == "MainSequence_optimized"
    )
    hydrated_files = [
        file_node
        for file_node in sequence_node.findall(".//file")
        if file_node.attrib.get("id") == "file-3" and list(file_node)
    ]

    assert hydrated_files
    assert hydrated_files[0].findtext("./name") == "park smile_20260322_100003_video_1.mp4"
    assert hydrated_files[0].findtext("./pathurl", "").endswith("/master_file_three.mp4")


def test_run_sequence_optimizer_preserves_shared_file_references_in_exported_xml() -> None:
    _root, xml_path, regeneration_assets_dir = _build_shared_file_reference_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_shared_refs_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_xml = output_root / "optimized_sequence.xml"

    run_sequence_optimizer(
        xml_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_xml=output_xml,
    )

    source_counts = _collect_sequence_file_id_counts(xml_path, "SharedReferenceSequence")
    output_counts = _collect_sequence_file_id_counts(output_xml, "SharedReferenceSequence_optimized")

    assert source_counts == output_counts
    assert output_counts["bg-file-1"] == 2
    assert output_counts["bg-file-2"] == 2


def test_parse_premiere_project_sequence_clips_selects_sequence_with_most_mp4() -> None:
    _root, project_path, _xml_path, _regeneration_assets_dir = _build_sample_premiere_project()

    selected_sequence_name, clips = parse_premiere_project_sequence_clips(project_path)

    assert selected_sequence_name == "MainProjectSequence"
    assert [clip.order_index for clip in clips] == [1, 2, 3]
    assert [clip.name for clip in clips] == [
        "party close_20260322_100001_video_1.mp4",
        "park morning_20260322_100002_video_1.mp4",
        "park smile_20260322_100003_video_1.mp4",
    ]
    assert clips[0].in_point == 0
    assert clips[0].out_point == 120


def test_parse_premiere_project_sequence_clips_prefers_primary_mp4_track_over_duplicate_mp4s() -> None:
    _root, project_path = _build_duplicate_mp4_priority_project()

    selected_sequence_name, clips = parse_premiere_project_sequence_clips(project_path)

    assert selected_sequence_name == "DuplicatePrioritySequence"
    assert [clip.name for clip in clips] == [
        "alpha_20260323_010001_video_1.mp4",
        "beta_20260323_010002_video_1.mp4",
    ]
    assert all(clip.track_index == 3 for clip in clips)


def test_run_project_sequence_optimizer_exports_optimized_prproj_with_background_payloads() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
    )

    selected_sequence_name, clips = parse_premiere_project_sequence_clips(output_project)
    assert selected_sequence_name == "MainProjectSequence"
    assert [clip.name for clip in clips] == [
        "park morning_20260322_100002_video_1.mp4",
        "park smile_20260322_100003_video_1.mp4",
        "party close_20260322_100001_video_1.mp4",
    ]

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in output_root_xml.iter()
        if node.attrib.get("ObjectID")
    }
    bg_track_item = object_lookup["2011"]
    assert bg_track_item.findtext("./DebugEffect") == "bg-stage-park-morning"
    assert bg_track_item.findtext("./ClipTrackItem/TrackItem/Start") == "0"
    assert bg_track_item.findtext("./ClipTrackItem/TrackItem/End") == "120"


def test_run_project_sequence_optimizer_writes_russian_structure_report() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_structure_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_txt = output_root / "sequence.txt"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_txt,
        output_prproj=output_root / "optimized_sequence.prproj",
    )

    structure_report_path = output_root / "sequence_structure.txt"
    structure_text = structure_report_path.read_text(encoding="utf-8")

    assert structure_report_path.exists()
    assert "ОПТИМАЛЬНАЯ СТРУКТУРА ВИДЕОКЛИПА" in structure_text
    assert "Описание видеоролика" in structure_text
    assert "Основная тема:" in structure_text
    assert "Рекомендуемая музыка" in structure_text
    assert "Главный приоритет среди всех вариантов" in structure_text
    assert "Вариант с самым высоким приоритетом:" in structure_text
    assert "по 5 вариантов" in structure_text
    assert "1. Не из мировой классической музыки" in structure_text
    assert "2. Из мировой классической музыки" in structure_text
    assert "3. Из джаза" in structure_text
    music_section = structure_text.split("Рекомендуемая музыка", 1)[1].split("Каркас ролика", 1)[0]
    assert music_section.count("Вариант с самым высоким приоритетом:") == 1
    assert music_section.count("\n- ") == 15
    assert "1. Вход" in structure_text
    assert "6. Послевкусие" in structure_text
    assert "Музыкальный акцент:" in structure_text
    assert "Переход к следующему блоку:" in structure_text


def test_sequence_structure_report_places_main_theme_before_brief_description() -> None:
    entries = [
        _make_structure_entry(
            clip_name="family_archive_video_1.mp4",
            summary="Семейный архивный кадр с тёплой домашней атмосферой.",
            subject_tokens=["ребенок", "семья"],
            appearance_tokens=["архив", "дом"],
            mood=["тепло", "память"],
            relationships=["семья"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="family_garden_video_1.mp4",
            summary="Семья гуляет в саду и улыбается.",
            subject_tokens=["семья"],
            appearance_tokens=["улица"],
            mood=["светло", "радость"],
            relationships=["семья"],
            people_count=3,
        ),
    ]
    for index, entry in enumerate(entries, start=1):
        entry.recommended_index = index

    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("manual_order_source.prproj"),
            selected_sequence_name="ManualOrderSequence",
            entries=entries,
        )
    )
    description_section = structure_text.split("Описание видеоролика", 1)[1].split("Рекомендуемая музыка", 1)[0]
    description_lines = [line.strip() for line in description_section.splitlines() if line.strip()]

    assert description_lines[0].startswith("Основная тема:")
    assert description_lines[1].startswith("Краткое описание:")
    assert "\n\nКраткое описание:" in description_section


def test_write_current_sequence_reports_can_rebuild_reports_from_manual_sequence_order() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_manual_reports_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    optimization_json = output_root / "sequence.json"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=optimization_json,
        output_txt=output_root / "sequence.txt",
    )

    rebuilt_json, rebuilt_structure_txt, rebuilt_transition_txt = write_current_sequence_reports(
        project_path=project_path,
        sequence_name="MainProjectSequence",
        optimization_report_json=optimization_json,
        output_json=output_root / "manual_order.json",
        output_structure_txt=output_root / "manual_order_structure.txt",
        output_transition_txt=output_root / "manual_order_transition_recommendations.txt",
    )

    rebuilt_payload = json.loads(rebuilt_json.read_text(encoding="utf-8"))
    rebuilt_structure = rebuilt_structure_txt.read_text(encoding="utf-8")
    rebuilt_transitions = rebuilt_transition_txt.read_text(encoding="utf-8")

    assert [entry["candidate"]["clip"]["name"] for entry in rebuilt_payload["entries"]] == [
        "party close_20260322_100001_video_1.mp4",
        "park morning_20260322_100002_video_1.mp4",
        "park smile_20260322_100003_video_1.mp4",
    ]
    assert [entry["recommended_index"] for entry in rebuilt_payload["entries"]] == [1, 2, 3]
    assert "Reports rebuilt from the current manual order in the Premiere sequence." in rebuilt_payload["warnings"]
    assert "Основная тема:" in rebuilt_structure
    assert "party close_20260322_100001_video_1.mp4 -> park morning_20260322_100002_video_1.mp4" in rebuilt_transitions


def test_human_profile_report_merges_video_story_with_human_preferences() -> None:
    entries = [
        _make_structure_entry(
            clip_name="maya_trip_walk_video_1.mp4",
            summary="Семья гуляет вне дома, двигается по маршруту и улыбается.",
            subject_tokens=["семья"],
            appearance_tokens=["улица"],
            mood=["тепло", "радость"],
            relationships=["семья"],
            people_count=3,
            energy_level=1,
        ),
        _make_structure_entry(
            clip_name="maya_travel_stop_video_1.mp4",
            summary="Героиня семьи стоит на террасе во время поездки, в кадре видны воздух и пространство.",
            subject_tokens=["взрослая женщина"],
            appearance_tokens=["терраса"],
            mood=["светло"],
            relationships=["родственники"],
            people_count=2,
            energy_level=1,
        ),
    ]
    for index, entry in enumerate(entries, start=1):
        entry.recommended_index = index

    report_text = build_human_profile_sequence_report(
        SimpleNamespace(
            source_xml=Path("maya_source.prproj"),
            selected_sequence_name="Maya26_e03",
            entries=entries,
        ),
        human_detail_text=(
            "Майя любит напряжённые и сложные туристические походы, любит заграничные поездки, "
            "любит лёгкую Популярную музыку, любит веселиться и хорошо танцует, "
            "помогает близким и спокойно выслушивает собеседников."
        ),
        human_detail_path=Path("maya_detail.txt"),
        optimization_report_json=Path("01_Maya26_o03.json"),
    )

    assert "Что видно из видео" in report_text
    assert "Что добавлено человеком" in report_text
    assert "Новый объединенный репорт" in report_text
    assert "популяр" in report_text.lower()
    assert "музык" in report_text.lower()
    assert "поход" in report_text.lower()
    assert "Корректировка музыкальных рекомендаций" in report_text
    assert "Что не стоит превращать в факт видеоряда" in report_text


def test_human_profile_report_detects_cultural_classical_preferences() -> None:
    entries = [
        _make_structure_entry(
            clip_name="vika_stage_japan_video_1.mp4",
            summary="Взрослая женщина проходит по японской улице и рассматривает культурные места во время поездки.",
            subject_tokens=["взрослая женщина"],
            appearance_tokens=["улица", "город"],
            mood=["светло", "интерес"],
            relationships=[],
            people_count=1,
            energy_level=1,
        ),
    ]
    entries[0].recommended_index = 1

    human_detail_text = (
        "Вика получила образование в музыкальной школе, обожает театр во всех его проявлениях: "
        "оперы, балеты, симфонические оркестры, камерные концерты, регулярно посещает культурные мероприятия."
    )
    overlay = extract_human_profile_overlay(human_detail_text)
    reason = _describe_human_adjusted_soundtrack(("elegant", "cultural", "flow"), overlay)
    report_text = build_human_profile_sequence_report(
        SimpleNamespace(
            source_xml=Path("vika_source.prproj"),
            selected_sequence_name="Vika26_e04",
            entries=entries,
        ),
        human_detail_text=human_detail_text,
        human_detail_path=Path("vika_detail.txt"),
        optimization_report_json=Path("01_Vika26_e04.json"),
    )

    assert "cultural_classical_sensibility" in overlay.matched_keys
    assert "театр" in reason.lower()
    assert "изящн" in report_text.lower()
    assert "камерно-классическ" in report_text.lower()
    assert "культурн" in report_text.lower()


def test_write_human_profile_sequence_report_from_json_writes_default_named_file() -> None:
    output_root = Path("test_runtime") / f"human_profile_report_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    report_json = output_root / "01_Maya26_o03.json"
    detail_path = output_root / "maya_detail.txt"
    detail_path.write_text(
        "Майя любит лёгкую Популярную музыку, заграничные поездки и веселье.",
        encoding="utf-8",
    )

    result = SequenceOptimizationResult(
        source_xml=str(Path("maya_source.prproj")),
        selected_sequence_name="Maya26_e03",
        engine_requested="heuristic",
        engine_used="heuristic",
        warnings=[],
        entries=[
            SequenceRecommendationEntry(
                recommended_index=1,
                original_index=1,
                score=1.0,
                reason="sample",
                candidate=SequenceCandidate(
                    clip=PremiereSequenceClip(
                        sequence_name="Maya26_e03",
                        order_index=1,
                        track_index=2,
                        clipitem_id="clip-1",
                        name="maya_trip_video_1.mp4",
                        source_path="",
                        start=0,
                        end=120,
                        in_point=0,
                        out_point=120,
                        duration=120,
                        stage_id="maya_trip_stage",
                        video_index=1,
                    ),
                    assets=ClipAssetBundle(
                        stage_id="maya_trip_stage",
                        bundle_dir=str(output_root),
                        scene_analysis={
                            "summary": "Семья гуляет во время поездки и улыбается.",
                            "background": "",
                            "shot_type": "",
                            "main_action": "",
                            "mood": ["светло"],
                            "relationships": ["семья"],
                        },
                    ),
                    keywords=[],
                    people_count=2,
                    shot_scale=1,
                    energy_level=1,
                    series_subject_tokens=["семья"],
                    series_appearance_tokens=["улица"],
                    series_pose_tokens=[],
                ),
            )
        ],
    )
    report_json.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    output_path = write_human_profile_sequence_report_from_json(
        optimization_report_json=report_json,
        human_detail_txt=detail_path,
    )
    report_text = output_path.read_text(encoding="utf-8")

    assert output_path == output_root / "01_Maya26_o03_human_profile_report.txt"
    assert "Источник human-detail:" in report_text
    assert "популяр" in report_text.lower()
    assert "музык" in report_text.lower()


def test_sequence_structure_report_fragment_matching_avoids_substring_false_positives() -> None:
    phrases = [
        "Nicole Steele getting perm",
        "character study of a girl indoors",
        "pumpkin on the table",
        "little girl in pink dress",
        "Ready to go to the Mother's Day tea",
    ]

    assert _count_fragment_hits(phrases, ("care",)) == 0
    assert _count_fragment_hits(phrases, ("pan",)) == 0
    assert _count_fragment_hits(phrases, ("tea",)) == 1
    assert _count_fragment_hits(phrases, ("dress",)) == 1


def test_sequence_structure_report_filters_generic_series_tokens() -> None:
    assert _top_tokens(["возможно", "девочка", "главный", "девочка", "персонаж", "ребенок"]) == [
        "девочка",
        "ребенок",
    ]


def test_sequence_structure_report_childhood_profile_does_not_turn_into_travel() -> None:
    entries = [
        _make_structure_entry(
            clip_name="Ready to go to the Mother's Day tea_20260322_191013_video_1.mp4",
            summary="Маленькая девочка в розовом платье стоит дома и улыбается перед выходом.",
            subject_tokens=["девочка", "главный", "возможно"],
            appearance_tokens=["платье", "ребенок", "главный"],
            main_character_notes=["features a young child who can anchor the story"],
        ),
        _make_structure_entry(
            clip_name="Nicole Steele learning to ride bike_20260322_184644_video_1.mp4",
            summary="Маленькая девочка стоит рядом с велосипедом на дорожке во дворе дома.",
            subject_tokens=["девочка", "ребенок"],
            appearance_tokens=["белые", "джинсы", "ребенок"],
            mood=["радость"],
            relationships=["ребёнок один в кадре"],
            main_character_notes=["keeps the youngest child near the front of the story"],
        ),
        _make_structure_entry(
            clip_name="Night before Xmas positions_20260322_184849_video_1.mp4",
            summary="Две девочки лежат в кровати рядом с мягкими игрушками и улыбаются.",
            subject_tokens=["девочка", "сестра"],
            appearance_tokens=["пижама", "ребенок"],
            mood=["уют", "домашнее тепло"],
            main_character_notes=["features a young child who can anchor the story"],
        ),
    ]

    _metrics, profile_tags, story_mode = _build_profile_context(entries)
    non_classical_titles = {
        option.title for option in _select_soundtrack_references("non_classical", profile_tags, story_mode)
    }

    assert "childhood" in profile_tags
    assert "warm" in profile_tags or "playful" in profile_tags
    assert story_mode in {"festive_childhood", "childhood_album"}
    assert not ({"travel", "cultural_travel", "leisure_travel", "family_trip"} & profile_tags)
    assert non_classical_titles <= {
        "Bless This Morning Year",
        "Your Hand in Mine",
        "Music for a Found Harmonium",
        "Hoppípolla",
        "Window",
    }
    assert not ({"Friday Morning", "Postcards From Italy", "La Femme d'Argent"} & non_classical_titles)


def test_sequence_structure_report_archive_and_leisure_profiles_choose_different_music() -> None:
    archive_entries = [
        _make_structure_entry(
            clip_name="1959_Igor_col_20260315_201458_video_1.mp4",
            summary="Архивная семейная фотография ребёнка с матерью в домашнем интерьере.",
            subject_tokens=["ребенок", "мать"],
            appearance_tokens=["архив", "ретро"],
            mood=["спокойствие", "память"],
            relationships=["семья", "близкие отношения"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="1960_IgorIrinkaSosova_col_20260315_201655_video_1.mp4",
            summary="Семейный архивный портрет нескольких поколений.",
            subject_tokens=["бабушка", "ребенок"],
            appearance_tokens=["винтаж", "архив"],
            mood=["ностальгия"],
            relationships=["three generations"],
            people_count=3,
        ),
        _make_structure_entry(
            clip_name="1969_Slavyansk_20260315_204743_video_1.mp4",
            summary="Старый семейный снимок взрослого и ребёнка на улице.",
            subject_tokens=["ребенок", "женщина"],
            appearance_tokens=["старое фото"],
            mood=["тихий семейный момент"],
            relationships=["семья"],
            people_count=2,
        ),
    ]
    leisure_entries = [
        _make_structure_entry(
            clip_name="sea_terrace_evening_video_1.mp4",
            summary="Взрослая женщина отдыхает на террасе у моря и смотрит на берег.",
            subject_tokens=["женщина"],
            appearance_tokens=["платье"],
            mood=["спокойствие", "отдых"],
            relationships=["взрослый отдых"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="restaurant_walk_video_1.mp4",
            summary="Пара идёт по набережной между рестораном и причалом.",
            subject_tokens=["женщина", "мужчина"],
            appearance_tokens=["взрослые"],
            mood=["relax"],
            relationships=["взрослая пара"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="boat_sea_trip_video_1.mp4",
            summary="Морская прогулка на лодке во время поездки.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["взрослые"],
            mood=["умиротворение"],
            relationships=["совместный отдых"],
            people_count=2,
        ),
    ]

    _archive_metrics, archive_tags, archive_mode = _build_profile_context(archive_entries)
    _leisure_metrics, leisure_tags, leisure_mode = _build_profile_context(leisure_entries)

    archive_non_classical = {
        option.title for option in _select_soundtrack_references("non_classical", archive_tags, archive_mode)
    }
    leisure_non_classical = {
        option.title for option in _select_soundtrack_references("non_classical", leisure_tags, leisure_mode)
    }
    archive_classical = {
        option.title for option in _select_soundtrack_references("classical", archive_tags, archive_mode)
    }
    leisure_classical = {
        option.title for option in _select_soundtrack_references("classical", leisure_tags, leisure_mode)
    }
    archive_jazz = {
        option.title for option in _select_soundtrack_references("jazz", archive_tags, archive_mode)
    }
    leisure_jazz = {
        option.title for option in _select_soundtrack_references("jazz", leisure_tags, leisure_mode)
    }

    assert archive_mode == "archive_family_memory"
    assert leisure_mode == "adult_leisure_escape"
    assert archive_non_classical <= {
        "Saman",
        "Steep Hills of Vicodin Tears",
        "Threnody",
        "Flight from the City",
        "I Can Almost See You",
    }
    assert leisure_non_classical <= {
        "Friday Morning",
        "A Walk",
        "La Femme d'Argent",
        "Two Thousand and Seventeen",
        "Window",
    }
    assert archive_classical <= {
        "Spiegel im Spiegel",
        "Gymnopédie No. 1",
        "Nimrod",
        "Pavane pour une infante défunte",
        "Nocturne in E-flat major, Op. 9 No. 2",
    }
    assert leisure_classical <= {
        "Sicilienne",
        "Arabesque No. 1",
        "Air on the G String",
        "Flower Duet",
        "Scene by the Brook",
    }
    assert archive_jazz <= {
        "Peace Piece",
        "Naima",
        "Night Lights",
        "Django",
        "Blue in Green",
    }
    assert leisure_jazz <= {
        "The Girl from Ipanema",
        "Poinciana",
        "Wave",
        "Bossa Antigua",
        "In a Sentimental Mood",
    }
    assert archive_non_classical.isdisjoint(leisure_non_classical)
    assert archive_classical.isdisjoint(leisure_classical)
    assert archive_jazz.isdisjoint(leisure_jazz)


def test_sequence_structure_report_travel_with_elderly_group_does_not_collapse_into_family_portrait() -> None:
    entries = [
        _make_structure_entry(
            clip_name="airport_selfie_video_1.mp4",
            summary="Пожилой мужчина делает селфи в самолёте во время поездки с группой людей.",
            subject_tokens=["мужчина", "турист"],
            appearance_tokens=["пожилой", "футболка"],
            pose_tokens=["самолёт", "поездка", "турист"],
            mood=["спокойствие"],
            relationships=["дорога", "путешествие"],
            people_count=11,
            shot_scale=2,
        ),
        _make_structure_entry(
            clip_name="airport_runway_video_1.mp4",
            summary="Взрослый мужчина ждёт у самолёта в аэропорту перед путешествием.",
            subject_tokens=["мужчина", "турист"],
            appearance_tokens=["пожилой", "кепка"],
            pose_tokens=["поездка", "ожидание"],
            mood=["quiet"],
            relationships=["дорога"],
            people_count=8,
        ),
        _make_structure_entry(
            clip_name="street_walk_video_1.mp4",
            summary="Пожилой турист спокойно идёт по улице и наслаждается прогулкой во время поездки.",
            subject_tokens=["мужчина", "турист"],
            appearance_tokens=["пожилой", "шорты"],
            pose_tokens=["прогулка", "отдых"],
            mood=["relax"],
            relationships=["улица"],
            people_count=6,
        ),
        _make_structure_entry(
            clip_name="lake_viewpoint_video_1.mp4",
            summary="Группа туристов смотрит на озеро и пейзаж во время путешествия.",
            subject_tokens=["мужчина", "фотограф"],
            appearance_tokens=["пожилой", "рубашка"],
            pose_tokens=["озеро", "пейзаж", "отдых"],
            mood=["спокойствие"],
            relationships=["outdoor", "берег", "озеро"],
            people_count=7,
        ),
        _make_structure_entry(
            clip_name="restaurant_evening_video_1.mp4",
            summary="Взрослый мужчина отдыхает у ресторана после прогулки во время поездки.",
            subject_tokens=["мужчина", "турист"],
            appearance_tokens=["пожилой", "футболка"],
            pose_tokens=["портрет", "отдых"],
            mood=["умиротворение"],
            relationships=["ресторан", "терраса"],
            people_count=2,
            shot_scale=2,
        ),
    ]

    _metrics, profile_tags, story_mode = _build_profile_context(entries)
    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("elder_travel_source.prproj"),
            selected_sequence_name="ElderTravelSequence",
            entries=entries,
        )
    )

    assert "group_family" in profile_tags
    assert "multi_generation" not in profile_tags
    assert story_mode == "adult_leisure_escape"
    assert "Основная тема: взрослая семейная встреча" not in structure_text
    assert "спокойная взрослая поездка-отдых" in structure_text


def test_sequence_structure_report_adult_portrait_does_not_assume_family_role() -> None:
    entries = [
        _make_structure_entry(
            clip_name="daylight_portrait_video_1.mp4",
            summary="Взрослая женщина спокойно смотрит в окно днём в светлой комнате.",
            subject_tokens=["женщина"],
            appearance_tokens=["светлая блузка"],
            mood=["спокойствие"],
            people_count=1,
            shot_scale=2,
        ),
        _make_structure_entry(
            clip_name="reading_chair_video_1.mp4",
            summary="Женщина сидит в кресле и читает книгу в тихой комнате.",
            subject_tokens=["женщина"],
            appearance_tokens=["взрослая"],
            mood=["quiet"],
            people_count=1,
            shot_scale=2,
        ),
    ]

    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("adult_portrait_source.prproj"),
            selected_sequence_name="AdultPortraitSequence",
            entries=entries,
        )
    )

    assert "семейная роль героя" not in structure_text
    assert "семейного окружения" not in structure_text


def test_sequence_structure_report_recurring_dogs_surface_in_theme_and_description() -> None:
    entries = [
        _make_structure_entry(
            clip_name="home_baby_dog_video_1.mp4",
            summary="Пожилой мужчина, младенец и белая собака находятся в уютной гостиной.",
            subject_tokens=["мужчина", "дедушка", "ребенок"],
            appearance_tokens=["пожилой", "дом"],
            pose_tokens=["собака", "младенец", "улыбка"],
            mood=["тепло", "уют"],
            relationships=["семья", "домашние животные"],
            people_count=2,
            shot_scale=2,
        ),
        _make_structure_entry(
            clip_name="family_balcony_dog_video_1.mp4",
            summary="Семья позирует на балконе рядом с большой белой собакой.",
            subject_tokens=["женщина", "родственник"],
            appearance_tokens=["семья", "портрет"],
            pose_tokens=["собака", "семейный", "фото"],
            mood=["радость", "семейное"],
            relationships=["семья", "собака"],
            people_count=5,
        ),
        _make_structure_entry(
            clip_name="living_room_dogs_video_1.mp4",
            summary="Два больших пса лежат рядом с людьми в домашней обстановке.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["домашний", "пожилой"],
            pose_tokens=["собаки", "отдых"],
            mood=["спокойствие"],
            relationships=["дом", "pets"],
            people_count=2,
        ),
    ]

    _metrics, profile_tags, _story_mode = _build_profile_context(entries)
    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("family_dogs_source.prproj"),
            selected_sequence_name="FamilyDogsSequence",
            entries=entries,
        )
    )

    assert "dogs" in profile_tags
    assert "домашние собаки" in structure_text
    assert "присутствие домашних собак" in structure_text
    assert "Повторяющийся живой мотив: домашние собаки" in structure_text


def test_sequence_structure_report_short_pet_words_do_not_match_inside_other_words() -> None:
    entries = [
        _make_structure_entry(
            clip_name="capturing_portrait_video_1.mp4",
            summary="A calm adult portrait indoors with the camera capturing a soft smile and character detail.",
            subject_tokens=["woman"],
            appearance_tokens=["indoors"],
            mood=["calm"],
            people_count=1,
            shot_scale=2,
        ),
        _make_structure_entry(
            clip_name="character_close_video_1.mp4",
            summary="Character study of an adult woman in a quiet room, capturing a reflective expression.",
            subject_tokens=["woman"],
            appearance_tokens=["neutral"],
            mood=["quiet"],
            people_count=1,
            shot_scale=2,
        ),
    ]

    _metrics, profile_tags, _story_mode = _build_profile_context(entries)
    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("false_cat_source.prproj"),
            selected_sequence_name="FalseCatSequence",
            entries=entries,
        )
    )

    assert "cats" not in profile_tags
    assert "кошк" not in structure_text.lower()


def test_sequence_structure_report_wedding_motif_surfaces_in_theme_and_description() -> None:
    entries = [
        _make_structure_entry(
            clip_name="wedding_bride_bouquet_video_1.mp4",
            summary="Невеста в белом платье держит букет рядом с женихом во время свадьбы.",
            subject_tokens=["невеста", "жених"],
            appearance_tokens=["платье", "костюм"],
            pose_tokens=["букет", "поцелуй"],
            mood=["романтика", "тепло"],
            relationships=["молодожёны"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="wedding_kiss_video_1.mp4",
            summary="Bride and groom kiss during the wedding ceremony while the bouquet stays in frame.",
            subject_tokens=["bride", "groom"],
            appearance_tokens=["formal"],
            pose_tokens=["kiss", "bouquet"],
            mood=["romantic"],
            relationships=["wedding couple"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="wedding_family_table_video_1.mp4",
            summary="Свадебная пара принимает поздравления за праздничным столом.",
            subject_tokens=["пара"],
            appearance_tokens=["праздник"],
            mood=["радость"],
            relationships=["свадьба"],
            people_count=4,
        ),
    ]

    _metrics, profile_tags, _story_mode = _build_profile_context(entries)
    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("wedding_source.prproj"),
            selected_sequence_name="WeddingSequence",
            entries=entries,
        )
    )

    assert "wedding" in profile_tags
    assert "свад" in structure_text.lower()
    assert "невест" in structure_text.lower()
    assert "жених" in structure_text.lower()


def test_sequence_structure_report_romantic_scene_without_wedding_terms_does_not_become_wedding() -> None:
    entries = [
        _make_structure_entry(
            clip_name="romantic_couple_video_1.mp4",
            summary="Романтическая пара обнимается и целуется в уютном помещении.",
            subject_tokens=["мужчина", "женщина"],
            pose_tokens=["поцелуй", "объятие"],
            mood=["романтика"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="soft_kiss_video_1.mp4",
            summary="A couple shares a gentle kiss indoors in a close portrait scene.",
            subject_tokens=["man", "woman"],
            pose_tokens=["kiss"],
            mood=["romantic"],
            people_count=2,
        ),
    ]

    _metrics, profile_tags, _story_mode = _build_profile_context(entries)
    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("romantic_non_wedding_source.prproj"),
            selected_sequence_name="RomanticNonWeddingSequence",
            entries=entries,
        )
    )

    assert "wedding" not in profile_tags
    assert "свад" not in structure_text.lower()
    assert "невест" not in structure_text.lower()
    assert "жених" not in structure_text.lower()


def test_sequence_structure_report_fishing_motif_surfaces_in_theme_and_description() -> None:
    entries = [
        _make_structure_entry(
            clip_name="river_fisherman_video_1.mp4",
            summary="Мужчина-рыбак держит крупную пойманную рыбу у воды.",
            subject_tokens=["рыбак"],
            appearance_tokens=["камуфляж"],
            pose_tokens=["рыба", "улов"],
            mood=["спокойствие"],
            relationships=["рыбалка"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="fisherman_with_pike_video_1.mp4",
            summary="Fisherman shows a large fish and a fresh catch near the river bank.",
            subject_tokens=["fisherman"],
            appearance_tokens=["outdoor"],
            pose_tokens=["fish", "catch"],
            mood=["quiet"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="bank_after_fishing_video_1.mp4",
            summary="После рыбалки мужчина сидит рядом с уловом и смотрит на воду.",
            subject_tokens=["мужчина"],
            appearance_tokens=["берег"],
            pose_tokens=["улов"],
            mood=["спокойствие"],
            relationships=["рыбалка"],
            people_count=1,
        ),
    ]

    _metrics, profile_tags, _story_mode = _build_profile_context(entries)
    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("fishing_source.prproj"),
            selected_sequence_name="FishingSequence",
            entries=entries,
        )
    )

    assert "fishing" in profile_tags
    assert "рыбал" in structure_text.lower()
    assert "рыбак" in structure_text.lower()
    assert "рыб" in structure_text.lower()


def test_sequence_structure_report_wedding_can_be_an_accent_inside_growth_story() -> None:
    entries = [
        _make_structure_entry(
            clip_name="child_room_video_1.mp4",
            summary="Маленький мальчик сидит дома и смотрит в камеру.",
            subject_tokens=["мальчик"],
            appearance_tokens=["ребенок"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="school_boy_video_1.mp4",
            summary="Подросток идёт по улице и улыбается.",
            subject_tokens=["мальчик"],
            appearance_tokens=["подросток"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="adult_portrait_video_1.mp4",
            summary="Взрослый мужчина спокойно стоит рядом с семьёй.",
            subject_tokens=["мужчина"],
            appearance_tokens=["взрослый"],
            relationships=["семья"],
            people_count=3,
        ),
        _make_structure_entry(
            clip_name="wedding_couple_video_1.mp4",
            summary="Жених и невеста стоят рядом во время свадьбы, невеста держит букет.",
            subject_tokens=["жених", "невеста"],
            appearance_tokens=["платье", "костюм"],
            pose_tokens=["букет"],
            relationships=["свадьба"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="wedding_kiss_video_1.mp4",
            summary="Bride and groom kiss during the wedding ceremony.",
            subject_tokens=["bride", "groom"],
            pose_tokens=["kiss"],
            people_count=2,
        ),
    ]

    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("growth_with_wedding_source.prproj"),
            selected_sequence_name="GrowthWithWeddingSequence",
            entries=entries,
        )
    )

    assert "взросления героя" in structure_text
    assert "свадеб" in structure_text.lower()
    assert "свадебная история, где в центре стоят жених, невеста" not in structure_text


def test_sequence_structure_report_fishing_can_be_an_accent_inside_broader_story() -> None:
    entries = [
        _make_structure_entry(
            clip_name="family_trip_video_1.mp4",
            summary="Семья едет по новому месту и смотрит на панораму.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["взрослые"],
            relationships=["семейная поездка"],
            people_count=3,
        ),
        _make_structure_entry(
            clip_name="holiday_table_video_1.mp4",
            summary="Несколько взрослых сидят за праздничным столом и улыбаются.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["праздник"],
            relationships=["семья"],
            people_count=5,
        ),
        _make_structure_entry(
            clip_name="walk_by_water_video_1.mp4",
            summary="Взрослый герой гуляет у воды во время поездки.",
            subject_tokens=["мужчина"],
            appearance_tokens=["взрослый"],
            relationships=["поездка"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="river_fish_video_1.mp4",
            summary="Рыбак держит крупную пойманную рыбу у воды.",
            subject_tokens=["рыбак"],
            pose_tokens=["рыба", "улов"],
            relationships=["рыбалка"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="fisherman_catch_video_1.mp4",
            summary="Fisherman shows a large fish after the catch near the river.",
            subject_tokens=["fisherman"],
            pose_tokens=["fish", "catch"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="group_rest_video_1.mp4",
            summary="Группа взрослых отдыхает на природе и разговаривает.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["отдых"],
            relationships=["семья"],
            people_count=4,
        ),
    ]

    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("broad_story_with_fishing_source.prproj"),
            selected_sequence_name="BroadStoryWithFishingSequence",
            entries=entries,
        )
    )

    assert "рыбацкая линия" in structure_text
    assert "линия рыбалки" in structure_text
    assert "история рыбалки, где в центре стоят рыбак, пойманная рыба" not in structure_text


def test_sequence_structure_report_travel_dominant_family_sequence_stays_travel_centered() -> None:
    entries = [
        _make_structure_entry(
            clip_name="plane_family_video_1.mp4",
            summary="Семья летит в самолете и улыбается перед поездкой.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["семья"],
            relationships=["семейная поездка"],
            people_count=4,
        ),
        _make_structure_entry(
            clip_name="desert_trip_video_1.mp4",
            summary="Семья гуляет по пустыне во время путешествия и делает селфи.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["взрослые"],
            relationships=["путешествие"],
            people_count=4,
        ),
        _make_structure_entry(
            clip_name="beach_trip_video_1.mp4",
            summary="Пара отдыхает у моря во время поездки.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["взрослые"],
            relationships=["поездка"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="mountain_selfie_video_1.mp4",
            summary="Два туриста делают селфи в горах во время путешествия.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["поход"],
            relationships=["путешествие"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="holiday_home_video_1.mp4",
            summary="Семья празднует дома и улыбается вместе.",
            subject_tokens=["мужчина", "женщина"],
            appearance_tokens=["праздник"],
            relationships=["семья"],
            people_count=5,
        ),
    ]

    _metrics, profile_tags, story_mode = _build_profile_context(entries)
    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("travel_family_source.prproj"),
            selected_sequence_name="TravelFamilySequence",
            entries=entries,
        )
    )

    assert "family_trip" in profile_tags
    assert story_mode == "family_outing"
    assert "семейная хроника прогулок" in structure_text or "совместные прогулки" in structure_text
    assert "взрослая семейная встреча" not in structure_text


def test_sequence_structure_report_cultural_travel_uses_dedicated_route_music_pool() -> None:
    entries = [
        _make_structure_entry(
            clip_name="temple_walk_video_1.mp4",
            summary="Женщина идёт по территории старого храма во время путешествия по Японии.",
            subject_tokens=["женщина"],
            appearance_tokens=["пальто"],
            mood=["интерес", "спокойствие"],
            relationships=["путешествие"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="castle_view_video_1.mp4",
            summary="Пара смотрит на старый замок и панораму города с моста.",
            subject_tokens=["женщина", "мужчина"],
            appearance_tokens=["взрослые"],
            mood=["вдохновение"],
            relationships=["совместная поездка"],
            people_count=2,
        ),
        _make_structure_entry(
            clip_name="village_route_video_1.mp4",
            summary="Путешественники проходят через деревню и парк с оленями по маршруту дня.",
            subject_tokens=["пара"],
            appearance_tokens=["взрослые"],
            mood=["оживление"],
            relationships=["культурный маршрут"],
            people_count=2,
        ),
    ]

    _metrics, profile_tags, story_mode = _build_profile_context(entries)
    non_classical_titles = {
        option.title for option in _select_soundtrack_references("non_classical", profile_tags, story_mode)
    }
    classical_titles = {
        option.title for option in _select_soundtrack_references("classical", profile_tags, story_mode)
    }
    jazz_titles = {
        option.title for option in _select_soundtrack_references("jazz", profile_tags, story_mode)
    }

    assert story_mode == "cultural_travel"
    assert non_classical_titles <= {
        "Postcards From Italy",
        "Arrival of the Birds",
        "Cirrus",
        "Porz Goret",
        "La Joya",
    }
    assert classical_titles <= {
        "The Moldau",
        "In the Steppes of Central Asia",
        "Morning Mood",
        "Arabesque No. 1",
        "The Lark Ascending",
    }
    assert jazz_titles <= {
        "Blue Rondo à la Turk",
        "Song for My Father",
        "Caravan",
        "Take Five",
        "Cantaloupe Island",
    }


def test_sequence_structure_report_adult_family_celebration_does_not_collapse_into_childhood() -> None:
    entries = [
        _make_structure_entry(
            clip_name="prom_girls_video_1.mp4",
            summary="Четыре девушки-подростка в вечерних платьях улыбаются на выпускном вечере.",
            subject_tokens=["женщина", "девушка"],
            appearance_tokens=["взрослая", "платье"],
            mood=["праздничный", "радостный"],
            relationships=["группа друзей"],
            people_count=6,
            main_character_notes=["prominently features the youngest child / infant"],
        ),
        _make_structure_entry(
            clip_name="family_living_room_video_1.mp4",
            summary="Взрослая женщина, пожилая родственница и молодая взрослая сидят вместе в гостиной и улыбаются.",
            subject_tokens=["женщина", "родственница"],
            appearance_tokens=["взрослая", "семья"],
            mood=["уют", "праздник"],
            relationships=["семья", "родственники"],
            people_count=4,
        ),
        _make_structure_entry(
            clip_name="mother_day_group_video_1.mp4",
            summary="Несколько взрослых женщин собрались на семейный праздник и позируют для фото.",
            subject_tokens=["женщина", "семья"],
            appearance_tokens=["взрослая", "наряд"],
            mood=["тепло", "праздничный"],
            relationships=["семья", "группа"],
            people_count=5,
        ),
    ]

    _metrics, profile_tags, story_mode = _build_profile_context(entries)
    non_classical_titles = {
        option.title for option in _select_soundtrack_references("non_classical", profile_tags, story_mode)
    }

    assert "childhood" not in profile_tags
    assert story_mode == "adult_family_portrait"
    assert non_classical_titles <= {
        "Arrival of the Birds",
        "La Femme d'Argent",
        "Bless This Morning Year",
        "Window",
        "Saman",
    }


def test_sequence_structure_report_adult_family_portrait_uses_stronger_block_language() -> None:
    entries = [
        _make_structure_entry(
            clip_name="prom_girls_video_1.mp4",
            summary="Четыре девушки-подростка в вечерних платьях улыбаются на выпускном вечере.",
            subject_tokens=["женщина", "девушка"],
            appearance_tokens=["взрослая", "платье"],
            mood=["праздничный", "радостный"],
            relationships=["группа друзей"],
            people_count=6,
            shot_scale=1,
            energy_level=1,
        ),
        _make_structure_entry(
            clip_name="family_living_room_video_1.mp4",
            summary="Взрослая женщина, пожилая родственница и молодая взрослая сидят вместе в гостиной и улыбаются.",
            subject_tokens=["женщина", "родственница"],
            appearance_tokens=["взрослая", "семья"],
            mood=["уют", "праздник"],
            relationships=["семья", "родственники"],
            people_count=4,
            shot_scale=2,
            energy_level=1,
        ),
        _make_structure_entry(
            clip_name="mother_day_group_video_1.mp4",
            summary="Несколько взрослых женщин собрались на семейный праздник и позируют для фото.",
            subject_tokens=["женщина", "семья"],
            appearance_tokens=["взрослая", "наряд"],
            mood=["тепло", "праздничный"],
            relationships=["семья", "группа"],
            people_count=5,
            shot_scale=2,
            energy_level=1,
        ),
    ]
    for index, entry in enumerate(entries, start=1):
        entry.recommended_index = index

    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("adult_family_portrait_source.prproj"),
            selected_sequence_name="AdultFamilyPortrait",
            entries=entries,
        )
    )

    assert "взрослый семейный портрет" in structure_text
    assert "семейные фигуры, осанка" in structure_text
    assert "без клиповой декоративности" in structure_text
    assert "без детской игривости" in structure_text
    assert "Избегать резких SFX поверх лиц, волос, украшений и ткани" in structure_text
    assert "женск" not in structure_text.lower()


def test_sequence_structure_report_adult_family_portrait_does_not_force_female_center_in_male_led_story() -> None:
    entries = [
        _make_structure_entry(
            clip_name="adult_man_family_table_video_1.mp4",
            summary="Взрослый мужчина сидит за столом рядом с родственниками во время семейной встречи.",
            subject_tokens=["мужчина", "родственник"],
            appearance_tokens=["взрослый", "семья"],
            mood=["тепло", "спокойствие"],
            relationships=["семья", "встреча"],
            people_count=4,
            shot_scale=2,
            energy_level=1,
        ),
        _make_structure_entry(
            clip_name="adult_man_with_relatives_video_1.mp4",
            summary="Мужчина общается с близкими разных поколений в домашней обстановке.",
            subject_tokens=["мужчина", "семья"],
            appearance_tokens=["взрослый", "дом"],
            mood=["уют"],
            relationships=["родственники", "дом"],
            people_count=5,
            shot_scale=2,
            energy_level=1,
        ),
        _make_structure_entry(
            clip_name="adult_man_portrait_family_video_1.mp4",
            summary="Взрослый мужчина позирует рядом с близкими для семейного портрета.",
            subject_tokens=["мужчина", "родственник"],
            appearance_tokens=["портрет", "семья"],
            mood=["радость"],
            relationships=["семья"],
            people_count=3,
            shot_scale=2,
            energy_level=1,
        ),
    ]

    structure_text = build_sequence_structure_report(
        SimpleNamespace(
            source_xml=Path("adult_family_portrait_male_led_source.prproj"),
            selected_sequence_name="AdultFamilyPortraitMaleLed",
            entries=entries,
        )
    )

    assert "женск" not in structure_text.lower()
    assert "женщин" not in structure_text.lower()
    assert "сем" in structure_text.lower()
    assert "мужчина" in structure_text.lower()


def test_sequence_structure_report_reflective_daytime_profile_does_not_force_night() -> None:
    entries = [
        _make_structure_entry(
            clip_name="daylight_portrait_video_1.mp4",
            summary="Взрослая женщина спокойно смотрит в окно днём в светлой комнате.",
            subject_tokens=["женщина"],
            appearance_tokens=["светлая блузка"],
            mood=["спокойствие", "созерцательность"],
            relationships=["один человек в кадре"],
            people_count=1,
        ),
        _make_structure_entry(
            clip_name="daylight_garden_video_1.mp4",
            summary="Мужчина стоит в саду при дневном свете и задумчиво смотрит вдаль.",
            subject_tokens=["мужчина"],
            appearance_tokens=["взрослый"],
            mood=["calm"],
            relationships=["один человек в кадре"],
            people_count=1,
        ),
    ]

    _metrics, profile_tags, _story_mode = _build_profile_context(entries)

    assert "reflective" in profile_tags
    assert "night" not in profile_tags


def test_run_project_sequence_optimizer_does_not_add_transitions_for_contiguous_clips() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_transition_template()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_transitions_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    track_main = next(
        node
        for node in output_root_xml.iter("VideoClipTrack")
        if node.attrib.get("ObjectUID") == "track-main"
    )
    transition_refs = track_main.findall("./ClipTrack/TransitionItems/TrackItems/TrackItem")

    assert transition_refs == []


def test_run_project_sequence_optimizer_can_enable_transitions_for_contiguous_clips() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_transition_template()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_transitions_enabled_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
        enable_auto_transitions=True,
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    transition_nodes = list(output_root_xml.iter("VideoTransitionTrackItem"))
    generated_nodes = [
        node
        for node in transition_nodes
        if node.findtext("./TransitionTrackItem/HasIncomingClip") == "true"
    ]

    assert len(generated_nodes) == 2
    generated_boundaries = sorted(
        int(node.findtext("./TransitionTrackItem/TrackItem/Start", "0"))
        for node in generated_nodes
    )
    assert generated_boundaries == [120, 240]
    assert {
        node.findtext("./TransitionTrackItem/Alignment", "")
        for node in generated_nodes
    } == {"0"}
    component_refs = [
        node.find("./VideoFilterComponent").attrib["ObjectRef"]
        for node in generated_nodes
        if node.find("./VideoFilterComponent") is not None
    ]
    assert len(component_refs) == len(generated_nodes)
    assert len(set(component_refs)) == len(generated_nodes)


def test_run_project_sequence_optimizer_enables_transitions_only_on_pure_mp4_track() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_transition_template()
    project_root = ET.fromstring(gzip.decompress(project_path.read_bytes()))

    mixed_track_subclip = next(
        node
        for node in project_root.iter("SubClip")
        if node.attrib.get("ObjectID") == "3010"
    )
    name_node = mixed_track_subclip.find("./Name")
    assert name_node is not None
    name_node.text = "party close_20260322_100001_video_1.mp4"
    project_path.write_bytes(gzip.compress(ET.tostring(project_root, encoding="utf-8", xml_declaration=True)))

    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_transitions_mixed_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
        enable_auto_transitions=True,
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    transition_nodes = [
        node
        for node in output_root_xml.iter("VideoTransitionTrackItem")
        if node.findtext("./TransitionTrackItem/HasIncomingClip") == "true"
    ]

    assert len(transition_nodes) == 2


def test_run_project_sequence_optimizer_skips_transitions_without_tail_handles() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_transition_template()
    project_root = ET.fromstring(gzip.decompress(project_path.read_bytes()))
    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in project_root.iter()
        if node.attrib.get("ObjectID")
    }

    for clip_object_id, source_object_id in (("4030", "7100"), ("4031", "7101"), ("4032", "7102")):
        clip_node = object_lookup[clip_object_id]
        clip_payload = clip_node.find("./Clip")
        assert clip_payload is not None
        ET.SubElement(clip_payload, "Source", {"ObjectRef": source_object_id})

    for source_object_id in ("7100", "7101", "7102"):
        media_source = ET.SubElement(
            project_root,
            "VideoMediaSource",
            {"ObjectID": source_object_id, "ClassID": "video-media-source", "Version": "1"},
        )
        ET.SubElement(media_source, "OriginalDuration").text = "123"

    project_path.write_bytes(gzip.compress(ET.tostring(project_root, encoding="utf-8", xml_declaration=True)))

    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_transitions_handles_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
        enable_auto_transitions=True,
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    transition_nodes = [
        node
        for node in output_root_xml.iter("VideoTransitionTrackItem")
        if node.findtext("./TransitionTrackItem/HasIncomingClip") == "true"
    ]

    assert transition_nodes == []


def test_run_project_sequence_optimizer_can_trim_clip_tails_to_create_transition_handles() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_transition_template()
    project_root = ET.fromstring(gzip.decompress(project_path.read_bytes()))
    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in project_root.iter()
        if node.attrib.get("ObjectID")
    }

    for clip_object_id, source_object_id in (("4030", "7200"), ("4031", "7201"), ("4032", "7202")):
        clip_node = object_lookup[clip_object_id]
        clip_payload = clip_node.find("./Clip")
        assert clip_payload is not None
        ET.SubElement(clip_payload, "Source", {"ObjectRef": source_object_id})

    for source_object_id in ("7200", "7201", "7202"):
        media_source = ET.SubElement(
            project_root,
            "VideoMediaSource",
            {"ObjectID": source_object_id, "ClassID": "video-media-source", "Version": "1"},
        )
        ET.SubElement(media_source, "OriginalDuration").text = "123"

    project_path.write_bytes(gzip.compress(ET.tostring(project_root, encoding="utf-8", xml_declaration=True)))

    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_transitions_trim_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
        enable_auto_transitions=True,
        allow_transition_handle_trimming=True,
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    transition_nodes = [
        node
        for node in output_root_xml.iter("VideoTransitionTrackItem")
        if node.findtext("./TransitionTrackItem/HasIncomingClip") == "true"
    ]

    assert len(transition_nodes) == 2

    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in output_root_xml.iter()
        if node.attrib.get("ObjectID")
    }
    track_main = next(
        node
        for node in output_root_xml.iter("VideoClipTrack")
        if node.attrib.get("ObjectUID") == "track-main"
    )
    track_refs = track_main.findall("./ClipTrack/ClipItems/TrackItems/TrackItem")
    starts = [
        int(object_lookup[ref.attrib["ObjectRef"]].findtext("./ClipTrackItem/TrackItem/Start", "0"))
        for ref in track_refs
    ]
    assert starts == [0, 6, 69]


def test_run_project_sequence_optimizer_preserves_stage_relative_offsets_for_full_coverage_track() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_main_track_gaps()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_gaps_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in output_root_xml.iter()
        if node.attrib.get("ObjectID")
    }
    track_main = next(
        node
        for node in output_root_xml.iter("VideoClipTrack")
        if node.attrib.get("ObjectUID") == "track-main"
    )
    track_refs = track_main.findall("./ClipTrack/ClipItems/TrackItems/TrackItem")
    ordered_track_items = [
        (
            resolve_project_track_item_stage_id(object_lookup[ref.attrib["ObjectRef"]], object_lookup),
            int(object_lookup[ref.attrib["ObjectRef"]].findtext("./ClipTrackItem/TrackItem/Start", "0")),
        )
        for ref in track_refs
    ]

    assert ordered_track_items == [
        ("park morning_20260322_100002", 40),
        ("park smile_20260322_100003", 260),
        ("party close_20260322_100001", 380),
    ]


def test_run_project_sequence_optimizer_preserves_initial_stage_offset_for_full_coverage_track() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = (
        _build_sample_premiere_project_with_main_track_initial_offset()
    )
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_offset_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in output_root_xml.iter()
        if node.attrib.get("ObjectID")
    }
    track_main = next(
        node
        for node in output_root_xml.iter("VideoClipTrack")
        if node.attrib.get("ObjectUID") == "track-main"
    )
    track_refs = track_main.findall("./ClipTrack/ClipItems/TrackItems/TrackItem")
    ordered_track_items = [
        (
            resolve_project_track_item_stage_id(object_lookup[ref.attrib["ObjectRef"]], object_lookup),
            int(object_lookup[ref.attrib["ObjectRef"]].findtext("./ClipTrackItem/TrackItem/Start", "0")),
        )
        for ref in track_refs
    ]
    sequence_node = next(
        node
        for node in output_root_xml.iter("Sequence")
        if node.findtext("./Name") == "MainProjectSequence"
    )

    assert ordered_track_items == [
        ("park morning_20260322_100002", 60),
        ("park smile_20260322_100003", 240),
        ("party close_20260322_100001", 420),
    ]
    assert sequence_node.findtext("./Node/Properties/MZ.WorkOutPoint") == "540"


def test_run_project_sequence_optimizer_preserves_group_offsets_across_related_tracks() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = (
        _build_sample_premiere_project_with_variable_group_offsets()
    )
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_group_offsets_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in output_root_xml.iter()
        if node.attrib.get("ObjectID")
    }

    actual_timelines = {
        object_id: (
            int(object_lookup[object_id].findtext("./ClipTrackItem/TrackItem/Start", "0")),
            int(object_lookup[object_id].findtext("./ClipTrackItem/TrackItem/End", "0")),
        )
        for object_id in ("2011", "2021", "2031", "2012", "2022", "2032", "2010", "2020", "2030")
    }

    assert actual_timelines == {
        "2011": (0, 120),
        "2021": (30, 100),
        "2031": (40, 110),
        "2012": (120, 240),
        "2022": (135, 215),
        "2032": (150, 230),
        "2010": (240, 360),
        "2020": (250, 330),
        "2030": (260, 340),
    }


def test_run_project_sequence_optimizer_can_add_optimized_sequence_copy() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_copy_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence_copy.prproj"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        sequence_name="MainProjectSequence",
        output_json=output_root / "sequence.json",
        output_txt=output_root / "sequence.txt",
        output_prproj=output_project,
        new_sequence_name="MainProjectSequence_optimized",
    )

    output_root_xml = ET.fromstring(gzip.decompress(output_project.read_bytes()))
    sequence_names = sorted(
        (node.findtext("./Name") or "").strip()
        for node in output_root_xml.iter("Sequence")
        if (node.findtext("./Name") or "").strip()
    )

    assert "MainProjectSequence" in sequence_names
    assert "MainProjectSequence_optimized" in sequence_names

    original_selected, original_clips = parse_premiere_project_sequence_clips(output_project, "MainProjectSequence")
    optimized_selected, optimized_clips = parse_premiere_project_sequence_clips(output_project, "MainProjectSequence_optimized")

    assert original_selected == "MainProjectSequence"
    assert [clip.name for clip in original_clips] == [
        "party close_20260322_100001_video_1.mp4",
        "park morning_20260322_100002_video_1.mp4",
        "park smile_20260322_100003_video_1.mp4",
    ]
    assert optimized_selected == "MainProjectSequence_optimized"
    assert [clip.name for clip in optimized_clips] == [
        "park morning_20260322_100002_video_1.mp4",
        "park smile_20260322_100003_video_1.mp4",
        "party close_20260322_100001_video_1.mp4",
    ]


def test_run_project_sequence_batch_from_config_uses_legacy_prin_path_as_translation_hint() -> None:
    root, project_path, xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_batch_legacy_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    config_path = output_root / "batch_config_legacy.json"
    output_project = output_root / "batch_output_legacy.prproj"
    reports_dir = output_root / "reports"
    prin_path = root / f"{project_path.stem}.prin"
    prin_path.write_bytes(b"\x1f\x8b\x08\x00legacy prin payload")
    report_path = _write_translation_results_report(
        xml_path,
        [
            "Sequence <MainProjectSequence> at , video track 1: Effect <Gaussian Blur> on Clip <park morning_20260322_100002_bg_image_16x9.jpg> not translated.",
        ],
    )

    config_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "prin_path": str(prin_path),
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "output_project_path": str(output_project),
                "reports_dir": str(reports_dir),
                "sequence_jobs": [
                    {
                        "source_sequence_name": "MainProjectSequence",
                        "new_sequence_name": "MainProjectSequence_optimized",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_json_path, _summary_txt_path = run_project_sequence_batch_from_config(config_path)

    summary_payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
    report_json_path = Path(str(summary_payload["sequence_jobs"][0]["report_json"]))
    job_payload = json.loads(report_json_path.read_text(encoding="utf-8"))

    assert job_payload["translation_report_path"] == str(report_path)
    assert job_payload["clips_with_lost_effects"][0]["clip_name"] == "park morning_20260322_100002_bg_image_16x9.jpg"


def test_run_project_sequence_batch_from_config_runs_and_writes_summary() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_batch_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    config_path = output_root / "batch_config.json"
    output_project = output_root / "batch_output.prproj"
    reports_dir = output_root / "reports"

    config_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "output_project_path": str(output_project),
                "reports_dir": str(reports_dir),
                "enable_auto_transitions": False,
                "enable_subject_series_grouping": False,
                "sequence_jobs": [
                    {
                        "source_sequence_name": "MainProjectSequence",
                        "new_sequence_name": "MainProjectSequence_optimized",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_json_path, summary_txt_path = run_project_sequence_batch_from_config(config_path)

    assert summary_json_path.exists()
    assert summary_txt_path.exists()
    summary_payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
    source_project_copy = project_path.parent / output_project.name
    reports_temp_project = Path(str(summary_payload["reports_output_project_path"]))
    assert summary_payload["configured_output_project_path"] == str(output_project)
    assert summary_payload["output_project_path"] == str(source_project_copy)
    assert summary_payload["source_project_output_project_path"] == str(source_project_copy)
    assert summary_payload["transition_mode"] == "disabled"
    assert summary_payload["enable_auto_transitions"] is False
    assert summary_payload["enable_subject_series_grouping"] is False
    assert summary_payload["generate_personalized_report"] is False
    assert summary_payload["human_detail_txt"] is None
    assert summary_payload["batch_transition_recommendations_txt"] is None
    assert not output_project.exists()
    assert source_project_copy.exists()
    assert reports_temp_project.exists()
    assert reports_temp_project.parent.name == "temp_projects"
    assert Path(str(summary_payload["sequence_jobs"][0]["structure_report_txt"])).exists()
    assert summary_payload["sequence_jobs"][0]["transition_recommendations_txt"] is None
    assert summary_payload["sequence_jobs"][0]["human_profile_report_txt"] is None
    assert summary_payload["sequence_jobs"][0]["new_sequence_name"] == "MainProjectSequence_optimized"

    selected, optimized_clips = parse_premiere_project_sequence_clips(source_project_copy, "MainProjectSequence_optimized")
    assert selected == "MainProjectSequence_optimized"
    assert [clip.name for clip in optimized_clips] == [
        "park morning_20260322_100002_video_1.mp4",
        "park smile_20260322_100003_video_1.mp4",
        "party close_20260322_100001_video_1.mp4",
    ]


def test_run_project_sequence_batch_from_config_can_generate_personalized_report_when_requested() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_batch_human_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    config_path = output_root / "batch_config_human.json"
    output_project = output_root / "batch_output_human.prproj"
    reports_dir = output_root / "reports"
    human_detail_path = output_root / "maya_detail.txt"
    human_detail_path.write_text(
        "Майя любит лёгкую Популярную музыку, заграничные поездки и любит веселиться.",
        encoding="utf-8",
    )

    config_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "output_project_path": str(output_project),
                "reports_dir": str(reports_dir),
                "generate_personalized_report": True,
                "human_detail_txt": str(human_detail_path),
                "sequence_jobs": [
                    {
                        "source_sequence_name": "MainProjectSequence",
                        "new_sequence_name": "MainProjectSequence_optimized",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_json_path, summary_txt_path = run_project_sequence_batch_from_config(config_path)

    summary_payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
    personalized_report_path = Path(str(summary_payload["sequence_jobs"][0]["human_profile_report_txt"]))
    personalized_report_text = personalized_report_path.read_text(encoding="utf-8")
    summary_text = summary_txt_path.read_text(encoding="utf-8")

    assert summary_payload["generate_personalized_report"] is True
    assert summary_payload["human_detail_txt"] == str(human_detail_path)
    assert personalized_report_path.exists()
    assert "Источник human-detail:" in personalized_report_text
    assert "Новый объединенный репорт" in personalized_report_text
    assert "Generate personalized report: True" in summary_text
    assert f"Human detail TXT: {human_detail_path}" in summary_text


def test_run_project_sequence_batch_from_config_personalized_report_requires_human_detail_txt() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_batch_human_missing_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    config_path = output_root / "batch_config_human_missing.json"
    output_project = output_root / "batch_output_human_missing.prproj"
    reports_dir = output_root / "reports"

    config_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "output_project_path": str(output_project),
                "reports_dir": str(reports_dir),
                "generate_personalized_report": True,
                "sequence_jobs": [
                    {
                        "source_sequence_name": "MainProjectSequence",
                        "new_sequence_name": "MainProjectSequence_optimized",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="generate_personalized_report"):
        run_project_sequence_batch_from_config(config_path)


def test_run_project_sequence_batch_from_config_recommend_only_writes_transition_reports() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_transition_template()
    output_root = Path("test_runtime") / f"sequence_optimizer_prproj_batch_transition_recommend_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    config_path = output_root / "batch_config_recommend_only.json"
    output_project = output_root / "batch_output_recommend_only.prproj"
    reports_dir = output_root / "reports"

    config_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "output_project_path": str(output_project),
                "reports_dir": str(reports_dir),
                "transition_mode": "recommend_only",
                "enable_auto_transitions": False,
                "enable_subject_series_grouping": False,
                "sequence_jobs": [
                    {
                        "source_sequence_name": "MainProjectSequence",
                        "new_sequence_name": "MainProjectSequence_optimized",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_json_path, summary_txt_path = run_project_sequence_batch_from_config(config_path)

    summary_payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
    recommendation_path = Path(str(summary_payload["sequence_jobs"][0]["transition_recommendations_txt"]))
    batch_recommendation_path = Path(str(summary_payload["batch_transition_recommendations_txt"]))
    source_project_copy = project_path.parent / output_project.name
    recommendation_text = recommendation_path.read_text(encoding="utf-8")
    batch_recommendation_text = batch_recommendation_path.read_text(encoding="utf-8")
    summary_text = summary_txt_path.read_text(encoding="utf-8")

    assert not output_project.exists()
    assert source_project_copy.exists()
    assert summary_payload["transition_mode"] == "recommend_only"
    assert summary_payload["enable_auto_transitions"] is False
    assert recommendation_path.exists()
    assert batch_recommendation_path.exists()
    assert "TRANSITION RECOMMENDATIONS" in recommendation_text
    assert f"Project: {source_project_copy}" in recommendation_text
    assert "Sequence: MainProjectSequence_optimized" in recommendation_text
    assert "Cross Dissolve (Legacy)" in recommendation_text
    assert "=== MainProjectSequence_optimized ===" in batch_recommendation_text
    assert f"Final output project: {source_project_copy}" in summary_text
    assert "Batch transition recommendations:" in summary_text

    output_root_xml = ET.fromstring(gzip.decompress(source_project_copy.read_bytes()))
    generated_transition_nodes = [
        node
        for node in output_root_xml.iter("VideoTransitionTrackItem")
        if node.findtext("./TransitionTrackItem/HasIncomingClip") == "true"
    ]
    assert generated_transition_nodes == []


def test_run_project_sequence_batch_from_config_moves_legacy_output_reports_into_project_reports() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_transition_template()
    workspace_root = Path("test_runtime") / f"sequence_optimizer_prproj_batch_delivery_{uuid4().hex}" / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    settings = Settings(project_root=workspace_root)
    settings.ensure_output()

    config_path = workspace_root / "batch_config_delivery.json"
    legacy_reports_dir = settings.output_dir / "legacy_reports"
    legacy_output_project = legacy_reports_dir / "legacy_batch_output.prproj"
    expected_final_reports_dir = regeneration_assets_dir.parent / "reports"

    config_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "output_project_path": str(legacy_output_project),
                "reports_dir": str(legacy_reports_dir),
                "transition_mode": "recommend_only",
                "sequence_jobs": [
                    {
                        "source_sequence_name": "MainProjectSequence",
                        "new_sequence_name": "MainProjectSequence_optimized",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_json_path, summary_txt_path = run_project_sequence_batch_from_config(config_path, settings=settings)

    summary_payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
    delivered_project_path = Path(str(summary_payload["reports_output_project_path"]))
    source_project_copy = project_path.parent / legacy_output_project.name
    delivered_recommendation_path = Path(str(summary_payload["sequence_jobs"][0]["transition_recommendations_txt"]))
    delivered_recommendation_text = delivered_recommendation_path.read_text(encoding="utf-8")

    assert summary_json_path.parent == expected_final_reports_dir
    assert summary_txt_path.parent == expected_final_reports_dir
    assert Path(str(summary_payload["reports_dir"])) == expected_final_reports_dir
    assert summary_payload["configured_output_project_path"] == str(legacy_output_project)
    assert summary_payload["output_project_path"] == str(source_project_copy)
    assert summary_payload["source_project_output_project_path"] == str(source_project_copy)
    assert not legacy_output_project.exists()
    assert delivered_project_path.exists()
    assert source_project_copy.exists()
    assert delivered_project_path.parent == expected_final_reports_dir / "temp_projects"
    assert delivered_recommendation_path.exists()
    assert f"Project: {source_project_copy}" in delivered_recommendation_text
    assert f"Project: {delivered_project_path}" not in delivered_recommendation_text
    assert f"Project: {legacy_output_project}" not in delivered_recommendation_text
    assert not legacy_reports_dir.exists()
    assert list(settings.output_dir.iterdir()) == []


def test_transition_recommendations_report_lists_supported_types() -> None:
    _root, project_path, _xml_path, regeneration_assets_dir = _build_sample_premiere_project_with_transition_template()
    output_root = Path("test_runtime") / f"sequence_optimizer_transition_catalog_{uuid4().hex}"
    output_root.mkdir(parents=True, exist_ok=True)
    output_project = output_root / "optimized_sequence.prproj"
    output_json = output_root / "sequence.json"
    output_txt = output_root / "sequence.txt"

    run_project_sequence_optimizer(
        project_path,
        regeneration_assets_dir=regeneration_assets_dir,
        output_json=output_json,
        output_txt=output_txt,
        output_prproj=output_project,
        new_sequence_name="MainProjectSequence_optimized",
    )

    report_text = build_transition_recommendations_report(
        project_path=output_project,
        sequence_name="MainProjectSequence_optimized",
        optimization_payload=json.loads(output_json.read_text(encoding="utf-8")),
    )

    assert "Supported recommendation types" in report_text
    assert "Cross Dissolve (Legacy)" in report_text
    assert "Dip to Black" in report_text
    assert "Film Dissolve" in report_text
    assert "Morph Cut" in report_text


def test_select_recommended_transition_type_uses_scene_rules() -> None:
    morph_cut, morph_reason = _select_recommended_transition_type(
        _make_transition_candidate(
            series_subject_tokens=["vika"],
            series_appearance_tokens=["white_dress", "blonde"],
            keywords=["portrait", "smile", "face"],
            shot_scale=2,
            people_count=1,
            summary="Close portrait of the same woman smiling softly.",
            shot_type_text="close portrait",
            prompt_text="same woman in the same white dress, new pose, direct face",
        ),
        _make_transition_candidate(
            series_subject_tokens=["vika"],
            series_appearance_tokens=["white_dress", "blonde"],
            keywords=["portrait", "face", "look"],
            shot_scale=2,
            people_count=1,
            summary="Close portrait of the same woman with a slightly different expression.",
            shot_type_text="close portrait",
            prompt_text="same woman in the same white dress, new pose, face continuity",
        ),
    )
    dip_to_black, dip_reason = _select_recommended_transition_type(
        _make_transition_candidate(
            keywords=["family", "sunny", "park"],
            shot_scale=0,
            people_count=4,
            summary="Wide sunny family park scene.",
            shot_type_text="wide establishing shot",
            prompt_text="warm family park opener",
        ),
        _make_transition_candidate(
            keywords=["night", "alone", "dramatic"],
            shot_scale=3,
            people_count=1,
            summary="Night silhouette of one person alone.",
            shot_type_text="extreme close detail silhouette",
            prompt_text="dramatic lonely night ending",
        ),
    )
    film_dissolve, film_reason = _select_recommended_transition_type(
        _make_transition_candidate(
            keywords=["beauty", "soft", "memory"],
            shot_scale=1,
            people_count=1,
            summary="Soft beauty shot with nostalgic mood.",
            shot_type_text="medium portrait",
            prompt_text="dreamy nostalgic beauty portrait",
        ),
        _make_transition_candidate(
            keywords=["romantic", "gentle", "poetic"],
            shot_scale=1,
            people_count=1,
            summary="Poetic romantic closeup with gentle motion.",
            shot_type_text="medium portrait",
            prompt_text="soft cinematic memory feeling",
        ),
    )

    assert morph_cut.display_name == "Morph Cut"
    assert "same-person" in morph_reason
    assert dip_to_black.display_name == "Dip to Black"
    assert "scene or tone break" in dip_reason
    assert film_dissolve.display_name == "Film Dissolve"
    assert "softer cinematic dissolve" in film_reason


def _build_sample_project() -> tuple[Path, Path, Path]:
    root = Path("test_runtime") / f"sequence_optimizer_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir = root / "regeneration_assets"
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

    clip_definitions = [
        {
            "stage_id": "party close_20260322_100001",
            "name": "party close_20260322_100001_video_1.mp4",
            "start": 0,
            "summary": "Close portrait of a child dancing at a birthday party.",
            "background": "Indoor birthday room with balloons.",
            "shot_type": "close-up portrait",
            "main_action": "dancing and laughing",
            "mood": ["joy"],
            "prompt": "close portrait of birthday dance and bright balloons",
        },
        {
            "stage_id": "park morning_20260322_100002",
            "name": "park morning_20260322_100002_video_1.mp4",
            "start": 120,
            "summary": "Wide shot of a family entering a park in the morning.",
            "background": "Sunny park path with trees and open space.",
            "shot_type": "wide establishing shot",
            "main_action": "walking slowly into the scene",
            "mood": ["warmth"],
            "prompt": "establishing view of park and family at morning light",
        },
        {
            "stage_id": "park smile_20260322_100003",
            "name": "park smile_20260322_100003_video_1.mp4",
            "start": 240,
            "summary": "Medium shot of the same family smiling together in the park.",
            "background": "The same park with trees and a bright path.",
            "shot_type": "medium shot",
            "main_action": "smiling together after entering the park",
            "mood": ["warmth"],
            "prompt": "family smile in park after opening view",
        },
    ]

    for clip in clip_definitions:
        bundle_dir = regeneration_assets_dir / clip["stage_id"]
        bundle_dir.mkdir(parents=True, exist_ok=True)
        stage_id = clip["stage_id"]
        (bundle_dir / f"{stage_id}_scene_analysis.json").write_text(
            json.dumps(
                {
                    "summary": clip["summary"],
                    "people_count": 3,
                    "background": clip["background"],
                    "shot_type": clip["shot_type"],
                    "main_action": clip["main_action"],
                    "mood": clip["mood"],
                    "relationships": ["family connection"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (bundle_dir / f"{stage_id}_v_prompt_1.txt").write_text(str(clip["prompt"]), encoding="utf-8")
        (bundle_dir / f"{stage_id}_api_pipeline_manifest.json").write_text(
            json.dumps(
                {
                    "stage_id": stage_id,
                    "steps": [
                        {
                            "index": 1,
                            "v_prompt_file": str(bundle_dir / f"{stage_id}_v_prompt_1.txt"),
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    root_file_three = root / "master_file_three.mp4"
    root_file_three.write_bytes(b"mp4")

    xml_path = root / "sample.xml"
    xml_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="4">
  <project>
    <children>
      <clip id="masterclip-3">
        <file id="file-3">
          <name>{clip_definitions[2]["name"]}</name>
          <pathurl>{_pathurl(root_file_three)}</pathurl>
        </file>
      </clip>
      <sequence id="sequence-main">
        <name>MainSequence</name>
        <media>
          <video>
            <track>
              <clipitem id="bg-1">
                <name>party close_20260322_100001_bg_image_16x9.jpg</name>
                <start>0</start>
                <end>120</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="bg-file-1">
                  <name>party close_20260322_100001_bg_image_16x9.jpg</name>
                  <pathurl>{_pathurl(root / "party close_20260322_100001_bg_image_16x9.jpg")}</pathurl>
                </file>
                {_basic_motion_filter_xml(scale=118.0, keyframe_scale=132.0)}
              </clipitem>
              <clipitem id="bg-2">
                <name>park morning_20260322_100002_bg_image_16x9.jpg</name>
                <start>120</start>
                <end>240</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="bg-file-2">
                  <name>park morning_20260322_100002_bg_image_16x9.jpg</name>
                  <pathurl>{_pathurl(root / "park morning_20260322_100002_bg_image_16x9.jpg")}</pathurl>
                </file>
                {_basic_motion_filter_xml(scale=121.5, keyframe_scale=139.25)}
              </clipitem>
              <clipitem id="bg-3">
                <name>park smile_20260322_100003_bg_image_16x9.jpg</name>
                <start>240</start>
                <end>360</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="bg-file-3">
                  <name>park smile_20260322_100003_bg_image_16x9.jpg</name>
                  <pathurl>{_pathurl(root / "park smile_20260322_100003_bg_image_16x9.jpg")}</pathurl>
                </file>
                {_basic_motion_filter_xml(scale=124.0, keyframe_scale=141.75)}
              </clipitem>
            </track>
            <track>
              <clipitem id="clip-1">
                <name>{clip_definitions[0]["name"]}</name>
                <start>{clip_definitions[0]["start"]}</start>
                <end>120</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="file-1">
                  <name>{clip_definitions[0]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[0]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-2">
                <name>{clip_definitions[1]["name"]}</name>
                <start>{clip_definitions[1]["start"]}</start>
                <end>240</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="file-2">
                  <name>{clip_definitions[1]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[1]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-3">
                <name>{clip_definitions[2]["name"]}</name>
                <start>{clip_definitions[2]["start"]}</start>
                <end>360</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="file-3" />
              </clipitem>
            </track>
          </video>
        </media>
      </sequence>
      <sequence id="sequence-secondary">
        <name>SecondarySequence</name>
        <media>
          <video>
            <track>
              <clipitem id="clip-secondary">
                <name>{clip_definitions[0]["name"]}</name>
                <start>0</start>
                <end>100</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-secondary">
                  <name>{clip_definitions[0]["name"]}</name>
                  <pathurl>{_pathurl(root / "secondary.mp4")}</pathurl>
                </file>
              </clipitem>
            </track>
          </video>
        </media>
      </sequence>
    </children>
  </project>
</xmeml>
""",
        encoding="utf-8",
    )

    return root, xml_path, regeneration_assets_dir


def _make_transition_candidate(**overrides: object) -> SimpleNamespace:
    payload: dict[str, object] = {
        "series_subject_tokens": [],
        "series_appearance_tokens": [],
        "keywords": [],
        "shot_scale": 1,
        "people_count": 0,
        "energy_level": 0,
        "summary": "",
        "background": "",
        "shot_type_text": "",
        "main_action": "",
        "mood": [],
        "relationships": [],
        "prompt_text": "",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _build_duplicate_mp4_priority_project() -> tuple[Path, Path]:
    root = Path("test_runtime") / f"sequence_optimizer_duplicate_mp4_project_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "duplicate_priority.prproj"
    project_xml = """<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <Sequence ObjectUID="dup-seq" ClassID="sequence" Version="1">
    <TrackGroups Version="1">
      <TrackGroup Version="1" Index="0">
        <Second ObjectRef="8000" />
      </TrackGroup>
      <TrackGroup Version="1" Index="1">
        <Second ObjectRef="8001" />
      </TrackGroup>
      <TrackGroup Version="1" Index="2">
        <Second ObjectRef="8002" />
      </TrackGroup>
    </TrackGroups>
    <Name>DuplicatePrioritySequence</Name>
  </Sequence>
  <VideoTrackGroup ObjectID="8000" ClassID="video-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="dup-track-a" />
        <Track Index="1" ObjectURef="dup-track-b" />
        <Track Index="2" ObjectURef="dup-track-main" />
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <AudioTrackGroup ObjectID="8001" ClassID="audio-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1" />
    </TrackGroup>
  </AudioTrackGroup>
  <DataTrackGroup ObjectID="8002" ClassID="data-group" Version="1">
    <TrackGroup Version="1" />
  </DataTrackGroup>
  <VideoClipTrack ObjectUID="dup-track-a" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="8100" />
          <TrackItem Index="1" ObjectRef="8101" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="dup-track-b" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1" />
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="dup-track-main" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="8200" />
          <TrackItem Index="1" ObjectRef="8201" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrackItem ObjectID="8100" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <End>50</End>
      </TrackItem>
      <SubClip ObjectRef="8300" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="8101" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>50</Start>
        <End>100</End>
      </TrackItem>
      <SubClip ObjectRef="8301" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="8200" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <End>100</End>
      </TrackItem>
      <SubClip ObjectRef="8400" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="8201" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>100</Start>
        <End>200</End>
      </TrackItem>
      <SubClip ObjectRef="8401" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="8300" ClassID="subclip" Version="1">
    <Clip ObjectRef="8500" />
    <Name>alpha_20260323_010001_video_1.mp4</Name>
  </SubClip>
  <SubClip ObjectID="8301" ClassID="subclip" Version="1">
    <Clip ObjectRef="8501" />
    <Name>beta_20260323_010002_video_1.mp4</Name>
  </SubClip>
  <SubClip ObjectID="8400" ClassID="subclip" Version="1">
    <Clip ObjectRef="8600" />
    <Name>alpha_20260323_010001_video_1.mp4</Name>
  </SubClip>
  <SubClip ObjectID="8401" ClassID="subclip" Version="1">
    <Clip ObjectRef="8601" />
    <Name>beta_20260323_010002_video_1.mp4</Name>
  </SubClip>
  <VideoClip ObjectID="8500" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>50</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="8501" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>50</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="8600" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>100</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="8601" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>100</OutPoint>
    </Clip>
  </VideoClip>
</PremiereData>
"""
    project_path.write_bytes(gzip.compress(project_xml.encode("utf-8")))
    return root, project_path


def _build_sample_premiere_project() -> tuple[Path, Path, Path, Path]:
    root, xml_path, regeneration_assets_dir = _build_sample_project()
    project_path = root / "sample.prproj"
    project_xml = """<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <RootProjectItem ObjectURef="root-project" />
  <RootProjectItem ObjectUID="root-project" ClassID="root-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>Root</Name>
    </ProjectItem>
    <ProjectItemContainer Version="1">
      <Items Version="1">
        <Item Index="0" ObjectURef="project-item-main-sequence" />
      </Items>
    </ProjectItemContainer>
  </RootProjectItem>
  <Sequence ObjectUID="seq-main" ClassID="sequence" Version="1">
    <Node Version="1">
      <Properties Version="1">
        <MZ.WorkOutPoint>360</MZ.WorkOutPoint>
        <MZ.EditLine>360</MZ.EditLine>
      </Properties>
    </Node>
    <TrackGroups Version="1">
      <TrackGroup Version="1" Index="0">
        <Second ObjectRef="1000" />
      </TrackGroup>
      <TrackGroup Version="1" Index="1">
        <Second ObjectRef="1001" />
      </TrackGroup>
      <TrackGroup Version="1" Index="2">
        <Second ObjectRef="1002" />
      </TrackGroup>
    </TrackGroups>
    <Name>MainProjectSequence</Name>
  </Sequence>
  <VideoTrackGroup ObjectID="1000" ClassID="video-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-bg-a" />
        <Track Index="1" ObjectURef="track-bg-b" />
        <Track Index="2" ObjectURef="track-main" />
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <AudioTrackGroup ObjectID="1001" ClassID="audio-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1" />
    </TrackGroup>
  </AudioTrackGroup>
  <DataTrackGroup ObjectID="1002" ClassID="data-group" Version="1">
    <TrackGroup Version="1" />
  </DataTrackGroup>
  <VideoSequenceSource ObjectID="1003" ClassID="video-sequence-source" Version="1">
    <SequenceSource Version="1">
      <Sequence ObjectURef="seq-main" />
    </SequenceSource>
    <OriginalDuration>360</OriginalDuration>
  </VideoSequenceSource>
  <AudioSequenceSource ObjectID="1004" ClassID="audio-sequence-source" Version="1">
    <SequenceSource Version="1">
      <Sequence ObjectURef="seq-main" />
    </SequenceSource>
    <OriginalDuration>360</OriginalDuration>
  </AudioSequenceSource>
  <VideoClipTrack ObjectUID="track-bg-a" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="2010" />
          <TrackItem Index="1" ObjectRef="2011" />
          <TrackItem Index="2" ObjectRef="2012" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="track-bg-b" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="2020" />
          <TrackItem Index="1" ObjectRef="2021" />
          <TrackItem Index="2" ObjectRef="2022" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="track-main" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="2030" />
          <TrackItem Index="1" ObjectRef="2031" />
          <TrackItem Index="2" ObjectRef="2032" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrackItem ObjectID="2010" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <End>120</End>
      </TrackItem>
      <SubClip ObjectRef="3010" />
    </ClipTrackItem>
    <DebugEffect>bg-stage-party-close</DebugEffect>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="2011" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>120</Start>
        <End>240</End>
      </TrackItem>
      <SubClip ObjectRef="3011" />
    </ClipTrackItem>
    <DebugEffect>bg-stage-park-morning</DebugEffect>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="2012" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>240</Start>
        <End>360</End>
      </TrackItem>
      <SubClip ObjectRef="3012" />
    </ClipTrackItem>
    <DebugEffect>bg-stage-park-smile</DebugEffect>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="2020" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <End>120</End>
      </TrackItem>
      <SubClip ObjectRef="3020" />
    </ClipTrackItem>
    <DebugEffect>bg-copy-stage-party-close</DebugEffect>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="2021" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>120</Start>
        <End>240</End>
      </TrackItem>
      <SubClip ObjectRef="3021" />
    </ClipTrackItem>
    <DebugEffect>bg-copy-stage-park-morning</DebugEffect>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="2022" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>240</Start>
        <End>360</End>
      </TrackItem>
      <SubClip ObjectRef="3022" />
    </ClipTrackItem>
    <DebugEffect>bg-copy-stage-park-smile</DebugEffect>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="2030" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <End>120</End>
      </TrackItem>
      <SubClip ObjectRef="3030" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="2031" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>120</Start>
        <End>240</End>
      </TrackItem>
      <SubClip ObjectRef="3031" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <VideoClipTrackItem ObjectID="2032" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>240</Start>
        <End>360</End>
      </TrackItem>
      <SubClip ObjectRef="3032" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="3010" ClassID="subclip" Version="1">
    <Clip ObjectRef="4010" />
    <Name>party close_20260322_100001_bg_image_16x9.jpg</Name>
  </SubClip>
  <SubClip ObjectID="3011" ClassID="subclip" Version="1">
    <Clip ObjectRef="4011" />
    <Name>park morning_20260322_100002_bg_image_16x9.jpg</Name>
  </SubClip>
  <SubClip ObjectID="3012" ClassID="subclip" Version="1">
    <Clip ObjectRef="4012" />
    <Name>park smile_20260322_100003_bg_image_16x9.jpg</Name>
  </SubClip>
  <SubClip ObjectID="3020" ClassID="subclip" Version="1">
    <Clip ObjectRef="4020" />
    <Name>party close_20260322_100001_bg_image_16x9.jpg</Name>
  </SubClip>
  <SubClip ObjectID="3021" ClassID="subclip" Version="1">
    <Clip ObjectRef="4021" />
    <Name>park morning_20260322_100002_bg_image_16x9.jpg</Name>
  </SubClip>
  <SubClip ObjectID="3022" ClassID="subclip" Version="1">
    <Clip ObjectRef="4022" />
    <Name>park smile_20260322_100003_bg_image_16x9.jpg</Name>
  </SubClip>
  <SubClip ObjectID="3030" ClassID="subclip" Version="1">
    <Clip ObjectRef="4030" />
    <Name>party close_20260322_100001_video_1.mp4</Name>
  </SubClip>
  <SubClip ObjectID="3031" ClassID="subclip" Version="1">
    <Clip ObjectRef="4031" />
    <Name>park morning_20260322_100002_video_1.mp4</Name>
  </SubClip>
  <SubClip ObjectID="3032" ClassID="subclip" Version="1">
    <Clip ObjectRef="4032" />
    <Name>park smile_20260322_100003_video_1.mp4</Name>
  </SubClip>
  <VideoClip ObjectID="4010" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="4011" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="4012" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="4020" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="4021" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="4022" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="4030" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="4031" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <VideoClip ObjectID="4032" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>120</OutPoint>
    </Clip>
  </VideoClip>
  <ClipProjectItem ObjectUID="project-item-main-sequence" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Node Version="1">
        <Properties Version="1">
          <project.icon.view.grid.order>1</project.icon.view.grid.order>
        </Properties>
      </Node>
      <Name>MainProjectSequence</Name>
    </ProjectItem>
    <MasterClip ObjectURef="master-main-sequence" />
  </ClipProjectItem>
  <MasterClip ObjectUID="master-main-sequence" ClassID="masterclip" Version="1">
    <LoggingInfo ObjectRef="5000" />
    <AudioComponentChains Version="1">
      <AudioComponentChain Index="0" ObjectRef="5001" />
    </AudioComponentChains>
    <Clips Version="1">
      <Clip Index="0" ObjectRef="5002" />
      <Clip Index="1" ObjectRef="5003" />
    </Clips>
    <AudioClipChannelGroups ObjectRef="5004" />
    <Name>MainProjectSequence</Name>
  </MasterClip>
  <LoggingInfo ObjectID="5000" ClassID="logging-info" Version="1" />
  <AudioComponentChain ObjectID="5001" ClassID="audio-component-chain" Version="1" />
  <AudioClip ObjectID="5002" ClassID="audio-clip" Version="1">
    <Clip Version="1">
      <Source ObjectRef="5005" />
      <ClipID>00000000-0000-0000-0000-000000000052</ClipID>
      <InUse>false</InUse>
    </Clip>
  </AudioClip>
  <VideoClip ObjectID="5003" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <Source ObjectRef="5006" />
      <ClipID>00000000-0000-0000-0000-000000000053</ClipID>
      <InUse>false</InUse>
    </Clip>
  </VideoClip>
  <AudioClipChannelGroups ObjectID="5004" ClassID="audio-groups" Version="1" />
  <AudioSequenceSource ObjectID="5005" ClassID="audio-sequence-source" Version="1">
    <SequenceSource Version="1">
      <Sequence ObjectURef="seq-main" />
    </SequenceSource>
    <OriginalDuration>360</OriginalDuration>
  </AudioSequenceSource>
  <VideoSequenceSource ObjectID="5006" ClassID="video-sequence-source" Version="1">
    <SequenceSource Version="1">
      <Sequence ObjectURef="seq-main" />
    </SequenceSource>
    <OriginalDuration>360</OriginalDuration>
  </VideoSequenceSource>
  <Sequence ObjectUID="seq-secondary" ClassID="sequence" Version="1">
    <TrackGroups Version="1">
      <TrackGroup Version="1" Index="0">
        <Second ObjectRef="1100" />
      </TrackGroup>
      <TrackGroup Version="1" Index="1">
        <Second ObjectRef="1101" />
      </TrackGroup>
      <TrackGroup Version="1" Index="2">
        <Second ObjectRef="1102" />
      </TrackGroup>
    </TrackGroups>
    <Name>SecondaryProjectSequence</Name>
  </Sequence>
  <VideoTrackGroup ObjectID="1100" ClassID="video-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-secondary-main" />
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <AudioTrackGroup ObjectID="1101" ClassID="audio-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1" />
    </TrackGroup>
  </AudioTrackGroup>
  <DataTrackGroup ObjectID="1102" ClassID="data-group" Version="1">
    <TrackGroup Version="1" />
  </DataTrackGroup>
  <VideoClipTrack ObjectUID="track-secondary-main" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="2100" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrackItem ObjectID="2100" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <End>100</End>
      </TrackItem>
      <SubClip ObjectRef="3100" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="3100" ClassID="subclip" Version="1">
    <Clip ObjectRef="4100" />
    <Name>party close_20260322_100001_video_1.mp4</Name>
  </SubClip>
  <VideoClip ObjectID="4100" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>100</OutPoint>
    </Clip>
  </VideoClip>
</PremiereData>
"""
    project_path.write_bytes(gzip.compress(project_xml.encode("utf-8")))
    return root, project_path, xml_path, regeneration_assets_dir


def _build_sample_premiere_project_with_transition_template() -> tuple[Path, Path, Path, Path]:
    root, project_path, xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    project_root = ET.fromstring(gzip.decompress(project_path.read_bytes()))

    track_main = next(
        node
        for node in project_root.iter("VideoClipTrack")
        if node.attrib.get("ObjectUID") == "track-main"
    )
    clip_track = track_main.find("./ClipTrack")
    assert clip_track is not None

    transition_items = ET.SubElement(clip_track, "TransitionItems")
    transition_items.attrib["Version"] = "1"
    transition_refs = ET.SubElement(transition_items, "TrackItems")
    transition_refs.attrib["Version"] = "1"
    transition_ref = ET.SubElement(transition_refs, "TrackItem")
    transition_ref.attrib["Index"] = "0"
    transition_ref.attrib["ObjectRef"] = "6100"
    media_type = ET.SubElement(transition_items, "MediaType")
    media_type.text = "228cda18-3625-4d2d-951e-348879e4ed93"
    index_node = ET.SubElement(transition_items, "Index")
    index_node.text = "2"

    transition_node = ET.SubElement(
        project_root,
        "VideoTransitionTrackItem",
        {"ObjectID": "6100", "ClassID": "transition", "Version": "6"},
    )
    transition_track_item = ET.SubElement(transition_node, "TransitionTrackItem", {"Version": "3"})
    track_item = ET.SubElement(transition_track_item, "TrackItem", {"Version": "4"})
    ET.SubElement(track_item, "Start").text = "240"
    ET.SubElement(track_item, "End").text = "360"
    ET.SubElement(transition_track_item, "DisplayName").text = "Cross Dissolve (Legacy)"
    ET.SubElement(transition_track_item, "MatchName").text = "AE.ADBE Cross Dissolve New"
    ET.SubElement(transition_track_item, "Alignment").text = "60"
    ET.SubElement(transition_track_item, "HasOutgoingClip").text = "true"
    ET.SubElement(transition_track_item, "HasIncomingClip").text = "false"
    ET.SubElement(transition_node, "VideoFilterComponent", {"ObjectRef": "6101"})

    filter_component = ET.SubElement(
        project_root,
        "VideoFilterComponent",
        {"ObjectID": "6101", "ClassID": "transition-filter", "Version": "9"},
    )
    component = ET.SubElement(filter_component, "Component", {"Version": "7"})
    params = ET.SubElement(component, "Params", {"Version": "1"})
    ET.SubElement(params, "Param", {"Index": "0", "ObjectRef": "6102"})
    ET.SubElement(component, "DisplayName").text = "Cross Dissolve (Legacy)"
    ET.SubElement(filter_component, "MatchName").text = "AE.ADBE Cross Dissolve New"
    ET.SubElement(filter_component, "VideoFilterType").text = "2"

    ET.SubElement(
        project_root,
        "Parameter",
        {"ObjectID": "6102", "ClassID": "transition-param", "Version": "1"},
    )

    project_path.write_bytes(gzip.compress(ET.tostring(project_root, encoding="utf-8", xml_declaration=True)))
    return root, project_path, xml_path, regeneration_assets_dir


def _build_sample_premiere_project_with_main_track_gaps() -> tuple[Path, Path, Path, Path]:
    root, project_path, xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    project_root = ET.fromstring(gzip.decompress(project_path.read_bytes()))

    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in project_root.iter()
        if node.attrib.get("ObjectID")
    }
    timeline_values = {
        "2030": ("0", "120"),
        "2031": ("160", "280"),
        "2032": ("340", "460"),
    }

    for object_id, (start_value, end_value) in timeline_values.items():
        node = object_lookup[object_id]
        track_item = node.find("./ClipTrackItem/TrackItem")
        assert track_item is not None
        start_node = track_item.find("./Start")
        if start_node is None:
            start_node = ET.Element("Start")
            end_node = track_item.find("./End")
            assert end_node is not None
            track_item.insert(list(track_item).index(end_node), start_node)
        start_node.text = start_value
        end_node = track_item.find("./End")
        assert end_node is not None
        end_node.text = end_value

    project_path.write_bytes(gzip.compress(ET.tostring(project_root, encoding="utf-8", xml_declaration=True)))
    return root, project_path, xml_path, regeneration_assets_dir


def _build_sample_premiere_project_with_main_track_initial_offset() -> tuple[Path, Path, Path, Path]:
    root, project_path, xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    project_root = ET.fromstring(gzip.decompress(project_path.read_bytes()))

    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in project_root.iter()
        if node.attrib.get("ObjectID")
    }
    timeline_values = {
        "2030": ("60", "180"),
        "2031": ("180", "300"),
        "2032": ("300", "420"),
    }

    for object_id, (start_value, end_value) in timeline_values.items():
        node = object_lookup[object_id]
        track_item = node.find("./ClipTrackItem/TrackItem")
        assert track_item is not None
        start_node = track_item.find("./Start")
        if start_node is None:
            start_node = ET.Element("Start")
            end_node = track_item.find("./End")
            assert end_node is not None
            track_item.insert(list(track_item).index(end_node), start_node)
        start_node.text = start_value
        end_node = track_item.find("./End")
        assert end_node is not None
        end_node.text = end_value

    project_path.write_bytes(gzip.compress(ET.tostring(project_root, encoding="utf-8", xml_declaration=True)))
    return root, project_path, xml_path, regeneration_assets_dir


def _build_sample_premiere_project_with_variable_group_offsets() -> tuple[Path, Path, Path, Path]:
    root, project_path, xml_path, regeneration_assets_dir = _build_sample_premiere_project()
    project_root = ET.fromstring(gzip.decompress(project_path.read_bytes()))

    object_lookup = {
        node.attrib["ObjectID"]: node
        for node in project_root.iter()
        if node.attrib.get("ObjectID")
    }
    timeline_values = {
        "2020": ("10", "90"),
        "2021": ("150", "220"),
        "2022": ("255", "335"),
        "2030": ("20", "100"),
        "2031": ("160", "230"),
        "2032": ("270", "350"),
    }

    for object_id, (start_value, end_value) in timeline_values.items():
        node = object_lookup[object_id]
        track_item = node.find("./ClipTrackItem/TrackItem")
        assert track_item is not None
        start_node = track_item.find("./Start")
        if start_node is None:
            start_node = ET.Element("Start")
            end_node = track_item.find("./End")
            assert end_node is not None
            track_item.insert(list(track_item).index(end_node), start_node)
        start_node.text = start_value
        end_node = track_item.find("./End")
        assert end_node is not None
        end_node.text = end_value

    project_path.write_bytes(gzip.compress(ET.tostring(project_root, encoding="utf-8", xml_declaration=True)))
    return root, project_path, xml_path, regeneration_assets_dir


def _build_main_character_priority_project() -> tuple[Path, Path, Path]:
    root = Path("test_runtime") / f"sequence_optimizer_main_character_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir = root / "regeneration_assets"
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

    clip_definitions = [
        {
            "stage_id": "family wide_20260322_200001",
            "name": "family wide_20260322_200001_video_1.mp4",
            "start": 0,
            "scene_analysis": {
                "summary": "Wide family scene in a park.",
                "people_count": 2,
                "people": [
                    {
                        "label": "girl",
                        "position_in_frame": "left side",
                        "role_in_scene": "older child",
                        "apparent_age_group": "8-10 years old",
                        "face_visibility": "fully visible",
                    },
                    {
                        "label": "woman",
                        "position_in_frame": "center background",
                        "role_in_scene": "adult",
                        "apparent_age_group": "adult",
                        "face_visibility": "partially visible",
                    },
                ],
                "background": "Open park path.",
                "shot_type": "wide establishing shot",
                "main_action": "walking into the park",
                "mood": ["warmth"],
                "relationships": ["mother and daughter"],
            },
            "prompt": "wide park opener with family",
        },
        {
            "stage_id": "baby close_20260322_200002",
            "name": "baby close_20260322_200002_video_1.mp4",
            "start": 140,
            "scene_analysis": {
                "summary": "Baby looks directly into the camera while being held on an adult shoulder.",
                "people_count": 2,
                "people": [
                    {
                        "label": "baby",
                        "position_in_frame": "central foreground",
                        "role_in_scene": "youngest child",
                        "apparent_age_group": "infant",
                        "face_visibility": "fully visible",
                    },
                    {
                        "label": "woman",
                        "position_in_frame": "center background",
                        "role_in_scene": "adult holding the baby",
                        "apparent_age_group": "adult",
                        "face_visibility": "back to camera",
                    },
                ],
                "background": "Indoor curtain and wall.",
                "shot_type": "medium shot",
                "main_action": "adult holding the baby",
                "mood": ["care"],
                "relationships": ["mother and baby"],
            },
            "prompt": "baby portrait held close to camera",
        },
    ]

    for clip in clip_definitions:
        bundle_dir = regeneration_assets_dir / clip["stage_id"]
        bundle_dir.mkdir(parents=True, exist_ok=True)
        stage_id = clip["stage_id"]
        (bundle_dir / f"{stage_id}_scene_analysis.json").write_text(
            json.dumps(clip["scene_analysis"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (bundle_dir / f"{stage_id}_v_prompt_1.txt").write_text(str(clip["prompt"]), encoding="utf-8")
        (bundle_dir / f"{stage_id}_api_pipeline_manifest.json").write_text(
            json.dumps({"stage_id": stage_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    xml_path = root / "main_character_sample.xml"
    xml_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="4">
  <project>
    <children>
      <sequence id="sequence-main">
        <name>MainCharacterSequence</name>
        <media>
          <video>
            <track>
              <clipitem id="clip-1">
                <name>{clip_definitions[0]["name"]}</name>
                <start>{clip_definitions[0]["start"]}</start>
                <end>100</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-1">
                  <name>{clip_definitions[0]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[0]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-2">
                <name>{clip_definitions[1]["name"]}</name>
                <start>{clip_definitions[1]["start"]}</start>
                <end>240</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-2">
                  <name>{clip_definitions[1]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[1]["name"])}</pathurl>
                </file>
              </clipitem>
            </track>
          </video>
        </media>
      </sequence>
    </children>
  </project>
</xmeml>
""",
        encoding="utf-8",
    )

    return root, xml_path, regeneration_assets_dir


def _build_subject_series_project() -> tuple[Path, Path, Path]:
    root = Path("test_runtime") / f"sequence_optimizer_subject_series_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir = root / "regeneration_assets"
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

    clip_definitions = [
        {
            "stage_id": "series child standing_20260323_030001",
            "name": "series child standing_20260323_030001_video_1.mp4",
            "start": 0,
            "scene_analysis": {
                "summary": "A young girl in a red coat stands alone in a snowy yard.",
                "people_count": 1,
                "people": [
                    {
                        "label": "girl",
                        "position_in_frame": "central foreground",
                        "role_in_scene": "main child",
                        "apparent_age_group": "6 years old",
                        "face_visibility": "fully visible",
                        "clothing": "red coat and white knit hat",
                        "pose": "standing upright and looking to the left",
                        "facial_expression": "gentle smile",
                    }
                ],
                "background": "Snowy yard with wooden fence.",
                "shot_type": "wide establishing shot",
                "main_action": "standing in the snow",
                "mood": ["winter", "calm"],
                "relationships": ["single child portrait"],
            },
            "prompt": "wide winter portrait of the same girl in a red coat",
        },
        {
            "stage_id": "series family table_20260323_030002",
            "name": "series family table_20260323_030002_video_1.mp4",
            "start": 120,
            "scene_analysis": {
                "summary": "A family sits around a dinner table indoors.",
                "people_count": 3,
                "people": [
                    {
                        "label": "woman",
                        "position_in_frame": "center",
                        "role_in_scene": "mother",
                        "apparent_age_group": "adult",
                        "face_visibility": "fully visible",
                        "clothing": "green sweater",
                        "pose": "sitting at the table",
                        "facial_expression": "neutral",
                    }
                ],
                "background": "Dining room with warm lights.",
                "shot_type": "medium shot",
                "main_action": "talking during dinner",
                "mood": ["home", "warmth"],
                "relationships": ["family dinner"],
            },
            "prompt": "family dinner scene indoors",
        },
        {
            "stage_id": "series child sitting_20260323_030003",
            "name": "series child sitting_20260323_030003_video_1.mp4",
            "start": 240,
            "scene_analysis": {
                "summary": "The same young girl in a red coat now sits on a snowy bench.",
                "people_count": 1,
                "people": [
                    {
                        "label": "girl",
                        "position_in_frame": "central foreground",
                        "role_in_scene": "main child",
                        "apparent_age_group": "6 years old",
                        "face_visibility": "fully visible",
                        "clothing": "red coat and white knit hat",
                        "pose": "sitting on a bench and turning toward camera",
                        "facial_expression": "bright smile",
                    }
                ],
                "background": "Snowy yard with wooden fence.",
                "shot_type": "medium shot",
                "main_action": "sitting and smiling in the snow",
                "mood": ["winter", "calm"],
                "relationships": ["single child portrait"],
            },
            "prompt": "medium winter portrait of the same girl in a red coat",
        },
    ]

    for clip in clip_definitions:
        bundle_dir = regeneration_assets_dir / clip["stage_id"]
        bundle_dir.mkdir(parents=True, exist_ok=True)
        stage_id = clip["stage_id"]
        (bundle_dir / f"{stage_id}_scene_analysis.json").write_text(
            json.dumps(clip["scene_analysis"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (bundle_dir / f"{stage_id}_v_prompt_1.txt").write_text(str(clip["prompt"]), encoding="utf-8")
        (bundle_dir / f"{stage_id}_api_pipeline_manifest.json").write_text(
            json.dumps({"stage_id": stage_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    xml_path = root / "subject_series_sample.xml"
    xml_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="4">
  <project>
    <children>
      <sequence id="sequence-main">
        <name>SubjectSeriesSequence</name>
        <media>
          <video>
            <track>
              <clipitem id="clip-1">
                <name>{clip_definitions[0]["name"]}</name>
                <start>{clip_definitions[0]["start"]}</start>
                <end>120</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="file-1">
                  <name>{clip_definitions[0]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[0]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-2">
                <name>{clip_definitions[1]["name"]}</name>
                <start>{clip_definitions[1]["start"]}</start>
                <end>240</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="file-2">
                  <name>{clip_definitions[1]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[1]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-3">
                <name>{clip_definitions[2]["name"]}</name>
                <start>{clip_definitions[2]["start"]}</start>
                <end>360</end>
                <in>0</in>
                <out>120</out>
                <duration>120</duration>
                <file id="file-3">
                  <name>{clip_definitions[2]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[2]["name"])}</pathurl>
                </file>
              </clipitem>
            </track>
          </video>
        </media>
      </sequence>
    </children>
  </project>
</xmeml>
""",
        encoding="utf-8",
    )

    return root, xml_path, regeneration_assets_dir


def _build_age_progression_project() -> tuple[Path, Path, Path]:
    root = Path("test_runtime") / f"sequence_optimizer_age_progression_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir = root / "regeneration_assets"
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

    clip_definitions = [
        {
            "stage_id": "age toddler_20260323_020001",
            "name": "age toddler_20260323_020001_video_1.mp4",
            "start": 0,
            "scene_analysis": _make_age_scene_analysis("toddler", "youngest child", "toddler", "Child in a living room portrait."),
            "prompt": "living room child portrait",
        },
        {
            "stage_id": "age adult_20260323_020002",
            "name": "age adult_20260323_020002_video_1.mp4",
            "start": 100,
            "scene_analysis": _make_age_scene_analysis("adult", "mother", "woman", "Family portrait in the same living room."),
            "prompt": "living room family portrait",
        },
        {
            "stage_id": "age child_20260323_020003",
            "name": "age child_20260323_020003_video_1.mp4",
            "start": 200,
            "scene_analysis": _make_age_scene_analysis("7 years old", "school child", "girl", "Family portrait in the same living room."),
            "prompt": "living room family portrait",
        },
        {
            "stage_id": "age teen_20260323_020004",
            "name": "age teen_20260323_020004_video_1.mp4",
            "start": 300,
            "scene_analysis": _make_age_scene_analysis("teen", "older girl", "girl", "Family portrait in the same living room."),
            "prompt": "living room family portrait",
        },
    ]

    for clip in clip_definitions:
        bundle_dir = regeneration_assets_dir / clip["stage_id"]
        bundle_dir.mkdir(parents=True, exist_ok=True)
        stage_id = clip["stage_id"]
        (bundle_dir / f"{stage_id}_scene_analysis.json").write_text(
            json.dumps(clip["scene_analysis"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (bundle_dir / f"{stage_id}_v_prompt_1.txt").write_text(str(clip["prompt"]), encoding="utf-8")
        (bundle_dir / f"{stage_id}_api_pipeline_manifest.json").write_text(
            json.dumps({"stage_id": stage_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    xml_path = root / "age_progression_sample.xml"
    xml_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="4">
  <project>
    <children>
      <sequence id="sequence-main">
        <name>AgeProgressionSequence</name>
        <media>
          <video>
            <track>
              <clipitem id="clip-1">
                <name>{clip_definitions[0]["name"]}</name>
                <start>{clip_definitions[0]["start"]}</start>
                <end>100</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-1">
                  <name>{clip_definitions[0]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[0]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-2">
                <name>{clip_definitions[1]["name"]}</name>
                <start>{clip_definitions[1]["start"]}</start>
                <end>200</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-2">
                  <name>{clip_definitions[1]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[1]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-3">
                <name>{clip_definitions[2]["name"]}</name>
                <start>{clip_definitions[2]["start"]}</start>
                <end>300</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-3">
                  <name>{clip_definitions[2]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[2]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-4">
                <name>{clip_definitions[3]["name"]}</name>
                <start>{clip_definitions[3]["start"]}</start>
                <end>400</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-4">
                  <name>{clip_definitions[3]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[3]["name"])}</pathurl>
                </file>
              </clipitem>
            </track>
          </video>
        </media>
      </sequence>
    </children>
  </project>
</xmeml>
""",
        encoding="utf-8",
    )

    return root, xml_path, regeneration_assets_dir


def _build_shared_file_reference_project() -> tuple[Path, Path, Path]:
    root = Path("test_runtime") / f"sequence_optimizer_shared_refs_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir = root / "regeneration_assets"
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

    clip_definitions = [
        {
            "stage_id": "shared opener_20260323_010001",
            "name": "shared opener_20260323_010001_video_1.mp4",
            "start": 0,
            "summary": "Wide opening shot of a child entering a bright room.",
            "background": "Bright living room with daylight.",
            "shot_type": "wide establishing shot",
            "main_action": "walking into the room",
            "mood": ["warmth"],
            "prompt": "wide room opener with child",
        },
        {
            "stage_id": "shared smile_20260323_010002",
            "name": "shared smile_20260323_010002_video_1.mp4",
            "start": 100,
            "summary": "Closer shot of the same child smiling near the window.",
            "background": "Same living room and window light.",
            "shot_type": "medium shot",
            "main_action": "smiling at camera",
            "mood": ["warmth"],
            "prompt": "closer child smile near window",
        },
    ]

    for clip in clip_definitions:
        bundle_dir = regeneration_assets_dir / clip["stage_id"]
        bundle_dir.mkdir(parents=True, exist_ok=True)
        stage_id = clip["stage_id"]
        (bundle_dir / f"{stage_id}_scene_analysis.json").write_text(
            json.dumps(
                {
                    "summary": clip["summary"],
                    "people_count": 1,
                    "background": clip["background"],
                    "shot_type": clip["shot_type"],
                    "main_action": clip["main_action"],
                    "mood": clip["mood"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (bundle_dir / f"{stage_id}_v_prompt_1.txt").write_text(str(clip["prompt"]), encoding="utf-8")
        (bundle_dir / f"{stage_id}_api_pipeline_manifest.json").write_text(
            json.dumps({"stage_id": stage_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    xml_path = root / "shared_refs.xml"
    xml_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="4">
  <project>
    <children>
      <sequence id="sequence-main">
        <name>SharedReferenceSequence</name>
        <media>
          <video>
            <track>
              <clipitem id="bg-1">
                <name>shared opener_20260323_010001_bg_image_16x9.jpg</name>
                <start>0</start>
                <end>100</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="bg-file-1">
                  <name>shared opener_20260323_010001_bg_image_16x9.jpg</name>
                  <pathurl>{_pathurl(root / "shared opener_20260323_010001_bg_image_16x9.jpg")}</pathurl>
                </file>
              </clipitem>
              <clipitem id="bg-2">
                <name>shared smile_20260323_010002_bg_image_16x9.jpg</name>
                <start>100</start>
                <end>200</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="bg-file-2">
                  <name>shared smile_20260323_010002_bg_image_16x9.jpg</name>
                  <pathurl>{_pathurl(root / "shared smile_20260323_010002_bg_image_16x9.jpg")}</pathurl>
                </file>
              </clipitem>
            </track>
            <track>
              <clipitem id="bg-copy-1">
                <name>shared opener_20260323_010001_bg_image_16x9.jpg</name>
                <start>0</start>
                <end>100</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="bg-file-1" />
              </clipitem>
              <clipitem id="bg-copy-2">
                <name>shared smile_20260323_010002_bg_image_16x9.jpg</name>
                <start>100</start>
                <end>200</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="bg-file-2" />
              </clipitem>
            </track>
            <track>
              <clipitem id="clip-1">
                <name>{clip_definitions[0]["name"]}</name>
                <start>{clip_definitions[0]["start"]}</start>
                <end>100</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-1">
                  <name>{clip_definitions[0]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[0]["name"])}</pathurl>
                </file>
              </clipitem>
              <clipitem id="clip-2">
                <name>{clip_definitions[1]["name"]}</name>
                <start>{clip_definitions[1]["start"]}</start>
                <end>200</end>
                <in>0</in>
                <out>100</out>
                <duration>100</duration>
                <file id="file-2">
                  <name>{clip_definitions[1]["name"]}</name>
                  <pathurl>{_pathurl(root / clip_definitions[1]["name"])}</pathurl>
                </file>
              </clipitem>
            </track>
          </video>
        </media>
      </sequence>
    </children>
  </project>
</xmeml>
""",
        encoding="utf-8",
    )

    return root, xml_path, regeneration_assets_dir


def _write_translation_results_report(xml_path: Path, issue_lines: list[str]) -> Path:
    report_path = xml_path.parent / "FCP Translation Results 2026-03-23 04-40.txt"
    payload_lines: list[str] = []
    for issue_line in issue_lines:
        payload_lines.extend(["Translation issue:", f"\t{issue_line}"])
    report_path.write_text("\n".join(payload_lines) + "\n", encoding="utf-8")
    return report_path


def _make_structure_entry(
    *,
    clip_name: str,
    summary: str,
    subject_tokens: list[str] | None = None,
    appearance_tokens: list[str] | None = None,
    pose_tokens: list[str] | None = None,
    main_character_notes: list[str] | None = None,
    continuity_notes: list[str] | None = None,
    mood: list[str] | None = None,
    relationships: list[str] | None = None,
    people_count: int = 1,
    shot_scale: int = 1,
    energy_level: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        recommended_index=1,
        candidate=SimpleNamespace(
            clip=SimpleNamespace(name=clip_name, duration=120),
            series_subject_tokens=list(subject_tokens or []),
            series_appearance_tokens=list(appearance_tokens or []),
            series_pose_tokens=list(pose_tokens or []),
            main_character_notes=list(main_character_notes or []),
            continuity_notes=list(continuity_notes or []),
            people_count=people_count,
            shot_scale=shot_scale,
            energy_level=energy_level,
            keywords=[],
            assets=SimpleNamespace(
                scene_analysis={
                    "summary": summary,
                    "background": "",
                    "shot_type": "",
                    "main_action": "",
                    "mood": list(mood or []),
                    "relationships": list(relationships or []),
                },
                prompt_text="",
            ),
        ),
    )


def _collect_sequence_file_id_counts(xml_path: Path, sequence_name: str) -> Counter[str]:
    root = ET.parse(xml_path).getroot()
    sequence_node = next(
        node for node in root.findall(".//sequence") if node.findtext("./name") == sequence_name
    )
    return Counter(
        file_node.attrib["id"]
        for file_node in sequence_node.findall(".//file")
        if file_node.attrib.get("id")
    )


def _serialized_filters(xml_path: Path, sequence_name: str, track_index: int, clip_name: str) -> list[str]:
    root = ET.parse(xml_path).getroot()
    sequence_node = next(
        node for node in root.findall(".//sequence") if node.findtext("./name") == sequence_name
    )
    track = sequence_node.findall("./media/video/track")[track_index]
    clipitem = next(node for node in track.findall("./clipitem") if node.findtext("./name") == clip_name)
    return [ET.tostring(filter_node, encoding="unicode") for filter_node in clipitem.findall("./filter")]


def _make_age_scene_analysis(
    apparent_age_group: str,
    role_in_scene: str,
    label: str,
    summary: str,
) -> dict[str, object]:
    return {
        "summary": summary,
        "people_count": 1,
        "people": [
            {
                "label": label,
                "position_in_frame": "center foreground",
                "role_in_scene": role_in_scene,
                "apparent_age_group": apparent_age_group,
                "face_visibility": "fully visible",
            }
        ],
        "background": "Living room interior with window light.",
        "shot_type": "medium shot",
        "main_action": "looking toward camera",
        "mood": ["warmth"],
        "relationships": ["family connection"],
    }


def _basic_motion_filter_xml(*, scale: float, keyframe_scale: float) -> str:
    return f"""
                <filter>
                  <effect>
                    <name>Basic Motion</name>
                    <effectid>basic</effectid>
                    <effectcategory>motion</effectcategory>
                    <effecttype>motion</effecttype>
                    <mediatype>video</mediatype>
                    <pproBypass>false</pproBypass>
                    <parameter authoringApp="PremierePro">
                      <parameterid>scale</parameterid>
                      <name>Scale</name>
                      <valuemin>0</valuemin>
                      <valuemax>1000</valuemax>
                      <value>{scale}</value>
                      <keyframe>
                        <when>90000</when>
                        <value>{keyframe_scale}</value>
                      </keyframe>
                    </parameter>
                    <parameter authoringApp="PremierePro">
                      <parameterid>center</parameterid>
                      <name>Center</name>
                      <value>
                        <horiz>0</horiz>
                        <vert>0</vert>
                      </value>
                      <keyframe>
                        <when>90000</when>
                        <value>
                          <horiz>0</horiz>
                          <vert>0.000976534</vert>
                        </value>
                      </keyframe>
                    </parameter>
                  </effect>
                </filter>"""


def _pathurl(path: Path) -> str:
    normalized = quote(str(path.resolve()).replace("\\", "/"))
    return f"file://localhost/{normalized}"
