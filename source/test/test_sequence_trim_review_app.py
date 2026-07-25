from __future__ import annotations

import gzip
import json
from pathlib import Path
from uuid import uuid4

from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision, TrimSegmentDecision
from models.video_sequence import PremiereSequenceClip
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    iter_project_track_item_refs,
    load_premiere_project_root,
    parse_premiere_project_sequence_visual_clips,
    resolve_project_track_item_name,
)
from utils.sequence_trim_classifier import classify_sequence_trim_review
from utils.premiere_trim_review_export import export_trim_review_premiere_projects
from utils.sequence_trim_review import run_sequence_trim_review_from_config


def _ticks(seconds: float) -> int:
    return int(seconds * PREMIERE_TICKS_PER_SECOND)


def _clip(
    *,
    order_index: int,
    name: str,
    start_s: float,
    duration_s: float,
    clipitem_id: str,
) -> PremiereSequenceClip:
    start = _ticks(start_s)
    duration = _ticks(duration_s)
    return PremiereSequenceClip(
        sequence_name="RawSequence",
        order_index=order_index,
        track_index=1,
        clipitem_id=clipitem_id,
        name=name,
        source_path=f"E:/media/{name}",
        start=start,
        end=start + duration,
        in_point=0,
        out_point=duration,
        duration=duration,
        stage_id=Path(name).stem,
        video_index=1,
    )


def test_classify_sequence_trim_review_splits_inside_long_clips() -> None:
    clips = [
        _clip(order_index=1, name="intro_wide.mp4", start_s=0, duration_s=8, clipitem_id="1"),
        _clip(order_index=2, name="walk_long.mp4", start_s=8, duration_s=90, clipitem_id="2"),
        _clip(order_index=3, name="birthday_dance.mp4", start_s=98, duration_s=40, clipitem_id="3"),
        _clip(order_index=4, name="trash_outtake.mp4", start_s=138, duration_s=20, clipitem_id="4"),
        _clip(order_index=5, name="smile_close.mp4", start_s=158, duration_s=10, clipitem_id="5"),
        _clip(order_index=6, name="portrait_hold.jpg", start_s=168, duration_s=4, clipitem_id="6"),
    ]

    result = classify_sequence_trim_review(
        clips,
        source_project_path=Path("dummy.prproj"),
        source_sequence_name="RawSequence",
        new_sequence_name="RawSequence_trim_review",
        target_keep_seconds=40,
        min_keep_seconds=20,
        max_keep_seconds=40,
        context_notes="Birthday for Maya",
        force_keep_names=["birthday_dance"],
        force_drop_names=["trash_outtake"],
    )

    by_name = {item.name: item for item in result.decisions}
    assert by_name["birthday_dance.mp4"].decision in {"keep", "mixed"}
    assert 2.0 <= by_name["birthday_dance.mp4"].keep_seconds <= 8.0 + 0.05
    assert by_name["trash_outtake.mp4"].decision == "drop"
    assert by_name["trash_outtake.mp4"].keep_seconds == 0
    assert by_name["walk_long.mp4"].decision == "mixed"
    assert 2.0 <= by_name["walk_long.mp4"].keep_seconds <= 8.0 + 0.05
    assert by_name["walk_long.mp4"].drop_seconds > 0
    assert 1.5 <= by_name["portrait_hold.jpg"].keep_seconds <= 3.0 + 0.05
    assert by_name["portrait_hold.jpg"].drop_seconds >= 0.9
    assert result.keep_seconds >= 20.0
    assert any(seg.decision == "keep" for seg in by_name["walk_long.mp4"].segments)
    assert any(seg.decision == "drop" for seg in by_name["walk_long.mp4"].segments)
    assert "compact" in result.engine


