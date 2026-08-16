from argparse import Namespace
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from config import Settings
from main_chatgpt_portrait_batch import (
    PortraitBatchConfig,
    PortraitConfigError,
    PortraitJob,
    PortraitStyle,
    build_portrait_jobs,
    list_input_images,
    load_portrait_config,
    run_batch,
    _resolve_result_timeout,
    _run_desktop_jobs,
    _output_dir_for_backend,
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


def test_load_portrait_config_accepts_result_timeout() -> None:
    root = Path("test_runtime") / f"portrait_config_timeout_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "portrait.json"
    config_path.write_text(
        '{\n'
        '  "portrait_styles": ["watercolor portrait"],\n'
        '  "result_timeout": 900\n'
        '}',
        encoding="utf-8",
    )

    config = load_portrait_config(config_path)

    assert config.result_timeout == 900.0


def test_ilya_repin_style_is_available_in_banks_and_special_config() -> None:
    base_config = load_portrait_config(Path("chatgpt_portrait_base_config.json"))
    all_styles_config = load_portrait_config(Path("chatgpt_all_styles_config.json"))
    special_config = load_portrait_config(Path("chatgpt_ilya_repin_config.json"))

    base_style = next(style for style in base_config.styles if style.name.startswith("ILYA_REPIN"))
    all_styles_style = next(style for style in all_styles_config.styles if style.name.startswith("ILYA_REPIN"))

    assert base_style.slug == "irp"
    assert all_styles_style.prompt == base_style.prompt
    assert len(special_config.styles) == 1
    assert special_config.styles[0].slug == "irp"
    assert special_config.styles[0].prompt == base_style.prompt
    assert special_config.output_dir == Path("output/chatgpt_ilya_repin")


def test_portrait_styles_table_matches_full_json_banks() -> None:
    base_config = load_portrait_config(Path("chatgpt_portrait_base_config.json"))
    all_styles_config = load_portrait_config(Path("chatgpt_all_styles_config.json"))
    table_text = Path("docs/portrait_styles_tables.md").read_text(encoding="utf-8-sig")

    def table_rows(section: str, next_section: str | None) -> list[tuple[str, str]]:
        content = table_text.split(section, 1)[1]
        if next_section is not None:
            content = content.split(next_section, 1)[0]
        rows: list[tuple[str, str]] = []
        for line in content.splitlines():
            columns = [column.strip() for column in line.strip().strip("|").split("|")]
            if len(columns) == 3 and columns[0].isdigit():
                rows.append((columns[1].strip("`"), columns[2].strip("`")))
        return rows

    base_styles = [(style.name, style.slug, style.prompt) for style in base_config.styles]
    all_styles = [(style.name, style.slug, style.prompt) for style in all_styles_config.styles]
    expected_by_name = sorted(
        ((name, slug) for name, slug, _prompt in base_styles),
        key=lambda item: item[0].casefold(),
    )
    expected_by_slug = sorted((slug, name) for name, slug, _prompt in base_styles)

    assert all_styles == base_styles
    assert table_rows("## Сортировка по `name`", "## Сортировка по `slug`") == expected_by_name
    assert table_rows("## Сортировка по `slug`", None) == expected_by_slug
    assert f"Количество стилей: **{len(base_styles)}**." in table_text
    assert len({slug for _name, slug, _prompt in base_styles}) == len(base_styles)
    assert all(1 <= len(slug) <= 4 for _name, slug, _prompt in base_styles)


def test_russian_artists_config_matches_base_bank_subset() -> None:
    base_config = load_portrait_config(Path("chatgpt_portrait_base_config.json"))
    russian_config = load_portrait_config(Path("chatgpt_russian_artists_config.json"))
    base_by_slug = {style.slug: style for style in base_config.styles}
    expected_slugs = ["srv", "vas", "vru", "lev"]

    assert [style.slug for style in russian_config.styles] == expected_slugs
    assert russian_config.output_dir == Path("output/chatgpt_russian_artists")
    for style in russian_config.styles:
        assert style.prompt == base_by_slug[style.slug].prompt
        assert style.name == base_by_slug[style.slug].name


def test_selected_artists_config_matches_base_bank_subset() -> None:
    base_config = load_portrait_config(Path("chatgpt_portrait_base_config.json"))
    selected_config = load_portrait_config(Path("chatgpt_selected_artists_config.json"))
    base_by_slug = {style.slug: style for style in base_config.styles}
    expected_slugs = ["pbl", "prs", "ver", "car", "rod", "mic", "mat", "bot", "tlt", "mod"]

    assert [style.slug for style in selected_config.styles] == expected_slugs
    assert selected_config.output_dir == Path("output/chatgpt_selected_artists")
    for style in selected_config.styles:
        assert style.prompt == base_by_slug[style.slug].prompt
        assert style.name == base_by_slug[style.slug].name


def test_new_artist_styles_have_short_unique_slugs() -> None:
    config = load_portrait_config(Path("chatgpt_portrait_base_config.json"))
    styles = {style.name: style for style in config.styles}
    expected = {
        "SANDRO_BOTTICELLI Florentine Renaissance portrait": ("bot", "SANDRO_BOTTICELLI:"),
        "TOULOUSE_LAUTREC Belle Epoque poster portrait": ("tlt", "TOULOUSE_LAUTREC:"),
        "AMEDEO_MODIGLIANI modernist elongated portrait": ("mod", "AMEDEO_MODIGLIANI:"),
        "EDGAR_DEGAS intimate Impressionist portrait": ("deg", "EDGAR_DEGAS:"),
        "PICASSO_BLUE Blue Period Picasso portrait": ("pbl", "PICASSO_BLUE:"),
        "PICASSO_ROSE Rose Period Picasso portrait": ("prs", "PICASSO_ROSE:"),
        "JOHANNES_VERMEER Dutch Golden Age interior portrait": ("ver", "JOHANNES_VERMEER:"),
        "CARAVAGGIO dramatic Baroque chiaroscuro portrait": ("car", "CARAVAGGIO:"),
        "AUGUSTE_RODIN sculptural portrait": ("rod", "AUGUSTE_RODIN:"),
        "MICHELANGELO sculptural marble portrait": ("mic", "MICHELANGELO:"),
        "HENRI_MATISSE Fauvist color portrait": ("mat", "HENRI_MATISSE:"),
        "VALENTIN_SEROV Russian realist portrait": ("srv", "VALENTIN_SEROV:"),
        "VIKTOR_VASNETSOV Russian epic folk portrait": ("vas", "VIKTOR_VASNETSOV:"),
        "MIKHAIL_VRUBEL Symbolist crystalline portrait": ("vru", "MIKHAIL_VRUBEL:"),
        "ISAAC_LEVITAN lyrical landscape portrait atmosphere": ("lev", "ISAAC_LEVITAN:"),
    }

    for name, (slug, prompt_marker) in expected.items():
        assert styles[name].slug == slug
        assert prompt_marker in styles[name].prompt
        assert len(styles[name].slug) <= 4


def test_resolve_result_timeout_prefers_cli_over_config() -> None:
    args = Namespace(result_timeout=123.0)
    config = PortraitBatchConfig(styles=[PortraitStyle(name="watercolor")], result_timeout=900.0)

    assert _resolve_result_timeout(args, config) == 123.0


def test_resolve_result_timeout_uses_config_when_cli_missing() -> None:
    args = Namespace(result_timeout=None)
    config = PortraitBatchConfig(styles=[PortraitStyle(name="watercolor")], result_timeout=900.0)

    assert _resolve_result_timeout(args, config) == 900.0


def test_run_batch_uses_config_result_timeout_when_cli_missing() -> None:
    root = Path("test_runtime") / f"portrait_batch_timeout_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "first.png"
    image_path.write_bytes(b"a")
    config_path = root / "portrait.json"
    config_path.write_text(
        '{\n'
        '  "portrait_styles": ["watercolor portrait"],\n'
        '  "result_timeout": 777\n'
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
        delivery_config_file=None,
        profile_dir=root / ".browser-profile" / "chatgpt-web",
        target_url="https://chatgpt.com/",
        chrome_exe=None,
        result_timeout=None,
        launch_timeout=45.0,
        no_submit=False,
        skip_existing=False,
        save_response_text=None,
    )

    run_batch(args, settings=settings, runner=fake_runner)

    assert captured[0].result_timeout_ms == 777_000


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
        delivery_config_file=None,
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
        delivery_config_file=None,
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
        delivery_config_file=None,
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


def test_service_backends_mirror_chatgpt_output_dirs() -> None:
    root = Path("test_runtime") / f"portrait_service_output_{uuid4().hex}"
    settings = _settings_for(root)

    assert _output_dir_for_backend(root / "output" / "chatgpt_portraits", "gemini-desktop", settings) == (
        root / "output" / "gemini_portraits"
    )
    assert _output_dir_for_backend(
        root / "output" / "chatgpt_watercolor_scene_expansion",
        "gemini",
        settings,
    ) == root / "output" / "gemini_watercolor_scene_expansion"
    assert _output_dir_for_backend(root / "output" / "photo_restoration", "gemini", settings) == (
        root / "output" / "gemini_photo_restoration"
    )
    assert _output_dir_for_backend(root / "output" / "chatgpt_portraits", "grok", settings) == (
        root / "output" / "grok_portraits"
    )
    assert _output_dir_for_backend(
        root / "output" / "chatgpt_watercolor_on_paper",
        "grok-web",
        settings,
    ) == root / "output" / "grok_watercolor_on_paper"
    assert _output_dir_for_backend(root / "output" / "photo_restoration", "grok", settings) == (
        root / "output" / "grok_photo_restoration"
    )
    assert _output_dir_for_backend(root / "output" / "chatgpt_portraits", "desktop", settings) == (
        root / "output" / "chatgpt_portraits"
    )


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
        delivery_config_file=None,
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


def test_run_batch_copies_saved_portrait_into_user_final_output_dir() -> None:
    root = Path("test_runtime") / f"portrait_delivery_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "first.png"
    image_path.write_bytes(b"a")
    portrait_config_path = root / "portrait.json"
    portrait_config_path.write_text(
        '{\n'
        '  "portrait_styles": [{"name": "watercolor portrait", "slug": "watercolor"}],\n'
        '  "output_dir": "output/chatgpt_portraits"\n'
        '}',
        encoding="utf-8",
    )
    delivery_config_path = root / "delivery.json"
    delivery_config_path.write_text(
        '{\n'
        '  "final_output_dir": "delivered/output",\n'
        '  "hero_image_dir": "project/hero",\n'
        '  "human_detail_txt": "project/hero_detail.txt",\n'
        '  "reports_dir": "project/reports"\n'
        '}',
        encoding="utf-8",
    )

    def fake_runner(config):
        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        config.output_path.write_bytes(b"portrait")
        return config.output_path

    args = Namespace(
        input_dir=None,
        output_dir=None,
        config_file=portrait_config_path,
        delivery_config_file=delivery_config_path,
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

    assert outputs == [root / "output" / "chatgpt_portraits" / "first_watercolor.png"]
    delivered = root / "delivered" / "output" / "chatgpt_portraits" / "first_watercolor.png"
    assert delivered.exists()
    assert delivered.read_bytes() == b"portrait"


def test_run_batch_rejects_missing_delivery_config() -> None:
    root = Path("test_runtime") / f"portrait_missing_delivery_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "first.png"
    image_path.write_bytes(b"a")
    portrait_config_path = root / "portrait.json"
    portrait_config_path.write_text(
        '{\n'
        '  "portrait_styles": [{"name": "watercolor portrait", "slug": "watercolor"}],\n'
        '  "output_dir": "output/chatgpt_portraits"\n'
        '}',
        encoding="utf-8",
    )

    args = Namespace(
        input_dir=None,
        output_dir=None,
        config_file=portrait_config_path,
        delivery_config_file=Path("missing_delivery.json"),
        profile_dir=root / ".browser-profile" / "chatgpt-web",
        target_url="https://chatgpt.com/",
        chrome_exe=None,
        result_timeout=123.0,
        launch_timeout=45.0,
        no_submit=False,
        skip_existing=False,
        save_response_text=None,
    )

    with pytest.raises(FileNotFoundError, match="Delivery config was not found"):
        run_batch(args, settings=settings, runner=lambda _config: None)


def test_desktop_portrait_jobs_force_clean_chat_and_strict_clipboard_attachment(monkeypatch) -> None:
    import sys
    import types

    root = Path("test_runtime") / f"portrait_desktop_config_{uuid4().hex}"
    settings = _settings_for(root)
    image_path = settings.input_dir / "first.png"
    image_path.write_bytes(b"a")
    output_path = settings.output_dir / "chatgpt_watercolor_on_paper" / "first_watercolor.png"
    captured_configs = []

    class FakeDesktopAgentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeChatGPTDesktopAgent:
        def __init__(self, config):
            self.config = config
            captured_configs.append(config)

        def run(self):
            self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.output_path.write_bytes(b"portrait")

    fake_desktop_module = types.SimpleNamespace(
        ChatGPTDesktopAgent=FakeChatGPTDesktopAgent,
        DesktopAgentConfig=FakeDesktopAgentConfig,
    )
    monkeypatch.setitem(sys.modules, "api.chatgpt_desktop_v2", fake_desktop_module)

    job = PortraitJob(
        image_path=image_path,
        style=PortraitStyle(name="watercolor", slug="watercolor"),
        prompt_text="prompt",
        output_path=output_path,
        response_text_path=None,
    )
    args = Namespace(
        backend="desktop",
        skip_existing=False,
        desktop_reactivate_delay=0.0,
        desktop_browser_tab_title_re=".*ChatGPT.*",
        desktop_active_window=True,
        desktop_new_chat=False,
        desktop_click_composer=False,
        desktop_clipboard_attach=True,
        desktop_post_attach_delay=8.0,
        desktop_min_result_wait=100.0,
        desktop_result_stable_wait=12.0,
        desktop_require_single_tab_window=True,
        desktop_prefer_single_tab_window=False,
        desktop_save_context_menu=True,
        desktop_capture_result=False,
        desktop_send_cursor_delay=0.0,
        desktop_verbose=False,
        chrome_exe=None,
        launch_timeout=60.0,
        desktop_dialog_timeout=20.0,
        result_timeout=300.0,
        desktop_new_chat_timeout=15.0,
        no_submit=False,
        continue_on_error=False,
        pause_between_jobs=False,
    )

    outputs = _run_desktop_jobs(
        args,
        [job],
        PortraitBatchConfig(styles=[job.style], new_chat_per_job=True),
        settings,
        delivery_config=None,
    )

    assert outputs == [output_path]
    assert captured_configs[0].force_new_chat_navigation is False
    assert captured_configs[0].require_new_attachment_preview is True
    assert captured_configs[0].post_attach_delay_sec == 8.0
    assert captured_configs[0].min_result_wait_sec == 100.0
    assert captured_configs[0].result_stable_sec == 12.0


def test_chatgpt_clean_chat_failure_is_desktop_safety_stop() -> None:
    from main_chatgpt_portrait_batch import _is_desktop_unsafe_continue_error

    exc = RuntimeError(
        "Could not open a clean new ChatGPT chat using the visible New chat control "
        "or verified browser-address navigation."
    )

    assert _is_desktop_unsafe_continue_error(exc) is True
