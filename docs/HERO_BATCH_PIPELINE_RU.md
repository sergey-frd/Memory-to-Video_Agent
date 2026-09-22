# Общая технология batch-файлов

## Объединённый запуск технологии

Реализованы `tools/run_hero_pipeline.py` и `run_hero_pipeline.bat`.
Они последовательно вызывают существующие исполнители подготовки входа,
классификации, структуры, чернового MP4 и подготовки native Premiere.
Точные пути результатов каждого этапа передаются следующему автоматически.
Не выбирается случайная «последняя папка»: используется результат текущего запуска
либо проверенная запись `pipeline_state.json`.

```bat
run_hero_pipeline.bat config_pipeline_Hero.local.json --check
run_hero_pipeline.bat config_pipeline_Hero.local.json --until structure
run_hero_pipeline.bat config_pipeline_Hero.local.json
run_hero_pipeline.bat config_pipeline_Hero.local.json --until native
```

По умолчанию цепочка заканчивается черновиком 720p. `--until native` дополнительно
создаёт копию проекта и JSX для нативного экспорта 1080p. Сам экспорт выполняется
в Premiere через Run Transition Script; batch не управляет панелью автоматически.
Анимация Task 37 и финальная очистка не включены: анимация ещё не реализована,
а очищать исходники до утверждения видео нельзя.

`--check` читает конфиги и проект, проверяет наличие sequence; API и рендер не запускает.
Это предварительная проверка, не полная проверка всех медиа. Обычный запуск выполняет
AI-запросы и рендер. Лог каждого этапа одновременно виден в консоли и в отдельном
файле. Завершённые этапы записываются с хешами выходных артефактов и пропускаются
при повторной команде. Незавершённая классификация использует свой кэш.
После изменения общей конфигурации, конфига героя или исходного проекта задайте
новый output_root. Одновременный запуск в одну папку блокируется pipeline.lock.
После аварийного завершения ОС удалять lock можно только убедившись, что процесс
остановлен. При ai_enabled=false цепочка останавливается на ручных заготовках;
это не считается завершённой классификацией.

Все автоматические структуры и MP4 остаются черновиками. Для проверки структуры
перед рендером используйте --until structure. Неизменённую цепочку затем можно
продолжить. Изменение готовых артефактов останавливает автоматическое возобновление:
для ручной правки плана используйте отдельный batch следующего этапа с явным путём.


## Explicit edit-plan revision

`run_revise_edit_plan.bat config_revision_Ben26_v2.local.json` creates a separate plan with explicit source IN/OUT (seconds, OUT exclusive). Every existing ID must appear exactly once; `drop: true` removes a clip. Configuration list order defines timeline order. Images use `duration_seconds`. Durations must align to the plan frame rate. Video ranges must stay within the recorded source placement. `source_edit_plan` selects the input; `output_dir` must not exist, preventing accidental replacement. Paths are relative to the configuration file. `revision` labels the version; `schema_version` is 1.

Ben26 v2: 13 clips, 166 seconds instead of 328. Cuts are provisional pacing choices, not verified best reactions. The final material in the actual plan is a photograph. Review `output/Ben26/revisions/v2/review.md` for all source IN/OUT values. The v2 plan has already been generated and media hashes checked with renderer dry-run; the v2 movie has not been rendered.

Render the exact saved plan:

```bat
run_render_structure_draft.bat config_draft_Ben26_v2.local.json
```

Renderer configuration uses `edit_plan` instead of `structure` (mutually exclusive); fps must match. Existing width, height and bitrate parameters still apply. Output is a new timestamp directory under `output/Ben26/draft_video_v2`. No classification or AI structure regeneration is needed. The original plan and Premiere project remain available. To revise again, use a new `output_dir` and update renderer `edit_plan` accordingly. Native Premiere preparation can subsequently consume this same edit_plan; it is a separate operation.


