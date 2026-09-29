"""Prepare an independently assessed ART v2 plan. This tool has NO generation mode."""
import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps
from family_contract import ROOT, read, sha, task_path, verify
from ingest import write_json
from api.openai_image import GEOMETRY_POLICY


def dimensions(path):
    with Image.open(path) as image:
        raw = image.size
        exif = image.getexif().get(274, 1)
        width, height = ImageOps.exif_transpose(image).size
    return dict(raw_width=raw[0], raw_height=raw[1], exif_orientation=exif,
                width=width, height=height, aspect_ratio=width / height,
                orientation='PORTRAIT' if height > width else 'LANDSCAPE' if width > height else 'SQUARE')


def build(task):
    cfg = read(task / 'task.json')
    permanent = Path(cfg['classify']['permanent_project_dir']).resolve()
    old_root = permanent / 'ART'
    target = permanent / 'ART_2'
    if target.resolve() == old_root.resolve() or not old_root.is_dir():
        raise ValueError('Invalid independent ART_2 location')
    manifest = read(old_root / 'art_manifest.json')
    assessment = read(task / 'art_2/assessment.json')
    if manifest['task_id'] != task.name or assessment['task_id'] != task.name:
        raise ValueError('Task identity mismatch')
    lookup = {e['art_id']: e for e in assessment['entries']}
    if len(lookup) != len(assessment['entries']) or set(lookup) != {e['art_id'] for e in manifest['entries']}:
        raise ValueError('Assess every ART ID exactly once')
    entries = []
    for old in manifest['entries']:
        ident = old['art_id']; assessed = lookup[ident]
        source = Path(old['original_source_path']); result = Path(old['permanent_art_result_path'])
        verify({str(source): assessed['source_sha256'], str(result): assessed['old_art_sha256'],
                old['permanent_source_copy_path']: assessed['source_sha256']})
        source_info = dimensions(source); result_info = dimensions(result)
        decision = assessed['decision']
        if source_info['orientation'] == 'LANDSCAPE' and decision != 'SKIP_LANDSCAPE':
            raise ValueError('Landscape regeneration is prohibited: ' + ident)
        if decision == 'REGENERATE_PORTRAIT' and source_info['orientation'] != 'PORTRAIT':
            raise ValueError('Only assessed distorted portraits can be regenerated')
        if decision not in ['SKIP_LANDSCAPE', 'SKIP_ALREADY_CORRECT', 'REGENERATE_PORTRAIT']:
            raise ValueError('Unresolved assessment: ' + ident)
        new_path = target / old['art_type'] / ident / Path(old['relative_result']).name
        if decision == 'REGENERATE_PORTRAIT' and new_path.exists():
            raise FileExistsError('Never overwrite an ART_2 result: ' + str(new_path))
        entries.append(dict(art_id=ident, art_type=old['art_type'], version=2,
            source_path=str(source), source_copy_path=old['permanent_source_copy_path'],
            old_art_path=str(result), new_art_path=str(new_path) if decision == 'REGENERATE_PORTRAIT' else None,
            source_width=source_info['width'], source_height=source_info['height'],
            source_aspect_ratio=source_info['aspect_ratio'], source_orientation=source_info['orientation'],
            source_raw_width=source_info['raw_width'], source_raw_height=source_info['raw_height'],
            source_exif_orientation=source_info['exif_orientation'],
            old_width=result_info['width'], old_height=result_info['height'], old_aspect_ratio=result_info['aspect_ratio'],
            new_width=None, new_height=None, new_aspect_ratio=None, new_sha256=None,
            decision=decision, reason=assessed['reason'], assessment_method=assessed['assessment_method'],
            backend=old['generation_backend'], model=old['model'], prompt=old['prompt'],
            background_concept=old.get('background_concept'), old_request_signature=old['request_signature'],
            source_sha256=assessed['source_sha256'], old_art_sha256=assessed['old_art_sha256'],
            geometry_policy=GEOMETRY_POLICY, preserve_aspect_ratio=True, status='NOT_GENERATED',
            historical_api_response_available=False, historical_upload_available=False))
    candidates = [e for e in entries if e['decision'] == 'REGENERATE_PORTRAIT']
    counts = dict(total_art=len(entries), landscape_skipped=sum(e['decision'] == 'SKIP_LANDSCAPE' for e in entries),
                  portrait_found=sum(e['source_orientation'] == 'PORTRAIT' for e in entries),
                  portrait_already_correct=sum(e['source_orientation'] == 'PORTRAIT' and e['decision'] == 'SKIP_ALREADY_CORRECT' for e in entries),
                  portrait_distorted=len(candidates), watercolor_to_regenerate=sum(e['art_type'] == 'watercolor' for e in candidates),
                  double_exposure_to_regenerate=sum(e['art_type'] == 'double_exposure' for e in candidates),
                  estimated_paid_generations=len(candidates), paid_api_calls=0)
    return dict(schema_version=1, task_id=task.name, version=2,
                status='READY FOR ART_2 GENERATION — USER CONFIRMATION REQUIRED',
                generation_authorized=False, timestamp=datetime.now(timezone.utc).isoformat(),
                target=str(target), old_manifest_sha256=sha(old_root / 'art_manifest.json'),
                assessment_sha256=sha(task / 'art_2/assessment.json'), counts=counts, entries=entries)


