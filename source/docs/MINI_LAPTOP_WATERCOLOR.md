# Минимальный перенос на лэптоп (watercolor)

Текущая версия laptop-пакета: `2026.07.03.01`.

Цель: оставить только необходимое для запуска ChatGPT watercolor batch из `input/` в `output/chatgpt_watercolor_on_paper/`.

## 1) Базовая проверка среды (на "большом" ПК)

Запустите:

```bat
run_laptop_env_snapshot.bat
```

Создастся файл-эталон:

- `env_baseline_chatgpt_watercolor.json`

Скопируйте его вместе с проектом на лэптоп.

## 2) Проверка среды на лэптопе

Запустите:

```bat
run_laptop_env_compare.bat
```

Результат:

- консоль: `OK` или список проблем;
- JSON-отчёт: `env_compare_report_chatgpt_watercolor.json`.

## 3) Минимальный запуск watercolor

Основной запуск:

```bat
run_chatgpt_watercolor_on_paper_existing.bat --delivery-config-file config_Ziggi.json
```

По умолчанию использует:

- вход: `input/`
- style config: `chatgpt_watercolor_on_paper_config.json`
- выход: `output/chatgpt_watercolor_on_paper/`
- delivery mirror: `config_Ziggi.json` -> `<LOCAL_PATH>`

## 3.1) Примеры новых laptop batch-файлов

### A) Watercolor (готовый профиль)

```bat
run_chatgpt_watercolor_on_paper_existing.bat --delivery-config-file config_Ziggi.json
```

Особенности:
- автоматически очищает `api\__pycache__\chatgpt_desktop_v2*.pyc`;
- включает `PYTHONDONTWRITEBYTECODE=1`;
- проверяет/доустанавливает `pywinauto`, `pyperclip`, `pywin32`, `pillow`;
- запускает desktop-режим с `--desktop-new-chat`, `--desktop-clipboard-attach`, `--desktop-no-file-dialog-fallback`, `--desktop-save-context-menu`.

### B) Любой стиль через config-файл

```bat
run_chatgpt_style_batch_existing.bat chatgpt_artistic_photo_portret_config.json --delivery-config-file config_Ziggi.json
```

Можно заменить `chatgpt_artistic_photo_portret_config.json` на любой `chatgpt*_config.json`.

### C) Интерактивное меню стилей

```bat
run_chatgpt_style_menu_existing.bat
```

Сценарий:
- выбираете номер style-config;
- подтверждаете `config_Ziggi.json` (или запускаете без delivery).

### D) Быстрый shortcut для artistic portrait

```bat
run_chatgpt_artistic_photo_portret_existing.bat --delivery-config-file config_Ziggi.json
```

### E) Все стили из base-банка (ALL STYLES)

```bat
run_chatgpt_style_batch_existing.bat chatgpt_all_styles_config.json --delivery-config-file config_Ziggi.json --skip-existing
```

Особенности:
- config `chatgpt_all_styles_config.json` содержит все **21** стиль из
  `chatgpt_portrait_base_config.json` (те же `slug` и промпты);
- для каждого изображения из `input/` batch прогоняет **весь** список
  `portrait_styles` и сохраняет `<имя>_<slug>.png`;
- выход: `output/chatgpt_all_styles/` (отдельно от `output/chatgpt_portraits/`);
- `new_chat_per_job: true` — новый чат на каждую пару «картинка + стиль»;
- чтобы взять подмножество стилей, удалите лишние объекты из массива
  `portrait_styles` в этом файле (или сделайте урезанную копию конфига).

Полный список slug в `chatgpt_all_styles_config.json`:

| Стиль | slug |
| --- | --- |
| rembrandt | `rmb` |
| renaissance | `ren` |
| impressionist | `imp` |
| renoir | `rnr` |
| andrei_rublev | `arb` |
| watercolor | `wc` |
| watercolor_on_paper | `wcp` |
| post_impressionist_van_gogh | `vg` |
| art_nouveau_klimt | `klm` |
| art_deco | `deco` |
| black_and_white_karsh | `bwk` |
| pop_art | `pop` |
| cubist | `cub` |
| picasso_graphic | `pic` |
| poetic_modernism_chagall | `cha` |
| modern_color | `mc` |
| colorize | `clr` |
| face_enlargement | `fen` |
| scene_expansion | `sce` |
| photo_portret | `ppt` |
| artistic_portrait | `artp` |

## 3.2.-6) Что добавлено в версии 2026.07.03.01

- добавлен `chatgpt_all_styles_config.json` — готовый batch-конфиг для прогона
  **всех** стилей base-банка по изображениям из `input/`;
