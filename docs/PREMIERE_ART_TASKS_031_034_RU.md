# ART TASK_031–034: код, конфигурации и QA

Выпуск **2026.08.31.01** сохраняет новые монтажные процедуры, QA-инструменты и native JSX. Это фиксированные художественные контракты фильма SF, **не универсальные редакторы произвольного проекта**. Имена клипов, ID, монтажные окна и творческие решения остаются частью контрактов. JSON-конфигурация переносит пути, но не сочиняет новый монтаж.

## Общий запуск

`run_premiere_art_task.bat` вызывает `main_premiere_art_task.py` через локальную `.venv`. Без аргументов и при `--help` проекты не меняются. По умолчанию `--stage check` читает вход, проверяет sequence и параметры кадра; TASK_032 также сверяет SHA256. Это **не dry-run**, не полный media-audit и не Desktop open-check.

```powershell
.\run_premiere_art_task.bat --help
.\run_premiere_art_task.bat --task 034 --config .\examples\premiere\art_task_034.local.json --stage check
.\run_premiere_art_task.bat --task 034 --config .\examples\premiere\art_task_034.local.json --stage run --execute
```

Все стадии с записью файлов требуют `--execute`, включая планы и отчёты. Python `-O` запрещён: проверки старых исполнителей используют `assert`. TASK_031/033/034 имеют полный цикл `run`: аудит → план → внутренняя валидация → применение → QA/preview; отдельный CLI dry-run для них не заявляется. Выходные файлы и backup должны отсутствовать, каталоги новых отчётов должны быть пустыми.

| Task | Исполнитель | Профиль в `examples/premiere/` | Назначение |
| --- | --- | --- | --- |
| 031 | `main_premiere_task_031_art_final.py` | `art_task_031.example.json` | Новый монтаж, аудит/JSON, движение и цвет |
| 032 | `tools/task032_pipeline.py`, `tools/task032_preflight.py` | `art_task_032.example.json` | Plan/dry-run/apply, сильное движение, поклипный цвет и бронзовый фон |
| 033 | `main_premiere_task_033_fit_pulse_fill.py` | `art_task_033.example.json` | Цветовая версия и отдельная fit/fill pulse-версия |
| 034 | `main_premiere_task_034_single_soft_impulse.py` | `art_task_034.example.json` | Мягкий направленный импульс, монотонность, сохранение цвета/аудио |

Скопируйте пример в `*.local.json` **в том же каталоге**. Локальные профили исключены из Git и публичной публикации. `schema_version` равен 1; `task` — строка `031`/`032`/`033`/`034`. `settings` должен содержать все ключи своего примера, без лишних или пустых полей. Относительные пути считаются от JSON; поддерживаются абсолютные пути и переменные среды Windows.

| Ключи `settings` | Значение |
| --- | --- |
| `SOURCE_PROJECT` / `SOURCE` | Точный исходный проект |
| `SOURCE_SEQUENCE` / `NAME` | Исходная sequence |
| `OUTPUT_PROJECT` / `DEST` | Новый файл проекта |
| `OUTPUT_SEQUENCE`, `TARGET`, `COLOR_SEQUENCE`, `FINAL_SEQUENCE`, `BG` | Новые sequence согласно контракту |
| `BACKUP_PROJECT` / `BACKUP`, `CHECKPOINT_PROJECT` / `CHECKPOINT` | Backup и checkpoint; TASK_034 именует backup рядом с входом |
| `PREVIEW_PATH`, `PREVIEW`, `COLOR_PREVIEW`, `FINAL_PREVIEW`, `COMPARISON_PREVIEW`, `COMPARISON` | Выходные видео |
| `REPO_TASK_DIR`, `REPO_DIR`, `OUT` | Рабочий каталог JSON, аудита, QA и native-скриптов |
| `LOCAL_TASK_DIR`, `LOCAL_DIR`, `REPORT_DIR` | Каталог доставки отчётов |
| `BAD_REF_SEQUENCE`, `OLD_PREVIEW`, `TASK033_PLAN` | Предыдущая pulse-версия, preview и план TASK_033 для сравнения TASK_034 |
| `SHA` | SHA256 точного входа TASK_032; не менять для обхода несоответствия проекта |

Полный набор ключей определяется примером конкретной задачи. Вход, backup и выход не могут указывать на один файл.

## TASK_032: обязательные этапы

```powershell
.\run_premiere_art_task.bat --task 032 --config .\examples\premiere\art_task_032.local.json --stage preflight --execute
.\run_premiere_art_task.bat --task 032 --config .\examples\premiere\art_task_032.local.json --stage prepare-native --execute
```

