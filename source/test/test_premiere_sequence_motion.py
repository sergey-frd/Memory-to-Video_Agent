from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import main_premiere_import_keep
from models.premiere_sequence_motion import MotionProfile
from utils.premiere_sequence_motion import (
    MOTION_MODE,
    _TrackItemContext,
    _overlaps,
    _parse_profiles,
    _remove_all_audio_clips,
    _set_param_keyframes,
    _motion_values,
    build_position_keyframes,
    build_scale_keyframes,
    is_premiere_sequence_motion_config,
    protected_property_snapshot,
    select_motion_profile,
    validate_milestone_sequence_version,
    validate_premiere_sequence_motion_config,
)


def _minimal_config() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "mode": MOTION_MODE,
        "project": {
            "project_file": r"<LOCAL_PATH>",
            "save_as_project_file": r"<LOCAL_PATH>",
        },
        "sequences": {
            "source_sequence_name": "source",
            "output_sequence_name": "output",
        },
        "sequence_contract": {
            "edit_timebase_fps": 25,
            "expected_frames": 100,
        },
        "target_selection": {
            "minimum_visible_duration_frames": 21,
        },
        "motion_animation": {
            "motion_profiles": [
                {
                    "name": "SHORT",
                    "visible_duration_frames_min": 21,
                    "visible_duration_frames_max": 37,
                    "scale_delta_percent_of_baseline": 1.5,
                    "max_position_delta_percent_of_frame": 0.35,
                }
            ],
            "direction_cycle": ["PUSH_IN"],
        },
        "audio_policy": {"mode": "OUTPUT_SILENT"},
        "dry_run": {"required": True},
        "review_export": {"filename": "review.mp4"},
    }


def test_motion_config_detection_and_validation() -> None:
    payload = _minimal_config()
    assert is_premiere_sequence_motion_config(payload)
    assert validate_premiere_sequence_motion_config(payload) is payload
    payload["schema_version"] = "2.0"
    with pytest.raises(ValueError, match="schema_version"):
        validate_premiere_sequence_motion_config(payload)


def test_select_motion_profile_uses_inclusive_frame_bounds() -> None:
    profiles = [
        MotionProfile("SHORT", 21, 37, 1.5, 0.35),
        MotionProfile("MEDIUM", 38, 75, 3.0, 0.7),
        MotionProfile("LONG", 76, None, 5.0, 1.0),
    ]
    assert select_motion_profile(21, profiles).name == "SHORT"
    assert select_motion_profile(37, profiles).name == "SHORT"
    assert select_motion_profile(38, profiles).name == "MEDIUM"
    assert select_motion_profile(76, profiles).name == "LONG"


def test_motion_values_are_relative_to_existing_baseline() -> None:
    profile = MotionProfile("LONG", 76, None, 5.0, 1.0)
    push_in = _motion_values(
        baseline_scale=149.171264648438,
        baseline_x=0.5,
        baseline_y=0.5,
        profile=profile,
        direction="PUSH_IN",
    )
    assert push_in[0] == pytest.approx(149.171264648438)
    assert push_in[1] == pytest.approx(149.171264648438 * 1.05)
    assert push_in[2:] == pytest.approx((0.5, 0.5, 0.5, 0.5))


def test_motion_keyframe_serialization_has_two_frame_exact_entries() -> None:
    scale = build_scale_keyframes(100, 200, 149.171264648438, 156.62982788086)
    position = build_position_keyframes(100, 200, 0.5, 0.5, 0.5035, 0.5)
    assert scale.count(";") == 2
    assert scale.startswith("100,149.171264648438,")
    assert "200,156.62982788086," in scale
    assert position.count(";") == 2
    assert position.startswith("100,0.5:0.5,")
    assert "200,0.5035:0.5," in position


def test_setting_motion_keyframes_marks_parameter_time_varying() -> None:
    param = ET.fromstring(
        "<VideoComponentParam><Name>Scale</Name>"
        "<StartKeyframe>-1,149.,0,0,0,0,0,0</StartKeyframe>"
        "</VideoComponentParam>"
    )
    _set_param_keyframes(
        param,
        keyframes=build_scale_keyframes(100, 200, 149.0, 153.47),
        current_value="153.47",
    )
    assert param.findtext("./IsTimeVarying") == "true"
    assert (param.findtext("./Keyframes") or "").count(";") == 2
    assert param.findtext("./ParameterControlType") == "2"


