import os
import subprocess
from argparse import Namespace
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from api.grok_web import GrokWebAgent, GrokWebConfig, GrokWebError, GrokWebSessionRunner, TargetClosedError
from config import Settings
from main_grok_web import (
    _association_style_profile,
    _select_background_local_fallback_mode,
    _infer_association_theme,
    default_output_video_path,
    resolve_image_path,
    resolve_image_for_prompt,
    resolve_prompt_path,
    run_generation,
)


def _settings_for(root: Path) -> Settings:
    settings = Settings(project_root=root)
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_resolve_prompt_prefers_prompt_matching_image_stem() -> None:
    root = Path("test_runtime") / f"grok_web_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"img")

    other_prompt = settings.output_dir / "other_stage_v_prompt_1.txt"
    other_prompt.write_text("other", encoding="utf-8")
    matching_prompt = settings.output_dir / "frame_a_20260311_v_prompt_1.txt"
    matching_prompt.write_text("match", encoding="utf-8")

    resolved = resolve_prompt_path(None, image_path, settings)
    assert resolved == matching_prompt


def test_default_output_video_path_reuses_pipeline_naming() -> None:
    root = Path("test_runtime") / f"grok_web_{uuid4().hex}"
    settings = _settings_for(root)

    prompt_path = settings.output_dir / "scene_stage_v_prompt_2.txt"
    output_path = default_output_video_path(prompt_path, settings)

    assert output_path == settings.output_dir / "scene_stage_video_2.mp4"


def test_run_generation_builds_grok_config_and_calls_runner() -> None:
    root = Path("test_runtime") / f"grok_web_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"img")
    prompt_path = settings.output_dir / "frame_a_stage_v_prompt_1.txt"
    prompt_path.write_text("Generate a cinematic wedding video.", encoding="utf-8")

    captured = {}

    def fake_runner(config) -> Path:
        captured["config"] = config
        config.output_path.write_bytes(b"video")
        return config.output_path

    args = Namespace(
        image=image_path,
        prompt=prompt_path,
        output_video=None,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        upload_timeout=67.0,
        no_submit=False,
    )

    output_path = run_generation(args, settings=settings, runner=fake_runner)

    config = captured["config"]
    assert config.image_path == image_path
    assert config.prompt_text == "Generate a cinematic wedding video."
    assert config.output_path == settings.output_dir / "frame_a_stage_video_1.mp4"
    assert config.result_timeout_ms == 123_000
    assert config.launch_timeout_ms == 45_000
    assert config.upload_timeout_ms == 67_000
    assert output_path.exists()


def test_resolve_single_image_from_input_directory() -> None:
    root = Path("test_runtime") / f"grok_web_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "only.png"
    image_path.write_bytes(b"img")

    resolved = resolve_image_path(None, settings)
    assert resolved == image_path


def test_resolve_image_from_prompt_when_multiple_input_images_exist() -> None:
    root = Path("test_runtime") / f"grok_web_{uuid4().hex}"
    settings = _settings_for(root)

    first = settings.input_dir / "frame_a.png"
    second = settings.input_dir / "frame_b.png"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    prompt_path = settings.output_dir / "frame_b_20260311_120000_v_prompt_1.txt"
    prompt_path.write_text("prompt", encoding="utf-8")

    resolved = resolve_image_path(None, settings, prompt_path=prompt_path)

    assert resolved == second
    assert resolve_image_for_prompt(prompt_path, settings) == second


def test_run_generation_without_arguments_uses_latest_v_prompt_pair() -> None:
    root = Path("test_runtime") / f"grok_web_{uuid4().hex}"
    settings = _settings_for(root)

    old_image = settings.input_dir / "frame_a.png"
    new_image = settings.input_dir / "frame_b.png"
    old_image.write_bytes(b"a")
    new_image.write_bytes(b"b")

    old_prompt = settings.output_dir / "frame_a_20260311_120000_v_prompt_1.txt"
    new_prompt = settings.output_dir / "frame_b_20260311_130000_v_prompt_1.txt"
    old_prompt.write_text("old", encoding="utf-8")
    new_prompt.write_text("new", encoding="utf-8")
    os.utime(old_prompt, (1, 1))
    os.utime(new_prompt, (2, 2))

    captured = {}

    def fake_runner(config) -> Path:
        captured["config"] = config
        config.output_path.write_bytes(b"video")
        return config.output_path

    args = Namespace(
        image=None,
        prompt=None,
        output_video=None,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        upload_timeout=67.0,
        no_submit=False,
    )

    output_path = run_generation(args, settings=settings, runner=fake_runner)

    config = captured["config"]
    assert config.image_path == new_image
    assert config.prompt_text == "new"
    assert config.output_path == settings.output_dir / "frame_b_20260311_130000_video_1.mp4"
    assert output_path.exists()


def test_run_generation_creates_background_image_before_video_when_enabled(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_bg_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"img")
    prompt_path = settings.output_dir / "frame_a_20260311_120000_v_prompt_1.txt"
    prompt_path.write_text("Generate a cinematic video.", encoding="utf-8")
    bg_prompt_path = settings.output_dir / "frame_a_20260311_120000_bg_prompt.txt"
    bg_prompt_path.write_text("Create a cinematic 16:9 background image.", encoding="utf-8")
    assoc_bg_prompt_path = settings.output_dir / "frame_a_20260311_120000_assoc_bg_prompt.txt"
    assoc_bg_prompt_path.write_text("Create a realistic associative garden courtyard background.", encoding="utf-8")
    (root / "config.json").write_text('{"generate_source_background": true}', encoding="utf-8")

    captured: list[object] = []

    def fake_runner(config) -> Path:
        captured.append(config)
        config.output_path.write_bytes(b"artifact")
        return config.output_path

    monkeypatch.setattr(
        "main_grok_web._background_image_is_near_identical",
        lambda source_path, generated_path: (False, [12.0, 12.0, 12.0]),
    )

    args = Namespace(
        image=image_path,
        prompt=prompt_path,
        output_video=None,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        upload_timeout=67.0,
        generate_source_background=None,
        no_submit=False,
    )

    output_path = run_generation(args, settings=settings, runner=fake_runner)

    assert len(captured) == 2
    assert captured[0].generation_mode == "image"
    assert captured[0].prompt_text == "Create a realistic associative garden courtyard background."
    assert captured[0].image_path == image_path
    assert captured[0].aspect_ratio == "16:9"
    assert captured[0].orientation == "horizontal"
    assert captured[0].output_path == settings.output_dir / "frame_a_20260311_120000_bg_image_16x9.png"
    assert captured[1].generation_mode == "video"
    assert output_path == settings.output_dir / "frame_a_20260311_120000_video_1.mp4"


