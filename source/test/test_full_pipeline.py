from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from config import Settings
import main_full_pipeline


def _settings_for(root: Path) -> Settings:
    settings = Settings(project_root=root)
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _args(root: Path) -> Namespace:
    return Namespace(
        image=None,
        stage_id=None,
        config_file=root / "config.json",
        model="dall-e-2",
        scene_model="gpt-4.1",
        prompt_model="gpt-4.1",
        motion_model="gpt-4.1",
        generate_video=True,
        generate_styled_images=False,
        generate_final_frames=False,
        generate_source_background=False,
        generate_music=False,
        read_input_list=True,
        continue_after_failure=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        chrome_debug_port=9222,
        result_timeout=600.0,
        launch_timeout=60.0,
        upload_timeout=180.0,
        no_submit=False,
    )


def test_full_pipeline_processes_each_input_image_sequentially(monkeypatch, capsys) -> None:
    root = Path("test_runtime") / f"full_pipeline_{uuid4().hex}"
    settings = _settings_for(root)
    (root / "config.json").write_text('{"read_input_list": true, "continue_after_failure": false}', encoding="utf-8")

    first = settings.input_dir / "frame_a.png"
    second = settings.input_dir / "frame_b.png"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    sentinel = settings.output_dir / "unrelated.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    processed: list[str] = []
    batch_calls: list[str] = []
    batch_debug_ports: list[object] = []
    batch_reuse_pages: list[object] = []
    batch_require_debug_ports: list[object] = []
    removed_inputs: list[str] = []

    monkeypatch.setattr(main_full_pipeline, "stage_identifier", lambda provided, image_path: f"{image_path.stem}_stage")

    def fake_run_generation(run_args, generation_config, settings=None, **kwargs):
        processed.append(run_args.image.name)
        prompt_path = settings.output_dir / f"{run_args.stage_id}_v_prompt_1.txt"
        prompt_path.write_text("prompt", encoding="utf-8")
        return SimpleNamespace()

    def fake_manifest(settings, stage_id, image_path, generation_config, **kwargs):
        manifest_path = settings.output_dir / f"{stage_id}_api_pipeline_manifest.json"
        manifest_path.write_text("manifest", encoding="utf-8")
        return manifest_path

    def fake_sync(settings, generation_config, stage_id, **kwargs):
        return []

    def fake_run_batch(run_args, settings=None, runner=None):
        stage_id = processed[-1].replace(".png", "_stage")
        batch_calls.append(f"{stage_id}_v_prompt_1.txt")
        batch_debug_ports.append(run_args.chrome_debug_port)
        batch_reuse_pages.append(run_args.reuse_existing_page)
        batch_require_debug_ports.append(run_args.require_debug_port)
        output = settings.output_dir / f"{stage_id}_video_1.mp4"
        output.write_bytes(b"video")
        return [output]

    def fake_remove_processed_input(image_path: Path, settings: Settings) -> bool:
        removed_inputs.append(image_path.name)
        return True

    monkeypatch.setattr(main_full_pipeline, "_run_generation", fake_run_generation)
    monkeypatch.setattr(main_full_pipeline, "write_pipeline_manifest", fake_manifest)
    monkeypatch.setattr(main_full_pipeline, "sync_stage_non_video_assets", fake_sync)
    monkeypatch.setattr(main_full_pipeline, "run_batch", fake_run_batch)
    monkeypatch.setattr(main_full_pipeline, "_remove_processed_input", fake_remove_processed_input)

    outputs = main_full_pipeline.run_full_pipeline(_args(root), settings=settings)

    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    again = main_full_pipeline.run_full_pipeline(_args(root), settings=settings)
    assert again == outputs
    assert all(path.exists() for path in outputs)
    assert not list(settings.output_dir.glob('.grok-sessions/*/work/*'))
    assert processed == ["frame_a.png", "frame_b.png"]
    assert batch_calls == ["frame_a_stage_v_prompt_1.txt", "frame_b_stage_v_prompt_1.txt"]
    assert batch_debug_ports == [9222, 9222]
    assert batch_reuse_pages == [False, False]
    assert batch_require_debug_ports == [False, False]
    assert removed_inputs == ["frame_a.png", "frame_b.png"] * 2
    assert len(outputs) == 2
    captured = capsys.readouterr()
    assert "Starting Grok for current image..." in captured.out
    assert "Grok closed. Starting next image..." in captured.out