def test_main_dispatches_motion_mode_and_forwards_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = (Path("dry.json"), Path("implementation.txt"), None)
    calls: list[tuple[Path, bool]] = []

    def fake_run(
        config_path: Path,
        *,
        dry_run_only: bool = False,
    ) -> tuple[Path, Path, Path | None]:
        calls.append((config_path, dry_run_only))
        return expected

    monkeypatch.setattr(
        main_premiere_import_keep,
        "run_premiere_sequence_motion_from_config",
        fake_run,
    )
    result = main_premiere_import_keep.try_run_premiere_import_keep(
        Path("motion.json"),
        {"mode": MOTION_MODE},
        dry_run=True,
    )
    assert result == ("Premiere sequence motion", *expected)
    assert calls == [(Path("motion.json"), True)]


def test_protected_overlap_excludes_full_and_partial_items() -> None:
    protected = [(325, 483)]
    assert _overlaps(325, 483, protected)
    assert _overlaps(300, 326, protected)
    assert _overlaps(482, 500, protected)
    assert not _overlaps(0, 325, protected)
    assert not _overlaps(483, 600, protected)


def test_visible_profiles_are_read_from_json() -> None:
    profiles = _parse_profiles(
        {
            "motion_profiles": [
                {
                    "name": "SHORT_VISIBLE",
                    "visible_duration_frames_min": 21,
                    "visible_duration_frames_max": 37,
                    "scale_delta_percent_of_baseline": 3.0,
                    "max_position_delta_percent_of_frame": 0.8,
                },
                {
                    "name": "MEDIUM_LIVELY",
                    "visible_duration_frames_min": 38,
                    "visible_duration_frames_max": 75,
                    "scale_delta_percent_of_baseline": 5.5,
                    "max_position_delta_percent_of_frame": 1.5,
                },
                {
                    "name": "LONG_EXPRESSIVE",
                    "visible_duration_frames_min": 76,
                    "visible_duration_frames_max": None,
                    "scale_delta_percent_of_baseline": 8.0,
                    "max_position_delta_percent_of_frame": 2.5,
                },
            ]
        }
    )
    assert [profile.scale_delta_percent for profile in profiles] == [3.0, 5.5, 8.0]
    values = _motion_values(
        baseline_scale=150.0,
        baseline_x=0.5,
        baseline_y=0.5,
        profile=profiles[-1],
        direction="PUSH_IN",
    )
    assert values[1] == pytest.approx(162.0)


def test_linear_keyframes_have_no_temporal_ease_handles() -> None:
    scale = build_scale_keyframes(
        100,
        200,
        150.0,
        162.0,
        interpolation="LINEAR_OR_NEAR_LINEAR_WITH_NO_STATIONARY_HEAD_OR_TAIL",
    )
    position = build_position_keyframes(
        100,
        200,
        0.49,
        0.5,
        0.51,
        0.5,
        interpolation="LINEAR_OR_NEAR_LINEAR_WITH_NO_STATIONARY_HEAD_OR_TAIL",
    )
    assert all(entry.split(",")[2:4] == ["0", "0"] for entry in scale.split(";") if entry)
    assert all(entry.split(",")[2:4] == ["0", "0"] for entry in position.split(";") if entry)


def test_milestone_v10_is_accepted_and_intermediate_version_is_rejected() -> None:
    assert validate_milestone_sequence_version(
        "Yt_macro_styles_KEEP_v10",
        increment=5,
        expected_milestone=10,
    ) == 10
    with pytest.raises(ValueError, match="expected milestone"):
        validate_milestone_sequence_version(
            "Yt_macro_styles_KEEP_v09",
            increment=5,
            expected_milestone=10,
        )


