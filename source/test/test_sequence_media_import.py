from __future__ import annotations

import gzip
import json
from pathlib import Path
from uuid import uuid4

from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    iter_project_track_item_refs,
    list_named_project_sequence_names,
    load_premiere_project_root,
    parse_premiere_project_sequence_visual_clips,
    resolve_project_clip_media_node,
    resolve_project_track_item_clip,
    resolve_project_track_item_name,
    resolve_project_track_item_subclip,
)
from utils.sequence_media_import import (
    ImportFileLookupError,
    is_media_import_config,
    resolve_import_files,
    run_sequence_media_import_from_config,
)
from utils.sequence_trim_classifier import ticks_to_seconds


def _ticks(seconds: float) -> int:
    return int(round(seconds * PREMIERE_TICKS_PER_SECOND))


def test_resolve_import_files_matches_exact_filename_only() -> None:
    root = Path("test_runtime") / f"import_resolve_{uuid4().hex}"
    nested = root / "img" / "VD"
    nested.mkdir(parents=True)
    (nested / "IMG_4531.MP4").write_bytes(b"video")
    (nested / "IMG_4531_artp.png").write_bytes(b"style")
    (root / "img" / "JPEG").mkdir(parents=True)
    (root / "img" / "JPEG" / "IMG_4793.jpg").write_bytes(b"photo")
    resolved = resolve_import_files(root, ["IMG_4531.MP4", "IMG_4793.jpg"])
    assert [path.name for path in resolved] == ["IMG_4531.MP4", "IMG_4793.jpg"]
    assert resolved[0] == nested / "IMG_4531.MP4"


def test_resolve_import_files_stops_when_name_is_missing() -> None:
    root = Path("test_runtime") / f"import_missing_{uuid4().hex}"
    root.mkdir(parents=True)
    (root / "IMG_4531.MP4").write_bytes(b"video")
    try:
        resolve_import_files(root, ["IMG_4531.MP4", "absent.mov"])
    except ImportFileLookupError as exc:
        assert "absent.mov" in str(exc)
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected ImportFileLookupError")


def test_resolve_import_files_stops_on_duplicate_exact_names() -> None:
    root = Path("test_runtime") / f"import_dup_{uuid4().hex}"
    first = root / "chatgpt_all_styles" / "IMG_4859"
    second = root / "chatgpt_watercolor_on_paper"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "IMG_4859_wcp.png").write_bytes(b"all")
    (second / "IMG_4859_wcp.png").write_bytes(b"wcp")
    try:
        resolve_import_files(root, ["IMG_4859_wcp.png"])
    except ImportFileLookupError as exc:
        message = str(exc)
        assert "IMG_4859_wcp.png" in message
        assert "2 files" in message
        assert str(first / "IMG_4859_wcp.png") in message
        assert str(second / "IMG_4859_wcp.png") in message
    else:
        raise AssertionError("expected ImportFileLookupError")


def test_resolve_import_files_does_not_use_prefix_or_casefold_match() -> None:
    root = Path("test_runtime") / f"import_prefix_{uuid4().hex}"
    root.mkdir(parents=True)
    (root / "IMG_4859_artp.png").write_bytes(b"style")
    (root / "img_4531.mp4").write_bytes(b"video")
    try:
        resolve_import_files(root, ["IMG_4859.png", "IMG_4531.MP4"])
    except ImportFileLookupError as exc:
        message = str(exc)
        assert "IMG_4859.png" in message
        assert "IMG_4531.MP4" in message
        assert "different case" in message
    else:
        raise AssertionError("expected ImportFileLookupError")


def test_resolve_import_files_uses_relative_path_for_duplicate_names() -> None:
    root = Path("test_runtime") / f"import_rel_{uuid4().hex}"
    first = root / "output" / "chatgpt_all_styles" / "260806_08"
    second = root / "output" / "chatgpt_watercolor_on_paper"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    chosen = first / "260806_08__wcp.png"
    other = second / "260806_08__wcp.png"
    chosen.write_bytes(b"all")
    other.write_bytes(b"wcp")
    unique = root / "IMG_4530.MP4"
    unique.write_bytes(b"video")
    resolved = resolve_import_files(
        root,
        [
            "IMG_4530.MP4",
            {
                "file": "260806_08__wcp.png",
                "relative_path": "output\\chatgpt_all_styles\\260806_08\\260806_08__wcp.png",
            },
        ],
    )
    assert [path.resolve() for path in resolved] == [unique.resolve(), chosen.resolve()]


