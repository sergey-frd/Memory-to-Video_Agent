from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from models.video_sequence import (
    ClipAssetBundle,
    PremiereSequenceClip,
    SequenceCandidate,
    SequenceOptimizationResult,
    SequenceRecommendationEntry,
)
from utils.sequence_structure_report import (
    _SOUNDTRACK_CATEGORY_TITLES,
    _build_profile_context,
    _describe_main_theme,
    _describe_video_core,
    _describe_video_tone,
    _select_soundtrack_references,
)


@dataclass(frozen=True)
class HumanSignalSpec:
    key: str
    label: str
    any_fragments: tuple[str, ...] = ()
    all_groups: tuple[tuple[str, ...], ...] = ()
    music_boost_tags: tuple[str, ...] = ()
    narrative_focus: str = ""
    music_guidance: str = ""


@dataclass(frozen=True)
class HumanProfileOverlay:
    highlight_labels: list[str]
    evidence_snippets: list[str]
    narrative_focus_notes: list[str]
    music_guidance_notes: list[str]
    music_boost_tags: set[str]
    nonvisual_bio_facts: list[str]


_HUMAN_SIGNAL_SPECS = (
    HumanSignalSpec(
        key="adventure_travel",
        label="Герой любит сложные походы, заграничные поездки и активное движение.",
        any_fragments=("поход", "турист", "загранич", "поездк", "путешеств", "активн"),
        music_boost_tags=("travel", "motion", "dynamic", "scenic", "outdoor", "sun"),
        narrative_focus="В финальном описании и подаче полезно усиливать тему движения, маршрута, воздуха и новых мест.",
        music_guidance="По музыке стоит держаться светлого travel-пульса с ощущением пути, воздуха и живого движения.",
    ),
    HumanSignalSpec(
        key="light_pop_music",
        label="Герою нравится легкая популярная музыка.",
        all_groups=(("музык",), ("популяр", "поп"), ("легк",)),
        music_boost_tags=("light", "bright", "groove", "playful", "motion", "celebration"),
        narrative_focus="Текстовое описание лучше делать более живым и современным по тону, без тяжелой академической интонации.",
        music_guidance="Музыкальный поиск лучше смещать к легкому contemporary-pop, indie-pop и мягкому groove, а не к траурной или слишком тяжелой подаче.",
    ),
    HumanSignalSpec(
        key="dance_and_joy",
        label="Герой любит веселиться и хорошо танцует.",
        any_fragments=("весел", "танц"),
        music_boost_tags=("playful", "dynamic", "celebration", "motion", "groove", "bright"),
        narrative_focus="Если в видео есть улыбки, телесная свобода и движение, их стоит подавать как часть живого характера героя.",
        music_guidance="Можно разрешать более заметный ритм, пружину и танцевальную мягкость, если это не спорит с самим видеорядом.",
    ),
    HumanSignalSpec(
        key="supportive_caretaker",
        label="Герой помогает близким, умеет поддерживать и держать напряженные ситуации.",
        any_fragments=("помога", "близк", "напряжен", "выслуш", "психолог", "педагог"),
        music_boost_tags=("warm", "intimate", "family", "tender"),
        narrative_focus="В описании полезно подчеркивать не только красоту кадра, но и теплое поддерживающее присутствие героя рядом с другими.",
        music_guidance="Даже при более живом ритме стоит сохранять теплую, человечную и неагрессивную основу музыки.",
    ),
    HumanSignalSpec(
        key="calm_balanced",
        label="Герой воспринимается как спокойный, добрый и уравновешенный человек.",
        any_fragments=("спокой", "добр", "уравновеш"),
        music_boost_tags=("warm", "gentle", "intimate"),
        narrative_focus="Текст не стоит делать слишком истеричным или конфликтным: образ лучше держать собранным и теплым.",
        music_guidance="Ритм можно усиливать, но не за счет грубой агрессии или тяжелого драматизма.",
    ),
    HumanSignalSpec(
        key="active_body",
        label="Герой ведет спортивный и телесно собранный образ жизни.",
        any_fragments=("йог", "спорт", "гибк", "активн", "фигур"),
        music_boost_tags=("motion", "light", "balanced"),
        narrative_focus="Если в ролике есть походка, жест, осанка и телесная свобода, их полезно держать читаемыми в монтаже.",
        music_guidance="В музыке хорошо работают легкая собранность, ритм шага и ощущение живой физической энергии.",
    ),
)