def test_run_sequence_trim_review_from_config_writes_segmented_tracks() -> None:
    root = Path("test_runtime") / f"trim_review_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "raw.prproj"
    _write_two_track_project(project_path)

    output_project = root / "raw_trim_review.prproj"
    reports_dir = root / "reports"
    config_path = root / "trim_review.json"
    config_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "source_sequence_name": "RawSequence",
                "new_sequence_name": "RawSequence_trim_review",
                "new_sequence_name_heuristic": "RawSequence_trim_review",
                "output_project_path": str(output_project),
                "reports_dir": str(reports_dir),
                "target_keep_seconds": 20,
                "min_keep_seconds": 8,
                "max_keep_seconds": 20,
                "context_notes": "Keep the birthday moment.",
                "force_keep_names": ["clip_b"],
                "force_drop_names": ["clip_c"],
                "engines": ["heuristic"],
                "split_tracks": True,
                "keep_track_index": 0,
                "drop_track_index": 1,
                "write_project": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, txt_path, exported_project = run_sequence_trim_review_from_config(config_path)

    assert json_path.exists()
    assert txt_path.exists()
    assert exported_project is not None
    assert exported_project.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["engines"] == ["heuristic_segment_budget_v1_compact"]
    assert any(item["name"].startswith("clip_b") for item in payload["results"][0]["decisions"])
    assert any(len(item["segments"]) >= 1 for item in payload["results"][0]["decisions"])

    project_root = load_premiere_project_root(exported_project)
    id_lookup = build_project_object_id_lookup(project_root)
    uid_lookup = build_project_object_uid_lookup(project_root)
    sequence_node = find_project_sequence_node(project_root, "RawSequence_trim_review")
    assert sequence_node is not None
    tracks = {
        track_index: track_node
        for track_index, track_node in get_project_track_nodes(
            sequence_node,
            track_group_index=0,
            object_id_lookup=id_lookup,
            object_uid_lookup=uid_lookup,
        )
    }
    v1_names = [
        resolve_project_track_item_name(id_lookup[ref.attrib["ObjectRef"]], id_lookup)
        for ref in iter_project_track_item_refs(tracks[0])
        if ref.attrib.get("ObjectRef") in id_lookup
    ]
    v2_names = [
        resolve_project_track_item_name(id_lookup[ref.attrib["ObjectRef"]], id_lookup)
        for ref in iter_project_track_item_refs(tracks[1])
        if ref.attrib.get("ObjectRef") in id_lookup
    ]
    assert any(name.startswith("[KEEP]") for name in v1_names)
    assert any(name.startswith("[DROP]") for name in v2_names)

    report_text = txt_path.read_text(encoding="utf-8")
    assert "Per-clip segment plan" in report_text
    assert "KEEP" in report_text and "DROP" in report_text


def test_export_hero_levels_to_four_tracks_in_one_sequence() -> None:
    root = Path("test_runtime") / f"trim_filtered_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "raw.prproj"
    _write_two_track_project(project_path)
    _sequence_name, clips = parse_premiere_project_sequence_visual_clips(project_path, "RawSequence")
    decisions: list[TrimClipDecision] = []
    for index, clip in enumerate(clips):
        level = ("high", "medium", "absent")[index]
        segment_decision = "drop" if level == "absent" else "keep"
        segments = [
            TrimSegmentDecision(
                segment_index=1,
                decision=segment_decision,
                local_start=0,
                local_end=clip.duration,
                timeline_start=clip.start,
                timeline_end=clip.end,
                source_in=clip.in_point,
                source_out=clip.out_point,
                duration=clip.duration,
                duration_seconds=clip.duration / PREMIERE_TICKS_PER_SECOND,
                reason=f"hero {level}",
                confidence=0.95,
                hero_match_level=level,
            )
        ]
        decisions.append(
            TrimClipDecision(
                order_index=clip.order_index,
                clipitem_id=clip.clipitem_id,
                name=clip.name,
                source_path=clip.source_path,
                track_index=clip.track_index,
                start=clip.start,
                end=clip.end,
                duration=clip.duration,
                duration_seconds=clip.duration / PREMIERE_TICKS_PER_SECOND,
                source_in=clip.in_point,
                source_out=clip.out_point,
                keep_seconds=segments[0].duration_seconds if segment_decision == "keep" else 0,
                drop_seconds=segments[0].duration_seconds if segment_decision == "drop" else 0,
                score=0.95,
                reason="hero level",
                confidence=0.95,
                decision=segment_decision,
                segments=segments,
                hero_match_level=level,
            )
        )
    result = SequenceTrimReviewResult(
        source_project_path=str(project_path),
        source_sequence_name="RawSequence",
        new_sequence_name="FourTracks",
        engine="hero_report_replay_tracks_v1",
        target_keep_seconds=0,
        min_keep_seconds=0,
        max_keep_seconds=100,
        total_source_seconds=70,
        keep_seconds=50,
        drop_seconds=20,
        context_notes="",
        decisions=decisions,
    )
    output_path = root / "filtered.prproj"
    export_trim_review_premiere_projects(
        source_project_path=project_path,
        review_results=[result],
        output_project_path=output_path,
        split_tracks=False,
        hero_level_track_indexes={"high": 0, "medium": 1, "review": 2, "drop": 3},
    )

    project_root = load_premiere_project_root(output_path)
    id_lookup = build_project_object_id_lookup(project_root)
    uid_lookup = build_project_object_uid_lookup(project_root)
    sequence_node = find_project_sequence_node(project_root, "FourTracks")
    assert sequence_node is not None
    names_by_track = {
        track_index: [
            resolve_project_track_item_name(id_lookup[ref.attrib["ObjectRef"]], id_lookup)
            for ref in iter_project_track_item_refs(track_node)
            if ref.attrib.get("ObjectRef") in id_lookup
        ]
        for track_index, track_node in get_project_track_nodes(
            sequence_node,
            track_group_index=0,
            object_id_lookup=id_lookup,
            object_uid_lookup=uid_lookup,
        )
    }
    assert names_by_track == {
        0: ["[KEEP-HIGH] s1 clip_a.mp4"],
        1: ["[KEEP-MEDIUM] s1 clip_b_birthday.mp4"],
        2: [],
        3: ["[DROP] s1 clip_c_trash.mp4"],
    }