def test_save_generated_video_recovers_after_page_close(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_video_recover_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.mp4",
            result_timeout_ms=20,
        )
    )

    saved: list[tuple[str, str]] = []

    monkeypatch.setattr(
        agent,
        "_raise_if_auth_required",
        lambda page: (_ for _ in ()).throw(TargetClosedError("page closed")) if page.name == "closed" else None,
    )
    monkeypatch.setattr(agent, "_dismiss_interfering_overlay", lambda page: False)
    monkeypatch.setattr(agent, "_download_from_controls", lambda page, output_path: False)
    monkeypatch.setattr(agent, "_capture_download_from_folder", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_video_signatures", lambda page: [] if page.name == "closed" else ["fresh-video"])
    monkeypatch.setattr(agent, "_write_debug_snapshot", lambda page, output_path, reason: {})
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    def fake_save(page, locator, output_path):
        saved.append((page.name, locator))
        Path(output_path).write_bytes(b"video")

    monkeypatch.setattr(agent, "_save_video_from_locator", fake_save)

    class FakeVideos:
        def __init__(self, page_name: str):
            self.page_name = page_name

        def count(self) -> int:
            if self.page_name == "closed":
                raise TargetClosedError("page closed")
            return 1

        def nth(self, index: int) -> str:
            return f"{self.page_name}-video-{index}"

    class FakeContext:
        def __init__(self) -> None:
            self.pages: list[FakePage] = []

    class FakePage:
        def __init__(self, name: str, context: FakeContext) -> None:
            self.name = name
            self.context = context

        def is_closed(self) -> bool:
            return self.name == "closed"

        def locator(self, selector: str) -> FakeVideos:
            assert selector == "video"
            return FakeVideos(self.name)

    context = FakeContext()
    closed_page = FakePage("closed", context)
    recovered_page = FakePage("recovered", context)
    context.pages = [closed_page, recovered_page]

    output_path = root / "out.mp4"
    agent._save_generated_video(closed_page, 0, [], {}, output_path)

    assert output_path.exists()
    assert saved == [("recovered", "recovered-video-0")]


def test_run_generation_copies_background_image_to_final_media_dir(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_bg_copy_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"img")
    prompt_path = settings.output_dir / "frame_a_20260311_120000_v_prompt_1.txt"
    prompt_path.write_text("Generate a cinematic video.", encoding="utf-8")
    bg_prompt_path = settings.output_dir / "frame_a_20260311_120000_bg_prompt.txt"
    bg_prompt_path.write_text("Create a cinematic 16:9 background image.", encoding="utf-8")
    assoc_bg_prompt_path = settings.output_dir / "frame_a_20260311_120000_assoc_bg_prompt.txt"
    assoc_bg_prompt_path.write_text("Create a realistic associative garden courtyard background.", encoding="utf-8")
    (root / "config.json").write_text(
        '{"generate_source_background": true, "final_videos_dir": "final_media"}',
        encoding="utf-8",
    )

    copied_media: list[str] = []
    deleted_backgrounds: list[str] = []

    def fake_runner(config) -> Path:
        config.output_path.write_bytes(b"artifact")
        return config.output_path

    monkeypatch.setattr(
        "main_grok_web._background_image_is_near_identical",
        lambda source_path, generated_path: (False, [12.0, 12.0, 12.0]),
    )

    monkeypatch.setattr(
        "main_grok_web.sync_final_media_file",
        lambda settings, generation_config, artifact_path: copied_media.append(artifact_path.name) or artifact_path,
    )
    monkeypatch.setattr(
        "main_grok_web.sync_video_file",
        lambda settings, generation_config, artifact_path: copied_media.append(artifact_path.name) or artifact_path,
    )

    args = Namespace(
        image=image_path,
        prompt=prompt_path,
        output_video=None,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        upload_timeout=67.0,
        generate_source_background=None,
        no_submit=False,
    )

    run_generation(args, settings=settings, runner=fake_runner)

    assert "frame_a_20260311_120000_bg_image_16x9.png" in copied_media
    assert "frame_a_20260311_120000_video_1.mp4" in copied_media


def test_run_generation_keeps_background_image_even_if_it_is_near_identical(monkeypatch, capsys) -> None:
    root = Path("test_runtime") / f"grok_web_bg_keep_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    Image.new("RGB", (160, 120), (120, 90, 60)).save(image_path)
    prompt_path = settings.output_dir / "frame_a_20260311_120000_v_prompt_1.txt"
    prompt_path.write_text("Generate a cinematic video.", encoding="utf-8")
    bg_prompt_path = settings.output_dir / "frame_a_20260311_120000_bg_prompt.txt"
    bg_prompt_path.write_text("Create a cinematic 16:9 background image.", encoding="utf-8")
    assoc_bg_prompt_path = settings.output_dir / "frame_a_20260311_120000_assoc_bg_prompt.txt"
    assoc_bg_prompt_path.write_text("Create a realistic associative garden courtyard background.", encoding="utf-8")
    manifest_path = settings.output_dir / "frame_a_20260311_120000_api_pipeline_manifest.json"
    manifest_path.write_text(
        '{"artifacts": {"bg_image": "frame_a_20260311_120000_bg_image_16x9.png"}}',
        encoding="utf-8",
    )
    (root / "config.json").write_text('{"generate_source_background": true}', encoding="utf-8")

    copied_media: list[str] = []

    def fake_runner(config) -> Path:
        if config.generation_mode == "image":
            Image.new("RGB", (1600, 900), (120, 90, 60)).save(config.output_path)
        else:
            config.output_path.write_bytes(b"artifact")
        return config.output_path

    monkeypatch.setattr(
        "main_grok_web._background_image_is_near_identical",
        lambda source_path, generated_path: (True, [0.5, 0.5, 0.5]),
    )
    monkeypatch.setattr(
        "main_grok_web.sync_final_media_file",
        lambda settings, generation_config, artifact_path: copied_media.append(artifact_path.name) or artifact_path,
    )
    monkeypatch.setattr(
        "main_grok_web.sync_video_file",
        lambda settings, generation_config, artifact_path: copied_media.append(artifact_path.name) or artifact_path,
    )

    args = Namespace(
        image=image_path,
        prompt=prompt_path,
        output_video=None,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        upload_timeout=67.0,
        generate_source_background=None,
        no_submit=False,
    )

    output_path = run_generation(args, settings=settings, runner=fake_runner)

    assert output_path == settings.output_dir / "frame_a_20260311_120000_video_1.mp4"
    assert "frame_a_20260311_120000_bg_image_16x9.png" in copied_media
    assert "frame_a_20260311_120000_video_1.mp4" in copied_media
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert '"bg_image_status": "local_fallback"' not in manifest_text
    captured = capsys.readouterr()
    assert "Retrying with a stronger associative prompt..." not in captured.out
    assert "Grok background image saved:" in captured.out


def test_run_generation_requires_assoc_bg_prompt_when_background_generation_is_enabled() -> None:
    root = Path("test_runtime") / f"grok_web_assoc_prompt_required_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"img")
    prompt_path = settings.output_dir / "frame_a_20260311_120000_v_prompt_1.txt"
    prompt_path.write_text("Generate a cinematic video.", encoding="utf-8")
    (root / "config.json").write_text('{"generate_source_background": true}', encoding="utf-8")

    args = Namespace(
        image=image_path,
        prompt=prompt_path,
        output_video=None,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        upload_timeout=67.0,
        generate_source_background=None,
        no_submit=False,
    )

    with pytest.raises(FileNotFoundError, match="Associative background prompt file was not found"):
        run_generation(args, settings=settings, runner=lambda config: config.output_path)


def test_select_background_local_fallback_mode_prefers_light_for_dark_frames() -> None:
    assert _select_background_local_fallback_mode(72.0) == "light airy background"


def test_select_background_local_fallback_mode_prefers_dark_for_bright_frames() -> None:
    assert _select_background_local_fallback_mode(154.0) == "dark cinematic background"


def test_infer_association_theme_prefers_architecture_from_scene_analysis() -> None:
    root = Path("test_runtime") / f"grok_bg_assoc_{uuid4().hex}"
    settings = _settings_for(root)
    stage_id = "stage_scene"
    scene_path = settings.output_dir / f"{stage_id}_scene_analysis.json"
    scene_path.write_text(
        '{"summary": "woman at dinner", "background": "brick arch and rustic kitchen interior", "main_action": "raising a glass"}',
        encoding="utf-8",
    )

    assert _infer_association_theme(stage_id, settings) == "architecture"


def test_infer_association_theme_prefers_association_descriptor_prompt() -> None:
    root = Path("test_runtime") / f"grok_bg_assoc_prompt_{uuid4().hex}"
    settings = _settings_for(root)
    stage_id = "stage_scene"
    scene_path = settings.output_dir / f"{stage_id}_scene_analysis.json"
    scene_path.write_text(
        '{"summary": "woman at dinner", "background": "brick arch and rustic kitchen interior", "main_action": "raising a glass"}',
        encoding="utf-8",
    )
    assoc_prompt_path = settings.output_dir / f"{stage_id}_assoc_bg_prompt.txt"
    assoc_prompt_path.write_text(
        "Create a realistic associative environmental image of a leafy garden courtyard with rich vegetation and soft natural depth.",
        encoding="utf-8",
    )

    assert _infer_association_theme(stage_id, settings) == "vegetation"


def test_association_style_profile_uses_descriptor_keywords() -> None:
    profile = _association_style_profile(
        "light airy warm balanced lush garden courtyard with mist and soft blend",
        150.0,
    )

    assert profile["mode"] == "light airy background"
    assert profile["blur_radius"] > 30
    assert profile["overlay_blend"] < 0.60
    assert profile["tint_blend"] < 0.50


def test_prompt_matches_accepts_retained_prompt_text() -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.mp4"),
        )
    )

    class FakeLocator:
        def evaluate(self, _script):
            return "Create a realistic associative garden courtyard background with soft light"

    assert agent._prompt_matches(FakeLocator(), "Create a realistic associative garden courtyard background with soft light")