_NONVISUAL_BIO_FACTS = (
    ("психолог", "работа психологом"),
    ("йог", "йога и телесные практики"),
    ("китайск", "китайская медицина"),
    ("иглоук", "иглоукалывание"),
    ("вегетари", "вегетарианство"),
    ("hr", "работа в HR"),
    ("кадров", "работа с персоналом"),
)


def derive_human_profile_report_path(
    optimization_report_json: Path,
    *,
    output_dir: Path | None = None,
) -> Path:
    report_dir = output_dir or optimization_report_json.parent
    return report_dir / f"{optimization_report_json.stem}_human_profile_report.txt"


def write_human_profile_sequence_report_from_json(
    *,
    optimization_report_json: Path,
    human_detail_txt: Path,
    output_path: Path | None = None,
) -> Path:
    result = load_sequence_optimization_result_from_json(optimization_report_json)
    final_output_path = output_path or derive_human_profile_report_path(optimization_report_json)
    detail_text = human_detail_txt.read_text(encoding="utf-8")
    final_output_path.parent.mkdir(parents=True, exist_ok=True)
    final_output_path.write_text(
        build_human_profile_sequence_report(
            result,
            human_detail_text=detail_text,
            human_detail_path=human_detail_txt,
            optimization_report_json=optimization_report_json,
        ),
        encoding="utf-8",
    )
    return final_output_path


