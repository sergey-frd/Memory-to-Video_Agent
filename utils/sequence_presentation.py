from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

try:
    import cv2  # type: ignore[import-not-found]
except Exception:
    cv2 = None  # type: ignore[assignment]


_BEAT_COLORS = {
    1: "#b86841",
    2: "#8e4c5f",
    3: "#3e6b67",
    4: "#7f5f0f",
    5: "#516f9a",
    6: "#617347",
}

_TRANSITION_BADGE_COLORS = {
    "Cross Dissolve (Legacy)": "#3f7a6b",
    "Dip to Black": "#2f3640",
    "Film Dissolve": "#8f5b6b",
    "Morph Cut": "#8f6a2a",
    "gap": "#8b5e3c",
}

_STRUCTURE_FIELD_ALIASES = {
    "Тайминг блока": "timing",
    "Функция блока": "purpose",
    "Группа кадров": "group_range",
    "Ключевые клипы": "key_clips",
    "Кадры блока": "key_clips",
    "Визуальная задача": "visual_task",
    "Внутри блока": "internal_transition",
    "Переход к следующему блоку": "exit_transition",
    "Цветовой акцент": "color_accent",
    "Музыкальный акцент": "music_accent",
    "Музыкальная задача": "music_accent",
    "Звуковое решение": "sound_design",
}

_SUMMARY_FIELDS = (
    "Основная тема",
    "Краткое описание",
    "Эмоциональный тон",
    "Визуальная драматургия",
    "Монтажная логика",
)

_PREVIEW_SIZE = (960, 540)
_PLACEHOLDER_SIZE = (960, 540)


@dataclass(frozen=True)
class ClipPresentation:
    recommended_index: int
    original_index: int
    beat_index: int | None
    beat_title: str | None
    clip_name: str
    stage_id: str
    track_index: int
    scene_summary: str
    prompt_text: str
    preview_data_url: str
    preview_source: str
    video_path: str | None
    background_image_path: str | None
    initial_image_path: str | None


@dataclass(frozen=True)
class BeatPresentation:
    index: int
    title: str
    timing: str
    purpose: str
    group_range: str | None
    start_index: int | None
    end_index: int | None
    key_clip_names: list[str]
    visual_task: str | None
    internal_transition: str | None
    exit_transition: str | None
    color_accent: str | None
    music_accent: str | None
    sound_design: str | None


@dataclass(frozen=True)
class TransitionPresentation:
    track_label: str
    previous_name: str
    current_name: str
    transition_type: str | None
    duration: str | None
    reason: str | None
    feasibility: str | None
    gap: str | None


@dataclass(frozen=True)
class SequencePresentationBundle:
    project_path: str
    sequence_name: str
    structure_path: Path
    transition_path: Path
    optimization_json_path: Path
    summary_fields: dict[str, str]
    montage_notes: list[str]
    beats: list[BeatPresentation]
    transitions: list[TransitionPresentation]
    clips: list[ClipPresentation]


def derive_sequence_presentation_path(
    *,
    structure_report_txt: Path,
    output_dir: Path | None = None,
) -> Path:
    target_dir = output_dir or structure_report_txt.parent
    stem = structure_report_txt.stem
    if stem.endswith("_structure"):
        stem = stem[: -len("_structure")]
    return target_dir / f"{stem}_presentation.html"


def write_sequence_presentation(
    *,
    optimization_report_json: Path,
    structure_report_txt: Path,
    transition_report_txt: Path,
    output_path: Path,
    title: str | None = None,
) -> Path:
    bundle = build_sequence_presentation_bundle(
        optimization_report_json=optimization_report_json,
        structure_report_txt=structure_report_txt,
        transition_report_txt=transition_report_txt,
    )
    page_title = title or f"{bundle.sequence_name} presentation"
    html_text = render_sequence_presentation_html(bundle=bundle, page_title=page_title)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return output_path


def build_sequence_presentation_bundle(
    *,
    optimization_report_json: Path,
    structure_report_txt: Path,
    transition_report_txt: Path,
) -> SequencePresentationBundle:
    optimization_payload = json.loads(optimization_report_json.read_text(encoding="utf-8"))
    structure_text = _read_text_with_fallbacks(structure_report_txt)
    transition_text = _read_text_with_fallbacks(transition_report_txt)

    summary_fields, beats, montage_notes = _parse_structure_report(structure_text)
    transitions, project_path, sequence_name = _parse_transition_report(transition_text)
    clip_catalog = _build_clip_catalog(optimization_payload, beats)
    ordered_clips = sorted(clip_catalog.values(), key=lambda item: item.recommended_index)

    return SequencePresentationBundle(
        project_path=project_path or str(optimization_payload.get("source_xml") or ""),
        sequence_name=sequence_name or str(optimization_payload.get("selected_sequence_name") or ""),
        structure_path=structure_report_txt,
        transition_path=transition_report_txt,
        optimization_json_path=optimization_report_json,
        summary_fields=summary_fields,
        montage_notes=montage_notes,
        beats=beats,
        transitions=transitions,
        clips=ordered_clips,
    )


