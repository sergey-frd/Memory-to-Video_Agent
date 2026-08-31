# Premiere: монтажные задания, адаптивный Motion и цвет

Состояние рабочего кода в выпуске `2026.08.30.01`, тег `v2026.08.30.01`.

## Как выбрать исполнитель

Для другого проекта используйте переносимые режимы из
[PREMIERE_JSON_EDIT_AND_MOTION_RU.md](PREMIERE_JSON_EDIT_AND_MOTION_RU.md)
или import/KEEP из [USER_GUIDE_RU.md](USER_GUIDE_RU.md).
Ниже описаны **специализированные исполнители конкретных заданий**. Их имена
файлов не означают поддержку произвольного монтажа: в коде зафиксированы
версии sequence, число кадров, состав клипов, а иногда и абсолютные пути.
Они не зарегистрированы как новые `mode` в `main_premiere_import_keep.py`.

Все диапазоны кадров полуоткрытые: `[IN, OUT_EXCLUSIVE)`. При 25 fps диапазон
`[1351,1426)` содержит 75 кадров, то есть 3 секунды. `source_*` относится
к исходной sequence; `timeline_*` — к выходной. Нельзя подменять эти координаты.

| Исполнитель | Вход и фиксированный контракт | Результат полного запуска |
| --- | --- | --- |
| `main_premiere_timeline_assembly.py` | `--config`; TASK_019 revision 3, файл `SF_26_BD_1.prproj`, 25 fps; шесть KEEP, family montage и Nuri; три source sequences | Новая video-only sequence из nested sequence ranges |
| `main_premiere_sequence_delete_only.py` | `--config`; TASK_020 `A_DELETE_ONLY`; `SF_26_BD_Keep_08`, четыре KEEP | `SF_26_BD_KEEP_DELETE_ONLY_TEST_v01`: 3827 кадров; удалено 1173 из 5000; без Family/Nuri и аудио |
| `main_premiere_sequence_coarse_insert.py` | `--config`; TASK_020 `B_COARSE_FAMILY_NURI_INSERTION`; отдельное разрешение этапа | `SF_26_BD_LONG_FAMILY_NURI_STAGE_B_v01`: 28 клипов, 4754 кадра; 4 KEEP + 23 Family + 1 Nuri; Nuri непосредственно перед K4 |
| `main_premiere_sequence_ripple_delete.py` | `--config`; TASK_021; LONG v02 → v03; пять удалений на исходной v02 в порядке убывания | 3483 → 2876 кадров, удалено 607; без вставок, перестановки и fine trimming |
| `main_premiere_sequence_insert_only.py` | Позиционный `plan`; TASK_022; LONG v03 → v04; три вставки в порядке убывания исходных координат | 2876 → 3071 кадр; добавлено 195 кадров |
| `main_premiere_sequence_replace_only.py` | Позиционный `plan`; TASK_023; LONG v04 → v05; замена на timeline `[2684,2759)` | 3071 кадр без ripple; source `[1325,1400)` заменён на `[1351,1426)` |
| `main_premiere_short_core.py` | Позиционный `spec`; TASK_024; LONG v05 → `SF_26_BD_SHORT_CORE_v01`; диапазоны заданы в Python | 13 сегментов, 881 кадр |
| `main_premiere_short_expansion.py` | Позиционный `spec`; TASK_025; SHORT core → `SF_26_BD_SHORT_76S_v02` | 31 сегмент, 1878 кадров: 13 сохранённых + 18 добавлений на 997 кадров |
| `main_premiere_task_028_dual_refinement.py` | Позиционный `spec`; TASK_028 revision 1; SHORT v03 и LONG v10 | SHORT v04: 26 сегментов, 1878 кадров; LONG v11: 4103 кадра; фон пересобран, существующая музыка сохранена с обработкой финального хвоста LONG |
| `main_premiere_task_029_adaptive_animation.py` | Без JSON-аргумента; `SF_26_BD_1.prproj`; SHORT v05 / LONG v12 | SHORT `v06_TASK029_ANIM` / LONG `v13_TASK029_ANIM`; адаптивный Motion неподвижных изображений и helper sequences |
| `main_premiere_task_030_color_finish.py` | Без JSON-аргумента; **`SF_26_BD_2.prproj`**; SHORT v07 / LONG v13 | Цвет/свет Lumetri: SHORT v08 / LONG v14; отдельные STRONG v09/v15 и EXTREME v10/v16 |
| `main_premiere_alla_first_assembly.py` | Без JSON-аргумента; пути материала, музыки и проектов заданы константами | Копия `Alla26_wedding_v2_skeleton.prproj`, material bank и `ALLA_15_SKELETON_V01`; музыка не зацикливается |
| `main_premiere_alla_client_motion_v02.py` | Без JSON-аргумента; требует уже подготовленную `ALLA_15_SKELETON_V02` | `Alla26_wedding_v3_client_motion.prproj`, `ALLA_15_CLIENT_V02_MOTION`, добавленные фото, Motion и подогнанный фон |

