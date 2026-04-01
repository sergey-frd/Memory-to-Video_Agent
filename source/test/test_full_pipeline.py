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

    processed: list[str] = []
    batch_calls: list[str] = []
    batch_debug_ports: list[object] = []
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
        output = settings.output_dir / f"{stage_id}_video_1.mp4"
        output.write_bytes(b"video")
        return [output]

    def fake_remove_processed_input(image_path: Path, settings: Settings) -> None:
        removed_inputs.append(image_path.name)

    monkeypatch.setattr(main_full_pipeline, "_run_generation", fake_run_generation)
    monkeypatch.setattr(main_full_pipeline, "write_pipeline_manifest", fake_manifest)
    monkeypatch.setattr(main_full_pipeline, "sync_stage_non_video_assets", fake_sync)
    monkeypatch.setattr(main_full_pipeline, "run_batch", fake_run_batch)
    monkeypatch.setattr(main_full_pipeline, "_remove_processed_input", fake_remove_processed_input)
    monkeypatch.setattr(main_full_pipeline, "clear_directory_contents", lambda _directory: None)

    outputs = main_full_pipeline.run_full_pipeline(_args(root), settings=settings)

    assert processed == ["frame_a.png", "frame_b.png"]
    assert batch_calls == ["frame_a_stage_v_prompt_1.txt", "frame_b_stage_v_prompt_1.txt"]
    assert batch_debug_ports == [None, None]
    assert removed_inputs == ["frame_a.png", "frame_b.png"]
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

    def fake_remove_processed_input(image_path: Path, settings: Settings) -> None:
        removed_inputs.append(image_path.name)

    monkeypatch.setattr(main_full_pipeline, "_run_generation", fake_run_generation)
    monkeypatch.setattr(main_full_pipeline, "write_pipeline_manifest", fake_manifest)
    monkeypatch.setattr(main_full_pipeline, "sync_stage_non_video_assets", fake_sync)
    monkeypatch.setattr(main_full_pipeline, "run_batch", fake_run_batch)
    monkeypatch.setattr(main_full_pipeline, "move_input_files_to_error", fake_move_input_files_to_error)
    monkeypatch.setattr(main_full_pipeline, "move_output_stage_to_error", fake_move_output_stage_to_error)
    monkeypatch.setattr(main_full_pipeline, "_remove_processed_input", fake_remove_processed_input)
    monkeypatch.setattr(main_full_pipeline, "clear_directory_contents", lambda _directory: None)

    outputs = main_full_pipeline.run_full_pipeline(_args(root), settings=settings)

    assert processed == ["broken.png", "ok.png"]
    assert outputs == [settings.output_dir / "ok_stage_video_1.mp4"]
    assert failed_inputs == [("broken_stage", ["broken.png"])]
    assert failed_outputs == ["broken_stage"]
    assert removed_inputs == ["ok.png"]
    error_report = root / "error" / "output" / "broken_stage" / "broken_stage_error.txt"
    assert error_report.exists()
    assert "RuntimeError" in error_report.read_text(encoding="utf-8")