## Ben26 V3 — редакция по тайм-кодам V2

Подготовлена отдельная конфигурация `config_revision_Ben26_v3.local.json`.
Команда создания: `run_revise_edit_plan.bat config_revision_Ben26_v3.local.json`.
План уже создан в `output/Ben26/revisions/v3/edit_plan.json`; повторное создание в существующей папке запрещено.
Рендер: `run_render_structure_draft.bat config_draft_Ben26_v3.local.json`.

Новый режим `timeline_ranges` заменяет `clips`: список `in_seconds`, `out_seconds` задаёт сохраняемые интервалы исходного монтажного плана (OUT не включается). Порядок должен возрастать, пересечения запрещены. Интервалы переводятся в IN/OUT оригинальных файлов, включая разрезание одного исходника. Повторные части сохраняют ID материала и получают отдельный `clip_instance_id`. Это режим для FFmpeg; совместимость разделённых повторных материалов с нативным экспортом отдельно не проверена.

V3 следует таблице пользователя буквально: 124 секунды (2:04), финальная фотография 7 секунд. Из концертного блока исключено 1:47–1:53 V2. Для книги сохранено именно 2:24–2:36: в плане V2 она начинается в 2:23, поэтому первая секунда исключена согласно таблице. Используется монтажная сетка 25 fps, а не округлённый средний fps MP4. Приблизительные диапазоны не означают выполненного визуального выбора лучших реакций. Музыка на выбор сокращений не влияла. Сборка идёт из оригинальных файлов, не путём повторного сжатия V2.


### V3: подготовка нативного 1080p

После одобрения V3 подготовлен `config_native_Ben26_v3.local.json`.
Команда: `run_prepare_native_export.bat config_native_Ben26_v3.local.json`.
Dry-run пройден. Подготовлена отдельная копия проекта и `assemble_export.jsx` в
`output/Ben26/native_export_v3/20260916_200316_fce0c6dd`.
Открыть именно эту копию `.prproj`, затем выполнить JSX из той же папки через Run Transition Script.
Целевая последовательность `B_26_1`, 1920×1080, 25 fps, 3100 кадров (2:04).
План сохранён без изменения диапазонов, включая повторные фрагменты концертного исходника.
Используется ранее успешно сработавший Match Source - High bitrate и Windows fsName для exportAsMediaDirect.
Ожидаемый результат `native_1080p.mp4` в этой же папке. Статус PREPARED_NATIVE_NOT_RUN: нативное выполнение и проверка MP4 ещё требуются. После частичной сборки повторно запускать assemble_export на заполненной последовательности нельзя.


## Анимация утверждённого Ben26 V3

`run_prepare_plan_animation.bat config_animation_Ben26_v3.local.json` готовит копию сохранённого нативного проекта и `animate_export.jsx`. `--dry-run` вторым аргументом проверяет исходный проект и диапазоны без копирования. Перед запуском JSX нужно открыть именно созданную копию. Скрипт проверяет все размещения, применяет редактируемый Motion Scale к фотографиям, считывает значения ключей, сохраняет проект и экспортирует `animated_1080p.mp4`. Видеоклипы, длительности и звук остаются в утверждённом монтаже. Повторное применение к уже анимированному Scale запрещено.

Конфигурация: `source_job` — job.json успешной нативной сборки; `output_root` — каталог новых запусков; `peak_fraction=0.58`, `zoom_fraction=0.14`, `return_fraction=0.18`. Пути относительно конфигурации. Увеличение считается от фактического Scale Premiere. Профиль основан на Task037: приближение до 114% исходного масштаба и возврат 18% пройденного пути. Для воспроизводимости плавная кривая smoothstep записывается покадровыми ключами; это не точная копия Bezier-кривой Task037. Позиция кадра сохранена. В V3 две фотографии, всего 10 секунд; прочее — видео с собственным движением.