Это не непрерывный автоматический pipeline. Между названными версиями есть
ручные редакторские этапы. Например, первая Alla-сборка создаёт V01, а
client-motion требует V02; TASK_030 использует другой файл проекта и другие
input sequences, чем TASK_029.

## Подготовка и примеры JSON

Примеры находятся в [`examples/premiere/`](../source/examples/premiere/):

- [`task_020_delete_only.example.json`](../source/examples/premiere/task_020_delete_only.example.json) — четыре KEEP и три удаления из регрессионного примера, полный набор входных полей Stage A.
- [`task_021_ripple_delete.example.json`](../source/examples/premiere/task_021_ripple_delete.example.json) — пять исходных диапазонов удаления и соответствующая карта шести KEEP.
- [`task_024_short_core.example.json`](../source/examples/premiere/task_024_short_core.example.json) — минимальный spec; монтажный план остаётся в `_resolved_segments()` исполнителя.
- [`task_028_dual_refinement.example.json`](../source/examples/premiere/task_028_dual_refinement.example.json) — локальная версия имеющейся спецификации без внешних Drive-идентификаторов; имена и редакторские диапазоны сохранены.

Пути `<LOCAL_PATH>` — заполнители. Скопируйте пример в рабочую папку задания,
укажите существующий проект и проверьте все исходные sequence/media. Эти JSON
не подходят для произвольного фильма после одной лишь замены пути.
Не меняйте ожидаемые числа кадров, чтобы обойти проверку неподходящей версии.
У TASK_028 выходные папки создаются **рядом с переданным spec**.

Для TASK_019/020B/022/023/025 нужен полный исходный план соответствующего задания;
универсального шаблона для них сейчас нет. TASK_020B требует
`authorized_by_user: true` только после фактического разрешения этапа B;
разрешение Stage A не означает разрешения Stage B.

## Команды предварительной проверки

Из корня репозитория, после подготовки рабочей копии JSON:

```powershell
python .\main_premiere_sequence_delete_only.py --config .\examples\premiere\task_020_delete_only.example.json --dry-run
python .\main_premiere_sequence_ripple_delete.py --config .\examples\premiere\task_021_ripple_delete.example.json --dry-run
python .\main_premiere_short_core.py .\examples\premiere\task_024_short_core.example.json --dry-run
python .\main_premiere_task_028_dual_refinement.py .\examples\premiere\task_028_dual_refinement.example.json --dry-run
```

Для остальных планов (имена `<LOCAL_PATH>` ниже обозначают ваши существующие файлы):

```powershell
python .\main_premiere_timeline_assembly.py --config "<LOCAL_PATH>" --dry-run
python .\main_premiere_sequence_coarse_insert.py --config "<LOCAL_PATH>" --dry-run
python .\main_premiere_sequence_insert_only.py "<LOCAL_PATH>" --dry-run
python .\main_premiere_sequence_replace_only.py "<LOCAL_PATH>" --dry-run
python .\main_premiere_short_expansion.py "<LOCAL_PATH>" --dry-run
```

Готовый PowerShell-пример с выбором правильного синтаксиса:

```powershell
.\examples\scripts\premiere_task_dry_run.ps1 -Task TASK_021 -Config "<LOCAL_PATH>"
```

Он всегда добавляет `--dry-run`. Проверка читает проект и может создавать или
обновлять JSON/TXT-отчёты, но не сохраняет изменения в `.prproj`.
После проверки плана полный запуск выполняется той же Python-командой без
`--dry-run`. Это уже запись проекта, а не ещё одна проверка.

## Что меняется на диске

- TASK_019–025 и TASK_028 создают новые sequence, сохраняют backup и затем
  **обновляют исходный `.prproj` по указанному пути**. TASK_029/030 также
  записывают свой фиксированный проект после резервного копирования.
- Оба переносимых Motion-режима создают отдельный Save As; Alla-сценарии пишут
  свои фиксированные output-проекты. Не переносите правило Save As на все CLI.
- Перед полным запуском сохраните и закройте проект в Premiere, чтобы открытая
  копия не перезаписала изменения на диске. Не удаляйте backup или output
  sequence для обхода отказа повторного запуска: сначала проверьте состояние.
- Stage A/B, ripple/delete/insert и SHORT core/expansion рассчитаны на video-only.
  TASK_028–030 сохраняют музыку; их preview должен иметь аудио. Alla имеет
  отдельный музыкальный сценарий. `OUTPUT_SILENT` не является общим правилом.

## TASK_029: аудит и адаптивная анимация

```powershell
python .\main_premiere_task_029_adaptive_animation.py --audit-only
```

Аудит проверяет исходные размеры/ориентацию media, Motion baseline и кадрирование;
формирует отчёты в `TASK_029_SERGEY_HIGH_RES_AUDIT_ADAPTIVE_ANIMATION`.
`--audit-only` не изменяет проект, но записывает материалы аудита.
Запуск **без флагов** выполняет анимацию. `--restrengthen` усиливает Motion
существующих output sequences, `--all-stills` выполняет отдельный проход по
всем неподвижным изображениям. Оба режима записывают проект.
`--config` и `--dry-run` здесь отсутствуют; выбирайте только один режим за запуск.