def render_sequence_presentation_html(
    *,
    bundle: SequencePresentationBundle,
    page_title: str,
) -> str:
    clip_by_name = {clip.clip_name: clip for clip in bundle.clips}
    transition_by_previous_name = {item.previous_name: item for item in bundle.transitions}

    summary_cards = "\n".join(
        _render_metric_card(label, value)
        for label, value in bundle.summary_fields.items()
        if value
    )
    beat_strip = "\n".join(_render_beat_strip_card(beat) for beat in bundle.beats)
    sequence_flow = "\n".join(
        _render_sequence_flow_item(
            clip=clip,
            transition=transition_by_previous_name.get(clip.clip_name),
        )
        for clip in bundle.clips
    )
    beat_sections = "\n".join(
        _render_beat_section(beat, bundle.clips, clip_by_name)
        for beat in bundle.beats
    )
    transition_sections = "\n".join(
        _render_transition_card(item, clip_by_name)
        for item in bundle.transitions
    )
    clip_index = "\n".join(_render_clip_index_card(clip) for clip in bundle.clips)
    montage_notes = "\n".join(
        f"<li>{html.escape(note)}</li>"
        for note in bundle.montage_notes
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)}</title>
  <style>
    :root {{
      --paper: #f6efe4;
      --paper-2: rgba(255,255,255,0.76);
      --ink: #261d16;
      --muted: #6e6257;
      --line: rgba(52, 36, 27, 0.14);
      --shadow: 0 24px 50px rgba(58, 33, 12, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: "Palatino Linotype", "Book Antiqua", Georgia, serif;
      color: var(--ink);
      background: linear-gradient(180deg, #f3eadc 0%, #f7f3ec 100%);
    }}
    .page {{ width: min(1480px, calc(100vw - 32px)); margin: 0 auto; padding: 24px 0 56px; }}
    .hero, .slide, .metric-card, .beat-strip-card, .sequence-card, .beat-panel, .clip-index-card, .transition-card {{
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }}
    .hero {{
      padding: 34px;
      border-radius: 30px;
      background:
        radial-gradient(circle at top left, rgba(227, 191, 133, 0.65), transparent 36%),
        radial-gradient(circle at top right, rgba(108, 145, 137, 0.35), transparent 28%),
        linear-gradient(180deg, #f3eadc 0%, #f8f4ee 100%);
    }}
    .eyebrow {{ margin: 0 0 10px; text-transform: uppercase; letter-spacing: 0.18em; font-size: 12px; color: #7d4f35; }}
    h1 {{ margin: 0; font-size: clamp(34px, 5vw, 64px); line-height: 0.94; max-width: 12ch; }}
    .hero-grid, .summary-grid, .beat-panel-grid, .sequence-main, .transition-pair {{
      display: grid; gap: 18px;
    }}
    .hero-grid, .summary-grid {{ grid-template-columns: 1.2fr 1fr; margin-top: 22px; }}
    .source-box {{
      display: grid; gap: 10px; padding: 18px; border-radius: 22px;
      background: rgba(255,255,255,0.66); border: 1px solid rgba(56, 38, 23, 0.10);
    }}
    .source-label, .metric-label, .meta-label, .thumb-note, .track-pill {{
      font-size: 12px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
    }}
    .source-value {{ font-family: Consolas, "Courier New", monospace; font-size: 13px; word-break: break-all; }}
    .toc {{
      position: sticky; top: 0; z-index: 5; display: flex; gap: 10px; flex-wrap: wrap; padding: 14px 0 18px;
      background: linear-gradient(180deg, rgba(248, 244, 238, 0.92), rgba(248, 244, 238, 0.68) 74%, rgba(248, 244, 238, 0));
    }}
    .toc a {{
      padding: 10px 16px; border-radius: 999px; text-decoration: none; color: inherit;
      background: rgba(255,255,255,0.84); border: 1px solid var(--line);
    }}
    .slide {{ margin-top: 22px; padding: 28px; border-radius: 28px; background: var(--paper-2); }}
    .slide h2 {{ margin: 0 0 18px; font-size: clamp(28px, 3vw, 44px); line-height: 1; }}
    .slide h3 {{ margin: 0 0 10px; font-size: 24px; line-height: 1.05; }}
    .metric-grid, .beat-strip, .beat-clips, .clip-index-grid, .transition-grid {{
      display: grid; gap: 14px;
    }}
    .metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .beat-strip, .clip-index-grid {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
    .transition-grid, .sequence-flow, .beat-grid {{ display: grid; gap: 18px; }}
    .metric-card, .beat-strip-card, .sequence-card, .beat-panel, .clip-index-card, .transition-card {{
      padding: 18px; border-radius: 22px; background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(248, 243, 236, 0.92));
    }}
    .notes-list {{ margin: 14px 0 0; padding-left: 20px; display: grid; gap: 8px; }}
    .notes-list li, .metric-card p, .beat-panel p, .transition-card p, .clip-index-card p {{ margin: 0; line-height: 1.55; }}
    .beat-strip-card {{ border-top: 5px solid var(--accent); min-height: 214px; }}
    .beat-strip-range {{ margin: 0 0 8px; font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }}
    .sequence-flow {{ list-style: none; margin: 0; padding: 0; }}
    .sequence-main {{ grid-template-columns: 320px 1fr; align-items: start; }}
    .clip-thumb {{
      width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 18px; display: block;
      background: #e6ddcf; border: 1px solid rgba(43, 31, 24, 0.09);
    }}
    .sequence-topline, .clip-topline, .transition-head {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    .step-badge, .beat-badge, .transition-badge {{
      display: inline-flex; align-items: center; padding: 6px 10px; border-radius: 999px;
      font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; font-weight: 700;
    }}
    .step-badge {{ background: rgba(41, 32, 24, 0.08); color: #47382d; }}
    .beat-badge, .transition-badge {{ color: white; }}
    .sequence-title, .clip-title {{ margin: 0; font-size: 28px; line-height: 1.08; word-break: break-word; }}
    .sequence-meta, .clip-meta {{ margin-top: 14px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .meta-card, .field-block, .beat-clip-card {{
      padding: 12px; border-radius: 18px; background: rgba(241, 235, 226, 0.76); border: 1px solid rgba(61, 41, 28, 0.08);
    }}
    .meta-value {{ margin: 0; font-size: 15px; line-height: 1.42; word-break: break-word; }}
    .connector {{ margin-top: 10px; padding-top: 14px; border-top: 1px dashed rgba(60, 40, 28, 0.20); display: grid; gap: 8px; }}
    .beat-panel {{ border-top: 6px solid var(--accent); }}
    .beat-panel-grid {{ grid-template-columns: 1.1fr 0.9fr; }}
    .beat-fields {{ display: grid; gap: 12px; }}
    .field-block h4 {{ margin: 0 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.10em; color: var(--muted); }}
    .beat-clips {{ grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }}
    .beat-clip-card h4, .transition-card h4 {{ margin: 10px 0 8px; font-size: 16px; line-height: 1.25; word-break: break-word; }}
    .transition-pair {{ grid-template-columns: 1fr 90px 1fr; align-items: center; }}
    .transition-arrow {{ text-align: center; font-size: 42px; color: rgba(49, 34, 24, 0.38); }}
    .muted {{ color: var(--muted); }}
    footer {{ margin-top: 24px; padding: 24px 4px 0; color: var(--muted); font-size: 14px; }}
    @media (max-width: 1100px) {{
      .hero-grid, .summary-grid, .beat-panel-grid, .sequence-main, .transition-pair, .metric-grid, .sequence-meta, .clip-meta {{ grid-template-columns: 1fr; }}
      .transition-arrow {{ display: none; }}
    }}
    @media (max-width: 720px) {{
      .page {{ width: min(100vw - 20px, 1480px); padding-top: 10px; }}
      .hero, .slide {{ padding: 20px; border-radius: 22px; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="hero" id="top">
      <p class="eyebrow">Sequence Presentation Prototype</p>
      <h1>{html.escape(bundle.sequence_name)}</h1>
      <div class="hero-grid">
        <div>
          <p>Наглядная монтажная презентация по текущему <code>manual_order</code> bundle: видно, какие клипы участвуют в каждом драматургическом блоке, где лежат рекомендуемые переходы и какой музыкальный акцент предлагается по ритму ролика.</p>
          <p>Для этого прототипа использованы рекомендации из <code>structure.txt</code> и <code>transition_recommendations.txt</code>, а визуалы подтянуты по <code>manual_order.json</code> и превью-кадрам из соответствующих <code>mp4</code>.</p>
        </div>
        <aside class="source-box">
          <div><div class="source-label">Project</div><div class="source-value">{html.escape(bundle.project_path)}</div></div>
          <div><div class="source-label">Structure Report</div><div class="source-value">{html.escape(str(bundle.structure_path))}</div></div>
          <div><div class="source-label">Transition Report</div><div class="source-value">{html.escape(str(bundle.transition_path))}</div></div>
          <div><div class="source-label">Manual Order JSON</div><div class="source-value">{html.escape(str(bundle.optimization_json_path))}</div></div>
        </aside>
      </div>
    </header>
    <nav class="toc">
      <a href="#summary">Сводка</a>
      <a href="#sequence">Последовательность</a>
      <a href="#beats">Beat-блоки</a>
      <a href="#transitions">Transitions</a>
      <a href="#clips">Клип-индекс</a>
      <a href="#top">Наверх</a>
    </nav>
    <section class="slide" id="summary">
      <h2>Сводка Истории</h2>
      <div class="summary-grid">
        <div>
          <div class="metric-grid">{summary_cards}</div>
          <ul class="notes-list">{montage_notes}</ul>
        </div>
        <div class="beat-strip">{beat_strip}</div>
      </div>
    </section>
    <section class="slide" id="sequence">
      <h2>Последовательность Кадров</h2>
      <ol class="sequence-flow">{sequence_flow}</ol>
    </section>
    <section class="slide" id="beats">
      <h2>Beat-Блоки И Музыкальные Акценты</h2>
      <div class="beat-grid">{beat_sections}</div>
    </section>
    <section class="slide" id="transitions">
      <h2>Transitions / Transmission</h2>
      <div class="transition-grid">{transition_sections}</div>
    </section>
    <section class="slide" id="clips">
      <h2>Клип-Индекс</h2>
      <div class="clip-index-grid">{clip_index}</div>
    </section>
    <footer>Превью берутся из <code>mp4</code>, а при отсутствии видео падают обратно на <code>bg_image</code> или исходное фото из manifest. Документ self-contained: все изображения встроены прямо в HTML.</footer>
  </div>
</body>
</html>
"""


def _render_metric_card(label: str, value: str) -> str:
    return (
        '<article class="metric-card">'
        f'<p class="metric-label">{html.escape(label)}</p>'
        f'<p>{html.escape(value)}</p>'
        "</article>"
    )


def _render_beat_strip_card(beat: BeatPresentation) -> str:
    accent = _beat_accent(beat.index)
    music_excerpt = _truncate_text(beat.music_accent or "Музыкальный акцент не указан.", limit=170)
    return (
        f'<article class="beat-strip-card" style="--accent: {accent}">'
        f'<p class="beat-strip-range">{html.escape(beat.timing)}</p>'
        f'<h3>{html.escape(f"{beat.index}. {beat.title}")}</h3>'
        f'<p>{html.escape(music_excerpt)}</p>'
        "</article>"
    )


def _render_sequence_flow_item(
    *,
    clip: ClipPresentation,
    transition: TransitionPresentation | None,
) -> str:
    accent = _beat_accent(clip.beat_index)
    beat_label = clip.beat_title or "Без beat-группы"
    scene_summary = _truncate_text(clip.scene_summary or clip.prompt_text or "Нет краткого описания.", limit=260)
    meta_cards = "".join(
        (
            _render_meta_card("Track", str(clip.track_index)),
            _render_meta_card("Beat", beat_label),
            _render_meta_card("Preview", clip.preview_source),
        )
    )
    connector = _render_transition_connector(transition) if transition is not None else ""
    return (
        '<li class="sequence-card">'
        '<div class="sequence-main">'
        '<div>'
        f'<img class="clip-thumb" src="{clip.preview_data_url}" alt="{html.escape(clip.clip_name)}">'
        f'<div class="thumb-note">{html.escape(clip.preview_source)}</div>'
        '</div>'
        '<div>'
        '<div class="sequence-topline">'
        f'<span class="step-badge">#{clip.recommended_index}</span>'
        f'<span class="beat-badge" style="background: {accent}">{html.escape(beat_label)}</span>'
        '</div>'
        f'<h3 class="sequence-title">{html.escape(clip.clip_name)}</h3>'
        f'<p>{html.escape(scene_summary)}</p>'
        f'<div class="sequence-meta">{meta_cards}</div>'
        '</div>'
        '</div>'
        f'{connector}'
        '</li>'
    )


def _render_transition_connector(transition: TransitionPresentation) -> str:
    if transition.transition_type is None:
        accent = _transition_accent("gap")
        return (
            '<div class="connector">'
            '<div class="transition-head">'
            f'<span class="transition-badge" style="background: {accent}">Gap / без transition</span>'
            '</div>'
            f'<p>Между клипами остаётся разрыв {html.escape(transition.gap or "")}.</p>'
            '</div>'
        )

    accent = _transition_accent(transition.transition_type)
    parts = [
        f"Рекомендация: {transition.transition_type}.",
        f"Duration: {transition.duration}." if transition.duration else "",
        transition.reason or "",
        transition.feasibility or "",
    ]
    body_text = " ".join(part for part in parts if part).strip()
    return (
        '<div class="connector">'
        '<div class="transition-head">'
        f'<span class="transition-badge" style="background: {accent}">{html.escape(transition.transition_type)}</span>'
        '</div>'
        f'<p>{html.escape(body_text)}</p>'
        '</div>'
    )


def _render_beat_section(
    beat: BeatPresentation,
    clips: list[ClipPresentation],
    clip_by_name: dict[str, ClipPresentation],
) -> str:
    accent = _beat_accent(beat.index)
    range_clips = [
        clip
        for clip in clips
        if beat.start_index is not None
        and beat.end_index is not None
        and beat.start_index <= clip.recommended_index <= beat.end_index
    ]
    featured = [clip_by_name[name] for name in beat.key_clip_names if name in clip_by_name]
    if not featured:
        featured = range_clips

    clip_cards = "\n".join(_render_beat_clip_card(clip) for clip in featured) or "<p>Клипы для блока не найдены в bundle.</p>"
    field_blocks = "\n".join(
        block
        for block in (
            _render_field_block("Тайминг", beat.timing),
            _render_field_block("Функция", beat.purpose),
            _render_field_block("Визуальная задача", beat.visual_task),
            _render_field_block("Внутри блока", beat.internal_transition),
            _render_field_block("Переход к следующему блоку", beat.exit_transition),
            _render_field_block("Цветовой акцент", beat.color_accent),
            _render_field_block("Музыкальный акцент", beat.music_accent),
            _render_field_block("Звук", beat.sound_design),
        )
        if block
    )
    return (
        f'<article class="beat-panel" style="--accent: {accent}">'
        f'<h3>{html.escape(f"{beat.index}. {beat.title}")}</h3>'
        '<div class="beat-panel-grid">'
        f'<div class="beat-fields">{field_blocks}</div>'
        f'<div class="beat-clips">{clip_cards}</div>'
        '</div>'
        '</article>'
    )


def _render_field_block(label: str, value: str | None) -> str:
    if not value:
        return ""
    return (
        '<div class="field-block">'
        f'<h4>{html.escape(label)}</h4>'
        f'<p>{html.escape(value)}</p>'
        '</div>'
    )


def _render_beat_clip_card(clip: ClipPresentation) -> str:
    excerpt = _truncate_text(clip.scene_summary or clip.prompt_text or "Нет описания.", limit=165)
    return (
        '<article class="beat-clip-card">'
        f'<img class="clip-thumb" src="{clip.preview_data_url}" alt="{html.escape(clip.clip_name)}">'
        f'<div class="thumb-note">{html.escape(clip.preview_source)}</div>'
        f'<h4>#{clip.recommended_index} {html.escape(clip.clip_name)}</h4>'
        f'<p>{html.escape(excerpt)}</p>'
        '</article>'
    )


def _render_transition_card(
    transition: TransitionPresentation,
    clip_by_name: dict[str, ClipPresentation],
) -> str:
    previous = clip_by_name.get(transition.previous_name)
    current = clip_by_name.get(transition.current_name)
    previous_block = _render_transition_clip(previous, transition.previous_name)
    current_block = _render_transition_clip(current, transition.current_name)
    badge_label = transition.transition_type or "Gap / без transition"
    accent = _transition_accent(transition.transition_type or "gap")
    if transition.transition_type is None:
        copy = f"Между клипами остаётся gap {transition.gap}, поэтому конкретный transition не рекомендуется."
        meta = ""
    else:
        copy = transition.reason or "Рекомендация без дополнительного текстового описания."
        meta = " | ".join(
            item
            for item in (
                f"Duration: {transition.duration}" if transition.duration else "",
                transition.feasibility or "",
            )
            if item
        )
    return (
        '<article class="transition-card">'
        '<div class="transition-head">'
        f'<span class="track-pill">{html.escape(transition.track_label)}</span>'
        f'<span class="transition-badge" style="background: {accent}">{html.escape(badge_label)}</span>'
        '</div>'
        '<div class="transition-pair">'
        f'{previous_block}'
        '<div class="transition-arrow">→</div>'
        f'{current_block}'
        '</div>'
        f'<p>{html.escape(copy)}</p>'
        f'<p class="muted">{html.escape(meta)}</p>'
        '</article>'
    )


def _render_transition_clip(clip: ClipPresentation | None, clip_name: str) -> str:
    if clip is None:
        preview = _placeholder_data_url(clip_name)
        note = "Нет визуала в bundle"
        summary = "Клип не найден в manual_order.json."
    else:
        preview = clip.preview_data_url
        note = clip.preview_source
        summary = _truncate_text(clip.scene_summary or clip.prompt_text or "Нет описания.", limit=150)
    return (
        '<div>'
        f'<img class="clip-thumb" src="{preview}" alt="{html.escape(clip_name)}">'
        f'<div class="thumb-note">{html.escape(note)}</div>'
        f'<h4>{html.escape(clip_name)}</h4>'
        f'<p class="muted">{html.escape(summary)}</p>'
        '</div>'
    )


def _render_clip_index_card(clip: ClipPresentation) -> str:
    accent = _beat_accent(clip.beat_index)
    meta_cards = "".join(
        (
            _render_meta_card("Beat", clip.beat_title or "—"),
            _render_meta_card("Track", str(clip.track_index)),
            _render_meta_card("Preview", clip.preview_source),
        )
    )
    return (
        '<article class="clip-index-card">'
        f'<img class="clip-thumb" src="{clip.preview_data_url}" alt="{html.escape(clip.clip_name)}">'
        '<div class="clip-topline">'
        f'<span class="step-badge">#{clip.recommended_index}</span>'
        f'<span class="beat-badge" style="background: {accent}">{html.escape(clip.beat_title or "Без beat-группы")}</span>'
        '</div>'
        f'<h3 class="clip-title">{html.escape(clip.clip_name)}</h3>'
        f'<p>{html.escape(_truncate_text(clip.scene_summary or clip.prompt_text or "Нет описания.", limit=220))}</p>'
        f'<div class="clip-meta">{meta_cards}</div>'
        '</article>'
    )


def _render_meta_card(label: str, value: str) -> str:
    return (
        '<div class="meta-card">'
        f'<p class="meta-label">{html.escape(label)}</p>'
        f'<p class="meta-value">{html.escape(value)}</p>'
        '</div>'
    )


def _parse_structure_report(text: str) -> tuple[dict[str, str], list[BeatPresentation], list[str]]:
    lines = [line.rstrip() for line in text.splitlines()]
    summary_fields: dict[str, str] = {}
    beats: list[BeatPresentation] = []
    montage_notes: list[str] = []

    in_description = False
    in_notes = False
    in_beats = False
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue

        if stripped == "Описание видеоролика":
            in_description = True
            in_notes = False
            index += 1
            continue
        if stripped == "Рекомендуемая музыка":
            in_description = False
            in_notes = False
            index += 1
            continue
        if stripped == "Каркас ролика":
            in_description = False
            in_notes = False
            in_beats = True
            index += 1
            continue
        if stripped == "Монтажные ориентиры":
            in_description = False
            in_notes = True
            in_beats = False
            index += 1
            continue

        beat_match = re.match(r"^(?P<index>\d+)\.\s+(?P<title>.+)$", stripped)
        if in_beats and beat_match:
            beat, next_index = _parse_beat_block(lines, index)
            beats.append(beat)
            in_description = False
            in_notes = False
            index = next_index
            continue

        if in_description and ": " in stripped:
            label, value = stripped.split(": ", 1)
            if label in _SUMMARY_FIELDS:
                summary_fields[label] = value.strip()
            index += 1
            continue

        if in_notes and stripped.startswith("- "):
            montage_notes.append(stripped[2:].strip())
            index += 1
            continue

        index += 1

    return summary_fields, beats, montage_notes


def _parse_beat_block(lines: list[str], start_index: int) -> tuple[BeatPresentation, int]:
    heading = lines[start_index].strip()
    heading_match = re.match(r"^(?P<index>\d+)\.\s+(?P<title>.+)$", heading)
    if heading_match is None:
        raise ValueError(f"Unexpected beat heading: {heading}")

    fields: dict[str, str] = {}
    index = start_index + 1
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped == "Монтажные ориентиры":
            break
        if re.match(r"^\d+\.\s+.+$", stripped):
            break
        if ": " in stripped:
            label, value = stripped.split(": ", 1)
            alias = _STRUCTURE_FIELD_ALIASES.get(label)
            if alias is not None:
                fields[alias] = value.strip()
        index += 1

    start_clip_index, end_clip_index = _parse_group_range(fields.get("group_range"))
    key_clip_names = _parse_clip_labels(fields.get("key_clips", ""))
    beat = BeatPresentation(
        index=int(heading_match.group("index")),
        title=heading_match.group("title").strip(),
        timing=fields.get("timing", ""),
        purpose=fields.get("purpose", ""),
        group_range=fields.get("group_range"),
        start_index=start_clip_index,
        end_index=end_clip_index,
        key_clip_names=key_clip_names,
        visual_task=fields.get("visual_task"),
        internal_transition=fields.get("internal_transition"),
        exit_transition=fields.get("exit_transition"),
        color_accent=fields.get("color_accent"),
        music_accent=fields.get("music_accent"),
        sound_design=fields.get("sound_design"),
    )
    return beat, index


def _parse_transition_report(text: str) -> tuple[list[TransitionPresentation], str | None, str | None]:
    lines = [line.rstrip() for line in text.splitlines()]
    project_path: str | None = None
    sequence_name: str | None = None
    transitions: list[TransitionPresentation] = []
    current_track: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Project: "):
            project_path = line.split(": ", 1)[1].strip()
            continue
        if line.startswith("Sequence: "):
            sequence_name = line.split(": ", 1)[1].strip()
            continue
        if re.match(r"^Track \d+$", line):
            current_track = line
            continue
        if not line.startswith("- ") or current_track is None:
            continue

        content = line[2:]
        gap_match = re.match(
            r"^(?P<previous>.+?) -> (?P<current>.+?): no transition recommendation because a gap of (?P<gap>\d+) remains\.$",
            content,
        )
        if gap_match is not None:
            transitions.append(
                TransitionPresentation(
                    track_label=current_track,
                    previous_name=gap_match.group("previous").strip(),
                    current_name=gap_match.group("current").strip(),
                    transition_type=None,
                    duration=None,
                    reason=None,
                    feasibility=None,
                    gap=gap_match.group("gap").strip(),
                )
            )
            continue

        recommendation_match = re.match(
            r"^(?P<previous>.+?) -> (?P<current>.+?): (?P<type>.+?), recommended duration (?P<duration>\d+)\. (?P<details>.+)$",
            content,
        )
        if recommendation_match is None:
            continue

        details = recommendation_match.group("details").rstrip(".")
        if ". " in details:
            reason, feasibility = details.rsplit(". ", 1)
        else:
            reason, feasibility = details, ""

        transitions.append(
            TransitionPresentation(
                track_label=current_track,
                previous_name=recommendation_match.group("previous").strip(),
                current_name=recommendation_match.group("current").strip(),
                transition_type=recommendation_match.group("type").strip(),
                duration=recommendation_match.group("duration").strip(),
                reason=reason.strip(),
                feasibility=feasibility.strip() or None,
                gap=None,
            )
        )

    return transitions, project_path, sequence_name


def _build_clip_catalog(
    optimization_payload: dict[str, object],
    beats: list[BeatPresentation],
) -> dict[str, ClipPresentation]:
    beat_by_index: dict[int, BeatPresentation] = {}
    for beat in beats:
        if beat.start_index is None or beat.end_index is None:
            continue
        for clip_index in range(beat.start_index, beat.end_index + 1):
            beat_by_index[clip_index] = beat

    previews_cache: dict[tuple[str, str], tuple[str, str]] = {}
    catalog: dict[str, ClipPresentation] = {}
    for entry in optimization_payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        candidate = entry.get("candidate")
        if not isinstance(candidate, dict):
            continue
        clip = candidate.get("clip")
        if not isinstance(clip, dict):
            continue
        assets = candidate.get("assets")
        if not isinstance(assets, dict):
            assets = {}

        clip_name = str(clip.get("name") or "").strip()
        if not clip_name:
            continue
        recommended_index = _safe_int(entry.get("recommended_index"))
        beat = beat_by_index.get(recommended_index)
        stage_id = str(clip.get("stage_id") or "").strip()

        manifest = assets.get("manifest")
        if not isinstance(manifest, dict):
            manifest = {}
        config = manifest.get("config")
        if not isinstance(config, dict):
            config = {}
        scene_analysis = assets.get("scene_analysis")
        if not isinstance(scene_analysis, dict):
            scene_analysis = {}

        final_videos_dir = _optional_path(config.get("final_videos_dir"))
        video_path = None
        if final_videos_dir is not None:
            candidate_video = final_videos_dir / Path(clip_name).name
            if candidate_video.exists():
                video_path = candidate_video

        background_path = _resolve_background_image_path(stage_id, final_videos_dir)
        initial_image_path = _resolve_initial_image_path(manifest)

        preview_key = (
            str(video_path) if video_path is not None else "",
            stage_id,
        )
        preview_data = previews_cache.get(preview_key)
        if preview_data is None:
            preview_data = _build_preview_data_url(
                clip_name=clip_name,
                video_path=video_path,
                background_image_path=background_path,
                initial_image_path=initial_image_path,
            )
            previews_cache[preview_key] = preview_data
        preview_data_url, preview_source = preview_data

        scene_summary = str(scene_analysis.get("summary") or "").strip()
        prompt_text = str(assets.get("prompt_text") or "").strip()

        catalog[clip_name] = ClipPresentation(
            recommended_index=recommended_index,
            original_index=_safe_int(entry.get("original_index")),
            beat_index=beat.index if beat is not None else None,
            beat_title=beat.title if beat is not None else None,
            clip_name=clip_name,
            stage_id=stage_id,
            track_index=_safe_int(clip.get("track_index")),
            scene_summary=scene_summary,
            prompt_text=prompt_text,
            preview_data_url=preview_data_url,
            preview_source=preview_source,
            video_path=str(video_path) if video_path is not None else None,
            background_image_path=str(background_path) if background_path is not None else None,
            initial_image_path=str(initial_image_path) if initial_image_path is not None else None,
        )
    return catalog


def _build_preview_data_url(
    *,
    clip_name: str,
    video_path: Path | None,
    background_image_path: Path | None,
    initial_image_path: Path | None,
) -> tuple[str, str]:
    if video_path is not None and video_path.exists():
        frame_image = _extract_video_preview(video_path)
        if frame_image is not None:
            return _image_to_data_url(frame_image), "Preview from mp4"

    for path, label in (
        (background_image_path, "Fallback: bg_image"),
        (initial_image_path, "Fallback: source image"),
    ):
        if path is not None and path.exists():
            with Image.open(path) as image:
                prepared = _prepare_preview_image(image)
            return _image_to_data_url(prepared), label

    return _placeholder_data_url(clip_name), "Placeholder"


def _extract_video_preview(video_path: Path) -> Image.Image | None:
    if cv2 is None:
        return None
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        return None
    try:
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        for target_frame in _candidate_frame_positions(total_frames):
            if target_frame is not None:
                capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            success, frame = capture.read()
            if not success or frame is None:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            return _prepare_preview_image(image)
    finally:
        capture.release()
    return None


def _candidate_frame_positions(total_frames: int) -> list[int | None]:
    if total_frames <= 0:
        return [None]
    return [
        min(total_frames - 1, max(0, int(total_frames * 0.35))),
        min(total_frames - 1, max(0, int(total_frames * 0.55))),
        min(total_frames - 1, max(0, int(total_frames * 0.10))),
        0,
    ]


def _prepare_preview_image(image: Image.Image) -> Image.Image:
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    prepared = ImageOps.exif_transpose(image).convert("RGB")
    return ImageOps.fit(prepared, _PREVIEW_SIZE, method=resampling)


def _image_to_data_url(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=86, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _placeholder_data_url(label: str) -> str:
    image = Image.new("RGB", _PLACEHOLDER_SIZE, color="#ddd1c2")
    draw = ImageDraw.Draw(image)
    safe_label = label[:52] + "…" if len(label) > 52 else label
    draw.rounded_rectangle((50, 50, _PLACEHOLDER_SIZE[0] - 50, _PLACEHOLDER_SIZE[1] - 50), radius=36, fill="#f8f2ea", outline="#b6a495", width=4)
    draw.text((90, 185), "Preview unavailable", fill="#5a4535")
    draw.text((90, 245), safe_label, fill="#6e5d4d")
    return _image_to_data_url(image)


def _resolve_background_image_path(stage_id: str, final_videos_dir: Path | None) -> Path | None:
    if final_videos_dir is None:
        return None
    for suffix in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = final_videos_dir / f"{stage_id}_bg_image_16x9{suffix}"
        if candidate.exists():
            return candidate
    return None


def _resolve_initial_image_path(manifest: dict[str, object]) -> Path | None:
    initial_image = _optional_path(manifest.get("initial_image"))
    if initial_image is not None and initial_image.exists():
        return initial_image

    steps = manifest.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            candidate = _optional_path(step.get("input_image"))
            if candidate is not None and candidate.exists():
                return candidate
    return initial_image


def _parse_group_range(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    match = re.search(r"(\d+)\s*-\s*(\d+)", value)
    if match is None:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _parse_clip_labels(value: str) -> list[str]:
    items: list[str] = []
    for part in value.split(";"):
        cleaned = re.sub(r"^\s*\d+\.\s*", "", part).strip()
        if cleaned:
            items.append(cleaned)
    return items


def _beat_accent(beat_index: int | None) -> str:
    return _BEAT_COLORS.get(beat_index or 0, "#7a6b5a")


def _transition_accent(transition_type: str) -> str:
    return _TRANSITION_BADGE_COLORS.get(transition_type, "#6f655c")


def _truncate_text(value: str, *, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    shortened = compact[: limit - 1].rsplit(" ", 1)[0].strip()
    return f"{shortened}…"


def _optional_path(value: object) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_text_with_fallbacks(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "cp866"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        fixed = _repair_mojibake(text)
        if fixed:
            return fixed
    return raw.decode("utf-8", errors="replace")


def _repair_mojibake(text: str) -> str:
    if re.search(r"[А-Яа-яЁё]", text):
        return text
    suspicious = text.count("Ð") + text.count("Ñ")
    if suspicious < 8:
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if re.search(r"[А-Яа-яЁё]", repaired) else text