def build_human_profile_sequence_report(
    result: SequenceOptimizationResult,
    *,
    human_detail_text: str,
    human_detail_path: Path | None = None,
    optimization_report_json: Path | None = None,
) -> str:
    overlay = extract_human_profile_overlay(human_detail_text)
    profile_metrics, video_tags, story_mode = _build_profile_context(result.entries)
    merged_music_tags = set(video_tags)
    merged_music_tags.update(overlay.music_boost_tags)

    lines = [
        "ПЕРСОНАЛИЗИРОВАННЫЙ РЕПОРТ ПО ГЕРОЮ И МУЗЫКЕ",
        "",
        f"Источник sequence: {result.selected_sequence_name}",
        f"Источник видео-проекта: {result.source_xml}",
    ]
    if optimization_report_json is not None:
        lines.append(f"Источник video-report JSON: {optimization_report_json}")
    if human_detail_path is not None:
        lines.append(f"Источник human-detail: {human_detail_path}")
    lines.extend(
        [
            "",
            "Как объединять video-only описание и человеческое описание",
            "",
            "Тема, сюжет и фактическая драматургия ролика должны оставаться video-only слоем: они берутся из самих кадров.",
            "Человеческое описание нужно использовать как корректирующий слой для образа героя, тона финального текста и музыкальных предпочтений.",
            "Биографические факты, которых не видно в кадре, лучше добавлять только как дополнительный контекст, а не как прямое утверждение о видеоряде.",
            "",
            "1. Что видно из видео",
            "",
            f"- Основная тема по видео: {_describe_main_theme(video_tags, story_mode, profile_metrics)}",
            f"- Краткое описание по видео: {_describe_video_core(result.entries, video_tags, story_mode, profile_metrics)}",
            f"- Эмоциональный тон по видео: {_describe_video_tone(video_tags, story_mode, profile_metrics)}",
            "",
            "2. Что добавлено человеком",
            "",
        ]
    )

    if overlay.highlight_labels:
        for item in overlay.highlight_labels:
            lines.append(f"- {item}")
    else:
        lines.append("- Явных устойчивых human-сигналов извлечь не удалось, поэтому human-layer лучше вводить вручную.")
    lines.append("")

    if overlay.evidence_snippets:
        lines.append("Опорные human-фразы")
        lines.append("")
        for snippet in overlay.evidence_snippets[:5]:
            lines.append(f"- {snippet}")
        lines.append("")

    lines.extend(
        [
            "3. Новый объединенный репорт",
            "",
            f"Новый портрет героя: {_describe_combined_character_portrait(video_tags, story_mode, overlay)}",
            f"Что стоит подчеркнуть в финальном монтаже и тексте: {_describe_edit_focus(overlay)}",
            f"Как лучше писать итоговое описание: {_describe_description_guidance(video_tags, story_mode, overlay)}",
        ]
    )
    if overlay.nonvisual_bio_facts:
        lines.append(
            "Что не стоит превращать в факт видеоряда без прямого визуального подтверждения: "
            + ", ".join(overlay.nonvisual_bio_facts)
            + "."
        )
    else:
        lines.append(
            "Что не стоит превращать в факт видеоряда без прямого визуального подтверждения: профессии, практики, диетические привычки и другие биографические детали, которых нет в кадре."
        )
    lines.extend(
        [
            "",
            "4. Корректировка музыкальных рекомендаций",
            "",
            f"Video-only музыкальная база: {_describe_video_music_basis(video_tags, story_mode)}",
            f"Human-поправка: {_describe_human_music_overlay(overlay)}",
            f"Итоговый музыкальный вектор: {_describe_combined_music_vector(video_tags, story_mode, overlay)}",
            "",
        ]
    )

    for category_key in ("non_classical", "classical", "jazz"):
        lines.append(_SOUNDTRACK_CATEGORY_TITLES[category_key])
        lines.append("")
        for option in _select_soundtrack_references(category_key, merged_music_tags, story_mode)[:3]:
            lines.append(f"- {option.artist} - {option.title}: {_describe_human_adjusted_soundtrack(option.tags, overlay)}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def extract_human_profile_overlay(detail_text: str) -> HumanProfileOverlay:
    normalized_text = _normalize_text(detail_text)
    snippets = _split_detail_snippets(detail_text)
    highlight_labels: list[str] = []
    evidence_snippets: list[str] = []
    narrative_focus_notes: list[str] = []
    music_guidance_notes: list[str] = []
    music_boost_tags: set[str] = set()

    for spec in _HUMAN_SIGNAL_SPECS:
        if not _matches_signal(normalized_text, spec):
            continue
        highlight_labels.append(spec.label)
        if spec.narrative_focus:
            narrative_focus_notes.append(spec.narrative_focus)
        if spec.music_guidance:
            music_guidance_notes.append(spec.music_guidance)
        music_boost_tags.update(spec.music_boost_tags)
        for snippet in _find_signal_snippets(snippets, spec):
            if snippet not in evidence_snippets:
                evidence_snippets.append(snippet)

    nonvisual_bio_facts: list[str] = []
    for fragment, label in _NONVISUAL_BIO_FACTS:
        if fragment in normalized_text and label not in nonvisual_bio_facts:
            nonvisual_bio_facts.append(label)

    if not evidence_snippets:
        evidence_snippets = snippets[:3]

    return HumanProfileOverlay(
        highlight_labels=highlight_labels,
        evidence_snippets=evidence_snippets,
        narrative_focus_notes=narrative_focus_notes,
        music_guidance_notes=music_guidance_notes,
        music_boost_tags=music_boost_tags,
        nonvisual_bio_facts=nonvisual_bio_facts,
    )


def load_sequence_optimization_result_from_json(report_json_path: Path) -> SequenceOptimizationResult:
    payload = json.loads(report_json_path.read_text(encoding="utf-8"))
    entries = [_load_sequence_entry(item) for item in payload.get("entries") or [] if isinstance(item, dict)]
    return SequenceOptimizationResult(
        source_xml=str(payload.get("source_xml") or ""),
        selected_sequence_name=str(payload.get("selected_sequence_name") or ""),
        engine_requested=str(payload.get("engine_requested") or payload.get("engine_used") or "heuristic"),
        engine_used=str(payload.get("engine_used") or payload.get("engine_requested") or "heuristic"),
        warnings=[str(item) for item in payload.get("warnings") or []],
        entries=entries,
        feature_flags={str(key): bool(value) for key, value in (payload.get("feature_flags") or {}).items()},
        translation_report_path=_optional_str(payload.get("translation_report_path")),
        translation_warnings=[str(item) for item in payload.get("translation_warnings") or []],
    )


def _load_sequence_entry(entry_payload: dict[str, object]) -> SequenceRecommendationEntry:
    candidate_payload = _as_dict(entry_payload.get("candidate"))
    clip_payload = _as_dict(candidate_payload.get("clip"))
    assets_payload = _as_dict(candidate_payload.get("assets"))
    clip = PremiereSequenceClip(
        sequence_name=str(clip_payload.get("sequence_name") or ""),
        order_index=_safe_int(clip_payload.get("order_index")),
        track_index=_safe_int(clip_payload.get("track_index")),
        clipitem_id=str(clip_payload.get("clipitem_id") or ""),
        name=str(clip_payload.get("name") or ""),
        source_path=str(clip_payload.get("source_path") or ""),
        start=_safe_int(clip_payload.get("start")),
        end=_safe_int(clip_payload.get("end")),
        in_point=_safe_int(clip_payload.get("in_point")),
        out_point=_safe_int(clip_payload.get("out_point")),
        duration=_safe_int(clip_payload.get("duration")),
        stage_id=str(clip_payload.get("stage_id") or ""),
        video_index=_safe_int(clip_payload.get("video_index")),
    )
    assets = ClipAssetBundle(
        stage_id=str(assets_payload.get("stage_id") or clip.stage_id),
        bundle_dir=str(assets_payload.get("bundle_dir") or ""),
        manifest_path=_optional_str(assets_payload.get("manifest_path")),
        scene_analysis_path=_optional_str(assets_payload.get("scene_analysis_path")),
        prompt_path=_optional_str(assets_payload.get("prompt_path")),
        manifest=_as_dict(assets_payload.get("manifest")),
        scene_analysis=_as_dict(assets_payload.get("scene_analysis")),
        prompt_text=str(assets_payload.get("prompt_text") or ""),
        missing_files=_string_list(assets_payload.get("missing_files")),
    )
    candidate = SequenceCandidate(
        clip=clip,
        assets=assets,
        keywords=_string_list(candidate_payload.get("keywords")),
        people_count=_safe_int(candidate_payload.get("people_count")),
        shot_scale=_safe_int(candidate_payload.get("shot_scale")),
        energy_level=_safe_int(candidate_payload.get("energy_level")),
        series_subject_tokens=_string_list(candidate_payload.get("series_subject_tokens")),
        series_appearance_tokens=_string_list(candidate_payload.get("series_appearance_tokens")),
        series_pose_tokens=_string_list(candidate_payload.get("series_pose_tokens")),
        main_character_priority=_safe_float(candidate_payload.get("main_character_priority")),
        opening_score=_safe_float(candidate_payload.get("opening_score")),
        main_character_age_hint=_optional_float(candidate_payload.get("main_character_age_hint")),
        main_character_notes=_string_list(candidate_payload.get("main_character_notes")),
        continuity_notes=_string_list(candidate_payload.get("continuity_notes")),
    )
    return SequenceRecommendationEntry(
        recommended_index=_safe_int(entry_payload.get("recommended_index"), default=1),
        original_index=_safe_int(entry_payload.get("original_index"), default=1),
        score=_safe_float(entry_payload.get("score")),
        reason=str(entry_payload.get("reason") or ""),
        candidate=candidate,
    )


def _describe_combined_character_portrait(
    video_tags: set[str],
    story_mode: str,
    overlay: HumanProfileOverlay,
) -> str:
    portrait_bits: list[str] = []
    if "travel" in video_tags or story_mode in {"family_outing", "cultural_travel", "adult_leisure_escape"}:
        portrait_bits.append("в ролике героя лучше воспринимать через движение, прогулки, поездки и смену пространств")
    if overlay.highlight_labels:
        if any("поход" in item.lower() or "поезд" in item.lower() for item in overlay.highlight_labels):
            portrait_bits.append("как человека, которому органично подходят маршрут, активность и новые места")
        if any("музык" in item.lower() or "танц" in item.lower() for item in overlay.highlight_labels):
            portrait_bits.append("с живой, современной и не тяжеловесной внутренней энергией")
        if any("поддерж" in item.lower() or "спокой" in item.lower() for item in overlay.highlight_labels):
            portrait_bits.append("при этом с теплым и поддерживающим присутствием рядом с близкими")
    if not portrait_bits:
        return (
            "В итоговой подаче стоит сохранить video-only тему ролика и лишь мягко добавить человеческий характер героя через тон текста и музыку."
        )
    return " ".join(part.rstrip(".") for part in portrait_bits).strip().capitalize() + "."


def _describe_edit_focus(overlay: HumanProfileOverlay) -> str:
    if overlay.narrative_focus_notes:
        return " ".join(note.rstrip(".") for note in overlay.narrative_focus_notes[:3]).strip() + "."
    return "Human-layer лучше использовать аккуратно: усиливать то, что уже читается в кадре, а не подменять видеоряд биографией."


def _describe_description_guidance(
    video_tags: set[str],
    story_mode: str,
    overlay: HumanProfileOverlay,
) -> str:
    base = "Главную тему и структуру лучше оставить video-only, а human-layer вводить как дополнительный портретный акцент."
    if not overlay.highlight_labels:
        return base
    additions: list[str] = []
    if any("музык" in item.lower() for item in overlay.highlight_labels):
        additions.append("Тон описания можно сделать более живым, современным и легким")
    if any("поход" in item.lower() or "поезд" in item.lower() for item in overlay.highlight_labels):
        additions.append("если в кадре есть движение и дорога, их стоит описывать как близкую герою среду")
    if any("поддерж" in item.lower() or "спокой" in item.lower() for item in overlay.highlight_labels):
        additions.append("образ героя полезно подавать как теплый и удерживающий")
    if not additions:
        return base
    return base + " " + ". ".join(additions) + "."


def _describe_video_music_basis(video_tags: set[str], story_mode: str) -> str:
    basis: list[str] = []
    if "travel" in video_tags or story_mode in {"family_outing", "cultural_travel", "adult_leisure_escape"}:
        basis.append("светлое движение и ощущение маршрута")
    if "group_family" in video_tags or "warm" in video_tags:
        basis.append("теплое человеческое присутствие")
    if "motion" in video_tags or "playful" in video_tags:
        basis.append("мягкий ритмический пульс")
    if "reflective" in video_tags and not basis:
        basis.append("более мягкая и наблюдательная интонация")
    if not basis:
        return "Музыка по одному видеоряду подбирается от общего характера sequence без дополнительной персонализации."
    return "По одному видео музыка просится через " + ", ".join(basis) + "."


def _describe_human_music_overlay(overlay: HumanProfileOverlay) -> str:
    if overlay.music_guidance_notes:
        return " ".join(note.rstrip(".") for note in overlay.music_guidance_notes[:3]).strip() + "."
    return "Дополнительных human-сигналов для музыкальной коррекции не извлечено."


def _describe_combined_music_vector(
    video_tags: set[str],
    story_mode: str,
    overlay: HumanProfileOverlay,
) -> str:
    parts: list[str] = []
    if "travel" in video_tags or story_mode in {"family_outing", "cultural_travel", "adult_leisure_escape"}:
        parts.append("светлый travel / motion вектор")
    if any("музык" in item.lower() for item in overlay.highlight_labels):
        parts.append("легкий contemporary-pop или indie-pop характер")
    if any("танц" in item.lower() or "весел" in item.lower() for item in overlay.highlight_labels):
        parts.append("мягкий groove и живая телесная пружина")
    if any("поддерж" in item.lower() or "спокой" in item.lower() for item in overlay.highlight_labels):
        parts.append("с сохранением теплой и неагрессивной человеческой основы")
    if not parts:
        return "Оставить video-only музыкальный вектор без сильной дополнительной коррекции."
    return "Итоговый поиск музыки лучше вести через " + ", ".join(parts) + "."


def _describe_human_adjusted_soundtrack(option_tags: tuple[str, ...], overlay: HumanProfileOverlay) -> str:
    option_tag_set = set(option_tags)
    reasons: list[str] = []
    if overlay.music_boost_tags & {"travel", "motion", "dynamic", "scenic", "outdoor", "sun"} and option_tag_set & {
        "travel",
        "motion",
        "dynamic",
        "scenic",
        "outdoor",
        "sun",
    }:
        reasons.append("держит ощущение пути, воздуха и движения")
    if overlay.music_boost_tags & {"light", "bright", "groove", "playful", "celebration"} and option_tag_set & {
        "light",
        "bright",
        "groove",
        "playful",
        "celebration",
    }:
        reasons.append("не спорит с тягой героя к легкой и более живой музыке")
    if overlay.music_boost_tags & {"warm", "intimate", "family", "tender", "gentle"} and option_tag_set & {
        "warm",
        "intimate",
        "family",
        "tender",
        "gentle",
    }:
        reasons.append("сохраняет теплое человеческое звучание")
    if not reasons:
        return "подходит как персонализированный музыкальный референс под текущий видео-характер и human-layer"
    return "; ".join(reasons) + "."


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def _split_detail_snippets(text: str) -> list[str]:
    fragments = re.split(r"[\r\n]+|(?<=[.!?])\s+", text)
    snippets = [fragment.strip(" \t-") for fragment in fragments if fragment.strip()]
    return snippets


def _matches_signal(normalized_text: str, spec: HumanSignalSpec) -> bool:
    if spec.any_fragments and any(fragment in normalized_text for fragment in spec.any_fragments):
        return True
    if spec.all_groups and all(any(fragment in normalized_text for fragment in group) for group in spec.all_groups):
        return True
    return False


def _find_signal_snippets(snippets: list[str], spec: HumanSignalSpec) -> list[str]:
    matched: list[str] = []
    for snippet in snippets:
        normalized_snippet = _normalize_text(snippet)
        if _matches_signal(normalized_snippet, spec):
            matched.append(snippet)
    return matched[:2]


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _safe_int(value: object, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