`TASK_029_ANIMATION_MAP_SHORT.json` и `TASK_029_ANIMATION_MAP_LONG.json` —
сгенерированные карты результата, не переносимые входные конфиги.
Видео не должно получать новые Motion-keyframes; порядок, тайминг и музыка
контролируются отдельно. Для preview используются ссылки на локальные
материалы TASK_028; одного `.prproj` недостаточно.

## TASK_030: аудит цвета и варианты обработки

```powershell
python .\main_premiere_task_030_color_finish.py --audit-only
```

Пути проекта, input/output sequences и preview заданы константами. Аудит
записывает JSON-планы и отчёты в `TASK_030_SERGEY_FINAL_COLOR_LIGHT_FINISH`.
В проекте нужен подходящий шаблон Lumetri. Ни `--config`, ни `--dry-run` нет.

| Режим | Действие |
| --- | --- |
| Без флагов | Снова выполняет аудит, читает полученные планы, создаёт базовые COLOR_FINISH sequences и preview |
| `--finalize-only` | Повторно рендерит preview базового прохода, выполняет QA и обновляет отчёты |
| `--strong-pass` | Создаёт отдельные COLOR_STRONG sequences; требует базовые результаты и preview для сравнения |
| `--extreme-pass` | Создаёт отдельный вариант COLOR_EXTREME; требует STRONG sequences и preview |
| `--extreme-finalize` | Рендерит/проверяет preview EXTREME и завершает его отчёты |
| `--extreme-qa-only` | Проверяет имеющиеся preview EXTREME без повторного рендера |

Используйте один флаг за раз: CLI выбирает ветвь по приоритету, а не выполняет
цепочку указанных флагов. `TASK_030_COLOR_LIGHT_PLAN_*.json` — производные планы,
а не аргументы CLI. Ручная правка базового плана перед обычным запуском будет
заменена повторным аудитом.

## Alla: сборка и клиентский Motion

У `main_premiere_alla_first_assembly.py` и
`main_premiere_alla_client_motion_v02.py` нет параметров настройки и dry-run.
Безопасно посмотреть справку:

```powershell
python .\main_premiere_alla_first_assembly.py --help
python .\main_premiere_alla_client_motion_v02.py --help
```

Запуск без `--help` начинает реальную сборку. Сначала проверьте константы
`SOURCE_PROJECT`, `OUTPUT_PROJECT`, `MATERIAL_ROOT`, музыку, наборы фотографий
и требуемые версии sequence. `config_alla_15_humor_api.json` относится к
генерации видео через API и не конфигурирует эти Premiere-скрипты.

## Что считается проверенным результатом

1. После сохранения повторно прочитать именно записанный `.prproj`, проверить
   ссылки ObjectRef/ObjectURef, protected sequences, source IN/OUT, длительности,
   дорожки и уникальность output sequence.
2. Сопоставить фактический `*_TIMELINE_ACTUAL.json` с планом. План сам по себе
   не доказывает, что монтаж сохранён правильно.
3. Проверить preview через FFmpeg/ffprobe: размер, fps, число кадров, наличие
   или отсутствие аудио согласно заданию; проверить стыки и чёрные кадры.
4. Открыть проект в Premiere без repair/conversion dialog и просмотреть весь
   результат со звуком, если он предусмотрен.

Python/FFmpeg preview и структурный PASS не заменяют визуальную проверку в
Premiere, особенно для Motion, Lumetri и вложенных фонов. Файлы `WAITING_*`
фиксируют незавершённые этапы; они не являются признаком DONE или успешной
выгрузки. Эти CLI не выполняют автоматическую загрузку в Google Drive.

## Проверка кода без Premiere и API

```powershell
python -m pytest test/test_parameter_documentation.py test/test_premiere_sequence_motion.py test/test_premiere_sequence_insert_motion.py test/test_premiere_sequence_timeline_assembly.py test/test_premiere_sequence_delete_only.py test/test_premiere_sequence_coarse_insert.py test/test_premiere_sequence_ripple_delete.py
```

Тесты проверяют схемы, арифметику и отдельные операции. Они не подтверждают
актуальность внешних медиа, успешное открытие реального проекта или визуальную
приёмку результатов TASK_028–030/Alla.

## Выпуск 2026.08.31.01: перенос установки и ART

- [Установка той же версии на ноутбуке](INSTALL_ON_NEW_COMPUTER_RU.md).
- [TASK_031–034: конфигурации, native JSX, QA и ограничения](PREMIERE_ART_TASKS_031_034_RU.md).
- `main_premiere_art_task.py` / `utils/premiere_art_runtime.py` — общий безопасный запуск; `premiere_scripts/task032/` — код Adobe без медиа.
- `setup_project.ps1`, `requirements-lock-windows-py314.txt`, `main_verify_installation.py` — установка и проверка окружения.
