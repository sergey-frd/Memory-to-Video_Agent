# What's New

Краткая история заметных пользовательских изменений. Подробные инструкции находятся в [`docs/`](docs/).

## 2026.08.30.01

- Документированы специализированные Premiere-исполнители TASK_019–025, TASK_028–030 и Alla: сборка timeline, удаления/вставки/замена, SHORT, двойная редакторская доработка, адаптивный Motion и цвет/свет Lumetri.
- Добавлены JSON-примеры TASK_020/021/024/028 и API single-image, PowerShell-примеры проверки/запуска, ограничения фиксированных контрактов и правила backup/проверки сохранённого проекта.
- В `main_full_pipeline_api.py` поиск исходного изображения для Grok batch использует `image_path.parent`; документированы внешний `--image`, `--single-image` и ограничения `--no-submit`.
- Исправлены неполные JSON-примеры Motion; в Motion-шаблоне явно выбран линейный temporal interpolation. Синхронизированы RU/EN Markdown и HTML, карта структуры и матрица параметров.
- Подготовлен выпуск `2026.08.30.01`: очищены временные каталоги и кэши, реальные TASK_026/027 перенесены из `test_runtime` в `TASK_ARCHIVE`. Публичный bundle исключает TASK-артефакты, временные папки, пары input/output и вложенный старый `source/`; добавлена проверка этих исключений.
- Исправлена очистка локальных путей при публикации: она больше не поглощает закрывающие кавычки и аргументы Python-вызовов; добавлен регрессионный тест сохранения синтаксиса.

## 2026.08.26.02

- Добавлен JSON-режим `premiere_sequence_motion_animation`: безопасный Save As, дублирование sequence, frame-exact intrinsic Motion Scale/Position, protected ranges, non-ripple удаление output-аудио и silent review.
- Добавлен комбинированный режим `premiere_sequence_insert_from_sequence_and_motion_animation`: video-only диапазон берётся из другой sequence того же `.prproj`, последующие picture items сдвигаются ровно на длительность вставки, а Motion применяется только к статичным кадрам.
- Frame-exact решения вставки сохраняются в JSON через `resolved_source_range_frames: [IN, OUT_EXCLUSIVE]` и `resolved_destination_frame`; correction source никогда не трактуется как внешний медиафайл.
- Добавлены `run_premiere_sequence_motion.bat`, два reusable JSON-шаблона, dry-run/QA, protected property snapshots, milestone validation и устойчивый ffmpeg renderer для MP4 с нестандартными color metadata.
- Документация, batch history, project structure, parameter matrix и RU/EN user guides синхронизированы; полные примеры находятся в `docs/PREMIERE_JSON_EDIT_AND_MOTION_RU.md`.
- Premiere import/KEEP/Motion regression suite расширен до 74 проходящих тестов.

## 2026.08.26.01

- Import/keep BAT-файлы вызывают отдельный runner `main_premiere_import_keep.py`; trim-review JSON по-прежнему идёт через `main_sequence_trim_review.py`.
- Добавлены переносимые alias: `run_sequence_media_import_standalone.bat`, `run_sequence_keep_apply_standalone.bat`, `run_sequence_import_and_keep_standalone.bat`.
- Media import поддерживает `root_search_paths` и `items[].source_name`, сохраняя строгую проверку неоднозначных совпадений.
- Исправлен выбор проекта-шаблона для пустого `.prproj`: сам пустой проект не используется как donor, при необходимости выбирается подходящий соседний проект.
- Документация, карта запуска, матрица параметров, BAT-примеры и тесты синхронизированы с отдельным import/keep runner.

## 2026.08.20.01

- Добавлен режим `keep_to_new_sequence`: source-sequence копируется в новую sequence внутри того же `.prproj`, KEEP применяется только к копии, исходная sequence не меняется.
- Добавлен режим `import_to_new_sequence`: в существующем Premiere-проекте создаётся новая sequence, туда импортируется список файлов, остальные sequence не трогаются.
- Для обоих режимов отдельные `.prproj` не создаются, пока не задан `output_project_path`. Повторный запуск останавливается, если output-имя уже занято.
- Новые шаблоны: `sequence_keep_to_new_sequence_template.json`, `sequence_media_import_to_new_sequence_template.json`.
- Рабочие конфиги Yotam macro styles: `sequence_media_import_yotam26_macro_styles.json` и `sequence_keep_apply_yotam26_macro_styles.json`.
- В KEEP `operations` файл можно задать через `source_path` (нужно, если одно имя встречается в разных папках); для фото достаточно `duration`.
- `import_to_new_sequence` принимает `items` с `order` и абсолютным `source_path`; если файл не найден, пробуется замена `__`↔`_` и уникальный поиск под ближайшим существующим родителем.