- отдельный каталог результатов `output/chatgpt_all_styles/`, чтобы не
  смешивать с обычным прогоном `chatgpt_portrait_base_config.json`;
- конфиг можно урезать: достаточно удалить ненужные элементы из
  `portrait_styles`, не меняя остальную структуру JSON;
- файл включён в минимальный laptop-bundle (`run_copy_minimal_to_laptop_dir.bat`).

## 3.2.-5) Что исправлено в версии 2026.06.29.05

- **исправлено открытие «Диспетчера закладок» Chrome**: горячая клавиша `Ctrl+Shift+O`
  перехватывалась браузером (а не страницей ChatGPT) и открывала панель закладок —
  она полностью убрана;
- новый чат между кадрами теперь создаётся **кликом по кнопке «Новый чат»** в боковой
  панели ChatGPT (поиск по нескольким типам элементов: Button/ссылка/пункт меню,
  только в левой части окна, с исключением «Новый проект» и т.п.). Без перезагрузки
  страницы — composer остаётся готовым, вставка фото работает;
- если кнопку не удалось найти, в лог пишется диагностика
  `left-side controls (diagnostic for New chat): [...]` — пришлите её, чтобы точно
  настроить распознавание кнопки;
- навигация по адресной строке осталась запасным путём.

Ожидаемые строки в логе:
- `forcing a fresh ChatGPT chat before this job (New chat button)`
- `new-chat control candidate: '...'`
- `started a clean new chat via New chat button`

Если в логе появилась строка `New chat control not found by accessible name` —
скопируйте следующую за ней строку `left-side controls (diagnostic for New chat): ...`.

## 3.2.-4) Что исправлено в версии 2026.06.29.04

- добавлено ожидание готовности страницы ChatGPT (до ~25 c) перед первым кадром,
  чтобы программа не стартовала по ещё не загруженной/пустой странице
  ("ChatGPT не успел открыться").

ВАЖНО: если в логе вы видите строку `forcing a fresh temporary ChatGPT chat before
this job` — значит запускается СТАРАЯ версия. В новой версии должно быть
`forcing a fresh ChatGPT chat before this job (in-app New chat shortcut)`. Проверьте
номер версии в файле `VERSION` (должно быть `2026.06.29.04`).

## 3.2.-3) Что исправлено в версии 2026.06.29.03

Временный чат (`temporary-chat=true`) на этом лэптопе ломал прикрепление фото
(полная перезагрузка + временный чат не принимал вставку изображения). Поэтому
сменён сам механизм изоляции кадров:

- новый чат между кадрами теперь создаётся **встроенной горячей клавишей ChatGPT
  `Ctrl+Shift+O`** (страница перехватывает её и сбрасывает разговор **без
  перезагрузки**). Composer остаётся интерактивным, вставка фото работает, а прошлый
  результат не попадает в следующий кадр;
- перед нажатием клавиши фокус принудительно ставится в поле ввода страницы, чтобы
  сочетание не открыло «Диспетчер закладок» Chrome;
- навигация по адресной строке осталась только как запасной путь (если горячая
  клавиша не дала чистый чат). Временный чат и cache-busting-токен убраны.

Ожидаемые строки в логе:
- `forcing a fresh ChatGPT chat before this job (in-app New chat shortcut)`
- `sending ChatGPT new-chat shortcut Ctrl+Shift+O`
- `started a clean new chat via Ctrl+Shift+O shortcut`

## 3.2.-2) Что исправлено в версии 2026.06.29.02

Версия `2026.06.29.01` ввела временный чат для изоляции кадров, но на медленном
лэптопе полная перезагрузка страницы вызвала два сбоя — устранены:

- **"первое фото не прикрепилось"**: после перезагрузки composer не успевал стать
  интерактивным. Теперь поиск поля ввода повторяется (до 3 раз) и ждёт реальный
  готовый элемент, прежде чем падать на фиксированную область; после навигации
  добавлено доп. время на отрисовку и закрытие приветственного баннера temporary-chat;
- **"Browser address-bar focus could not be verified" (жёсткий стоп)**: фокус адресной
  строки и чтение URL из буфера обмена теперь повторяются несколькими раундами с
  увеличенными паузами, что убирает ложные срабатывания на медленной машине.

## 3.2.-1) Что исправлено в версии 2026.06.29.01