def test_resolve_import_files_rejects_relative_path_name_mismatch() -> None:
    root = Path("test_runtime") / f"import_rel_bad_{uuid4().hex}"
    target = root / "output" / "a.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"img")
    try:
        resolve_import_files(
            root,
            [{"file": "b.png", "relative_path": "output\\a.png"}],
        )
    except ImportFileLookupError as exc:
        assert "b.png" in str(exc)
        assert "basename" in str(exc)
    else:
        raise AssertionError("expected ImportFileLookupError")


def test_resolve_import_files_uses_absolute_source_path_and_order() -> None:
    root = Path("test_runtime") / f"import_abs_{uuid4().hex}"
    root.mkdir(parents=True)
    first = root / "later.mp4"
    second = root / "first.jpg"
    first.write_bytes(b"video")
    second.write_bytes(b"photo")
    resolved = resolve_import_files(
        None,
        [
            {"order": 2, "source_path": str(first)},
            {"order": 1, "source_path": str(second)},
        ],
    )
    assert [path.resolve() for path in resolved] == [second.resolve(), first.resolve()]


def test_is_media_import_config_detects_items_source_path() -> None:
    assert is_media_import_config(
        {
            "project_path": r"<LOCAL_PATH>",
            "sequence_name": "Ready",
            "items": [{"order": 1, "source_path": r"<LOCAL_PATH>"}],
        }
    )