def test_prompt_matches_rejects_empty_prompt_field() -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.mp4"),
        )
    )

    class FakeLocator:
        def evaluate(self, _script):
            return ""

    assert not agent._prompt_matches(FakeLocator(), "Create a realistic associative garden courtyard background with soft light")


def test_dismiss_interfering_overlay_clicks_disable_ad_button(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.png"),
        )
    )
    observed_logs: list[str] = []
    attempted_patterns: list[str] = []

    monkeypatch.setattr(agent, "_safe_body_text", lambda page: "Share Template Disable ad Go Skiing")
    monkeypatch.setattr(agent, "_log", observed_logs.append)
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    def fake_click_named_control(_page, patterns):
        attempted_patterns.append(patterns[0].pattern)
        return "disable ad" in patterns[0].pattern

    monkeypatch.setattr(agent, "_click_named_control", fake_click_named_control)

    assert agent._dismiss_interfering_overlay(object()) is True
    assert attempted_patterns
    assert observed_logs[-1].startswith("dismissed interfering Grok overlay")


def test_run_generation_skips_background_flow_when_disabled() -> None:
    root = Path("test_runtime") / f"grok_web_no_bg_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"img")
    prompt_path = settings.output_dir / "frame_a_20260311_120000_v_prompt_1.txt"
    prompt_path.write_text("Generate a cinematic video.", encoding="utf-8")
    bg_prompt_path = settings.output_dir / "frame_a_20260311_120000_bg_prompt.txt"
    bg_prompt_path.write_text("This prompt must not be used.", encoding="utf-8")
    (root / "config.json").write_text('{"generate_source_background": false}', encoding="utf-8")

    captured: list[object] = []

    def fake_runner(config) -> Path:
        captured.append(config)
        config.output_path.write_bytes(b"artifact")
        return config.output_path

    args = Namespace(
        image=image_path,
        prompt=prompt_path,
        output_video=None,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        upload_timeout=67.0,
        generate_source_background=None,
        no_submit=False,
    )

    output_path = run_generation(args, settings=settings, runner=fake_runner)

    assert len(captured) == 1
    assert captured[0].generation_mode == "video"
    assert output_path == settings.output_dir / "frame_a_20260311_120000_video_1.mp4"