- исправлена контаминация между кадрами ("на входе второго фото оказывался результат
  первого"): переход к новому кадру теперь открывает **временный чат**
  (`https://chatgpt.com/?temporary-chat=true&_n=<токен>`), а не голую главную страницу.
  Голая `chatgpt.com/` заставляла веб-приложение восстанавливать последний разговор с
  предыдущим результатом; временный чат всегда стартует пустым;
- к URL добавляется уникальный токен `_n`, чтобы браузер делал реальную навигацию и
  ChatGPT каждый раз создавал новый пустой чат (без отдачи из кэша/`mweb_fallback`);
- усилена защита: если на поверхности чата всё ещё виден результат предыдущего кадра,
  программа теперь не продолжает молча, а останавливается, чтобы не сохранить чужой
  результат под новым именем.

Ожидаемые строки в логе:
- `forcing a fresh temporary ChatGPT chat before this job`

## 3.2.0) Что исправлено в версии 2026.06.28.03

- исправлен баг "сохраняется иконка вместо картинки": результат теперь сохраняется через
  контекстное меню браузера ("Сохранить картинку как..."), как при ручном сохранении, —
  получается полноразмерное изображение без наложенной надписи "Редактировать";
- batch-файлы переключены с `--desktop-capture-result` на `--desktop-save-context-menu`;
- поиск Save-диалога и пунктов контекстного меню обёрнут в безопасный UIA-запрос
  (`_safe_windows_query`), чтобы не было зависаний `pywinauto` при сканировании окон.

## 3.2) Что исправлено в версии 2026.06.28.01

- стабилизировано сохранение результата: добавлен fallback screen-region capture через `PIL.ImageGrab`;
- добавлена защита от ложного "generation still running" при уже готовом результате;
- улучшена изоляция между кадрами (переход на новый чат и очистка stale-вложений);
- для clipboard-only режима отключён проблемный UIA scan open/save dialogs (убирает зависания в `pywinauto`);
- в batch-скриптах добавлен anti-cache запуск (очистка `__pycache__`) и автоустановка `pillow`.

## 3.2.1) Короткий суффикс имени результата (slug)

Имя файла результата формируется как `<имя_исходника>_<slug>.png`, где `slug` берётся из
поля `slug` стиля в config-файле (а не из имени каталога). Чтобы суффикс был коротким
(2-4 символа), задайте короткий `slug`.

Примеры результата:

- `IMG-20230803-WA0044_wcp.png`  (watercolor_on_paper, slug `wcp`)
- `IMG-20230803-WA0046_artp.png` (artistic_portrait, slug `artp`)

Текущие короткие slug-и:

| Стиль | slug |
| --- | --- |
| watercolor_on_paper | `wcp` |
| watercolor | `wc` |
| artistic_portrait | `artp` |
| photo_portret | `ppt` |
| picasso_graphic | `pic` |
| rembrandt | `rmb` |
| renaissance | `ren` |
| impressionist | `imp` |
| renoir | `rnr` |
| andrei_rublev | `arb` |
| post_impressionist_van_gogh | `vg` |
| art_nouveau_klimt | `klm` |
| art_deco | `deco` |
| black_and_white_karsh | `bwk` |
| pop_art | `pop` |
| cubist | `cub` |
| poetic_modernism_chagall | `cha` |
| modern_color | `mc` |
| colorize | `clr` |
| face_enlargement | `fen` |
| scene_expansion | `sce` |

Чтобы изменить суффикс, отредактируйте поле `slug` нужного стиля в соответствующем
`chatgpt*_config.json`.

## 3.3) Быстрые признаки правильного запуска в логе

Ожидаемые строки:
- `skipping file-dialog scan in clipboard-only mode`
- `skipping open-dialog scan in clipboard-only mode`
- `attaching image via Windows clipboard`
- `activating context menu item: 'Сохранить картинку как...'`
- `save path pasted: ...`
- `result saved: ...\output\chatgpt_watercolor_on_paper\...png`

## 4) Минимальный набор файлов для переноса

- `main_chatgpt_portrait_batch.py`
- `run_chatgpt_watercolor_on_paper_existing.bat`
- `run_laptop_env_snapshot.bat`
- `run_laptop_env_compare.bat`
- `tools/check_laptop_watercolor_env.py`
- `chatgpt_watercolor_on_paper_config.json`
- `chatgpt_all_styles_config.json`
- `chatgpt_portrait_base_config.json`
- `config_Ziggi.json`
- `requirements.txt`
- папка `api/`
- папка `utils/`
- папка `input/` (ваши исходники)

## 5) Автоматический копировщик минимального набора

Один запуск (копирует минимум в `<LOCAL_PATH>` и сразу делает compare):

```bat
run_copy_minimal_to_laptop_dir.bat
```

При необходимости свой путь:

```bat
run_copy_minimal_to_laptop_dir.bat --target "<LOCAL_PATH>"
```