def test_run_sequence_media_import_creates_sequence_and_appends_listed_files() -> None:
    root = Path("test_runtime") / f"import_run_{uuid4().hex}"
    media_root = root / "media"
    (media_root / "img" / "VD").mkdir(parents=True)
    (media_root / "img" / "JPEG").mkdir(parents=True)
    new_video = media_root / "img" / "VD" / "new_take.mp4"
    new_photo = media_root / "img" / "JPEG" / "new_still.jpg"
    new_video.write_bytes(b"video")
    new_photo.write_bytes(b"photo")
    existing_copy = media_root / "img" / "VD" / "clip_a.mp4"
    existing_copy.write_bytes(b"existing")

    project_path = root / "raw.prproj"
    _write_import_source_project(project_path)
    original_bytes = project_path.read_bytes()
    output_project = root / "raw_import.prproj"
    config_path = root / "import.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "import_media",
                "project_path": str(project_path),
                "sequence_name": "ImportedSequence",
                "create_sequence_if_missing": True,
                "root_directory": str(media_root),
                "files": ["clip_a.mp4", "new_take.mp4", "new_still.jpg"],
                "still_duration_seconds": 5,
                "output_project_path": str(output_project),
                "reports_dir": str(root / "reports"),
                "write_project": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, txt_path, exported = run_sequence_media_import_from_config(config_path)

    assert exported == output_project
    assert project_path.read_bytes() == original_bytes
    assert json_path.exists()
    assert txt_path.exists()
    names = list_named_project_sequence_names(load_premiere_project_root(output_project))
    assert "ImportedSequence" in names
    _seq, clips = parse_premiere_project_sequence_visual_clips(output_project, "ImportedSequence")
    assert [clip.name for clip in clips] == ["clip_a.mp4", "new_take.mp4", "new_still.jpg"]
    assert ticks_to_seconds(clips[0].duration) == 10.0
    assert ticks_to_seconds(clips[1].duration) == 5.0
    assert ticks_to_seconds(clips[2].duration) == 5.0
    _assert_unique_masterclips_for_imported_files(
        output_project,
        "ImportedSequence",
        ["clip_a.mp4", "new_take.mp4", "new_still.jpg"],
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["missing_files"] == []
    assert payload["imported"][0]["reused_existing_media"] is True
    assert payload["imported"][1]["reused_existing_media"] is False


def test_run_sequence_media_import_reads_job_json_from_import_path() -> None:
    root = Path("test_runtime") / f"import_job_{uuid4().hex}"
    media_root = root / "tree"
    media_root.mkdir(parents=True)
    (media_root / "photo.jpg").write_bytes(b"photo")
    project_path = root / "raw.prproj"
    _write_import_source_project(project_path)
    job_path = root / "11_import.json"
    job_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "sequence_name": "ImportedSequence",
                "create_sequence_if_missing": True,
                "root_directory": str(media_root),
                "files": ["photo.jpg"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    wrapper_path = root / "wrapper.json"
    output_project = root / "raw_import.prproj"
    wrapper_path.write_text(
        json.dumps(
            {
                "mode": "import_media",
                "import_path": str(job_path),
                "output_project_path": str(output_project),
                "reports_dir": str(root / "reports"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _json_path, _txt_path, exported = run_sequence_media_import_from_config(wrapper_path)
    assert exported == output_project
    _seq, clips = parse_premiere_project_sequence_visual_clips(output_project, "ImportedSequence")
    assert [clip.name for clip in clips] == ["photo.jpg"]


def test_run_sequence_media_import_reads_items_with_source_path() -> None:
    root = Path("test_runtime") / f"import_items_{uuid4().hex}"
    media_root = root / "media"
    media_root.mkdir(parents=True)
    later = media_root / "later.mp4"
    first = media_root / "first.jpg"
    later.write_bytes(b"video")
    first.write_bytes(b"photo")
    project_path = root / "raw.prproj"
    _write_import_source_project(project_path)
    output_project = root / "raw_import.prproj"
    config_path = root / "import.json"
    config_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "sequence_name": "ImportedSequence",
                "create_sequence_if_missing": True,
                "items": [
                    {"order": 2, "source_path": str(later)},
                    {"order": 1, "source_path": str(first)},
                ],
                "output_project_path": str(output_project),
                "reports_dir": str(root / "reports"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, _txt_path, exported = run_sequence_media_import_from_config(config_path)
    assert exported == output_project
    _seq, clips = parse_premiere_project_sequence_visual_clips(output_project, "ImportedSequence")
    assert [clip.name for clip in clips] == ["first.jpg", "later.mp4"]
    _assert_unique_masterclips_for_imported_files(
        output_project,
        "ImportedSequence",
        ["first.jpg", "later.mp4"],
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["root_directory"] is None
    assert payload["requested_files"][0]["order"] == 2


def test_run_sequence_media_import_uses_sibling_project_when_source_has_no_clips() -> None:
    root = Path("test_runtime") / f"import_empty_{uuid4().hex}"
    media_root = root / "media"
    media_root.mkdir(parents=True)
    video = media_root / "new_take.mp4"
    photo = media_root / "new_still.jpg"
    video.write_bytes(b"video")
    photo.write_bytes(b"photo")

    empty_project = root / "empty.prproj"
    donor_project = root / "donor_with_clips.prproj"
    _write_empty_import_source_project(empty_project)
    _write_import_source_project(donor_project)
    original_bytes = empty_project.read_bytes()
    output_project = root / "empty_import.prproj"
    config_path = root / "import.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "import_media",
                "project_path": str(empty_project),
                "sequence_name": "EmptySequence",
                "create_sequence_if_missing": False,
                "root_directory": str(media_root),
                "files": ["new_take.mp4", "new_still.jpg"],
                "still_duration_seconds": 5,
                "output_project_path": str(output_project),
                "reports_dir": str(root / "reports"),
                "write_project": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, _txt_path, exported = run_sequence_media_import_from_config(config_path)

    assert exported == output_project
    assert empty_project.read_bytes() == original_bytes
    _seq, clips = parse_premiere_project_sequence_visual_clips(output_project, "EmptySequence")
    assert [clip.name for clip in clips] == ["new_take.mp4", "new_still.jpg"]
    _assert_unique_masterclips_for_imported_files(
        output_project,
        "EmptySequence",
        ["new_take.mp4", "new_still.jpg"],
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert any("donor_with_clips.prproj" in warning for warning in payload["warnings"])
    _assert_no_dangling_project_refs(output_project)


def _assert_unique_masterclips_for_imported_files(
    project_path: Path,
    sequence_name: str,
    expected_names: list[str],
) -> None:
    root = load_premiere_project_root(project_path)
    object_id_lookup = build_project_object_id_lookup(root)
    object_uid_lookup = build_project_object_uid_lookup(root)
    sequence_node = find_project_sequence_node(root, sequence_name)
    assert sequence_node is not None
    observed: list[tuple[str, str, str]] = []
    for _index, track_node in get_project_track_nodes(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    ):
        for ref in iter_project_track_item_refs(track_node):
            item = object_id_lookup.get(ref.attrib.get("ObjectRef", ""))
            if item is None:
                continue
            name = resolve_project_track_item_name(item, object_id_lookup)
            subclip = resolve_project_track_item_subclip(item, object_id_lookup)
            clip = resolve_project_track_item_clip(item, object_id_lookup)
            media = (
                resolve_project_clip_media_node(clip, object_id_lookup, object_uid_lookup)
                if clip is not None
                else None
            )
            master_ref = subclip.find("./MasterClip") if subclip is not None else None
            master_uid = master_ref.attrib.get("ObjectURef") if master_ref is not None else ""
            master = object_uid_lookup.get(master_uid or "")
            master_name = (master.findtext("./Name") or "").strip() if master is not None else ""
            media_title = (media.findtext("./Title") or "").strip() if media is not None else ""
            media_paths = []
            video_stream_id = ""
            if media is not None:
                for tag_name in ("ActualMediaFilePath", "FilePath", "RelativePath"):
                    media_paths.extend(
                        (child.text or "").strip()
                        for child in media
                        if child.tag == tag_name and (child.text or "").strip()
                    )
                stream_ref = media.find("./VideoStream")
                if stream_ref is not None:
                    video_stream_id = stream_ref.attrib.get("ObjectRef") or ""
            observed.append((name, master_name, master_uid or "", video_stream_id))
            assert master is not None, f"clip '{name}' is missing a unique MasterClip"
            assert master_name == name
            assert media_title == name
            assert media_paths, f"clip '{name}' is missing media paths"
            assert all(name in path for path in media_paths), f"clip '{name}' still points to template media: {media_paths}"
            assert video_stream_id, f"clip '{name}' is missing a VideoStream"
    assert [name for name, _master_name, _master_uid, _stream in observed] == expected_names
    master_uids = [master_uid for _name, _master_name, master_uid, _stream in observed]
    stream_ids = [stream_id for _name, _master_name, _master_uid, stream_id in observed]
    assert len(set(master_uids)) == len(expected_names)
    assert len(set(stream_ids)) == len(expected_names)


def _assert_no_dangling_project_refs(project_path: Path) -> None:
    root = load_premiere_project_root(project_path)
    object_ids = {node.attrib.get("ObjectID") for node in root.iter() if node.attrib.get("ObjectID")}
    object_uids = {node.attrib.get("ObjectUID") for node in root.iter() if node.attrib.get("ObjectUID")}
    missing = []
    for element in root.iter():
        object_ref = element.attrib.get("ObjectRef")
        if object_ref and object_ref not in object_ids:
            missing.append(f"{element.tag}@ObjectRef={object_ref}")
        object_uref = element.attrib.get("ObjectURef")
        if object_uref and object_uref not in object_uids:
            missing.append(f"{element.tag}@ObjectURef={object_uref}")
    assert missing == []


def _write_empty_import_source_project(project_path: Path) -> None:
    project_xml = """<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <RootProjectItem ObjectURef="root-project" />
  <RootProjectItem ObjectUID="root-project" ClassID="root-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>Root</Name>
    </ProjectItem>
    <ProjectItemContainer Version="1">
      <Items Version="1">
        <Item Index="0" ObjectURef="project-item-empty" />
      </Items>
    </ProjectItemContainer>
  </RootProjectItem>
  <ClipProjectItem ObjectUID="project-item-empty" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>EmptySequence</Name>
    </ProjectItem>
    <MasterClip ObjectURef="master-empty" />
  </ClipProjectItem>
  <MasterClip ObjectUID="master-empty" ClassID="master-clip" Version="1">
    <Name>EmptySequence</Name>
  </MasterClip>
  <Sequence ObjectUID="seq-empty" ClassID="sequence" Version="1">
    <Node Version="1">
      <Properties Version="1">
        <MZ.WorkOutPoint>0</MZ.WorkOutPoint>
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
    <Name>EmptySequence</Name>
  </Sequence>
  <VideoTrackGroup ObjectID="1000" ClassID="video-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-v1" />
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <AudioTrackGroup ObjectID="1001" ClassID="audio-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-a1" />
      </Tracks>
    </TrackGroup>
  </AudioTrackGroup>
  <VideoClipTrack ObjectUID="track-v1" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1" />
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <AudioClipTrack ObjectUID="track-a1" ClassID="audio-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1" />
      </ClipItems>
    </ClipTrack>
  </AudioClipTrack>
</PremiereData>
"""
    project_path.write_bytes(gzip.compress(project_xml.encode("utf-8")))


def _write_import_source_project(project_path: Path) -> None:
    video_start = _ticks(0)
    video_end = _ticks(10)
    photo_start = _ticks(10)
    photo_end = _ticks(15)
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
        <Item Index="1" ObjectURef="project-item-video" />
        <Item Index="2" ObjectURef="project-item-photo" />
      </Items>
    </ProjectItemContainer>
  </RootProjectItem>
  <ClipProjectItem ObjectUID="project-item-raw" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>RawSequence</Name>
    </ProjectItem>
    <MasterClip ObjectURef="master-raw" />
  </ClipProjectItem>
  <MasterClip ObjectUID="master-raw" ClassID="master-clip" Version="1">
    <Name>RawSequence</Name>
  </MasterClip>
  <Sequence ObjectUID="seq-raw" ClassID="sequence" Version="1">
    <Node Version="1">
      <Properties Version="1">
        <MZ.WorkOutPoint>{_ticks(15)}</MZ.WorkOutPoint>
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
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <AudioTrackGroup ObjectID="1001" ClassID="audio-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-a1" />
      </Tracks>
    </TrackGroup>
  </AudioTrackGroup>
  <VideoClipTrack ObjectUID="track-v1" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="2000" />
          <TrackItem Index="1" ObjectRef="2100" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <AudioClipTrack ObjectUID="track-a1" ClassID="audio-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="2200" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </AudioClipTrack>
  <VideoClipTrackItem ObjectID="2000" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{video_start}</Start>
        <End>{video_end}</End>
      </TrackItem>
      <SubClip ObjectRef="2001" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="2001" ClassID="subclip" Version="1">
    <Name>clip_a.mp4</Name>
    <Clip ObjectRef="2002" />
    <MasterClip ObjectURef="master-video" />
  </SubClip>
  <VideoClip ObjectID="2002" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>{video_end - video_start}</OutPoint>
      <Source ObjectRef="2003" />
      <ClipID>00000000-0000-0000-0000-000000000001</ClipID>
    </Clip>
  </VideoClip>
  <VideoMediaSource ObjectID="2003" ClassID="video-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-video" />
    </MediaSource>
    <OriginalDuration>{video_end - video_start}</OriginalDuration>
  </VideoMediaSource>
  <Media ObjectUID="media-video" ClassID="media" Version="1">
    <AudioStream ObjectRef="2004" />
    <VideoStream ObjectRef="2005" />
    <ActualMediaFilePath>E:/media/clip_a.mp4</ActualMediaFilePath>
    <FilePath>E:/media/clip_a.mp4</FilePath>
    <RelativePath>E:/media/clip_a.mp4</RelativePath>
    <RelativePath>../media/clip_a.mp4</RelativePath>
    <Title>clip_a.mp4</Title>
    <FileKey>11111111-1111-1111-1111-111111111111</FileKey>
    <ContentAndMetadataState>22222222-2222-2222-2222-222222222222</ContentAndMetadataState>
  </Media>
  <AudioStream ObjectID="2004" ClassID="audio-stream" Version="1">
    <PeakFilePath>C:/temp/clip_a.pek</PeakFilePath>
  </AudioStream>
  <VideoStream ObjectID="2005" ClassID="video-stream" Version="1">
    <FrameRect>0,0,1920,1080</FrameRect>
  </VideoStream>
  <VideoClipTrackItem ObjectID="2100" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{photo_start}</Start>
        <End>{photo_end}</End>
      </TrackItem>
      <SubClip ObjectRef="2101" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="2101" ClassID="subclip" Version="1">
    <Name>still.jpg</Name>
    <Clip ObjectRef="2102" />
    <MasterClip ObjectURef="master-photo" />
  </SubClip>
  <VideoClip ObjectID="2102" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>{_ticks(3600)}</InPoint>
      <OutPoint>{_ticks(3605)}</OutPoint>
      <Source ObjectRef="2103" />
      <ClipID>00000000-0000-0000-0000-000000000002</ClipID>
    </Clip>
  </VideoClip>
  <VideoMediaSource ObjectID="2103" ClassID="video-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-photo" />
    </MediaSource>
  </VideoMediaSource>
  <Media ObjectUID="media-photo" ClassID="media" Version="1">
    <VideoStream ObjectRef="2104" />
    <ActualMediaFilePath>E:/media/still.jpg</ActualMediaFilePath>
    <FilePath>E:/media/still.jpg</FilePath>
    <RelativePath>E:/media/still.jpg</RelativePath>
    <RelativePath>../media/still.jpg</RelativePath>
    <Title>still.jpg</Title>
    <FileKey>33333333-3333-3333-3333-333333333333</FileKey>
    <ContentAndMetadataState>44444444-4444-4444-4444-444444444444</ContentAndMetadataState>
    <Infinite>true</Infinite>
  </Media>
  <VideoStream ObjectID="2104" ClassID="video-stream" Version="1">
    <FrameRect>0,0,800,600</FrameRect>
    <IsStill>true</IsStill>
  </VideoStream>
  <AudioClipTrackItem ObjectID="2200" ClassID="audio-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{video_start}</Start>
        <End>{video_end}</End>
      </TrackItem>
      <SubClip ObjectRef="2201" />
    </ClipTrackItem>
  </AudioClipTrackItem>
  <SubClip ObjectID="2201" ClassID="subclip" Version="1">
    <Name>clip_a.mp4</Name>
    <Clip ObjectRef="2202" />
    <MasterClip ObjectURef="master-video" />
  </SubClip>
  <AudioClip ObjectID="2202" ClassID="audio-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>{video_end - video_start}</OutPoint>
      <Source ObjectRef="2203" />
      <ClipID>10000000-0000-0000-0000-000000000001</ClipID>
    </Clip>
  </AudioClip>
  <AudioMediaSource ObjectID="2203" ClassID="audio-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-video" />
    </MediaSource>
  </AudioMediaSource>
  <MasterClip ObjectUID="master-video" ClassID="master-clip" Version="1">
    <Name>clip_a.mp4</Name>
    <Clips Version="1">
      <Clip Index="0" ObjectRef="2002" />
      <Clip Index="1" ObjectRef="2202" />
    </Clips>
  </MasterClip>
  <MasterClip ObjectUID="master-photo" ClassID="master-clip" Version="1">
    <Name>still.jpg</Name>
    <Clips Version="1">
      <Clip Index="0" ObjectRef="2102" />
    </Clips>
  </MasterClip>
  <ClipProjectItem ObjectUID="project-item-video" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>clip_a.mp4</Name>
    </ProjectItem>
    <MasterClip ObjectURef="master-video" />
  </ClipProjectItem>
  <ClipProjectItem ObjectUID="project-item-photo" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>still.jpg</Name>
    </ProjectItem>
    <MasterClip ObjectURef="master-photo" />
  </ClipProjectItem>
</PremiereData>
"""
    project_path.write_bytes(gzip.compress(project_xml.encode("utf-8")))