def test_wait_for_upload_ready_accepts_attachment_fallback(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.mp4"),
            upload_timeout_ms=5,
        )
    )
    observed_logs: list[str] = []

    monkeypatch.setattr(agent, "_raise_if_auth_required", lambda page: None)
    monkeypatch.setattr(agent, "_write_debug_snapshot", lambda page, output_path, reason: {})
    monkeypatch.setattr(agent, "_log", observed_logs.append)

    states = iter(
        [
            {
                "busyCount": 2,
                "uploadBusyText": True,
                "sendReady": False,
                "sendControlsPresent": True,
                "attachmentDetected": True,
                "fileNameDetected": True,
            },
            {
                "busyCount": 1,
                "uploadBusyText": False,
                "sendReady": False,
                "sendControlsPresent": True,
                "attachmentDetected": True,
                "fileNameDetected": True,
            },
            {
                "busyCount": 1,
                "uploadBusyText": False,
                "sendReady": False,
                "sendControlsPresent": True,
                "attachmentDetected": True,
                "fileNameDetected": True,
            },
        ]
    )

    monkeypatch.setattr(agent, "_upload_state", lambda page: next(states))

    agent._wait_for_upload_ready(object())

    assert observed_logs[-1] == "upload ready"


def test_submit_uses_nearest_prompt_submit_button_when_named_controls_are_missing(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.png"),
        )
    )
    observed_logs: list[str] = []

    monkeypatch.setattr(agent, "_dismiss_interfering_overlay", lambda page: False)
    monkeypatch.setattr(agent, "_wait_for_upload_ready", lambda page: None)
    monkeypatch.setattr(agent, "_wait_for_submit_enabled", lambda page: None)
    monkeypatch.setattr(agent, "_click_nearest_prompt_submit_button", lambda page: True)
    monkeypatch.setattr(agent, "_log", observed_logs.append)

    class EmptyGroup:
        @property
        def first(self):
            return self

        def click(self, timeout=None):
            raise AssertionError("named submit buttons should not be clicked in this fallback test")

    class FakePage:
        def get_by_role(self, role: str, name=None):
            assert role == "button"
            return EmptyGroup()

    agent._submit(FakePage())

    assert observed_logs[-1] == "submit clicked"


def test_wait_for_submit_enabled_nudges_until_button_becomes_enabled(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.png"),
        )
    )
    observed_nudges: list[str] = []
    states = iter(
        [
            {"selector": "[data-codex-submit-id='x']", "disabled": True},
            {"selector": "[data-codex-submit-id='x']", "disabled": False},
        ]
    )

    monkeypatch.setattr(agent, "_dismiss_interfering_overlay", lambda page: False)
    monkeypatch.setattr(agent, "_submit_button_state", lambda page: next(states))
    monkeypatch.setattr(agent, "_nudge_prompt_submit_controls", lambda page: observed_nudges.append("nudge") or True)
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    result = agent._wait_for_submit_enabled(object())

    assert result == {"selector": "[data-codex-submit-id='x']", "disabled": False}
    assert observed_nudges == ["nudge"]


def test_submit_clicks_enabled_submit_button_before_fallbacks(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.png"),
        )
    )
    observed_logs: list[str] = []
    clicked: list[str] = []

    monkeypatch.setattr(agent, "_dismiss_interfering_overlay", lambda page: False)
    monkeypatch.setattr(agent, "_wait_for_upload_ready", lambda page: None)
    monkeypatch.setattr(agent, "_wait_for_submit_enabled", lambda page: {"selector": "#submit", "disabled": False})
    monkeypatch.setattr(agent, "_click_submit_button", lambda page, submit_state: clicked.append(submit_state["selector"]) or True)
    monkeypatch.setattr(agent, "_click_nearest_prompt_submit_button", lambda page: (_ for _ in ()).throw(AssertionError("fallback should not run")))
    monkeypatch.setattr(agent, "_log", observed_logs.append)

    class FakePage:
        def get_by_role(self, role: str, name=None):
            raise AssertionError("named controls should not be used when submit button is enabled")

    agent._submit(FakePage())

    assert clicked == ["#submit"]
    assert observed_logs[-1] == "submit clicked"