def _protected_snapshot_fixture(
    *,
    chain_id: str,
    component_id: str,
    param_id: str,
    item_id: str,
) -> tuple[_TrackItemContext, dict[str, ET.Element]]:
    root = ET.fromstring(
        f"""
<PremiereData>
  <VideoComponentChain ObjectID="{chain_id}">
    <ComponentChain><Components><Component ObjectRef="{component_id}" /></Components></ComponentChain>
  </VideoComponentChain>
  <VideoFilterComponent ObjectID="{component_id}">
    <Component>
      <DisplayName>Motion</DisplayName><Intrinsic>true</Intrinsic>
      <Params><Param ObjectRef="{param_id}" /></Params>
    </Component>
    <MatchName>AE.ADBE Motion</MatchName>
  </VideoFilterComponent>
  <VideoComponentParam ObjectID="{param_id}">
    <Name>Scale</Name><ParameterID>2</ParameterID><ParameterControlType>2</ParameterControlType>
    <IsTimeVarying>false</IsTimeVarying>
    <StartKeyframe>-1,150.,0,0,0,0,0,0</StartKeyframe>
  </VideoComponentParam>
  <VideoClipTrackItem ObjectID="{item_id}">
    <ClipTrackItem>
      <ComponentOwner><Components ObjectRef="{chain_id}" /></ComponentOwner>
      <TrackItem><Start>325</Start><End>483</End></TrackItem>
    </ClipTrackItem>
    <PixelAspectRatio>1,1</PixelAspectRatio><FrameRect>0,0,3840,2160</FrameRect>
  </VideoClipTrackItem>
</PremiereData>
"""
    )
    lookup = {
        node.attrib["ObjectID"]: node
        for node in root.iter()
        if node.attrib.get("ObjectID")
    }
    item = lookup[item_id]
    context = _TrackItemContext(
        track_index=1,
        track_node=ET.Element("VideoClipTrack"),
        track_item_ref=ET.Element("TrackItem"),
        track_item_node=item,
        name="live.mp4",
        source_path=r"<LOCAL_PATH>",
        start=325,
        end=483,
        source_in=105,
        source_out=263,
    )
    return context, lookup


def test_protected_property_snapshot_ignores_cloned_object_ids() -> None:
    source, source_lookup = _protected_snapshot_fixture(
        chain_id="1", component_id="2", param_id="3", item_id="4"
    )
    output, output_lookup = _protected_snapshot_fixture(
        chain_id="101", component_id="102", param_id="103", item_id="104"
    )
    assert protected_property_snapshot(
        source, source_lookup
    ) == protected_property_snapshot(output, output_lookup)


def test_audio_removal_is_non_ripple_and_leaves_video_group_unchanged() -> None:
    root = ET.fromstring(
        """
<PremiereData>
  <Sequence ObjectUID="sequence">
    <TrackGroups>
      <TrackGroup Index="0"><Second ObjectRef="10" /></TrackGroup>
      <TrackGroup Index="1"><Second ObjectRef="20" /></TrackGroup>
    </TrackGroups>
  </Sequence>
  <VideoTrackGroup ObjectID="10"><TrackGroup><Tracks /></TrackGroup></VideoTrackGroup>
  <AudioTrackGroup ObjectID="20">
    <TrackGroup><Tracks><Track Index="0" ObjectURef="audio-track" /></Tracks></TrackGroup>
  </AudioTrackGroup>
  <AudioClipTrack ObjectUID="audio-track">
    <ClipTrack>
      <ClipItems><TrackItems><TrackItem Index="0" ObjectRef="30" /></TrackItems></ClipItems>
      <TransitionItems><TrackItems><TrackItem Index="0" ObjectRef="31" /></TrackItems></TransitionItems>
    </ClipTrack>
  </AudioClipTrack>
  <AudioClipTrackItem ObjectID="30" />
  <AudioTransitionTrackItem ObjectID="31" />
</PremiereData>
"""
    )
    sequence = root.find("./Sequence")
    assert sequence is not None
    id_lookup = {
        node.attrib["ObjectID"]: node
        for node in root.iter()
        if node.attrib.get("ObjectID")
    }
    uid_lookup = {
        node.attrib["ObjectUID"]: node
        for node in root.iter()
        if node.attrib.get("ObjectUID")
    }
    video_before = ET.tostring(
        sequence.find("./TrackGroups/TrackGroup[@Index='0']"), encoding="utf-8"
    )
    assert _remove_all_audio_clips(
        sequence,
        id_lookup=id_lookup,
        uid_lookup=uid_lookup,
    ) == 1
    assert ET.tostring(
        sequence.find("./TrackGroups/TrackGroup[@Index='0']"), encoding="utf-8"
    ) == video_before
    audio_track = uid_lookup["audio-track"]
    assert audio_track.findall("./ClipTrack/ClipItems/TrackItems/TrackItem") == []
    assert audio_track.findall("./ClipTrack/TransitionItems/TrackItems/TrackItem") == []
