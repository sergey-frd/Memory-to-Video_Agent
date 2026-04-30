from argparse import Namespace
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from config import Settings
from main_chatgpt_portrait_batch import (
    PortraitConfigError,
    build_portrait_jobs,
    list_input_images,
    load_portrait_config,
    run_batch,
)


def _settings_for(root: Path) -> Settings:
    settings = Settings(project_root=root)
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_load_portrait_config_accepts_string_styles() -> None:
    root = Path("test_runtime") / f"portrait_config_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "portrait.json"
    config_path.write_text(
        '{\n'
        '  "portrait_styles": ["watercolor portrait", "pastel portrait"],\n'
        '  "prompt_template": "Create {style} for {image_name}",\n'
        '  "output_dir": "output/chatgpt_portraits"\n'
        '}',
        encoding="utf-8",
    )

    config = load_portrait_config(config_path)

    assert [style.name for style in config.styles] == ["watercolor portrait", "pastel portrait"]
    assert config.prompt_template == "Create {style} for {image_name}"
    assert config.output_dir == Path("output/chatgpt_portraits")
    assert config.new_chat_per_job is True


def test_load_portrait_config_rejects_missing_styles() -> None:
    root = Path("test_runtime") / f"portrait_config_invalid_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "portrait.json"
    config_path.write_text('{"prompt_template": "Create {style}"}', encoding="utf-8")

    with pytest.raises(PortraitConfigError, match="portrait_styles"):
        load_portrait_config(config_path)


def test_build_portrait_jobs_uses_style_slugs_and_prompts() -> None:
    root = Path("test_runtime") / f"portrait_jobs_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "person_a.jpg"
    image_path.write_bytes(b"img")
    config_path = root / "portrait.json"
    config_path.write_text(
        '{\n'
        '  "portrait_styles": [\n'
        '    {"name": "watercolor portrait", "slug": "watercolor"},\n'
        '    {"name": "pastel portrait", "prompt": "Generate {style} from {image_stem}"}\n'
        '  ],\n'
        '  "prompt_template": "Create {style} for {image_name}"\n'
        '}',
        encoding="utf-8",
    )

    config = load_portrait_config(config_path)
    jobs = build_portrait_jobs([image_path], config, settings.output_dir, settings.output_dir)

    assert [job.output_path.name for job in jobs] == [
        "person_a_watercolor.png",
        "person_a_pastel_portrait.png",
    ]
    assert jobs[0].prompt_text == "Create watercolor portrait for person_a.jpg"
    assert jobs[1].prompt_text == "Generate pastel portrait from person_a"
    assert jobs[0].response_text_path == settings.output_dir / "person_a_watercolor_response.txt"


def test_list_input_images_filters_supported_suffixes() -> None:
    root = Path("test_runtime") / f"portrait_inputs_{uuid4().hex}"
    settings = _settings_for(root)
    (settings.input_dir / "a.png").write_bytes(b"a")
    (settings.input_dir / "b.webp").write_bytes(b"b")
    (settings.input_dir / "ignore.txt").write_text("x", encoding="utf-8")

    assert [path.name for path in list_input_images(settings.input_dir)] == ["a.png", "b.webp"]


def test_run_batch_builds_chatgpt_configs_and_calls_runner() -> None:
    root = Path("test_runtime") / f"portrait_batch_{uuid4().hex}"
    settings = _settings_for(root)
    first = settings.input_dir / "first.png"
    second = settings.input_dir / "second.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    config_path = root / "portrait.json"
    config_path.write_text(
        '{\n'
        '  "portrait_styles": ["watercolor portrait", "pastel portrait"],\n'
        '  "prompt_template": "Generate {style} from {image_name}",\n'
        '  "output_dir": "portraits",\n'
        '  "save_response_text": true\n'
        '}',
        encoding="utf-8",
    )

    captured = []

    def fake_runner(config):
        captured.append(config)
        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        config.output_path.write_bytes(b"portrait")
        return config.output_path

    args = Namespace(
        input_dir=None,
        output_dir=None,
        config_file=config_path,
        profile_dir=root / ".browser-profile" / "chatgpt-web",
        target_url="https://chatgpt.com/",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        no_submit=False,
        skip_existing=False,
        save_response_text=None,
    )

    outputs = run_batch(args, settings=settings, runner=fake_runner)

    assert len(outputs) == 4
    assert all(path.exists() for path in outputs)
    assert captured[0].image_path == first
    assert captured[0].prompt_text == "Generate watercolor portrait from first.png"
    assert captured[0].output_path == root / "portraits" / "first_watercolor_portrait.png"
    assert captured[0].response_text_path == root / "portraits" / "first_watercolor_portrait_response.txt"
    assert captured[0].launch_timeout_ms == 45_000
    assert captured[0].result_timeout_ms == 123_000
    assert captured[0].open_new_chat_before_run is True