def test_save_generated_image_waits_past_template_card_until_real_image_appears(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.png"),
            result_timeout_ms=20,
            generation_mode="image",
        )
    )

    image_states = iter(
        [
            [
                {
                    "kind": "img",
                    "index": 0,
                    "candidateId": "ad-card",
                    "signature": "img|ad-card|400x800",
                    "renderedWidth": 400,
                    "renderedHeight": 800,
                    "width": 400,
                    "height": 800,
                    "top": 20,
                    "contextText": "Share Template Go Skiing Video",
                    "href": "https://grok.com/templates/go-skiing",
                    "isLikelyAd": True,
                }
            ],
            [
                {
                    "kind": "background",
                    "index": 0,
                    "candidateId": "real-result",
                    "signature": "background|fresh-result|1600x900",
                    "renderedWidth": 1280,
                    "renderedHeight": 720,
                    "width": 1600,
                    "height": 900,
                    "top": 260,
                    "contextText": "Generated result",
                    "href": "",
                    "isLikelyAd": False,
                }
            ],
        ]
    )
    saved: list[str] = []

    monkeypatch.setattr(agent, "_raise_if_auth_required", lambda page: None)
    monkeypatch.setattr(agent, "_dismiss_interfering_overlay", lambda page: False)
    monkeypatch.setattr(agent, "_download_from_controls", lambda page, output_path: False)
    monkeypatch.setattr(agent, "_capture_download_from_folder", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_image_generation_busy", lambda page: False)
    monkeypatch.setattr(agent, "_image_metadata", lambda page: next(image_states))
    monkeypatch.setattr(agent, "_write_debug_snapshot", lambda page, output_path, reason: {})
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    def fake_save(page, locator, output_path):
        saved.append(locator)

    monkeypatch.setattr(agent, "_save_image_from_locator", fake_save)
    monkeypatch.setattr(
        agent,
        "_save_best_image_candidate",
        lambda page, ranked_items, output_path: fake_save(page, agent._candidate_locator(page, ranked_items[0]), output_path) or True,
    )

    class FakePage:
        def locator(self, selector: str):
            class FakeLocator:
                def __init__(self, value: str):
                    self.value = value

                @property
                def first(self):
                    return self.value

                def nth(self, index: int):
                    return f"{selector}:{index}"

            if "real-result" in selector:
                return FakeLocator("real-result-locator")
            if "ad-card" in selector:
                return FakeLocator("ad-card-locator")
            return FakeLocator(f"{selector}:fallback")

    agent._save_generated_image(FakePage(), 0, [], {}, Path("out.png"))

    assert saved == ["real-result-locator"]


def test_save_generated_image_waits_until_generation_is_not_busy(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.png"),
            result_timeout_ms=20,
        )
    )

    image_metadata = [
        {"index": 0, "signature": "new-image|1024x576"},
    ]
    busy_states = iter([True, False])
    saved: list[str] = []

    monkeypatch.setattr(agent, "_raise_if_auth_required", lambda page: None)
    monkeypatch.setattr(agent, "_download_from_controls", lambda page, output_path: False)
    monkeypatch.setattr(agent, "_capture_download_from_folder", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_image_generation_busy", lambda page: next(busy_states))
    monkeypatch.setattr(agent, "_image_metadata", lambda page: image_metadata)
    monkeypatch.setattr(agent, "_write_debug_snapshot", lambda page, output_path, reason: {})
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    class FakeImages:
        def nth(self, index: int):
            return f"img-{index}"

    class FakePage:
        def locator(self, selector: str):
            assert selector == "img"
            return FakeImages()

    monkeypatch.setattr(agent, "_save_image_from_locator", lambda page, locator, output_path: saved.append(locator))

    agent._save_generated_image(FakePage(), 0, [], {}, Path("out.png"))

    assert saved == ["img-0"]


def test_save_generated_image_prefers_largest_new_image(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.png"),
            result_timeout_ms=20,
        )
    )

    image_metadata = [
        {"index": 0, "signature": "small-new|320x320", "renderedWidth": 220, "renderedHeight": 220, "width": 320, "height": 320, "top": 120},
        {"index": 1, "signature": "large-new|1600x900", "renderedWidth": 1280, "renderedHeight": 720, "width": 1600, "height": 900, "top": 260},
    ]
    saved: list[str] = []

    monkeypatch.setattr(agent, "_raise_if_auth_required", lambda page: None)
    monkeypatch.setattr(agent, "_download_from_controls", lambda page, output_path: False)
    monkeypatch.setattr(agent, "_capture_download_from_folder", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_image_generation_busy", lambda page: False)
    monkeypatch.setattr(agent, "_image_metadata", lambda page: image_metadata)
    monkeypatch.setattr(agent, "_write_debug_snapshot", lambda page, output_path, reason: {})
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    class FakeImages:
        def nth(self, index: int):
            return f"img-{index}"

    class FakePage:
        def locator(self, selector: str):
            assert selector == "img"
            return FakeImages()

    monkeypatch.setattr(agent, "_save_image_from_locator", lambda page, locator, output_path: saved.append(locator))

    agent._save_generated_image(FakePage(), 0, ["baseline|200x200"], {}, Path("out.png"))

    assert saved == ["img-1"]


def test_save_generated_image_prefers_visual_canvas_candidate_for_background(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_image_similarity_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.png",
            result_timeout_ms=20,
            generation_mode="image",
            save_debug_artifacts=True,
        )
    )
    agent.config.image_path.write_bytes(b"source")

    image_metadata = [
        {
            "kind": "img",
            "index": 0,
            "candidateId": "source-thumb",
            "signature": "large-new|1600x900",
            "renderedWidth": 1280,
            "renderedHeight": 720,
            "width": 1600,
            "height": 900,
            "top": 260,
        },
        {
            "kind": "canvas",
            "index": 1,
            "candidateId": "generated-canvas",
            "signature": "canvas|fresh-result",
            "renderedWidth": 900,
            "renderedHeight": 600,
            "width": 1200,
            "height": 800,
            "top": 280,
        },
    ]

    monkeypatch.setattr(agent, "_raise_if_auth_required", lambda page: None)
    monkeypatch.setattr(agent, "_download_from_controls", lambda page, output_path: False)
    monkeypatch.setattr(agent, "_capture_download_from_folder", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_image_generation_busy", lambda page: False)
    monkeypatch.setattr(agent, "_image_metadata", lambda page: image_metadata)
    monkeypatch.setattr(agent, "_write_debug_snapshot", lambda page, output_path, reason: {})
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    def fake_save(page, locator, output_path):
        Path(output_path).write_text(str(locator), encoding="utf-8")

    monkeypatch.setattr(agent, "_save_image_from_locator", fake_save)

    class FakePage:
        def locator(self, selector: str):
            class FakeLocator:
                def __init__(self, value: str):
                    self.value = value

                @property
                def first(self):
                    return self.value

                def nth(self, index: int):
                    return f"{selector}:{index}"

            if "generated-canvas" in selector:
                return FakeLocator("canvas-result")
            if "source-thumb" in selector:
                return FakeLocator("img-result")
            return FakeLocator(f"{selector}:fallback")

    output_path = root / "selected.png"
    agent._save_generated_image(FakePage(), 0, ["baseline|200x200"], {}, output_path)

    assert output_path.read_text(encoding="utf-8") == "canvas-result"
    report_path = root / "selected_candidates.json"
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert '"selected_candidate"' in report_text
    assert '"kind": "canvas"' in report_text
    assert '"selected.candidate_0.png"' in report_text or '"selected.candidate_1.png"' in report_text


def test_save_generated_image_prefers_background_container_candidate(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_bg_container_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.png",
            result_timeout_ms=20,
            generation_mode="image",
            save_debug_artifacts=True,
        )
    )
    agent.config.image_path.write_bytes(b"source")

    image_metadata = [
        {
            "kind": "img",
            "index": 0,
            "candidateId": "source-preview",
            "signature": "img|preview|671x1075",
            "renderedWidth": 671,
            "renderedHeight": 1075,
            "width": 671,
            "height": 1075,
            "top": 34.5,
        },
        {
            "kind": "background",
            "index": 0,
            "candidateId": "generated-background",
            "signature": "background|url(result.jpg)|1200x900",
            "renderedWidth": 1200,
            "renderedHeight": 900,
            "width": 1200,
            "height": 900,
            "top": 260,
        },
    ]

    monkeypatch.setattr(agent, "_raise_if_auth_required", lambda page: None)
    monkeypatch.setattr(agent, "_download_from_controls", lambda page, output_path: False)
    monkeypatch.setattr(agent, "_capture_download_from_folder", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_image_generation_busy", lambda page: False)
    monkeypatch.setattr(agent, "_image_metadata", lambda page: image_metadata)
    monkeypatch.setattr(agent, "_write_debug_snapshot", lambda page, output_path, reason: {})
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    def fake_save(page, locator, output_path):
        Path(output_path).write_text(str(locator), encoding="utf-8")

    monkeypatch.setattr(agent, "_save_image_from_locator", fake_save)

    class FakePage:
        def locator(self, selector: str):
            class FakeLocator:
                def __init__(self, value: str):
                    self.value = value

                @property
                def first(self):
                    return self.value

                def nth(self, index: int):
                    return f"{selector}:{index}"

            if "generated-background" in selector:
                return FakeLocator("background-result")
            if "source-preview" in selector:
                return FakeLocator("preview-result")
            return FakeLocator(f"{selector}:fallback")

    output_path = root / "selected.png"
    agent._save_generated_image(FakePage(), 0, ["baseline|200x200"], {}, output_path)

    assert output_path.read_text(encoding="utf-8") == "background-result"
    report_text = (root / "selected_candidates.json").read_text(encoding="utf-8")
    assert '"kind": "background"' in report_text


def test_save_image_from_locator_prefers_visible_screenshot() -> None:
    root = Path("test_runtime") / f"grok_web_img_capture_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=root / "out.png",
        )
    )

    output_path = root / "captured.png"

    class FakeLocator:
        def screenshot(self, *, path: str):
            Path(path).write_bytes(b"visible-image")

        def get_attribute(self, name: str):
            raise AssertionError("get_attribute should not be used when screenshot succeeds")

    agent._save_image_from_locator(object(), FakeLocator(), output_path)

    assert output_path.read_bytes() == b"visible-image"


