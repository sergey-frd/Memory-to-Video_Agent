from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from models.video_sequence import PremiereSequenceClip
from utils.premiere_project import PREMIERE_TICKS_PER_SECOND
from utils.premiere_transform_script import (
    build_premiere_transform_extendscript,
    build_transform_script_jobs,
    write_premiere_transform_extendscript,
)


def test_build_transform_script_jobs_uses_optimizer_transform_plans() -> None:
    clips = [
        _clip("first.png", 0, 4),
        _clip("second.mp4", 4, 8),
        _clip("third.jpg", 8, 12),
    ]

    jobs = build_transform_script_jobs(
        clips,
        transform_plans=[
            {
                "media_kind": "image",
                "transform_key": "grow_portrait_focus",
                "effect_name": "Grow",
                "fallback_effect_name": "Transform",
                "start_scale": 100,
                "end_scale": 108,
                "reason": "portrait",
            },
            None,
            {
                "media_kind": "image",
                "transform_key": "shrink_group_photo",
                "effect_name": "Shrink",
                "fallback_effect_name": "Transform",
                "start_scale": 108,
                "end_scale": 100,
                "reason": "group",
            },
        ],
    )

    assert [job.clip_name for job in jobs] == ["first.png", "third.jpg"]
    assert [job.effect_name for job in jobs] == ["Grow", "Shrink"]
    assert jobs[0].start_scale == 100
    assert jobs[0].end_scale == 108
    assert jobs[1].clip_index == 2


def test_build_premiere_transform_extendscript_adds_plain_transform_by_default() -> None:
    jobs = build_transform_script_jobs([_clip("a.png", 0, 4)])

    script = build_premiere_transform_extendscript(
        sequence_name="Ivan26_o04",
        jobs=jobs,
        log_path=Path("C:/tmp/Ivan26_o04_apply_transforms.log"),
    )

    assert "app.enableQE()" in script
    assert "getVideoEffectByName" in script
    assert "addVideoEffect(effect)" in script
    assert "var ADD_VIDEO_EFFECTS = false" in script
    assert "var APPLY_SAFE_TRANSFORM_EFFECT = true" in script
    assert "addPlainTransformEffectToQeClip" in script
    assert "setValueAtKey" in script
    assert "setValueAtTime" in script
    assert "getKeys" in script
    assert "setSecondsAsFraction" in script
    assert "keyframes local" in script
    assert "KEYFRAME_SEQUENCE_OFFSET_SECONDS = 3600.0" in script
    assert "absolute" in script
    assert "Uniform Scale false" in script
    assert "clipDuration * 0.125" in script
    assert "readNumericParamValue" in script
    assert "baseScale * (Number(job.endScale) / 100.0)" in script
    assert "TRANSFORM_JOBS" in script
    assert "Ivan26_o04" in script
    assert "a.png" in script


def test_build_premiere_transform_extendscript_can_opt_into_video_effects() -> None:
    jobs = build_transform_script_jobs([_clip("a.png", 0, 4)])

    script = build_premiere_transform_extendscript(
        sequence_name="Ivan26_o04",
        jobs=jobs,
        add_video_effects=True,
    )

    assert "var ADD_VIDEO_EFFECTS = true" in script
    assert "requested effect mode is active; skipping intrinsic Motion / Scale keyframes" in script
    assert "no Motion / Scale fallback will be used in ADD_VIDEO_EFFECTS mode" in script


def test_build_premiere_transform_extendscript_can_disable_safe_effect() -> None:
    jobs = build_transform_script_jobs([_clip("a.png", 0, 4)])

    script = build_premiere_transform_extendscript(
        sequence_name="Ivan26_o04",
        jobs=jobs,
        apply_safe_transform_effect=False,
    )

    assert "var APPLY_SAFE_TRANSFORM_EFFECT = false" in script


def test_build_premiere_transform_extendscript_uses_motion_scale_when_safe_effect_is_disabled() -> None:
    jobs = build_transform_script_jobs([_clip("a.png", 0, 4)])

    script = build_premiere_transform_extendscript(
        sequence_name="Ivan26_o04",
        jobs=jobs,
        apply_safe_transform_effect=False,
    )

    assert "Motion / Scale" in script
    assert "if (APPLY_SAFE_TRANSFORM_EFFECT && addPlainTransformEffectToQeClip" in script


