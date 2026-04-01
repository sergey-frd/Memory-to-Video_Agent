from argparse import Namespace
from pathlib import Path
from uuid import uuid4

from config import Settings
import main_grok_batch
from main_grok_batch import derive_image_stem, resolve_image_for_prompt, run_batch


def _settings_for(root: Path) -> Settings:
    settings = Settings(project_root=root)
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_derive_image_stem_from_pipeline_prompt_name() -> None:
    prompt_path = Path("Gemini_Generated_Image_kd8ksikd8ksikd8k_20260311_081437_v_prompt_1.txt")
    assert derive_image_stem(prompt_path) == "Gemini_Generated_Image_kd8ksikd8ksikd8k"


def test_resolve_image_for_prompt_matches_input_file() -> None:
    root = Path("test_runtime") / f"grok_batch_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "Gemini_Generated_Image_kd8ksikd8ksikd8k.png"
    image_path.write_bytes(b"img")
    prompt_path = settings.output_dir / "Gemini_Generated_Image_kd8ksikd8ksikd8k_20260311_081437_v_prompt_1.txt"
    prompt_path.write_text("prompt", encoding="utf-8")

    resolved = resolve_image_for_prompt(prompt_path, settings.input_dir)
    assert resolved == image_path


def test_run_batch_processes_all_prompts() -> None:
    root = Path("test_runtime") / f"grok_batch_{uuid4().hex}"
    settings = _settings_for(root)

    image_a = settings.input_dir / "frame_a.png"
    image_b = settings.input_dir / "frame_b.png"
    image_a.write_bytes(b"a")
    image_b.write_bytes(b"b")

    prompt_a = settings.output_dir / "frame_a_20260311_081437_v_prompt_1.txt"
    prompt_b = settings.output_dir / "frame_b_20260311_081438_v_prompt_1.txt"
    prompt_a.write_text("prompt a", encoding="utf-8")
    prompt_b.write_text("prompt b", encoding="utf-8")

    produced: list[Path] = []

    def fake_runner(run_args: Namespace) -> Path:
        run_args.output_video.write_bytes(run_args.prompt.read_text(encoding="utf-8").encode("utf-8"))
        produced.append(run_args.output_video)
        return run_args.output_video

    args = Namespace(
        prompt_dir=settings.output_dir,
        input_dir=settings.input_dir,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=600.0,
        launch_timeout=60.0,
        upload_timeout=180.0,
        generate_source_background=None,
        no_submit=False,
        skip_existing=False,
        keep_workdirs=True,
    )

    outputs = run_batch(args, settings=settings, runner=fake_runner)

    assert len(outputs) == 2
    assert all(path.exists() for path in outputs)
    assert produced[0].name == "frame_a_20260311_081437_video_1.mp4"
    assert produced[1].name == "frame_b_20260311_081438_video_1.mp4"


def test_run_batch_skips_existing_when_requested() -> None:
    root = Path("test_runtime") / f"grok_batch_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"a")
    prompt_path = settings.output_dir / "frame_a_20260311_081437_v_prompt_1.txt"
    prompt_path.write_text("prompt a", encoding="utf-8")
    existing_output = settings.output_dir / "frame_a_20260311_081437_video_1.mp4"
    existing_output.write_bytes(b"existing")

    called = {"count": 0}

    def fake_runner(run_args: Namespace) -> Path:
        called["count"] += 1
        return run_args.output_video

    args = Namespace(
        prompt_dir=settings.output_dir,
        input_dir=settings.input_dir,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=600.0,
        launch_timeout=60.0,
        upload_timeout=180.0,
        generate_source_background=None,
        no_submit=False,
        skip_existing=True,
        keep_workdirs=True,
    )

    outputs = run_batch(args, settings=settings, runner=fake_runner)

    assert outputs == [existing_output]
    assert called["count"] == 0