def _write_two_track_project(project_path: Path) -> None:
    durations = [10, 40, 20]
    starts = [0, 10, 50]
    names = ["clip_a.mp4", "clip_b_birthday.mp4", "clip_c_trash.mp4"]
    track_items_v1 = []
    objects = []
    object_id = 2000
    for index, (name, start_s, duration_s) in enumerate(zip(names, starts, durations)):
        start = _ticks(start_s)
        end = _ticks(start_s + duration_s)
        item_id = object_id
        subclip_id = object_id + 1
        clip_id = object_id + 2
        media_id = object_id + 3
        object_id += 10
        track_items_v1.append(f'          <TrackItem Index="{index}" ObjectRef="{item_id}" />')
        objects.append(
            f"""
  <VideoClipTrackItem ObjectID="{item_id}" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{start}</Start>
        <End>{end}</End>
      </TrackItem>
      <SubClip ObjectRef="{subclip_id}" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="{subclip_id}" ClassID="subclip" Version="1">
    <Name>{name}</Name>
    <Clip ObjectRef="{clip_id}" />
  </SubClip>
  <VideoClip ObjectID="{clip_id}" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>{end - start}</OutPoint>
      <Source ObjectRef="{media_id}" />
      <ClipID>00000000-0000-0000-0000-00000000{index:04d}</ClipID>
    </Clip>
  </VideoClip>
  <VideoMediaSource ObjectID="{media_id}" ClassID="video-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-{index}" />
    </MediaSource>
  </VideoMediaSource>
  <Media ObjectUID="media-{index}" ClassID="media" Version="1">
    <ActualMediaFilePath>E:/media/{name}</ActualMediaFilePath>
  </Media>
"""
        )

    project_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <RootProjectItem ObjectURef="root-project" />
  <RootProjectItem ObjectUID="root-project" ClassID="root-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>Root</Name>
    </ProjectItem>
    <ProjectItemContainer Version="1">
      <Items Version="1">
        <Item Index="0" ObjectURef="project-item-raw" />
      </Items>
    </ProjectItemContainer>
  </RootProjectItem>
  <ClipProjectItem ObjectUID="project-item-raw" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>RawSequence</Name>
      <Node Version="1">
        <Properties Version="1">
          <project.icon.view.grid.order>1</project.icon.view.grid.order>
        </Properties>
      </Node>
    </ProjectItem>
    <MasterClip ObjectURef="master-raw" />
  </ClipProjectItem>
  <MasterClip ObjectUID="master-raw" ClassID="master-clip" Version="1">
    <Name>RawSequence</Name>
  </MasterClip>
  <Sequence ObjectUID="seq-raw" ClassID="sequence" Version="1">
    <Node Version="1">
      <Properties Version="1">
        <MZ.WorkOutPoint>{_ticks(70)}</MZ.WorkOutPoint>
        <MZ.EditLine>{_ticks(70)}</MZ.EditLine>
      </Properties>
    </Node>
    <TrackGroups Version="1">
      <TrackGroup Version="1" Index="0">
        <Second ObjectRef="1000" />
      </TrackGroup>
      <TrackGroup Version="1" Index="1">
        <Second ObjectRef="1001" />
      </TrackGroup>
    </TrackGroups>
    <Name>RawSequence</Name>
  </Sequence>
  <VideoTrackGroup ObjectID="1000" ClassID="video-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-v1" />
        <Track Index="1" ObjectURef="track-v2" />
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <AudioTrackGroup ObjectID="1001" ClassID="audio-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1" />
    </TrackGroup>
  </AudioTrackGroup>
  <VideoClipTrack ObjectUID="track-v1" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
{chr(10).join(track_items_v1)}
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrack ObjectUID="track-v2" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <MediaType />
        <Index />
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  {''.join(objects)}
</PremiereData>
"""
    project_path.write_bytes(gzip.compress(project_xml.encode("utf-8")))