def test_full_pipeline_moves_failed_stage_to_error_and_continues(monkeypatch) -> None:
    root = Path("test_runtime") / f"full_pipeline_{uuid4().hex}"
    settings = _settings_for(root)
    (root / "config.json").write_text('{"read_input_list": true, "continue_after_failure": true}', encoding="utf-8")

    first = settings.input_dir / "broken.png"
    second = settings.input_dir / "ok.png"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    sentinel = settings.output_dir / "unrelated.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    processed: list[str] = []
    failed_inputs: list[tuple[str, list[str]]] = []
    failed_outputs: list[str] = []
    removed_inputs: list[str] = []

    monkeypatch.setattr(main_full_pipeline, "stage_identifier", lambda provided, image_path: f"{image_path.stem}_stage")

    def fake_run_generation(run_args, generation_config, settings=None, **kwargs):
        processed.append(run_args.image.name)
        prompt_path = settings.output_dir / f"{run_args.stage_id}_v_prompt_1.txt"
        prompt_path.write_text("prompt", encoding="utf-8")
        if run_args.image.name == "broken.png":
            raise RuntimeError("boom")
        return SimpleNamespace()

    def fake_manifest(settings, stage_id, image_path, generation_config, **kwargs):
        manifest_path = settings.output_dir / f"{stage_id}_api_pipeline_manifest.json"
        manifest_path.write_text("manifest", encoding="utf-8")
        return manifest_path

    def fake_sync(settings, generation_config, stage_id, **kwargs):
        return []

    def fake_run_batch(run_args, settings=None, runner=None):
        output = settings.output_dir / "ok_stage_video_1.mp4"
        output.write_bytes(b"video")
        return [output]

    def fake_move_input_files_to_error(settings: Settings, stage_id: str, files: list[Path]):
        failed_inputs.append((stage_id, [path.name for path in files]))
        return []

    def fake_move_output_stage_to_error(settings: Settings, stage_id: str):
        failed_outputs.append(stage_id)
        return []

    def fake_remove_processed_input(image_path: Path, settings: Settings) -> bool:
        removed_inputs.append(image_path.name)
        return True

    monkeypatch.setattr(main_full_pipeline, "_run_generation", fake_run_generation)
    monkeypatch.setattr(main_full_pipeline, "write_pipeline_manifest", fake_manifest)
    monkeypatch.setattr(main_full_pipeline, "sync_stage_non_video_assets", fake_sync)
    monkeypatch.setattr(main_full_pipeline, "run_batch", fake_run_batch)
    monkeypatch.setattr(main_full_pipeline, "_remove_processed_input", fake_remove_processed_input)

    outputs = main_full_pipeline.run_full_pipeline(_args(root), settings=settings)

    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    assert processed == ["broken.png", "ok.png"]
    assert outputs == [(root / "final_project/videos/ok_stage_video_1.mp4").resolve()]
    assert failed_inputs == []
    assert first.exists()
    assert failed_outputs == []
    assert removed_inputs == ["ok.png"]
    error_report = root / "error" / "output" / "broken_stage" / "broken_stage_error.txt"
    assert error_report.exists()
    assert "RuntimeError" in error_report.read_text(encoding="utf-8")


