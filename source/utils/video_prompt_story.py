from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from utils.video_prompt_composer import (
    IMAGE_TAG_RE,
    JERUSALEM_TZ,
    ReferenceContext,
    ScenarioVariantSpec,
    VideoImageReference,
    VideoSceneSpec,
    _find_latest_stage_dir,
    resolve_reference_context,
)
from video_prompt_story_config import VideoPromptStoryConfig

STORY_DRAFT_VERSION = 1
STORY_DRAFT_SCRIPT_ID = "story-draft"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class StoryImageCandidate:
    source_file: str
    image_path: Path
    stage_id: str


@dataclass(frozen=True)
class VideoPromptStoryDraft:
    title: str
    technical_preamble: str
    total_duration_seconds: int
    scene_duration_seconds: int
    aspect_ratio: str
    max_prompt_chars: int
    regeneration_assets_dir: Path
    restored_images_dir: Path
    references: tuple[VideoImageReference, ...]
    scenes: tuple[VideoSceneSpec, ...]
    scenario_variants: tuple[ScenarioVariantSpec, ...]
    image_paths: tuple[Path, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STORY_DRAFT_VERSION,
            "title": self.title,
            "technical_preamble": self.technical_preamble,
            "total_duration_seconds": self.total_duration_seconds,
            "scene_duration_seconds": self.scene_duration_seconds,
            "aspect_ratio": self.aspect_ratio,
            "max_prompt_chars": self.max_prompt_chars,
            "regeneration_assets_dir": str(self.regeneration_assets_dir),
            "restored_images_dir": str(self.restored_images_dir),
            "references": [
                {
                    "source_file": reference.source_file,
                    "tag": reference.tag,
                    "image_path": str(self.image_paths[index]),
                }
                for index, reference in enumerate(self.references)
            ],
            "scenes": [
                {
                    "duration_seconds": scene.duration_seconds,
                    "description": scene.description,
                }
                for scene in self.scenes
            ],
            "scenario_variants": [
                {
                    "variant_id": variant.variant_id,
                    "label": variant.label,
                    "instruction": variant.instruction,
                }
                for variant in self.scenario_variants
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VideoPromptStoryDraft":
        references_payload = payload.get("references")
        if not isinstance(references_payload, list) or not references_payload:
            raise ValueError("Story draft must include a non-empty references list.")
        references: list[VideoImageReference] = []
        image_paths: list[Path] = []
        for item in references_payload:
            if not isinstance(item, dict):
                raise ValueError("Every references item must be a JSON object.")
            source_file = str(item.get("source_file", "")).strip()
            tag = str(item.get("tag", "")).strip()
            image_path_raw = str(item.get("image_path", "")).strip()
            if not source_file or not IMAGE_TAG_RE.fullmatch(tag):
                raise ValueError("Each reference must include source_file and a valid @image tag.")
            references.append(VideoImageReference(source_file=source_file, tag=tag))
            image_paths.append(Path(image_path_raw) if image_path_raw else Path(source_file))

        scenes_payload = payload.get("scenes")
        if not isinstance(scenes_payload, list) or not scenes_payload:
            raise ValueError("Story draft must include a non-empty scenes list.")
        scenes = _build_scene_specs(
            descriptions=[
                str(item.get("description", "")).strip()
                for item in scenes_payload
                if isinstance(item, dict)
            ],
            scene_duration_seconds=int(payload.get("scene_duration_seconds", 2)),
        )

        scenario_variants_payload = payload.get("scenario_variants")
        scenario_variants = _parse_scenario_variants_payload(scenario_variants_payload)

        regeneration_assets_dir = Path(str(payload.get("regeneration_assets_dir", "")).strip())
        restored_images_dir = Path(str(payload.get("restored_images_dir", "")).strip())
        if not regeneration_assets_dir:
            raise ValueError("Story draft must include regeneration_assets_dir.")
        if not restored_images_dir:
            raise ValueError("Story draft must include restored_images_dir.")

        technical_preamble = str(payload.get("technical_preamble", "")).strip()
        if not technical_preamble:
            raise ValueError("Story draft must include a non-empty technical_preamble.")

        return cls(
            title=str(payload.get("title", "Multi-Scene Video Story")).strip() or "Multi-Scene Video Story",
            technical_preamble=technical_preamble,
            total_duration_seconds=int(payload.get("total_duration_seconds", 10)),
            scene_duration_seconds=int(payload.get("scene_duration_seconds", 2)),
            aspect_ratio=str(payload.get("aspect_ratio", "16:9")).strip() or "16:9",
            max_prompt_chars=int(payload.get("max_prompt_chars", 2000)),
            regeneration_assets_dir=regeneration_assets_dir,
            restored_images_dir=restored_images_dir,
            references=tuple(references),
            scenes=tuple(scenes),
            scenario_variants=scenario_variants,
            image_paths=tuple(image_paths),
        )


def discover_story_image_candidates(config: VideoPromptStoryConfig) -> list[StoryImageCandidate]:
    candidates: list[StoryImageCandidate] = []
    for image_path in sorted(config.restored_images_dir.iterdir(), key=lambda path: path.name.casefold()):
        if not image_path.is_file() or image_path.suffix.casefold() not in IMAGE_EXTENSIONS:
            continue
        source_stem = image_path.stem
        try:
            stage_dir = _find_latest_stage_dir(config.regeneration_assets_dir, source_stem)
        except FileNotFoundError:
            continue
        candidates.append(
            StoryImageCandidate(
                source_file=image_path.name,
                image_path=image_path,
                stage_id=stage_dir.name,
            )
        )
    return candidates


def select_story_image_candidates(
    config: VideoPromptStoryConfig,
    candidates: list[StoryImageCandidate],
) -> list[StoryImageCandidate]:
    if config.source_files:
        by_name = {candidate.source_file.casefold(): candidate for candidate in candidates}
        selected: list[StoryImageCandidate] = []
        for source_file in config.source_files:
            candidate = by_name.get(source_file.casefold())
            if candidate is None:
                raise FileNotFoundError(
                    f"Configured source_file '{source_file}' was not found among restored images "
                    f"with regeneration_assets in {config.restored_images_dir}."
                )
            selected.append(candidate)
        if len(selected) != config.image_count:
            raise ValueError(
                f"source_files must contain exactly {config.image_count} entries; got {len(selected)}."
            )
        return selected

    if len(candidates) < config.image_count:
        raise ValueError(
            f"Need at least {config.image_count} restored images with regeneration_assets; "
            f"found {len(candidates)} in {config.restored_images_dir}."
        )
    return candidates[: config.image_count]


def build_story_references(candidates: list[StoryImageCandidate]) -> tuple[VideoImageReference, ...]:
    references: list[VideoImageReference] = []
    for index, candidate in enumerate(candidates, start=1):
        references.append(
            VideoImageReference(
                source_file=candidate.source_file,
                tag=f"@image{index}",
            )
        )
    return tuple(references)


def resolve_story_reference_contexts(
    config: VideoPromptStoryConfig,
    references: tuple[VideoImageReference, ...],
) -> list[ReferenceContext]:
    return [
        resolve_reference_context(config.regeneration_assets_dir, reference)
        for reference in references
    ]


def build_empty_story_draft(
    config: VideoPromptStoryConfig,
    *,
    references: tuple[VideoImageReference, ...],
    image_paths: tuple[Path, ...],
    technical_preamble: str = "",
    scene_descriptions: list[str] | None = None,
) -> VideoPromptStoryDraft:
    descriptions = scene_descriptions or [""] * config.scene_count
    if len(descriptions) != config.scene_count:
        raise ValueError(
            f"Expected {config.scene_count} scene descriptions; got {len(descriptions)}."
        )
    return VideoPromptStoryDraft(
        title=config.story_title,
        technical_preamble=technical_preamble,
        total_duration_seconds=config.total_duration_seconds,
        scene_duration_seconds=config.scene_duration_seconds,
        aspect_ratio=config.aspect_ratio,
        max_prompt_chars=config.max_prompt_chars,
        regeneration_assets_dir=config.regeneration_assets_dir,
        restored_images_dir=config.restored_images_dir,
        references=references,
        scenes=tuple(
            _build_scene_specs(
                descriptions=descriptions,
                scene_duration_seconds=config.scene_duration_seconds,
            )
        ),
        scenario_variants=_default_scenario_variants(config),
        image_paths=image_paths,
    )


def apply_story_content(
    draft: VideoPromptStoryDraft,
    *,
    technical_preamble: str,
    scene_descriptions: list[str],
) -> VideoPromptStoryDraft:
    if len(scene_descriptions) != len(draft.scenes):
        raise ValueError(
            f"Expected {len(draft.scenes)} scene descriptions; got {len(scene_descriptions)}."
        )
    return VideoPromptStoryDraft(
        title=draft.title,
        technical_preamble=technical_preamble.strip(),
        total_duration_seconds=draft.total_duration_seconds,
        scene_duration_seconds=draft.scene_duration_seconds,
        aspect_ratio=draft.aspect_ratio,
        max_prompt_chars=draft.max_prompt_chars,
        regeneration_assets_dir=draft.regeneration_assets_dir,
        restored_images_dir=draft.restored_images_dir,
        references=draft.references,
        scenes=tuple(
            _build_scene_specs(
                descriptions=scene_descriptions,
                scene_duration_seconds=draft.scene_duration_seconds,
            )
        ),
        scenario_variants=draft.scenario_variants,
        image_paths=draft.image_paths,
    )


def rescale_story_draft_duration(
    draft: VideoPromptStoryDraft,
    *,
    scene_duration_seconds: int,
) -> VideoPromptStoryDraft:
    descriptions = [scene.description for scene in draft.scenes]
    total_duration_seconds = len(descriptions) * scene_duration_seconds
    return VideoPromptStoryDraft(
        title=draft.title,
        technical_preamble=draft.technical_preamble,
        total_duration_seconds=total_duration_seconds,
        scene_duration_seconds=scene_duration_seconds,
        aspect_ratio=draft.aspect_ratio,
        max_prompt_chars=draft.max_prompt_chars,
        regeneration_assets_dir=draft.regeneration_assets_dir,
        restored_images_dir=draft.restored_images_dir,
        references=draft.references,
        scenes=tuple(
            _build_scene_specs(
                descriptions=descriptions,
                scene_duration_seconds=scene_duration_seconds,
            )
        ),
        scenario_variants=draft.scenario_variants,
        image_paths=draft.image_paths,
    )


def write_story_draft_json(path: Path, draft: VideoPromptStoryDraft) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(draft.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_story_draft_json(path: Path) -> VideoPromptStoryDraft:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Story draft JSON in {path} must be a JSON object.")
    return VideoPromptStoryDraft.from_dict(payload)


def load_story_draft_from_html(path: Path) -> VideoPromptStoryDraft:
    html_text = path.read_text(encoding="utf-8")
    match = re.search(
        rf'<script[^>]+id="{STORY_DRAFT_SCRIPT_ID}"[^>]*>(.*?)</script>',
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        raise ValueError(
            f"Story HTML in {path} does not contain a #{STORY_DRAFT_SCRIPT_ID} JSON block."
        )
    payload = json.loads(match.group(1).strip())
    if not isinstance(payload, dict):
        raise ValueError(f"Embedded story draft in {path} must be a JSON object.")
    return VideoPromptStoryDraft.from_dict(payload)


def write_story_html(path: Path, draft: VideoPromptStoryDraft) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_story_html(draft, html_path=path), encoding="utf-8")
    return path


def render_story_html(draft: VideoPromptStoryDraft, *, html_path: Path) -> str:
    draft_json = json.dumps(draft.to_dict(), ensure_ascii=False, indent=2)
    reference_cards = "\n".join(
        _render_reference_card(
            reference=reference,
            image_path=image_path,
            html_path=html_path,
        )
        for reference, image_path in zip(draft.references, draft.image_paths, strict=True)
    )
    scene_cards = "\n".join(
        _render_scene_card(scene, index=index)
        for index, scene in enumerate(draft.scenes, start=1)
    )
    used_tags = sorted(
        {
            tag
            for scene in draft.scenes
            for tag in IMAGE_TAG_RE.findall(scene.description)
        },
        key=lambda tag: int(tag.replace("@image", "")),
    )
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(draft.title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f1ea;
      --panel: #fffdf8;
      --ink: #1f1b16;
      --muted: #6d6458;
      --accent: #355c7d;
      --line: #ddd4c7;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: linear-gradient(180deg, #efe8dc 0%, var(--bg) 240px);
      color: var(--ink);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 48px;
    }}
    h1, h2 {{
      margin: 0 0 12px;
    }}
    .intro, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px 20px;
      box-shadow: 0 10px 24px rgba(53, 92, 125, 0.08);
    }}
    .intro {{
      margin-bottom: 18px;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      color: var(--muted);
      font-size: 14px;
      margin-top: 8px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 16px;
      margin-top: 16px;
    }}
    .ref-card, .scene-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
    }}
    .ref-card img {{
      display: block;
      width: 100%;
      aspect-ratio: 16 / 10;
      object-fit: cover;
      background: #ece4d8;
    }}
    .ref-body, .scene-body {{
      padding: 12px 14px 16px;
    }}
    .tag {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #e7eef5;
      color: var(--accent);
      font-weight: 600;
      font-size: 13px;
    }}
    .filename {{
      margin-top: 8px;
      font-size: 13px;
      word-break: break-word;
    }}
    textarea {{
      width: 100%;
      min-height: 120px;
      box-sizing: border-box;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      font: inherit;
      resize: vertical;
      background: #fff;
    }}
    .scene-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
      margin-bottom: 10px;
    }}
    .timing {{
      color: var(--muted);
      font-size: 13px;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 18px 0;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 10px 16px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      cursor: pointer;
    }}
    button.secondary {{
      background: #6d6458;
    }}
    .note {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .used-tags {{
      margin-top: 8px;
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <main>
    <section class="intro">
      <h1>{html.escape(draft.title)}</h1>
      <p class="note">
        Просмотрите историю, отредактируйте поля ниже и нажмите «Обновить черновик».
        После правок экспортируйте JSON для <code>main_video_prompt_composer.py</code>
        командой <code>--export-config</code>.
      </p>
      <div class="meta">
        <span>Длительность: {draft.total_duration_seconds}s</span>
        <span>Сцен: {len(draft.scenes)}</span>
        <span>Длительность сцены: {draft.scene_duration_seconds}s</span>
        <span>Формат: {html.escape(draft.aspect_ratio)}</span>
        <span>Изображений: {len(draft.references)}</span>
      </div>
      <div class="used-tags"><strong>Используемые теги:</strong> {html.escape(", ".join(used_tags) or "—")}</div>
    </section>

    <section class="panel">
      <h2>Technical Preamble</h2>
      <textarea id="technical_preamble">{html.escape(draft.technical_preamble)}</textarea>
    </section>

    <section class="panel" style="margin-top: 18px;">
      <h2>Изображения истории</h2>
      <div class="grid">
        {reference_cards}
      </div>
    </section>

    <section class="panel" style="margin-top: 18px;">
      <h2>Сцены</h2>
      <div class="toolbar">
        <button type="button" id="syncDraft">Обновить черновик</button>
        <button type="button" class="secondary" id="downloadDraft">Скачать story JSON</button>
      </div>
      <div class="grid" id="sceneGrid">
        {scene_cards}
      </div>
    </section>
  </main>

  <script type="application/json" id="{STORY_DRAFT_SCRIPT_ID}">{draft_json}</script>
  <script>
    const draftNode = document.getElementById("{STORY_DRAFT_SCRIPT_ID}");
    let draft = JSON.parse(draftNode.textContent);

    function readDraftFromForm() {{
      draft.technical_preamble = document.getElementById("technical_preamble").value.trim();
      draft.scenes = Array.from(document.querySelectorAll(".scene-description")).map((node) => ({{
        duration_seconds: Number(node.dataset.durationSeconds),
        description: node.value.trim(),
      }}));
      draftNode.textContent = JSON.stringify(draft, null, 2);
    }}

    document.getElementById("syncDraft").addEventListener("click", () => {{
      readDraftFromForm();
      alert("Черновик обновлён в HTML. Теперь можно экспортировать JSON через main_video_prompt_story.py --export-config.");
    }});

    document.getElementById("downloadDraft").addEventListener("click", () => {{
      readDraftFromForm();
      const blob = new Blob([JSON.stringify(draft, null, 2)], {{ type: "application/json" }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "video_prompt_story_draft.json";
      link.click();
      URL.revokeObjectURL(url);
    }});
  </script>
</body>
</html>
"""


def story_draft_to_video_prompt_config(
    draft: VideoPromptStoryDraft,
    *,
    model: str | None = None,
    output_dir: Path | None = None,
    seedance_json: bool = True,
    seedance_json_only: bool = True,
    seedance_director_file: Path | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "technical_preamble": draft.technical_preamble,
        "total_duration_seconds": draft.total_duration_seconds,
        "max_prompt_chars": draft.max_prompt_chars,
        "aspect_ratio": draft.aspect_ratio,
        "regeneration_assets_dir": str(draft.regeneration_assets_dir),
        "references": [
            {
                "source_file": reference.source_file,
                "tag": reference.tag,
            }
            for reference in draft.references
        ],
        "scenes": [
            {
                "duration_seconds": scene.duration_seconds,
                "description": scene.description,
            }
            for scene in draft.scenes
        ],
        "scenario_variants": [
            {
                "variant_id": variant.variant_id,
                "label": variant.label,
                "instruction": variant.instruction,
            }
            for variant in draft.scenario_variants
        ],
        "model": model,
        "output_dir": str(output_dir or draft.regeneration_assets_dir),
        "seedance_json": seedance_json,
        "seedance_json_only": seedance_json_only,
        "seedance_director_file": str(
            seedance_director_file or (Path("services") / "Seedance_2.0_Director.md")
        ),
    }
    return payload


def write_video_prompt_config(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def default_story_output_paths(
    output_dir: Path,
    *,
    timestamp: datetime | None = None,
    stem: str = "video_prompt_story",
) -> tuple[Path, Path, Path]:
    stamp = (timestamp or datetime.now(JERUSALEM_TZ)).strftime("%Y%m%d_%H%M%S")
    html_path = output_dir / f"{stem}_{stamp}.html"
    json_path = output_dir / f"{stem}_{stamp}.json"
    composer_config_path = output_dir / f"video_prompt_config_{stamp}.json"
    return html_path, json_path, composer_config_path


def _render_reference_card(
    *,
    reference: VideoImageReference,
    image_path: Path,
    html_path: Path,
) -> str:
    relative_href = _relative_path_for_html(html_path, image_path)
    file_uri = Path(image_path).resolve().as_uri()
    return f"""
<article class="ref-card">
  <a href="{html.escape(file_uri)}" target="_blank" rel="noopener noreferrer">
    <img src="{html.escape(relative_href)}" alt="{html.escape(reference.tag)}">
  </a>
  <div class="ref-body">
    <span class="tag">{html.escape(reference.tag)}</span>
    <div class="filename">{html.escape(reference.source_file)}</div>
  </div>
</article>
""".strip()


def _render_scene_card(scene: VideoSceneSpec, *, index: int) -> str:
    tags = ", ".join(IMAGE_TAG_RE.findall(scene.description))
    return f"""
<article class="scene-card">
  <div class="scene-body">
    <div class="scene-head">
      <strong>Сцена {index}</strong>
      <span class="timing">{scene.start_seconds:g}-{scene.end_seconds:g}s · {scene.duration_seconds:g}s</span>
    </div>
    <textarea class="scene-description" data-duration-seconds="{scene.duration_seconds}">{html.escape(scene.description)}</textarea>
    <div class="note">Теги: {html.escape(tags or "—")}</div>
  </div>
</article>
""".strip()


def _relative_path_for_html(html_path: Path, target_path: Path) -> str:
    relative = os.path.relpath(Path(target_path).resolve(), html_path.resolve().parent)
    return quote(relative.replace("\\", "/"), safe="/:@!$&'()*+,;=-._~")


def _build_scene_specs(
    *,
    descriptions: list[str],
    scene_duration_seconds: int,
) -> list[VideoSceneSpec]:
    scenes: list[VideoSceneSpec] = []
    current_start = 0.0
    for index, description in enumerate(descriptions, start=1):
        duration = float(scene_duration_seconds)
        current_end = current_start + duration
        scenes.append(
            VideoSceneSpec(
                index=index,
                duration_seconds=duration,
                start_seconds=current_start,
                end_seconds=current_end,
                description=description,
            )
        )
        current_start = current_end
    return scenes


def _default_scenario_variants(config: VideoPromptStoryConfig) -> tuple[ScenarioVariantSpec, ...]:
    if config.scenario_variants:
        return tuple(
            ScenarioVariantSpec(
                variant_id=item["variant_id"],
                label=item["label"],
                instruction=item["instruction"],
            )
            for item in config.scenario_variants
        )
    return (
        ScenarioVariantSpec(
            variant_id="Variant_1",
            label="Variant 1",
            instruction=(
                "Create the most likely, most suitable, and most coherent cinematic interpretation."
            ),
        ),
        ScenarioVariantSpec(
            variant_id="Variant_2",
            label="Variant 2",
            instruction=(
                "Create a fully distinct alternative interpretation while preserving the same "
                "scene order, durations, and story facts."
            ),
        ),
    )


def _parse_scenario_variants_payload(value: object) -> tuple[ScenarioVariantSpec, ...]:
    if value is None:
        return (
            ScenarioVariantSpec(
                variant_id="Variant_1",
                label="Variant 1",
                instruction=(
                    "Create the most likely, most suitable, and most coherent cinematic interpretation."
                ),
            ),
        )
    if not isinstance(value, list) or not value:
        raise ValueError("scenario_variants must be a non-empty list when provided.")
    variants: list[ScenarioVariantSpec] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each scenario_variants item must be a JSON object.")
        variants.append(
            ScenarioVariantSpec(
                variant_id=str(item.get("variant_id", "")).strip(),
                label=str(item.get("label", "")).strip(),
                instruction=str(item.get("instruction", "")).strip(),
            )
        )
    return tuple(variants)
