# Project Documentation

Каноническая документация проекта хранится в этом каталоге.

## Основные руководства

- [USER_GUIDE_RU.md](USER_GUIDE_RU.md) — руководство пользователя на русском.
- [USER_GUIDE_EN.md](USER_GUIDE_EN.md) — English user guide.
- [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) — архитектура и карта подсистем.
- [PARAMETER_PROGRAM_BATCH_MATRIX_RU.md](PARAMETER_PROGRAM_BATCH_MATRIX_RU.md) — параметр → программа → batch → результат, включая import/KEEP и оба Premiere Motion JSON-режима.
- [PREMIERE_JSON_EDIT_AND_MOTION_RU.md](PREMIERE_JSON_EDIT_AND_MOTION_RU.md) — frame-exact модификация Premiere sequence, intrinsic Motion и sequence-range insert с полными JSON-примерами.
- [portrait_styles_tables.md](portrait_styles_tables.md) — таблица `name` / `slug` для полного portrait-банка.

- [PREMIERE_TASK_WORKFLOWS_RU.md](PREMIERE_TASK_WORKFLOWS_RU.md) — специализированные TASK_019–030/Alla, точный CLI, ограничения JSON, backup и QA.
- [JSON-примеры](../source/examples/premiere/) и [PowerShell-примеры](../source/examples/scripts/) — подготовка новых сценариев без запуска записи проекта по умолчанию.

## Эксплуатация

- [BATCH_RUN_HISTORY.md](BATCH_RUN_HISTORY.md) — уникальные примеры batch-запусков.
- [PUBLISHING.md](PUBLISHING.md) — публикация проекта.
- [MINI_LAPTOP_WATERCOLOR.md](MINI_LAPTOP_WATERCOLOR.md) — запуск на mini-laptop.
- [Seedance_2.0_Director.md](Seedance_2.0_Director.md) — требования Seedance.

## Производные документы

- `USER_GUIDE_EN.html`, `USER_GUIDE_RU.html`, `PROJECT_STRUCTURE.html` — HTML-представления канонических Markdown. Обновление: `node tools/render_documentation.mjs` (нужен пакет `marked`; альтернативно `--marked-module <absolute-path-to-marked.esm.js>`). Генератор использует существующий CSS и не обращается к сети.
- `PROJECT_OVERVIEW.md`, `CHANGE_IMPACT.md` — генерируются публикационным pipeline.

В корне репозитория остаются только `README.md` и `CHANGELOG.md`.
