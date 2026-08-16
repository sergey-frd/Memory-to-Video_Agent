# What's New

Краткая история заметных пользовательских изменений. Подробные инструкции находятся в [`docs/`](docs/).

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