def test_save_generated_image_does_not_keep_candidate_artifacts_when_debug_disabled(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_no_debug_candidates_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.png",
            result_timeout_ms=20,
            generation_mode="image",
            save_debug_artifacts=False,
        )
    )
    agent.config.image_path.write_bytes(b"source")

    image_metadata = [
        {
            "kind": "img",
            "index": 0,
            "candidateId": "only-candidate",
            "signature": "img|preview|800x600",
            "renderedWidth": 800,
            "renderedHeight": 600,
            "width": 800,
            "height": 600,
            "top": 200,
        }
    ]

    monkeypatch.setattr(agent, "_raise_if_auth_required", lambda page: None)
    monkeypatch.setattr(agent, "_download_from_controls", lambda page, output_path: False)
    monkeypatch.setattr(agent, "_capture_download_from_folder", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_image_generation_busy", lambda page: False)
    monkeypatch.setattr(agent, "_image_metadata", lambda page: image_metadata)
    monkeypatch.setattr(agent, "_write_debug_snapshot", lambda page, output_path, reason: {})
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    def fake_save(page, locator, output_path):
        Path(output_path).write_text(str(locator), encoding="utf-8")

    monkeypatch.setattr(agent, "_save_image_from_locator", fake_save)

    class FakePage:
        def locator(self, selector: str):
            class FakeLocator:
                def __init__(self, value: str):
                    self.value = value

                @property
                def first(self):
                    return self.value

                def nth(self, index: int):
                    return f"{selector}:{index}"

            if "only-candidate" in selector:
                return FakeLocator("saved-result")
            return FakeLocator(f"{selector}:fallback")

    output_path = root / "selected.png"
    agent._save_generated_image(FakePage(), 0, ["baseline|200x200"], {}, output_path)

    assert output_path.read_text(encoding="utf-8") == "saved-result"
    assert not (root / "selected_candidates.json").exists()
    assert not list(root.glob("selected.candidate_*.png"))


def test_save_image_from_locator_prefers_css_background_image_over_container_screenshot() -> None:
    root = Path("test_runtime") / f"grok_web_bg_css_capture_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=root / "out.png",
            result_timeout_ms=1_000,
        )
    )

    output_path = root / "captured.png"

    class FakeResponse:
        ok = True

        def body(self):
            return b"background-image-bytes"

    class FakeRequest:
        def get(self, url: str, timeout: int):
            assert url == "https://example.com/generated-background.png"
            return FakeResponse()

    class FakeContext:
        request = FakeRequest()

    class FakePage:
        context = FakeContext()

    class FakeLocator:
        def evaluate(self, script: str):
            assert "backgroundImage" in script
            return "https://example.com/generated-background.png"

        def screenshot(self, *, path: str):
            Path(path).write_bytes(b"container-screenshot")

        def get_attribute(self, name: str):
            raise AssertionError("get_attribute should not be used when CSS background image exists")

    agent._save_image_from_locator(FakePage(), FakeLocator(), output_path)

    assert output_path.read_bytes() == b"background-image-bytes"


def test_save_image_from_locator_prefers_nested_media_over_container_screenshot() -> None:
    root = Path("test_runtime") / f"grok_web_nested_media_capture_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=root / "out.png",
            result_timeout_ms=1_000,
        )
    )

    output_path = root / "captured.png"

    class FakeResponse:
        ok = True

        def body(self):
            return b"nested-image-bytes"

    class FakeRequest:
        def get(self, url: str, timeout: int):
            assert url == "https://example.com/generated-result.png"
            return FakeResponse()

    class FakeContext:
        request = FakeRequest()

    class FakePage:
        context = FakeContext()

        def evaluate(self, script: str, src: str):
            raise AssertionError("page.evaluate should not be used for non-blob nested media")

    class FakeLocator:
        def evaluate(self, script: str):
            if "querySelectorAll('img, canvas" in script:
                return {
                    "kind": "img",
                    "src": "https://example.com/generated-result.png",
                    "area": 921600,
                    "width": 1280,
                    "height": 720,
                }
            if "backgroundImage" in script:
                return ""
            raise AssertionError("Unexpected evaluate call")

        def screenshot(self, *, path: str):
            Path(path).write_bytes(b"container-screenshot")

        def get_attribute(self, name: str):
            return None

    agent._save_image_from_locator(FakePage(), FakeLocator(), output_path)

    assert output_path.read_bytes() == b"nested-image-bytes"


def test_save_image_from_locator_screenshots_nested_visual_node_before_container() -> None:
    root = Path("test_runtime") / f"grok_web_nested_visual_screenshot_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=root / "out.png",
            result_timeout_ms=1_000,
        )
    )

    output_path = root / "captured.png"

    class FakePage:
        pass

    class NestedLocator:
        @property
        def first(self):
            return self

        def screenshot(self, *, path: str):
            Path(path).write_bytes(b"nested-visual-screenshot")

    class FakeLocator:
        def evaluate(self, script: str):
            if "querySelectorAll('img, canvas" in script:
                return {
                    "kind": "background",
                    "src": "",
                    "nestedId": "nested-visual-1",
                    "area": 921600,
                    "width": 1280,
                    "height": 720,
                }
            if "backgroundImage" in script:
                return ""
            raise AssertionError("Unexpected evaluate call")

        def locator(self, selector: str):
            assert selector == '[data-codex-nested-visual-id="nested-visual-1"]'
            return NestedLocator()

        def screenshot(self, *, path: str):
            Path(path).write_bytes(b"container-screenshot")

        def get_attribute(self, name: str):
            return None

    agent._save_image_from_locator(FakePage(), FakeLocator(), output_path)

    assert output_path.read_bytes() == b"nested-visual-screenshot"