def test_write_transform_script_derives_plan_from_legacy_report() -> None:
    root = Path("test_runtime") / f"premiere_transform_script_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "sample.prproj"
    report_path = root / "sequence.json"
    output_jsx = root / "apply_transforms.jsx"

    _write_sample_project(project_path)
    report_path.write_text(
        """{
  "entries": [
    {
      "candidate": {
        "people_count": 4,
        "shot_scale": 1,
        "energy_level": 1,
        "clip": {
          "name": "group.png",
          "source_path": "C:/media/group.png"
        },
        "assets": {
          "scene_analysis": {
            "summary": "family group"
          },
          "prompt_text": ""
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )

    _output_path, jobs = write_premiere_transform_extendscript(
        project_path=project_path,
        sequence_name="Sequence",
        output_jsx_path=output_jsx,
        optimization_report_json=report_path,
    )

    assert jobs[0].effect_name == "Shrink"
    assert jobs[0].start_scale == 108
    assert "Shrink" in output_jsx.read_text(encoding="utf-8")


def _clip(name: str, start_seconds: int, end_seconds: int) -> PremiereSequenceClip:
    start = start_seconds * PREMIERE_TICKS_PER_SECOND
    end = end_seconds * PREMIERE_TICKS_PER_SECOND
    return PremiereSequenceClip(
        sequence_name="Sequence",
        order_index=start_seconds + 1,
        track_index=1,
        clipitem_id=f"track-item-{name}",
        name=name,
        source_path=f"C:/media/{name}",
        start=start,
        end=end,
        in_point=0,
        out_point=end - start,
        duration=end - start,
        stage_id=Path(name).stem,
        video_index=1,
    )


def _write_sample_project(path: Path) -> None:
    import gzip
    import xml.etree.ElementTree as ET

    root = ET.Element("Project")
    sequence = ET.SubElement(root, "Sequence")
    ET.SubElement(sequence, "Name").text = "Sequence"

    track_group_ref = ET.SubElement(sequence, "TrackGroups")
    track_group = ET.SubElement(track_group_ref, "TrackGroup", {"Index": "0"})
    ET.SubElement(track_group, "Second", {"ObjectRef": "group-1"})

    group_node = ET.SubElement(root, "TrackGroup", {"ObjectID": "group-1"})
    tracks = ET.SubElement(ET.SubElement(group_node, "TrackGroup"), "Tracks")
    ET.SubElement(tracks, "Track", {"Index": "0", "ObjectURef": "track-1"})

    track = ET.SubElement(root, "Track", {"ObjectUID": "track-1"})
    track_items = ET.SubElement(ET.SubElement(ET.SubElement(track, "ClipTrack"), "ClipItems"), "TrackItems")
    ET.SubElement(track_items, "TrackItem", {"ObjectRef": "item-1"})

    item = ET.SubElement(root, "TrackItem", {"ObjectID": "item-1"})
    clip_track_item = ET.SubElement(item, "ClipTrackItem")
    ET.SubElement(clip_track_item, "SubClip", {"ObjectRef": "subclip-1"})
    ET.SubElement(clip_track_item, "TrackItem").extend(
        [
            _node("Start", "0"),
            _node("End", str(4 * PREMIERE_TICKS_PER_SECOND)),
        ]
    )

    subclip = ET.SubElement(root, "SubClip", {"ObjectID": "subclip-1"})
    ET.SubElement(subclip, "Name").text = "group.png"
    ET.SubElement(subclip, "Clip", {"ObjectRef": "clip-1"})

    clip = ET.SubElement(root, "Clip", {"ObjectID": "clip-1"})
    clip_node = ET.SubElement(clip, "Clip")
    ET.SubElement(clip_node, "Source", {"ObjectRef": "source-1"})
    ET.SubElement(clip_node, "InPoint").text = "0"
    ET.SubElement(clip_node, "OutPoint").text = str(4 * PREMIERE_TICKS_PER_SECOND)

    source = ET.SubElement(root, "Source", {"ObjectID": "source-1"})
    media_source = ET.SubElement(source, "MediaSource")
    ET.SubElement(media_source, "Media", {"ObjectURef": "media-1"})

    media = ET.SubElement(root, "Media", {"ObjectUID": "media-1"})
    ET.SubElement(media, "ActualMediaFilePath").text = "C:/media/group.png"

    path.write_bytes(gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True)))


def _node(tag: str, text: str) -> object:
    import xml.etree.ElementTree as ET

    node = ET.Element(tag)
    node.text = text
    return node
