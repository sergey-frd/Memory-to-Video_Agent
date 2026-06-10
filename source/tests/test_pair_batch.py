from argparse import Namespace
from pathlib import Path

from PIL import Image

from config import GenerationConfig, Settings
from main_chatgpt_portrait_batch import (
    build_pair_jobs,
    list_input_image_pairs,
    load_portrait_config,
    run_batch,
)
from utils.project_delivery import sync_final_output_file


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (120, 90, 60)).save(path)


def test_pair_jobs_use_two_source_images(tmp_path: Path) -> None:
    first = tmp_path / "input_pair" / "01" / "first.jpg"
    second = tmp_path / "input_pair" / "01" / "second.png"
    _write_image(first)
    _write_image(second)

    config_path = tmp_path / "pair_config.json"
    config_path.write_text(
        """
{
  "pair_styles": [{"name": "Couple portrait", "slug": "couple"}],
  "prompt_template": "Pair {pair_name}: {image_1_name} + {image_2_name}. {style}",
  "output_dir": "output/pair",
  "save_response_text": false,
  "new_chat_per_job": true
}
""".strip(),
        encoding="utf-8",
    )

    pair_config = load_portrait_config(config_path)
    pairs = list_input_image_pairs(tmp_path / "input_pair")
    jobs = build_pair_jobs(
        pairs,
        pair_config,
        tmp_path / "output" / "pair",
        run_timestamp="20260522_153000",
    )

    assert len(jobs) == 1
    assert jobs[0].source_image_paths == (first, second)
    assert jobs[0].display_name == "01"
    assert jobs[0].output_path == tmp_path / "output" / "pair" / "01_art_pair_20260522_153000.png"
    assert "first.jpg + second.png" in jobs[0].prompt_text


def test_pair_output_sync_preserves_pair_subfolder(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    project_output = settings.output_dir / "pair" / "01_art_pair_20260522_153000.png"
    _write_image(project_output)

    delivery_config = GenerationConfig(final_output_dir=str(tmp_path / "client_output"))
    copied = sync_final_output_file(settings, delivery_config, project_output)

    assert copied == tmp_path / "client_output" / "pair" / "01_art_pair_20260522_153000.png"
    assert copied.exists()


def test_pair_mode_requires_desktop_backend(tmp_path: Path) -> None:
    config_path = tmp_path / "pair_config.json"
    config_path.write_text(
        """
{
  "pair_styles": ["Couple portrait"],
  "output_dir": "output/pair"
}
""".strip(),
        encoding="utf-8",
    )
    settings = Settings(project_root=tmp_path)
    args = Namespace(
        config_file=config_path,
        delivery_config_file=None,
        backend="web",
        input_pair_dir=tmp_path / "input_pair",
        input_dir=None,
        output_dir=None,
    )

    try:
        run_batch(args, settings=settings)
    except Exception as exc:
        assert "requires a desktop backend" in str(exc)
    else:
        raise AssertionError("Pair mode must reject non-desktop backends")