def test_configure_image_generation_allows_implied_horizontal_orientation(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.png"),
            generation_mode="image",
            aspect_ratio="16:9",
            orientation="horizontal",
        )
    )
    observed_logs: list[str] = []

    monkeypatch.setattr(agent, "_log", observed_logs.append)

    def fake_click_named_control(_page, _patterns):
        return True

    monkeypatch.setattr(agent, "_click_named_control", fake_click_named_control)
    monkeypatch.setattr(agent, "_set_aspect_ratio", lambda _page, _ratio: True)
    monkeypatch.setattr(agent, "_set_orientation", lambda _page, _orientation: False)

    agent._configure_image_generation(object())

    assert any("orientation control not found" in item for item in observed_logs)


def test_run_in_context_switches_back_to_video_mode_after_image_mode(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.mp4"),
            generation_mode="video",
        )
    )
    observed_calls: list[str] = []

    monkeypatch.setattr(agent, "_get_page", lambda context: object())
    monkeypatch.setattr(agent, "_video_count", lambda page: 0)
    monkeypatch.setattr(agent, "_video_signatures", lambda page: [])
    monkeypatch.setattr(agent, "_image_count", lambda page: 0)
    monkeypatch.setattr(agent, "_image_signatures", lambda page: [])
    monkeypatch.setattr(agent, "_downloads_snapshot", lambda: {})
    monkeypatch.setattr(agent, "_configure_image_generation", lambda page: observed_calls.append("image"))
    monkeypatch.setattr(agent, "_configure_video_generation", lambda page: observed_calls.append("video"))
    monkeypatch.setattr(agent, "_attach_image", lambda page, image_path: observed_calls.append("attach"))
    monkeypatch.setattr(agent, "_fill_prompt", lambda page, prompt_text: observed_calls.append("fill"))
    monkeypatch.setattr(agent, "_submit", lambda page: observed_calls.append("submit"))
    monkeypatch.setattr(agent, "_save_generated_video", lambda *args: observed_calls.append("save-video"))

    result = agent.run_in_context(object())

    assert result == Path("out.mp4")
    assert observed_calls[:3] == ["video", "attach", "fill"]
    assert "image" not in observed_calls


def test_launch_context_retries_after_target_closed(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.mp4"),
        )
    )
    observed_logs: list[str] = []
    monkeypatch.setattr(agent, "_log", observed_logs.append)
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    class FakeChromium:
        def __init__(self) -> None:
            self.calls = 0

        def launch_persistent_context(self, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise TargetClosedError("profile still closing")
            return "context"

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    result = agent._launch_context(FakePlaywright(), {"user_data_dir": "profile"})

    assert result == "context"
    assert observed_logs == ["chrome launch retry 1/3", "chrome launch retry 2/3"]


def test_launch_context_uses_profile_clone_fallback_after_retries_when_enabled(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_clone_{uuid4().hex}"
    profile_dir = root / "grok-web"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "Default").mkdir(parents=True, exist_ok=True)
    (profile_dir / "Default" / "Preferences").write_text("{}", encoding="utf-8")

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.mp4",
            profile_dir=profile_dir,
            allow_profile_clone_fallback=True,
        )
    )
    observed_logs: list[str] = []
    monkeypatch.setattr(agent, "_log", observed_logs.append)
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    class FakeChromium:
        def __init__(self) -> None:
            self.calls = 0

        def launch_persistent_context(self, **kwargs):
            self.calls += 1
            if self.calls <= 4:
                raise TargetClosedError("profile locked")
            return {"user_data_dir": kwargs["user_data_dir"]}

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    result = agent._launch_context(FakePlaywright(), {"user_data_dir": str(profile_dir)})

    assert Path(result["user_data_dir"]).name.startswith("grok-web-runtime-")
    assert any("chrome profile clone fallback active" in item for item in observed_logs)


def test_launch_context_does_not_use_profile_clone_fallback_by_default(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_no_clone_{uuid4().hex}"
    profile_dir = root / "grok-web"
    profile_dir.mkdir(parents=True, exist_ok=True)

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.mp4",
            profile_dir=profile_dir,
        )
    )
    observed_logs: list[str] = []
    monkeypatch.setattr(agent, "_log", observed_logs.append)
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    class FakeChromium:
        def launch_persistent_context(self, **_kwargs):
            raise TargetClosedError("profile locked")

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    with pytest.raises(GrokWebError) as exc_info:
        agent._launch_context(FakePlaywright(), {"user_data_dir": str(profile_dir)})

    assert "Profile-clone fallback is intentionally disabled" in str(exc_info.value)
    assert "Grok profile is busy; close the automation Chrome window and retry" in observed_logs


def test_connect_context_reuses_open_debug_browser() -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.mp4"),
            debug_port=9222,
        )
    )

    class FakeBrowser:
        def __init__(self) -> None:
            self.contexts = ["existing-context"]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, timeout: int):
            assert endpoint == "http://127.0.0.1:9222"
            assert timeout == 60_000
            return FakeBrowser()

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    result = agent._connect_context(FakePlaywright())

    assert result == "existing-context"


def test_close_lingering_login_browser_closes_debug_port_session(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.mp4"),
        )
    )
    observed_logs: list[str] = []
    monkeypatch.setattr(agent, "_log", observed_logs.append)
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    class FakeBrowser:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_browser = FakeBrowser()

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, timeout: int):
            assert endpoint == "http://127.0.0.1:9222"
            assert timeout == 1_500
            return fake_browser

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    agent._close_lingering_login_browser(FakePlaywright())

    assert fake_browser.closed is True
    assert "closing lingering Grok login browser on debug port 9222" in observed_logs