`preflight` пишет структурный аудит без сохранения `.prproj`. `prepare-native` копирует `premiere_scripts/task032/*.jsx` в `OUT`, создаёт `TASK_032_RUNTIME.json` и не заменяет отличающиеся существующие файлы. Premiere не запускается автоматически.

Подготовьте export preset из установленного Adobe H.264 `.epr`:

```powershell
.\.venv\Scripts\python.exe .\tools\task032_make_preset.py --config .\examples\premiere\art_task_032.local.json --preset "ПОЛНЫЙ_ПУТЬ_К_ADOBE_H264.epr" --execute
```

Откройте исходный проект и sequence в Premiere. Через установленную ExtendScript/CEP-среду выполните подготовленные `task032_probe_effects.jsx` и `task032_source_audit.jsx`. Первый проверяет наличие эффектов, второй читает sequence и экспортирует исходник без сохранения проекта. Скрипты используют собственный каталог. Native API/QE зависят от версии Premiere.

После нового просмотра исходника и аудита:

```powershell
.\run_premiere_art_task.bat --task 032 --config .\examples\premiere\art_task_032.local.json --stage plan --execute
.\run_premiere_art_task.bat --task 032 --config .\examples\premiere\art_task_032.local.json --stage dry-run --execute
.\run_premiere_art_task.bat --task 032 --config .\examples\premiere\art_task_032.local.json --stage apply --execute
```

`dry-run` проигрывает операции в памяти и пишет отчёт, не создавая `.prproj`. `apply` требует PASS для **точного хеша JSON**, повторяет проверки, создаёт backup/checkpoint и новый проект. Native Tint, export и визуальная приёмка на этом этапе ещё **не завершены**. Выполните `task032_native_finish.jsx` на новом проекте и проведите QA. Эффекты остаются редактируемыми.

## QA и последовательные ревизии

Все Python-помощники безопасно импортируются и поддерживают `--help`; для работы требуют `--config` и `--execute`.

| `tools/` | Назначение |
| --- | --- |
| `task032_contact.py` | Контактные листы media по манифесту |
| `task032_measure_source.py` | Измерения и кадры native-экспорта исходника |
| `task032_final_qa.py` | Декодирование, громкость, кадры движения и структура; `--preview` для staging-MP4 |
| `task032_scopes_compare.py` | Waveform, RGB Parade, Vectorscope; `--scopes-only` без comparison export |
| `task032_revision.py` | Архив R01 и JSON R02 по уже полученным измерениям |
| `task032_revision_validate.py` | Проверка конкретной R02 после native-калибровки |
| `task032_color_safety_revision.py` | Архив R02 и JSON R03 по clipping-измерениям |
| `task032_publish_local.py` | Замена preview после сверки хешей и архивной копии |
| `task032_final_reports.py` | Отчёты фиксированного R03-контракта по JSON, native readback и сведениям о перезапуске Premiere |
| `task032_package.py` | Локальная доставка/ZIP после QA; не загружает в Drive и не подтверждает облачную доставку |

Native `task032_calibrate.jsx` работает на отдельной диагностической копии; `task032_revision_apply.jsx` и `task032_color_safety_apply.jsx` исполняют соответствующие ревизии. `task032_native_qa.jsx` читает `TASK_032_NATIVE_QA_COMMAND.json`: команды `{"action":"start_playback"}` / `{"action":"finish_playback"}` сохраняют readback и позицию воспроизведения. Фактический перезапуск Premiere фиксируется отдельно в `TASK_032_OPEN_CHECK_PROCESS.json`. Исторический PASS нельзя использовать вместо новой проверки.

R02/R03 и художественные пояснения привязаны к исходному SF-контракту, перед повторным применением нужен новый визуальный аудит. Проверка выпуска кода не означает повторного монтажа или Desktop QA на ноутбуке.

Preview TASK_031/033/034 строится FFmpeg и приближённо отображает часть эффектов; приёмка требует native Premiere-export. TASK_032 использует native export для финального QA. LUFS/true peak не заменяют прослушивание.

Личные TASK-каталоги, медиа и рабочие отчёты не включаются в новый исходный выпуск. Одноразовые Drive-загрузчики и диагностические черновики остаются в локальном архиве и не нужны для установки. Исторические материалы в приватной Git-истории не удаляются.

## Release verification

- Clean public-package installation: PASS on Windows/Python 3.14.2; pip check passes.
- Full suite: 421 passed, 10 pre-existing failures, also reproduced at commit 291557c.
- Public installation/ART/documentation checks: 25 passed.
- Syntax: 206 public Python files, 120 JSON files and 7 native JSX scripts.
- Read-only source checks passed for TASK_031/032/033/034; no Premiere output or media was overwritten.