def prepare(task, dry_run=False):
    plan = build(task)
    work = task / 'art_2'
    if dry_run:
        saved = read(work / 'ART_2_REGENERATE.json')
        for key in ['entries', 'counts', 'old_manifest_sha256', 'assessment_sha256', 'target']:
            if saved[key] != plan[key]:
                raise ValueError('Prepared revision changed: ' + key)
        print(json.dumps(dict(status='DRY_RUN_PASS', **plan['counts']), indent=2))
        return
    write_json(work / 'ART_2_REGENERATE.json', plan)
    write_json(work / 'art_manifest.json', plan)
    header = '''# BM26 — ART_2: диагностика без API

Статус: READY FOR ART_2 GENERATION — USER CONFIRMATION REQUIRED. Генерация не начата.

## Подтверждённый дефект кода

`api/openai_image.py` после API выполнял `final.resize((metadata.width, metadata.height))`.
При несовпадающих ratios это анизотропное растяжение всего содержимого. Размер PNG,
совпадающий с исходником, поэтому НЕ доказывает правильную геометрию. GPT backend
не получал явный size, а первоначальный canvas ответа не сохранялся. Его реальные
размеры и коэффициент деформации каждой работы теперь неизвестны. Визуальный отбор
ниже выполнен по фото и PNG, а не по одной разнице aspect ratio.

В preprocessing и `analyze_image` отсутствовал EXIF transpose. W10 имеет EXIF=6:
raw 4000×3000, визуально 3000×4000. Старый reference терял поворот и оставался landscape.
Остальные 19 исходников не требуют EXIF-поворота. Пропорциональное уменьшение reference
само по себе не создаёт систематического сжатия; в v1 возможно только округление до пикселя.

## ORIGINAL → REFERENCE → RESULT → HTML

Просмотрены все 20 пар: contact sheets `diagnostic/pairs_1.jpg` ... `pairs_4.jpg`,
увеличенные пары W01/W05/W10/D04/D08. Для W01/W10/D04/D08 старый preprocessing
воспроизведён без API: `*_reference_v1_reconstructed.png`, параметры в
`diagnostic/reference_reconstruction.json`. Это РЕКОНСТРУКЦИЯ по сохранённому коду,
а не найденный исторический upload. Реальные временные uploads v1 удалялись backend;
raw responses v1 не архивировались. Нельзя отделить точный вклад генератора от
postprocessing для каждой работы. При этом дефектный resize в коде воспроизводим.

В сохранённом ART_REVIEW.html используется `object-fit:contain` с max-width/max-height.
Дефекты кандидатов видны непосредственно на PNG, просмотренных с сохранением ratio:
это не только HTML. Browser rendering старой страницы отдельно не проверялся;
CSS не является обнаруженным источником сжатия. Ни ART_REVIEW.html v1, ни PNG v1 не правились.

## Исправление reusable mechanism

PRESERVE_ASPECT_RATIO=True: EXIF нормализуется до metadata/reference; уменьшение
reference пропорциональное. DALL-E square canvas использует contain/padding.
GPT size=auto. Возвращённый генератором canvas сохраняется без подгонки к source.
Оригинальный ответ, реально отправленный reference и geometry JSON сохраняются отдельно,
включая hashes, размеры, EXIF, prompt и model; task adapter копирует их в permanent storage.
Geometry policy входит в signature новой работы, поэтому v1 не смешивается с v2.
Существующие ART не перезаписываются; найденный raw response требует восстановления,
а не повторной оплаты. Изменение внешнего canvas не является ошибкой само по себе.

## Решения по каждой паре

'''
    table = '| ID | Тип | Source visual / EXIF | ART v1 | Решение | Причина |\n|---|---|---|---|---|---|\n'
    for e in plan['entries']:
        table += f"| {e['art_id']} | {e['art_type']} | {e['source_width']}×{e['source_height']} / {e['source_exif_orientation']} | {e['old_width']}×{e['old_height']} | {e['decision']} | {e['reason']} |\n"
    footer = '\n## Итог\n\n```json\n' + json.dumps(plan['counts'], indent=2) + '\n```\n\n'
    footer += 'ART_2 TARGET: `' + plan['target'] + '`\n\n'
    footer += 'Проверка размеров и полные paths/hashes/prompts — ART_2_REGENERATE.json. New dimensions/hash = null до генерации.\n\n'
    footer += 'Команда проверки без записи и API: `scripts\\prepare_art_revision.bat BM26 --dry-run`.\n\n'
    footer += 'ART v1 / originals / montage не меняются. Следующий запуск генерации — только отдельное подтверждение пользователя. STOP.\n'
    (work / 'ART_2_DIAGNOSTIC.md').write_text(header + table + footer, encoding='utf-8')
    rows = ['<!doctype html><html lang="ru"><meta charset="utf-8"><title>ART_2 diagnostic</title>',
            '<style>body{font:16px system-ui;margin:24px}table{width:100%}td{padding:10px;vertical-align:top;width:33%}img{max-width:100%;max-height:560px;width:auto;height:auto;object-fit:contain}h2{margin-top:40px}</style>',
            '<h1>ART_2 — сравнение до генерации</h1><p>API calls: 0. New ART: absent. Old reference: reconstructed, not original upload.</p>']
    refs = read(work / 'diagnostic/reference_reconstruction.json')
    for e in plan['entries']:
        rows.append(f"<h2>{e['art_id']} — {e['decision']}</h2><p>{html.escape(e['reason'])}</p><table><tr>")
        for label, path in [('EXIF-aware original', e['source_path']), ('v1 reference reconstruction', refs.get(e['art_id'], {}).get('path')), ('Saved ART v1 PNG', e['old_art_path'])]:
            rows.append('<td>' + label + ('<br><a href="'+html.escape(Path(path).as_uri(),quote=True)+'"><img src="'+html.escape(Path(path).as_uri(),quote=True)+'"></a>' if path else '<p>Historical upload unavailable</p>') + '</td>')
        rows.append('</tr></table>')
    (work / 'ART_2_REVIEW.html').write_text('\n'.join(rows) + '</html>', encoding='utf-8')
    print(json.dumps(plan['counts'], indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task'); parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    prepare(task_path(args.task), args.dry_run)
