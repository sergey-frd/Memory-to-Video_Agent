from pathlib import Path

from PIL import Image

from utils.image_analysis import analyze_image


def test_analyze_image_detects_orientation(tmp_path: Path) -> None:
    source = tmp_path / "portrait.png"
    Image.new("RGB", (200, 300)).save(source)

    metadata = analyze_image(source)

    assert metadata.width == 200
    assert metadata.height == 300
    assert metadata.orientation == "портретная"
    assert "200x300" in metadata.format_description