Состояние: dry-run пройден, JSX подготовлен. Применение анимации и нативный просмотр/экспорт ожидают выполнения в Premiere. Синтаксическая проверка JSX не заменяет native QA. После экспорта проверить лица у границ кадра, плавность увеличения и сохранность звука. Исходный проект не изменяется.


## Переходы между длинными видео V3

`run_prepare_plan_transitions.bat config_transitions_Ben26_v3.local.json` создаёт копию уже анимированного проекта и `transitions_export.jsx`. Второй аргумент `--dry-run` выполняет проверки без создания проекта. Открыть созданную копию и запустить JSX через Run Transition Script. Результат — `animated_transitions_1080p.mp4` рядом с копией.

Конфигурация: `schema_version=1`, `source_job` — animation_job.json, `output_root` — папка результатов, `minimum_clip_seconds=8` — минимальная длительность ОБОИХ соседних видео, `duration_seconds=0.4` — Cross Dissolve по центру стыка (10 кадров при 25 fps). Длительность должна составлять чётное число кадров. Фото, короткие видео, стыки одного исходника и стыки без подтверждённого запаса исходных кадров пропускаются; причины записаны в transition_plan.json. Используется консервативный запас внутри исходного placement из инвентаризации. Пересборка монтажа и стоп-кадры для нехватки запаса не используются.

Для Ben26 V3 выбраны 2 перехода, 12 стыков пропущены. Анимация сохраняется копированием проекта; повторно не применяется. JSX проверяет проект, монтаж и отсутствие старых переходов, добавляет Cross Dissolve через QE, проверяет число и границы переходов, повторно проверяет монтаж, сохраняет и экспортирует. QE требует проверки в установленном Premiere; подготовка/тесты не означают успешного нативного исполнения. Статус до запуска: PREPARED_NATIVE_NOT_RUN. При ошибке применения экспорт останавливается; не повторять на частично изменённой копии.


## Общая технология с редакциями и остановками

Реализован `run_video_workflow.bat`: начальный pipeline до 720p, повторяемые редакции, утверждение монтажа, нативный экспорт, анимация, переходы и приёмка. Полная инструкция и параметры: [VIDEO_WORKFLOW_FINAL_RU.md](VIDEO_WORKFLOW_FINAL_RU.md). Состояние сохраняется; операции Premiere выполняются через подготовленный JSX с последующим возобновлением сценария.


Добавлен завершающий `closeout` общего workflow: план размещения, проверяемое архивирование, финальные копии и удаление временных рабочих файлов после FINAL_ACCEPTED. Команды и правила хранения — в [итоговой технологии](VIDEO_WORKFLOW_FINAL_RU.md#последний-этап-хранение-и-очистка--closeout). Реальные данные Ben26 ещё не очищались.


## Дополнение: цвет перед финальной приёмкой

В технологию добавлен проект этапа индивидуальной цветокоррекции и общего художественного цвета после монтажа, анимации и переходов. Спецификация: `config_color_design.example.json` (DESIGN_ONLY_NOT_EXECUTABLE). Реализация Lumetri и интеграция утверждения цвета в workflow ещё требуются; текущий `finish` цвет не применяет. Для фильма с обязательным цветовым этапом не запускать финальную приёмку и closeout до проверки цвета. Полное описание: [VIDEO_WORKFLOW_FINAL_RU.md](VIDEO_WORKFLOW_FINAL_RU.md#цвет-индивидуальная-коррекция-и-единый-стиль-фильма).


## Реализация цветовой пробы

Добавлен run_prepare_plan_color.bat: анализ яркости по кадрам, индивидуальные поправки и общий художественный профиль двумя Lumetri. Подключён опциональный color в workflow после transitions; closeout выбирает цветовой финал. Подробности и ограничения — в VIDEO_WORKFLOW_FINAL_RU.md. Для Ben26 подготовлена проба warm_family 35%; нативное применение ещё не выполнено.