def test_run_batch_skips_existing_portrait() -> None:
    root = Path("test_runtime") / f"portrait_skip_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "first.png"
    image_path.write_bytes(b"a")
    config_path = root / "portrait.json"
    config_path.write_text(
        '{\n'
        '  "portrait_styles": [{"name": "watercolor portrait", "slug": "watercolor"}],\n'
        '  "output_dir": "portraits"\n'
        '}',
        encoding="utf-8",
    )
    existing_output = root / "portraits" / "first_watercolor.png"
    existing_output.parent.mkdir(parents=True, exist_ok=True)
    existing_output.write_bytes(b"existing")
    called = {"count": 0}

    def fake_runner(config):
        called["count"] += 1
        return config.output_path

    args = Namespace(
        input_dir=None,
        output_dir=None,
        config_file=config_path,
        profile_dir=root / ".browser-profile" / "chatgpt-web",
        target_url="https://chatgpt.com/",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        no_submit=False,
        skip_existing=True,
        save_response_text=None,
    )

    outputs = run_batch(args, settings=settings, runner=fake_runner)

    assert outputs == [existing_output]
    assert called["count"] == 0


def test_run_batch_api_backend_prepares_without_browser() -> None:
    root = Path("test_runtime") / f"portrait_api_prepare_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "first.png"
    image_path.write_bytes(b"a")
    config_path = root / "portrait.json"
    config_path.write_text(
        '{\n'
        '  "portrait_styles": [{"name": "pastel portrait", "slug": "pastel"}],\n'
        '  "output_dir": "portraits"\n'
        '}',
        encoding="utf-8",
    )

    args = Namespace(
        input_dir=None,
        output_dir=None,
        config_file=config_path,
        profile_dir=root / ".browser-profile" / "chatgpt-web",
        target_url="https://chatgpt.com/",
        chrome_exe=None,
        backend="api",
        result_timeout=123.0,
        launch_timeout=45.0,
        no_submit=True,
        skip_existing=False,
        save_response_text=None,
    )

    outputs = run_batch(args, settings=settings)

    assert outputs == [root / "portraits" / "first_pastel.png"]


def test_run_batch_local_backend_writes_distinct_style_files() -> None:
    root = Path("test_runtime") / f"portrait_local_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "first.png"
    image = Image.new("RGB", (64, 48), (120, 90, 70))
    for x in range(64):
        for y in range(48):
            image.putpixel((x, y), (80 + x * 2, 60 + y * 3, 140))
    image.save(image_path)
    config_path = root / "portrait.json"
    config_path.write_text(
        '{\n'
        '  "portrait_styles": [\n'
        '    {"name": "watercolor portrait", "slug": "watercolor"},\n'
        '    {"name": "pastel portrait", "slug": "pastel"}\n'
        '  ],\n'
        '  "output_dir": "portraits"\n'
        '}',
        encoding="utf-8",
    )

    args = Namespace(
        input_dir=None,
        output_dir=None,
        config_file=config_path,
        profile_dir=root / ".browser-profile" / "chatgpt-web",
        target_url="https://chatgpt.com/",
        chrome_exe=None,
        backend="local",
        api_model=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        no_submit=False,
        skip_existing=False,
        save_response_text=None,
    )

    outputs = run_batch(args, settings=settings)

    assert outputs == [
        root / "portraits" / "first_watercolor.png",
        root / "portraits" / "first_pastel.png",
    ]
    assert all(path.exists() for path in outputs)
    assert outputs[0].read_bytes() != outputs[1].read_bytes()
