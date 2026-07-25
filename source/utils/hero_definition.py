from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from api.openai_hero_definition import (
    DEFAULT_HERO_DEFINITION_MODEL,
    create_hero_definition_with_openai,
)


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
HeroAnalyzer = Callable[..., dict[str, object]]


def run_hero_definition_from_config(
    config_path: Path,
    *,
    analyzer: HeroAnalyzer = create_hero_definition_with_openai,
) -> Path:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    hero_image_dir = Path(str(payload["hero_image_dir"]))
    human_detail_txt = Path(str(payload["human_detail_txt"]))
    reports_dir = Path(str(payload["reports_dir"]))
    output_path_value = str(payload.get("output_path") or "").strip()
    output_path = Path(output_path_value) if output_path_value else reports_dir / "hero_def.json"
    hero_name = str(payload.get("hero_name") or "").strip()
    if not hero_name:
        raise ValueError("hero_name must not be empty.")
    if not hero_image_dir.is_dir():
        raise FileNotFoundError(f"Hero image directory does not exist: {hero_image_dir}")
    if not human_detail_txt.is_file():
        raise FileNotFoundError(f"Human detail text does not exist: {human_detail_txt}")

    extensions = {
        _normalize_extension(str(item))
        for item in payload.get("image_extensions", sorted(SUPPORTED_IMAGE_EXTENSIONS))
    }
    image_paths = sorted(
        (
            path
            for path in hero_image_dir.iterdir()
            if path.is_file() and path.suffix.casefold() in extensions
        ),
        key=lambda path: path.name.casefold(),
    )
    if not image_paths:
        raise ValueError(f"No supported reference images found in: {hero_image_dir}")

    human_detail_text = human_detail_txt.read_text(encoding="utf-8-sig").strip()
    if not human_detail_text:
        raise ValueError(f"Human detail text is empty: {human_detail_txt}")

    model = str(payload.get("model") or DEFAULT_HERO_DEFINITION_MODEL).strip()
    language = str(payload.get("language") or "ru").strip()
    max_image_edge = int(payload.get("max_image_edge", 1024))
    if max_image_edge < 256:
        raise ValueError("max_image_edge must be at least 256.")

    definition = analyzer(
        image_paths=image_paths,
        human_detail_text=human_detail_text,
        hero_name=hero_name,
        model=model,
        language=language,
        max_image_edge=max_image_edge,
    )
    _validate_definition(definition)

    result = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {
            "provider": "openai",
            "model": model,
        },
        "sources": {
            "hero_image_dir": str(hero_image_dir),
            "human_detail_txt": str(human_detail_txt),
            "human_detail_sha256": _sha256(human_detail_txt),
            "reference_image_count": len(image_paths),
            "reference_images": [
                {
                    "name": path.name,
                    "path": str(path),
                    "sha256": _sha256(path),
                }
                for path in image_paths
            ],
        },
        "definition": definition,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _normalize_extension(value: str) -> str:
    extension = value.strip().casefold()
    if not extension:
        raise ValueError("image_extensions must not contain empty values.")
    return extension if extension.startswith(".") else f".{extension}"


def _validate_definition(definition: dict[str, object]) -> None:
    required = {
        "hero_name",
        "visual_summary",
        "stable_visual_features",
        "appearance_variations",
        "strong_identity_evidence",
        "supporting_identity_evidence",
        "do_not_use_as_identity_evidence",
        "high_confidence_rule",
        "medium_confidence_rule",
        "uncertainties",
    }
    missing = sorted(required.difference(definition))
    if missing:
        raise ValueError(f"Hero definition is missing required fields: {', '.join(missing)}")
    if not isinstance(definition["stable_visual_features"], dict):
        raise ValueError("stable_visual_features must be a JSON object.")
    for field_name in (
        "appearance_variations",
        "strong_identity_evidence",
        "supporting_identity_evidence",
        "do_not_use_as_identity_evidence",
        "uncertainties",
    ):
        if not isinstance(definition[field_name], list):
            raise ValueError(f"{field_name} must be a JSON array.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