def test_run_batch_clears_input_and_output_after_success(monkeypatch) -> None:
    root = Path("test_runtime") / f"grok_batch_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"a")
    prompt_path = settings.output_dir / "frame_a_20260311_081437_v_prompt_1.txt"
    prompt_path.write_text("prompt a", encoding="utf-8")

    config_path = root / "config.json"
    config_path.write_text(
        (
            "{\n"
            '  "final_videos_dir": "delivered/videos",\n'
            '  "regeneration_assets_dir": "delivered/assets"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    def fake_runner(run_args: Namespace) -> Path:
        run_args.output_video.write_bytes(b"video")
        return run_args.output_video

    cleared: list[Path] = []

    def fake_clear(directory: Path) -> None:
        cleared.append(directory)

    args = Namespace(
        prompt_dir=settings.output_dir,
        input_dir=settings.input_dir,
        config_file=config_path,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=600.0,
        launch_timeout=60.0,
        upload_timeout=180.0,
        generate_source_background=None,
        no_submit=False,
        skip_existing=False,
        keep_workdirs=False,
    )

    monkeypatch.setattr(main_grok_batch, "clear_directory_contents", fake_clear)
    run_batch(args, settings=settings, runner=fake_runner)

    assert cleared == [settings.input_dir, settings.output_dir]
    assert (settings.output_dir / "frame_a_20260311_081437_video_1.mp4").exists()


def test_run_batch_requests_source_background_only_once_per_stage() -> None:
    root = Path("test_runtime") / f"grok_batch_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"a")
    prompt_one = settings.output_dir / "frame_a_20260311_081437_v_prompt_1.txt"
    prompt_two = settings.output_dir / "frame_a_20260311_081437_v_prompt_2.txt"
    prompt_one.write_text("prompt a", encoding="utf-8")
    prompt_two.write_text("prompt b", encoding="utf-8")

    config_path = root / "config.json"
    config_path.write_text('{"generate_source_background": true}', encoding="utf-8")

    seen_flags: list[bool | None] = []

    def fake_runner(run_args: Namespace) -> Path:
        seen_flags.append(run_args.generate_source_background)
        run_args.output_video.write_bytes(b"video")
        return run_args.output_video

    args = Namespace(
        prompt_dir=settings.output_dir,
        input_dir=settings.input_dir,
        config_file=config_path,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=600.0,
        launch_timeout=60.0,
        upload_timeout=180.0,
        generate_source_background=None,
        no_submit=False,
        skip_existing=False,
        keep_workdirs=True,
    )

    run_batch(args, settings=settings, runner=fake_runner)

    assert seen_flags == [True, False]


def test_run_batch_can_generate_background_only_when_video_disabled() -> None:
    root = Path("test_runtime") / f"grok_batch_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"a")
    prompt_one = settings.output_dir / "frame_a_20260311_081437_v_prompt_1.txt"
    prompt_two = settings.output_dir / "frame_a_20260311_081437_v_prompt_2.txt"
    prompt_one.write_text("prompt a", encoding="utf-8")
    prompt_two.write_text("prompt b", encoding="utf-8")

    config_path = root / "config.json"
    config_path.write_text('{"generate_video": false, "generate_source_background": true}', encoding="utf-8")

    observed: list[tuple[bool, bool]] = []

    def fake_runner(run_args: Namespace) -> Path:
        observed.append((run_args.generate_video, run_args.generate_source_background))
        output = settings.output_dir / "frame_a_20260311_081437_bg_image_16x9.png"
        output.write_bytes(b"bg")
        return output

    args = Namespace(
        prompt_dir=settings.output_dir,
        input_dir=settings.input_dir,
        config_file=config_path,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=600.0,
        launch_timeout=60.0,
        upload_timeout=180.0,
        generate_video=None,
        generate_source_background=None,
        save_grok_debug_artifacts=None,
        no_submit=False,
        skip_existing=False,
        keep_workdirs=True,
    )

    outputs = run_batch(args, settings=settings, runner=fake_runner)

    assert observed == [(False, True)]
    assert outputs == [settings.output_dir / "frame_a_20260311_081437_bg_image_16x9.png"]


def test_run_batch_reuses_single_grok_session_when_runner_not_provided(monkeypatch, capsys) -> None:
    root = Path("test_runtime") / f"grok_batch_{uuid4().hex}"
    settings = _settings_for(root)

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"a")
    prompt_path = settings.output_dir / "frame_a_20260311_081437_v_prompt_1.txt"
    prompt_path.write_text("prompt a", encoding="utf-8")

    calls: list[str] = []

    class FakeSessionRunner:
        def run(self, config):
            calls.append(f"session:{config.output_path.name}")
            config.output_path.write_bytes(b"video")
            return config.output_path

        def close(self) -> None:
            calls.append("close")

    def fake_run_generation(run_args: Namespace, settings: Settings | None = None, runner=None) -> Path:
        assert runner is not None
        config = type(
            "Cfg",
            (),
            {
                "output_path": run_args.output_video,
                "prompt_text": run_args.prompt.read_text(encoding="utf-8"),
                "image_path": run_args.image,
            },
        )()
        return runner(config)

    monkeypatch.setattr(main_grok_batch, "GrokWebSessionRunner", FakeSessionRunner)
    monkeypatch.setattr(main_grok_batch, "run_generation", fake_run_generation)

    args = Namespace(
        prompt_dir=settings.output_dir,
        input_dir=settings.input_dir,
        config_file=None,
        profile_dir=root / ".browser-profile" / "grok-web",
        target_url="https://grok.com/imagine",
        chrome_exe=None,
        result_timeout=600.0,
        launch_timeout=60.0,
        upload_timeout=180.0,
        generate_source_background=None,
        no_submit=False,
        skip_existing=False,
        keep_workdirs=True,
    )

    outputs = run_batch(args, settings=settings, runner=None)

    assert outputs == [settings.output_dir / "frame_a_20260311_081437_video_1.mp4"]
    assert calls == ["session:frame_a_20260311_081437_video_1.mp4", "close"]
    captured = capsys.readouterr()
    assert "Closing Grok for current image..." in captured.out
