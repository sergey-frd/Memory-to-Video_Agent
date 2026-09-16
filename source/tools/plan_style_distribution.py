"""Build a reviewable style plan from an existing segment plan; no media edits."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--classification", required=True)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    config, base = read(args.config), read(args.plan)
    catalog = {item["media_id"]: item for item in read(args.classification)}
    normalize = lambda path: str(path).replace("/", "\\").casefold()
    excluded = {normalize(path) for path in args.exclude}
    parents = {catalog.get(s["media_id"], {}).get("parent_media_id") for s in base["segments"] if normalize(s["source_path"]) not in excluded}
    kept, removed = [], []
    for segment in base["segments"]:
        mid = segment["media_id"]
        if normalize(segment["source_path"]) in excluded:
            removed.append({"media_id": mid, "source_path": segment["source_path"], "reason": "user_rejected_video"})
            continue
        if mid in parents:
            removed.append({"media_id": mid, "source_path": segment["source_path"], "reason": "original_replaced_by_selected_artwork"})
            continue
        category = "watercolor" if mid.startswith("WC_") else "double_exposure" if mid.startswith("DE_") else "video" if segment["media_type"] == "video" else "original"
        parent = catalog.get(mid, {}).get("parent_media_id")
        kept.append({"media_id": mid, "parent_media_id": parent, "source_path": segment["source_path"], "category": category, "duration_frames": segment["duration_frames"], "description": catalog.get(parent or mid, {}).get("observations", ""), "review": "Review meaningful background and portrait composition before approval" if category == "double_exposure" else "Proposed retention from previous selection"})
    frames = sum(s["duration_frames"] for s in kept)
    counts = Counter(s["category"] for s in kept)
    summary = {category: {"count": count, "seconds": sum(s["duration_frames"] for s in kept if s["category"] == category) / base["fps"], "screen_percent": round(100 * sum(s["duration_frames"] for s in kept if s["category"] == category) / frames, 2)} for category, count in counts.items()}
    result = {"status": "DRAFT_FOR_REVIEW_NOT_APPLIED_TO_PREMIERE", "source_plan": str(Path(args.plan).resolve()), "source_plan_sha256": hashlib.sha256(Path(args.plan).read_bytes()).hexdigest(), "config": config, "exclusions": args.exclude, "fps": base["fps"], "before_count": len(base["segments"]), "before_seconds": sum(s["duration_frames"] for s in base["segments"]) / base["fps"], "after_count": len(kept), "after_seconds": frames / base["fps"], "summary": summary, "segments": kept, "removed": removed, "notes": ["Existing durations retained as a baseline; music and motion timing remain to be agreed.", "No new styles or generation requested at this step.", "Original archive files are preserved; exclusion applies to this proposed edit.", "Existing Premiere project was not modified; source plan is ADDENDUM_02, not a verified readback of later manual edits."]}
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "style_distribution.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Task 36 — черновик распределения стилей", "", "Статус: план для обсуждения; Premiere-проект не изменён.", "", f"Было: {result['before_count']} показов, {result['before_seconds']} с. Предложено: {len(kept)} показов, {result['after_seconds']} с.", "", "| Категория | Количество | Секунды | Доля времени |", "|---|---:|---:|---:|"]
    for category, data in summary.items():
        lines.append(f"| {category} | {data['count']} | {data['seconds']} | {data['screen_percent']}% |")
    lines += ["", "Оригиналы, имеющие выбранный художественный вариант, убраны из показа. Исходные файлы сохраняются. Отклонённый пользователем MP4 исключён из плана. Семь двойных экспозиций требуют отдельного пересмотра фонов; это не утверждение их качества. Другие стили пока не добавлены. Длительности взяты из предыдущего плана и могут измениться после выбора музыки.", "", "Основа: ADDENDUM_02/PLAN.json. Возможные последующие ручные правки Premiere здесь не учтены.", "", "## Покадровый состав", "", "| ID | Стиль | Секунды | Содержание |", "|---|---|---:|---|"]
    lines += [f"| {s['media_id']} | {s['category']} | {s['duration_frames']/base['fps']} | {s['description']} |" for s in kept]
    lines += ["", "## Исключения", ""] + [f"- {s['media_id']}: {s['reason']} — {s['source_path']}" for s in removed]
    (out / "style_distribution.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not any(normalize(s["source_path"]) in excluded for s in kept)
    assert not any(s["media_id"] in parents for s in kept)
    print(json.dumps({"summary": summary, "count": len(kept), "seconds": result["after_seconds"], "removed": len(removed)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
