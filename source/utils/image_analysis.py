from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass
class ImageMetadata:
    width: int
    height: int
    orientation: str
    format_description: str
    brightness_label: str
    contrast_label: str
    palette_label: str
    depth_label: str
    composition_label: str
    atmosphere_label: str
    scene_summary: str


def analyze_image(image_path: Path) -> ImageMetadata:
    """Read image metadata and derive a deterministic scene summary from pixels."""
    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size
        orientation = _detect_orientation(width, height)
        format_description = f"{width}x{height}, {orientation}"

        stats = ImageStat.Stat(rgb)
        mean_r, mean_g, mean_b = stats.mean
        gray = rgb.convert("L")
        gray_stats = ImageStat.Stat(gray)
        brightness = gray_stats.mean[0]
        contrast = gray_stats.stddev[0]

        hsv = rgb.convert("HSV")
        saturation = ImageStat.Stat(hsv).mean[1]
        edge_strength = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]

        center_box = (
            width // 4,
            height // 4,
            width - width // 4,
            height - height // 4,
        )
        center_gray = gray.crop(center_box)
        border_mask = Image.new("L", (width, height), 255)
        inner_mask = Image.new("L", (center_box[2] - center_box[0], center_box[3] - center_box[1]), 0)
        border_mask.paste(inner_mask, (center_box[0], center_box[1]))
        border_mean = ImageStat.Stat(gray, mask=border_mask).mean[0]
        center_mean = ImageStat.Stat(center_gray).mean[0]
        center_focus = center_mean - border_mean

    brightness_label = _brightness_label(brightness)
    contrast_label = _contrast_label(contrast)
    palette_label = _palette_label(mean_r, mean_g, mean_b, saturation)
    depth_label = _depth_label(edge_strength)
    composition_label = _composition_label(orientation, center_focus, edge_strength)
    atmosphere_label = _atmosphere_label(brightness, contrast, mean_r, mean_b, saturation)
    scene_summary = _scene_summary(
        orientation=orientation,
        composition_label=composition_label,
        brightness_label=brightness_label,
        contrast_label=contrast_label,
        palette_label=palette_label,
        depth_label=depth_label,
        atmosphere_label=atmosphere_label,
    )
    return ImageMetadata(
        width=width,
        height=height,
        orientation=orientation,
        format_description=format_description,
        brightness_label=brightness_label,
        contrast_label=contrast_label,
        palette_label=palette_label,
        depth_label=depth_label,
        composition_label=composition_label,
        atmosphere_label=atmosphere_label,
        scene_summary=scene_summary,
    )


def _detect_orientation(width: int, height: int) -> str:
    if height > width:
        return "portrait"
    if width > height:
        return "landscape"
    return "square"


def _brightness_label(brightness: float) -> str:
    if brightness < 70:
        return "low-key"
    if brightness < 150:
        return "balanced"
    return "bright"


def _contrast_label(contrast: float) -> str:
    if contrast < 28:
        return "soft-contrast"
    if contrast < 58:
        return "moderate-contrast"
    return "high-contrast"


def _palette_label(mean_r: float, mean_g: float, mean_b: float, saturation: float) -> str:
    warmth = mean_r - mean_b
    if saturation < 45:
        return "muted neutral palette"
    if warmth > 12:
        return "warm palette"
    if warmth < -12:
        return "cool palette"
    return "balanced natural palette"


def _depth_label(edge_strength: float) -> str:
    if edge_strength < 18:
        return "soft layered depth"
    if edge_strength < 34:
        return "clear mid-depth separation"
    return "dense textured depth"


def _composition_label(orientation: str, center_focus: float, edge_strength: float) -> str:
    if orientation == "portrait" and center_focus > 6:
        return "subject-forward composition"
    if orientation == "landscape" and center_focus < 2:
        return "environment-forward composition"
    if edge_strength > 34:
        return "detail-rich composition"
    return "balanced center composition"


def _atmosphere_label(brightness: float, contrast: float, mean_r: float, mean_b: float, saturation: float) -> str:
    warmth = mean_r - mean_b
    mood_score = _clamp((brightness - 100) / 50.0, -1.0, 1.0)
    tension_score = _clamp((contrast - 40) / 25.0, -1.0, 1.0)
    if mood_score > 0.35 and warmth > 8:
        return "warm open atmosphere"
    if mood_score < -0.35 and tension_score > 0:
        return "moody dramatic atmosphere"
    if saturation < 35:
        return "restrained reflective atmosphere"
    return "grounded natural atmosphere"


def _scene_summary(
    *,
    orientation: str,
    composition_label: str,
    brightness_label: str,
    contrast_label: str,
    palette_label: str,
    depth_label: str,
    atmosphere_label: str,
) -> str:
    return (
        f"{orientation} frame with a {composition_label}, {brightness_label} light, "
        f"{contrast_label} tonality, {palette_label}, {depth_label}, and a {atmosphere_label}."
    )