def test_terminate_profile_processes_stops_only_automation_profile(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_kill_{uuid4().hex}"
    profile_dir = root / "grok-web"
    profile_dir.mkdir(parents=True, exist_ok=True)

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.mp4",
            profile_dir=profile_dir,
        )
    )
    observed_logs: list[str] = []
    observed_command: list[str] = []
    monkeypatch.setattr(agent, "_log", observed_logs.append)
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    class Completed:
        returncode = 0

    def fake_run(command, **_kwargs):
        observed_command.extend(command)
        return Completed()

    monkeypatch.setattr("api.grok_web.subprocess.run", fake_run)

    agent._terminate_profile_processes()

    assert observed_command[:3] == ["powershell", "-NoProfile", "-Command"]
    assert str(profile_dir.resolve()).lower() in observed_command[3].lower()
    assert "terminated lingering Chrome processes for the Grok automation profile" in observed_logs


def test_clear_profile_restore_artifacts_removes_session_files(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_restore_{uuid4().hex}"
    profile_dir = root / "grok-web"
    sessions_dir = profile_dir / "Default" / "Sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    files = [sessions_dir / "session_file", sessions_dir / "tabs_file"]
    removed: list[Path] = []

    monkeypatch.setattr(Path, "iterdir", lambda self: iter(files))
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=True: removed.append(self))

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.mp4",
            profile_dir=profile_dir,
        )
    )

    agent._clear_profile_restore_artifacts()

    assert removed == files


def test_launch_managed_context_starts_chrome_and_connects(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_managed_{uuid4().hex}"
    profile_dir = root / "grok-web"
    profile_dir.mkdir(parents=True, exist_ok=True)

    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=root / "out.mp4",
            profile_dir=profile_dir,
            executable_path=Path("C:/Chrome/chrome.exe"),
        )
    )
    monkeypatch.setattr(agent, "_find_free_port", lambda: 9555)
    monkeypatch.setattr("api.grok_web.time.sleep", lambda _seconds: None)

    observed_command: list[str] = []

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return None

    def fake_popen(command, **_kwargs):
        observed_command.extend(command)
        return FakeProcess()

    monkeypatch.setattr("api.grok_web.subprocess.Popen", fake_popen)

    class FakeBrowser:
        def __init__(self) -> None:
            self.contexts = ["managed-context"]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, timeout: int):
            assert endpoint == "http://127.0.0.1:9555"
            assert timeout == 1_500
            return FakeBrowser()

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    result = agent._launch_managed_context(FakePlaywright())

    assert result == "managed-context"
    assert Path(observed_command[0]).as_posix() == "C:/Chrome/chrome.exe"
    assert any("--remote-debugging-port=9555" == item for item in observed_command)
    assert "--disable-hang-monitor" in observed_command
    assert "--hide-crash-restore-bubble" in observed_command
    assert observed_command[-1] == "about:blank"


def test_session_runner_uses_managed_context_without_debug_port(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_session_managed_{uuid4().hex}"
    profile_dir = root / "grok-web"
    profile_dir.mkdir(parents=True, exist_ok=True)
    output_path = root / "out.mp4"
    start_calls: list[str] = []
    launch_calls: list[str] = []

    class FakePlaywrightManager:
        def start(self):
            start_calls.append("start")
            return object()

        def stop(self) -> None:
            return None

    monkeypatch.setattr("api.grok_web.sync_playwright", lambda: FakePlaywrightManager())
    monkeypatch.setattr(GrokWebAgent, "_ensure_dependencies", lambda self: None)
    monkeypatch.setattr(GrokWebAgent, "_close_lingering_login_browser", lambda self, playwright: None)
    terminated = []
    monkeypatch.setattr(GrokWebAgent, "_terminate_profile_processes", lambda self: terminated.append(True))
    monkeypatch.setattr(GrokWebAgent, "_clear_profile_restore_artifacts", lambda self: None)
    monkeypatch.setattr(GrokWebAgent, "_launch_managed_context", lambda self, playwright: launch_calls.append("launch") or "managed-context")
    monkeypatch.setattr(GrokWebAgent, "run_in_context", lambda self, context: self.config.output_path)

    runner = GrokWebSessionRunner()
    first = runner.run(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=output_path,
            profile_dir=profile_dir,
        )
    )
    runner.close_stage_session()
    second = runner.run(
        GrokWebConfig(
            prompt_text="prompt-2",
            image_path=root / "frame-2.png",
            output_path=root / "out-2.mp4",
            profile_dir=profile_dir,
        )
    )
    runner.close()

    assert first == output_path
    assert second == root / "out-2.mp4"
    assert start_calls == ["start"]
    assert launch_calls == ["launch", "launch"]
    assert len(terminated) == 4


def test_cleanup_managed_browser_process_uses_taskkill_on_windows(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=Path("frame.png"),
            output_path=Path("out.mp4"),
        )
    )

    class FakeProcess:
        pid = 4321

        def __init__(self) -> None:
            self.wait_calls = 0

        def poll(self):
            return None

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(cmd="chrome", timeout=timeout)
            return None

        def kill(self):
            return None

    fake_process = FakeProcess()
    agent._managed_browser_process = fake_process
    observed_commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        observed_commands.append(command)
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr("api.grok_web.subprocess.run", fake_run)

    forced_shutdown = agent._cleanup_managed_browser_process()

    assert observed_commands == [["taskkill", "/PID", "4321", "/T"]]
    assert fake_process.wait_calls == 2
    assert forced_shutdown is False


def test_session_runner_falls_back_to_profile_launch_when_cdp_unavailable(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_web_session_{uuid4().hex}"
    profile_dir = root / "grok-web"
    profile_dir.mkdir(parents=True, exist_ok=True)
    output_path = root / "out.mp4"

    class FakePlaywrightManager:
        def start(self):
            return object()

        def stop(self) -> None:
            return None

    monkeypatch.setattr("api.grok_web.sync_playwright", lambda: FakePlaywrightManager())
    monkeypatch.setattr(GrokWebAgent, "_ensure_dependencies", lambda self: None)
    monkeypatch.setattr(GrokWebAgent, "_connect_context", lambda self, playwright: (_ for _ in ()).throw(GrokWebError("cdp unavailable")))
    monkeypatch.setattr(GrokWebAgent, "_launch_managed_context", lambda self, playwright: "managed-context")
    monkeypatch.setattr(GrokWebAgent, "run_in_context", lambda self, context: self.config.output_path)

    runner = GrokWebSessionRunner()
    result = runner.run(
        GrokWebConfig(
            prompt_text="prompt",
            image_path=root / "frame.png",
            output_path=output_path,
            profile_dir=profile_dir,
            debug_port=9222,
        )
    )
    runner.close()

    assert result == output_path