## 2026.08.16.01

- Добавлен режим `apply_keep_ranges`: копия Premiere `.prproj` сохраняет весь проект, а для файлов из KEEP JSON остаются только указанные source-диапазоны. Связанное аудио режется вместе с видео, следующие клипы сдвигаются (`ripple_compact`).
- Для этого режима добавлен launcher `run_sequence_keep_apply.bat`; тот же JSON принимает и `run_sequence_trim_review.bat`.
- KEEP JSON нового формата (`project_path`, `sequence_name`, `operations` / `keep_ranges`) может сам задавать Adobe-проект и sequence. Несколько source-островов одного файла становятся отдельными клипами; диапазон вне текущего In/Out восстанавливается из исходного медиа.
- Добавлен режим `import_media`: список файлов ищется в `root_directory` и добавляется в указанную Premiere sequence (`run_sequence_media_import.bat`).
- Если исходный Premiere-проект пустой (нет клипов на timeline), `import_media` берёт шаблон клипа из `template_project_path` или соседнего `.prproj`.
- `import_media` ищет файлы только по полному имени с расширением; при 0 или >1 совпадении останавливается и сообщает пути.
- В `files` можно указать `{"file": "...", "relative_path": "..."}`, чтобы выбрать конкретный дубль относительно `root_directory`. Пустой `sequence_name` берёт первую sequence кроме `lib`.
- Новый формат импорта: `items` с `order` и абсолютным `source_path` (без `root_directory` и поиска по имени).
- `import_media` создаёт отдельный Premiere `MasterClip` на каждый файл, чтобы превью и идентичность клипа не повторяли шаблон.
- Для каждого нового файла клонируются `VideoStream`/`AudioStream` и обновляются все `RelativePath`, иначе Premiere показывает картинку шаблона при верном имени клипа.
- Добавлен режим `import_and_keep`: импорт списка файлов и keep/очистка выполняются за один проход (`run_sequence_import_and_keep.bat`).
- В KEEP JSON для фото можно задать `duration` вместо `keep_ranges`; если исходный `.prproj` пустой, keep-apply берёт соседний `*_import.prproj`.
- Таблица portrait-стилей `name` / `slug` вынесена в `docs/portrait_styles_tables.md` и сверяется тестом с полными JSON-банками.

## 2026.07.25.01

- Документация собрана в едином каталоге `docs/`.
- Добавлены Hero Definition и hero-aware HIGH/MEDIUM/REVIEW/DROP Sequence Trim Review.
- Добавлен `report_replay`: одна Premiere sequence с четырьмя синхронными видеодорожками без повторных OpenAI-запросов.
- Добавлена матрица `parameter → program → batch → output`.
- Project config теперь принимает `hero_image_dir`, `human_detail_txt` и `reports_dir`, поэтому `config_Alice.json` можно передавать portrait batch через `--delivery-config-file`.
- В художественные portrait-банки добавлен стиль `ILYA_REPIN` и отдельный `chatgpt_ilya_repin_config.json`.
- Основной portrait-банк расширен стилями `SANDRO_BOTTICELLI`, `TOULOUSE_LAUTREC` и `AMEDEO_MODIGLIANI`.
- Добавлен стиль `EDGAR_DEGAS` с коротким slug `deg`.
- Основной portrait-банк расширен стилями `PICASSO_BLUE`, `PICASSO_ROSE`, `JOHANNES_VERMEER`, `CARAVAGGIO`, `AUGUSTE_RODIN`, `MICHELANGELO` и `HENRI_MATISSE`.
- Добавлен `chatgpt_selected_artists_config.json` для запуска подмножества из 10 художественных стилей.
- Основной portrait-банк расширен стилями `VALENTIN_SEROV`, `VIKTOR_VASNETSOV`, `MIKHAIL_VRUBEL` и `ISAAC_LEVITAN`.
- Добавлен `chatgpt_russian_artists_config.json` для запуска подмножества Серов / Васнецов / Врубель / Левитан.

## 2026.07.22.01

- Добавлен Premiere Sequence Trim Review с heuristic и semantic KEEP/DROP-сегментацией.