def test_remove_processed_input_uses_retry_capable_remove_path(monkeypatch) -> None:
    root = Path("test_runtime") / f"full_pipeline_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"a")

    removed: list[Path] = []
    original_exists = Path.exists
    image_exists_checks = {"count": 0}

    def fake_remove_path(path: Path) -> None:
        removed.append(path)

    def fake_exists(path: Path) -> bool:
        if path == image_path:
            image_exists_checks["count"] += 1
            return image_exists_checks["count"] == 1
        return original_exists(path)

    monkeypatch.setattr(main_full_pipeline, "remove_path", fake_remove_path)
    monkeypatch.setattr(Path, "exists", fake_exists)

    assert main_full_pipeline._remove_processed_input(image_path, settings) is True

    assert removed == [image_path]


def test_remove_processed_input_ignores_files_outside_input_queue(monkeypatch) -> None:
    root = Path("test_runtime") / f"full_pipeline_{uuid4().hex}"
    settings = _settings_for(root)
    external_path = root / "elsewhere" / "frame_a.png"
    external_path.parent.mkdir(parents=True, exist_ok=True)
    external_path.write_bytes(b"a")

    called = {"value": False}

    def fake_remove_path(path: Path) -> None:
        called["value"] = True

    monkeypatch.setattr(main_full_pipeline, "remove_path", fake_remove_path)

    assert main_full_pipeline._remove_processed_input(external_path, settings) is True

    assert called["value"] is False
    assert external_path.exists()


def test_full_pipeline_retries_processed_input_cleanup_after_grok_closes(monkeypatch) -> None:
    root = Path("test_runtime") / f"full_pipeline_{uuid4().hex}"
    settings = _settings_for(root)
    (root / "config.json").write_text('{"read_input_list": true, "continue_after_failure": false}', encoding="utf-8")

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"a")

    cleanup_attempts: list[str] = []

    monkeypatch.setattr(main_full_pipeline, "stage_identifier", lambda provided, image: f"{image.stem}_stage")

    def fake_run_generation(run_args, generation_config, settings=None, **kwargs):
        prompt_path = settings.output_dir / f"{run_args.stage_id}_v_prompt_1.json"
        prompt_path.write_text("[]", encoding="utf-8")
        return SimpleNamespace()

    def fake_manifest(settings, stage_id, image_path, generation_config, **kwargs):
        manifest_path = settings.output_dir / f"{stage_id}_api_pipeline_manifest.json"
        manifest_path.write_text("manifest", encoding="utf-8")
        return manifest_path

    def fake_sync(settings, generation_config, stage_id, **kwargs):
        return []

    def fake_run_batch(run_args, settings=None, runner=None):
        output = settings.output_dir / "frame_a_stage_video_1.mp4"
        output.write_bytes(b"video")
        return [output]

    def fake_remove_processed_input(image_path: Path, settings: Settings) -> bool:
        cleanup_attempts.append(image_path.name)
        return len(cleanup_attempts) > 1

    monkeypatch.setattr(main_full_pipeline, "_run_generation", fake_run_generation)
    monkeypatch.setattr(main_full_pipeline, "write_pipeline_manifest", fake_manifest)
    monkeypatch.setattr(main_full_pipeline, "sync_stage_non_video_assets", fake_sync)
    monkeypatch.setattr(main_full_pipeline, "run_batch", fake_run_batch)
    monkeypatch.setattr(main_full_pipeline, "_remove_processed_input", fake_remove_processed_input)

    outputs = main_full_pipeline.run_full_pipeline(_args(root), settings=settings)

    assert outputs == [(root / "final_project/videos/frame_a_stage_video_1.mp4").resolve()]
    assert cleanup_attempts == ["frame_a.png", "frame_a.png"]


def test_partial_batch_resumes_without_regenerating_completed_video(monkeypatch):
    root = Path("test_runtime") / f"resume_{uuid4().hex}"
    settings = _settings_for(root)
    (root / "config.json").write_text('{"read_input_list": true, "continue_after_failure": true}')
    image = settings.input_dir / "photo.png"
    image.write_bytes(b"source")
    sentinel = settings.output_dir / "other-project"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("keep")
    prompt_calls = []
    video_calls = []

    def prompts(args, config, settings, **kwargs):
        prompt_calls.append(args.image)
        for index in (1, 2):
            (settings.output_dir / f"{args.stage_id}_v_prompt_{index}.txt").write_text("prompt")
        return SimpleNamespace()

    def manifest(settings, stage_id, *args, **kwargs):
        path = settings.output_dir / f"{stage_id}_manifest.json"
        path.write_text("{}")
        return path

    def generate(args, settings, runner):
        video_calls.append(args.prompt.name)
        if len(video_calls) == 2:
            raise RuntimeError("upload failed")
        args.output_video.write_bytes(b"complete video")
        return args.output_video

    monkeypatch.setattr(main_full_pipeline, "_run_generation", prompts)
    monkeypatch.setattr(main_full_pipeline, "write_pipeline_manifest", manifest)
    monkeypatch.setattr(main_full_pipeline, "run_generation", generate)
    assert main_full_pipeline.run_full_pipeline(_args(root), settings) == []
    assert image.exists()
    outputs = main_full_pipeline.run_full_pipeline(_args(root), settings)
    assert len(outputs) == 2
    assert len(prompt_calls) == 1
    assert len(video_calls) == 3
    assert video_calls[1] == video_calls[2]
    assert all(path.exists() for path in outputs)
    assert not image.exists()
    assert (sentinel / "keep.txt").read_text() == "keep"


def test_no_submit_preserves_input_and_workspace(monkeypatch):
    root = Path("test_runtime") / f"prepare_{uuid4().hex}"
    settings = _settings_for(root)
    (root / "config.json").write_text('{"read_input_list": true}')
    image = settings.input_dir / "photo.png"
    image.write_bytes(b"source")
    def prompts(args, config, settings, **kwargs):
        (settings.output_dir / f"{args.stage_id}_v_prompt_1.txt").write_text("prompt")
        return SimpleNamespace()
    monkeypatch.setattr(main_full_pipeline, "_run_generation", prompts)
    monkeypatch.setattr(main_full_pipeline, "write_pipeline_manifest", lambda *a, **k: Path("manifest"))
    monkeypatch.setattr(main_full_pipeline, "run_batch", lambda *a, **k: [Path("planned.mp4")])
    args = _args(root)
    args.no_submit = True
    main_full_pipeline.run_full_pipeline(args, settings)
    assert image.exists()
    assert list(settings.output_dir.glob('.grok-sessions/*/work/*_v_prompt_1.txt'))


def test_completion_tracks_content_and_validates_delivery():
    from utils.grok_workspace import record_completed, completed_image
    root = Path("test_runtime") / f"completed_{uuid4().hex}"
    settings = _settings_for(root)
    image = settings.input_dir / "one.jpg"
    image.write_bytes(b"image")
    delivery = root / "delivered"
    delivery.mkdir()
    video = delivery / "one.mp4"
    video.write_bytes(b"video")
    record_completed(settings, image, delivery, [video])
    renamed = settings.input_dir / "renamed.jpg"
    renamed.write_bytes(b"image")
    assert completed_image(settings, renamed, delivery) == [video.resolve()]
    assert completed_image(settings, renamed, root / "other-project") is None
    renamed.write_bytes(b"changed image")
    assert completed_image(settings, renamed, delivery) is None
    video.write_bytes(b"corrupted")
    assert completed_image(settings, image, delivery) is None
    video.unlink()
    assert completed_image(settings, image, delivery) is None


def test_empty_queue_is_success_without_generation(monkeypatch):
    root = Path("test_runtime") / f"empty_{uuid4().hex}"
    settings = _settings_for(root)
    (root / "config.json").write_text('{"read_input_list": true}')
    monkeypatch.setattr(main_full_pipeline, "_run_generation", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Unexpected generation")))
    assert main_full_pipeline.run_full_pipeline(_args(root), settings) == []
