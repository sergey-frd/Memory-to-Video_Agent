from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
import re
import zlib
from typing import Iterable, Optional
import ctypes
from ctypes import wintypes

try:
    import pyperclip
    from pywinauto import Application, Desktop
    from pywinauto.base_wrapper import BaseWrapper
    from pywinauto.keyboard import send_keys
except ModuleNotFoundError:
    Application = None  # type: ignore[assignment]
    Desktop = None  # type: ignore[assignment]
    BaseWrapper = object  # type: ignore[assignment,misc]
    pyperclip = None  # type: ignore[assignment]
    send_keys = None  # type: ignore[assignment]

try:
    from PIL import ImageGrab
except ModuleNotFoundError:
    ImageGrab = None  # type: ignore[assignment]


WINDOW_TITLE_RE = ".*ChatGPT.*"
ATTACH_BUTTON_PATTERNS = (
    "Add photos and files",
    "Add files",
    "Add photos",
    "Add files and more",
    "Attach",
    "Upload",
    "Open file picker",
    "Добавить",
    "Добавляйте файлы и многое другое",
    "Прикрепить",
    "Загрузить",
)
ATTACH_MENU_PATTERNS = (
    "Photos and files",
    "Upload from computer",
    "Upload file",
    "Upload files",
    "Open file",
    "Open file.",
    "Open files",
    "Access open file",
    "Choose file",
    "Add photos and files",
    "Add files",
    "Фото и файлы",
    "Загрузить с компьютера",
    "Загрузить файл",
    "Выбрать файл",
)
ATTACH_UPLOAD_MENU_PATTERNS = (
    "Photos and files",
    "Upload from computer",
    "Upload file",
    "Upload files",
    "Open file",
    "Open file.",
    "Open files",
    "Access open file",
    "Choose file",
    "Choose from computer",
    "Upload from device",
    "Фото и файлы",
    "Загрузить с компьютера",
    "Загрузить файл",
    "Выбрать файл",
    "Выбрать с компьютера",
)
OPEN_DIALOG_TITLE_RE = ".*(Open|Open File|Открыть|Открытие|Выбор файла).*"
SAVE_DIALOG_TITLE_RE = ".*(Save As|Save Image|Save|Save File|Сохранить|Сохранить как|Сохранение).*"
OPEN_DIALOG_BUTTONS = ("Open", "Открыть")
SAVE_DIALOG_BUTTONS = ("Save", "Сохранить")
SAVE_DIALOG_ACCEPT_BUTTONS = ("Save", "Сохранить", "Open", "Открыть", "OK", "ОК")
SAVE_DIALOG_EXCLUDED_TITLE_PARTS = (
    "google chrome",
    "chatgpt",
    "whatsapp",
    "total commander",
    "faststone",
    "command prompt",
    "powershell",
    "codex",
    "taskbar",
    "program manager",
    "youtube",
    "google search",
)
SEND_BUTTON_PATTERNS = ("Send", "Submit", "Отправить")
VOICE_BUTTON_PATTERNS = (
    "voice",
    "voice mode",
    "microphone",
    "mic",
    "record",
    "recording",
    "dictate",
    "dictation",
    "audio",
    "speech",
    "speak",
    "listen",
    "голос",
    "микроф",
    "запис",
    "диктов",
    "аудио",
    "речь",
    "говор",
    "слуш",
)
GENERATION_RUNNING_PATTERNS = (
    "Stop",
    "Stop generating",
    "Cancel",
    "Останов",
    "Стоп",
    "Отмен",
)
DEFAULT_CHATGPT_TAB_TITLE_RE = (
    ".*(ChatGPT|New chat|Portrait Request|Image Generation Request|"
    "Image Transformation Request|Image Expansion Request|Image Editing Request|"
    "Restoration Request|Photo Restoration).*"
)
CHATGPT_WINDOW_TITLE_MARKERS = (
    "chatgpt",
    "portrait",
    "portrait generation",
    "portrait request",
    "new chat",
    "watercolor portrait",
    "pastel portrait",
    "rembrandt",
    "renaissance",
    "impressionist",
    "klimt",
    "art deco",
    "karsh",
    "pop art",
    "pop-art",
    "cubist",
    "chagall",
    "photo restoration",
    "restoration request",
    "modern color",
    "image editing request",
    "image generation request",
    "image transformation request",
    "image expansion request",
    "image expansion",
    "face enlargement",
    "scene expansion",
)
CHATGPT_WINDOW_EXCLUDED_TITLE_PARTS = (
    "command prompt",
    "cmd.exe",
    "powershell",
    ".bat",
    "total commander",
    "faststone",
    "codex",
    "taskbar",
    "program manager",
)
IMAGE_SAVE_MENU_PATTERNS = (
    "Save image as",
    "Save Image As",
    "Сохранить изображение как",
    "Сохранить картинку как",
    "Сохранить изображение",
    "Сохранить картинку",
)
ATTACHMENT_CONFIRMATION_PATTERNS = (
    "remove attachment",
    "remove image",
    "remove file",
    "delete attachment",
    "delete image",
    "delete file",
    "удалить вложение",
    "удалить изображение",
    "удалить картинку",
    "удалить файл",
)
NEW_CHAT_BUTTON_PATTERNS = (
    "New chat",
    "New Chat",
    "Start new chat",
    "Новый чат",
    "Создать чат",
    "Создать новый чат",
    "Новая беседа",
)
NEW_CHAT_EXCLUDE_PARTS = (
    "проект",
    "project",
    "искать",
    "search",
    "поиск",
    "закрыт",
    "close",
    "боков",
    "sidebar",
    "закреп",
    "pin",
    "библиот",
    "library",
)
BROWSER_LOCATION_TEXT_PREFIXES = (
    "http://",
    "https://",
    "chrome://",
    "edge://",
    "about:",
)


class DesktopAutomationError(RuntimeError):
    pass


CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
ImageSignature = tuple[int, ...]
VK_BACK = 0x08
VK_A = 0x41
VK_C = 0x43
VK_CONTROL = 0x11
VK_DOWN = 0x28
VK_ESCAPE = 0x1B
VK_L = 0x4C
VK_MENU = 0x12
VK_N = 0x4E
VK_O = 0x4F
VK_RETURN = 0x0D
VK_S = 0x53
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _copy_file_to_windows_clipboard(image_path: Path) -> None:
    _copy_files_to_windows_clipboard((image_path,))


def _copy_files_to_windows_clipboard(image_paths: Iterable[Path]) -> None:
    resolved_paths = [str(path.resolve()) for path in image_paths]
    if not resolved_paths:
        raise DesktopAutomationError("No files were provided for clipboard attachment.")
    payload = ("\0".join(resolved_paths) + "\0\0").encode("utf-16le")
    header_size = ctypes.sizeof(DROPFILES)
    total_size = header_size + len(payload)

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, total_size)
    if not handle:
        raise DesktopAutomationError("Could not allocate clipboard memory for file attachment.")

    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise DesktopAutomationError("Could not lock clipboard memory for file attachment.")

    try:
        dropfiles = DROPFILES()
        dropfiles.pFiles = header_size
        dropfiles.pt_x = 0
        dropfiles.pt_y = 0
        dropfiles.fNC = False
        dropfiles.fWide = True
        ctypes.memmove(locked, ctypes.byref(dropfiles), header_size)
        ctypes.memmove(locked + header_size, payload, len(payload))
    finally:
        kernel32.GlobalUnlock(handle)

    opened = False
    for _ in range(10):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.1)
    if not opened:
        kernel32.GlobalFree(handle)
        raise DesktopAutomationError("Could not open Windows clipboard for file attachment.")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_HDROP, handle):
            kernel32.GlobalFree(handle)
            raise DesktopAutomationError("Could not set file attachment data on Windows clipboard.")
        handle = 0
    finally:
        user32.CloseClipboard()


@dataclass
class DesktopAgentConfig:
    image_path: Optional[Path]
    prompt_text: str
    image_paths: Optional[tuple[Path, ...]] = None
    output_path: Optional[Path] = None
    response_text_path: Optional[Path] = None
    executable_path: Optional[Path] = None
    window_title_re: str = WINDOW_TITLE_RE
    browser_tab_title_re: Optional[str] = None
    target_url: Optional[str] = None
    startup_timeout_sec: float = 30.0
    dialog_timeout_sec: float = 15.0
    result_timeout_sec: float = 120.0
    new_chat_timeout_sec: float = 10.0
    post_attach_delay_sec: float = 3.0
    post_paste_delay_sec: float = 0.5
    post_new_chat_delay_sec: float = 1.0
    min_result_wait_sec: float = 90.0
    result_stable_sec: float = 8.0
    open_new_chat_before_run: bool = False
    force_new_chat_navigation: bool = False
    allow_new_chat_navigation: bool = True
    use_active_window: bool = False
    fixed_window_handle: Optional[int] = None
    prefer_single_tab_window: bool = False
    require_single_tab_window: bool = False
    require_new_attachment_preview: bool = False
    attach_via_clipboard: bool = False
    allow_file_dialog_fallback: bool = True
    skip_capture_result: bool = False
    save_result_via_context_menu: bool = False
    click_composer_before_paste: bool = False
    manual_composer_position: Optional[tuple[int, int]] = None
    manual_send_position: Optional[tuple[int, int]] = None
    manual_send_capture_delay_sec: float = 0.0
    manual_send_min_distance_px: int = 50
    mouse_idle_sec: float = 0.0
    mouse_idle_timeout_sec: float = 60.0
    verbose: bool = False
    submit: bool = True


class ChatGPTDesktopAgent:
    def __init__(self, config: DesktopAgentConfig) -> None:
        self.config = config
        self._window: Optional[BaseWrapper] = None
        self._manual_click_position: Optional[tuple[int, int]] = None
        self._manual_send_position: Optional[tuple[int, int]] = None

    def run(self) -> None:
        self._ensure_dependencies()
        self._log("connecting to ChatGPT window")
        self._window = self._launch_or_connect()
        self._log(f"using window: {self._window.window_text()!r}")
        self._validate_expected_window(self._window)
        if self._preserve_manual_composer_focus():
            self._manual_click_position = self.config.manual_composer_position or self._cursor_position()
            self._manual_send_position = self.config.manual_send_position
            self._log(f"remembered manual composer click: {self._manual_click_position}")
            self._log(f"remembered manual send position: {self._manual_send_position}")
            self._log("keeping the manually focused ChatGPT composer")
        else:
            self._window.set_focus()
            self._log("window focused")
        self._activate_browser_tab_if_needed(self._window)
        self._close_chatgpt_overlay_if_needed(self._window)
        self._close_unexpected_file_dialogs_if_needed()
        if self.config.open_new_chat_before_run:
            self._log("opening a new chat")
            self._open_new_chat(self._window)
        self._enforce_clean_composer_surface(self._window)
        source_image_paths = self._source_image_paths()
        if len(source_image_paths) > 1 and self.config.attach_via_clipboard:
            names = ", ".join(path.name for path in source_image_paths)
            self._log(f"attaching {len(source_image_paths)} images as one clipboard batch: {names}")
            self._attach_images_via_clipboard(self._window, source_image_paths)
            self._log(f"{len(source_image_paths)} images attached")
        else:
            for index, image_path in enumerate(source_image_paths, start=1):
                suffix = "" if len(source_image_paths) == 1 else f" ({index}/{len(source_image_paths)})"
                self._log(f"attaching image{suffix}: {image_path.name}")
                self._attach_image(self._window, image_path)
                self._log(f"image attached{suffix}")
        self._log("pasting prompt")
        self._paste_prompt(self._window, self.config.prompt_text)
        self._ensure_prompt_after_paste(self._window, self.config.prompt_text)
        if self.config.submit:
            self._capture_manual_send_position_after_paste()
            self._log("capturing baseline state")
            baseline_signatures = self._collect_submission_baseline_signatures(self._window)
            self._log(f"baseline image candidates: {len(baseline_signatures)}")
            baseline_text = self._collect_visible_text(window=self._window)
            self._log("submitting prompt")
            self._submit_prompt(self._window)
            if self.config.output_path is not None and self.config.save_result_via_context_menu:
                self._log("waiting for result image and saving via browser context menu")
                self._save_result_image_via_context_menu(self._window, baseline_signatures)
                self._log(f"result saved: {self.config.output_path}")
            elif self.config.output_path is not None and not self.config.skip_capture_result:
                self._log("waiting for result image")
                self._save_result_image(self._window, baseline_signatures)
                self._log(f"result saved: {self.config.output_path}")
            elif self.config.output_path is not None:
                self._log("result capture skipped; request submitted")
            if self.config.response_text_path is not None:
                self._save_response_text(self._window, baseline_text)
        elif self.config.response_text_path is not None:
            self._save_text_snapshot(self.config.response_text_path, self._collect_visible_text(window=self._window))
        self._return_focus_to_chatgpt()

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(f"[desktop] {message}", flush=True)

    def _source_image_paths(self) -> tuple[Path, ...]:
        if self.config.image_paths:
            return tuple(self.config.image_paths)
        if self.config.image_path is not None:
            return (self.config.image_path,)
        return ()

    @property
    def selected_window_handle(self) -> Optional[int]:
        if self._window is None:
            return None
        handle = self._window_handle(self._window)
        return handle or None

    @property
    def selected_manual_send_position(self) -> Optional[tuple[int, int]]:
        return self._manual_send_position

    def _preserve_manual_composer_focus(self) -> bool:
        return (
            self.config.use_active_window
            and self.config.attach_via_clipboard
            and not self.config.click_composer_before_paste
        )

    def _capture_manual_send_position_after_paste(self) -> None:
        if not self._preserve_manual_composer_focus():
            return
        if self._manual_send_position is not None:
            return
        delay = max(0.0, self.config.manual_send_capture_delay_sec)
        if delay <= 0:
            return
        print(
            f"Move the mouse over the ACTIVE ChatGPT send arrow now, but do not click. Continuing in {delay:g} seconds...",
            flush=True,
        )
        time.sleep(delay)
        self._manual_send_position = self._cursor_position()
        if not self._send_position_is_valid(self._manual_send_position):
            print(
                "The mouse did not move far enough from the message box point. "
                "Move it over the active ChatGPT send arrow now, but do not click. Continuing in 8 seconds...",
                flush=True,
            )
            time.sleep(8.0)
            self._manual_send_position = self._cursor_position()
        self._log(f"captured active send-arrow point after paste: {self._manual_send_position}")
        if not self._send_position_is_valid(self._manual_send_position):
            self._log(
                "captured send-arrow point is too close to the message-box point; "
                "falling back to estimated send-arrow clicks"
            )
            self._manual_send_position = None

    def _send_position_is_valid(self, position: Optional[tuple[int, int]]) -> bool:
        if position is None or self._manual_click_position is None:
            return False
        dx = position[0] - self._manual_click_position[0]
        dy = position[1] - self._manual_click_position[1]
        distance = (dx * dx + dy * dy) ** 0.5
        return distance >= max(1, self.config.manual_send_min_distance_px)

    def _ensure_dependencies(self) -> None:
        if Application is None or Desktop is None or pyperclip is None or send_keys is None:
            raise DesktopAutomationError(
                "Desktop automation dependencies are missing. Install pywinauto and pyperclip."
            )

    def _launch_or_connect(self) -> BaseWrapper:
        if self.config.executable_path:
            if not self.config.executable_path.exists():
                raise DesktopAutomationError(
                    f"ChatGPT executable was not found: {self.config.executable_path}"
                )
            Application(backend="uia").start(str(self.config.executable_path))
            time.sleep(2.0)

        if self.config.fixed_window_handle:
            return self._window_from_handle(self.config.fixed_window_handle)

        if self.config.use_active_window:
            try:
                window = self._active_window()
            except DesktopAutomationError:
                if self.config.manual_composer_position is None:
                    self._log("could not wrap active window; searching visible ChatGPT window")
                    found = self._find_visible_chatgpt_window()
                    if found is not None:
                        return found
                raise
            if not self._looks_like_chatgpt_content(window):
                title = window.window_text()
                self._log(f"active window is not ChatGPT: {title!r}; searching ChatGPT window")
                if self.config.manual_composer_position is not None:
                    candidates = self._visible_chatgpt_window_candidates()
                    found_titles = [self._window_title(item) for item in candidates]
                    if found_titles:
                        self._log(f"visible ChatGPT window candidates: {found_titles}")
                    if len(candidates) == 1:
                        found = candidates[0]
                        self._log(
                            "reactivating the sole visible ChatGPT generation window "
                            "after the countdown foreground moved elsewhere"
                        )
                        self._ensure_foreground_window(
                            found,
                            "reactivate sole visible ChatGPT generation window after countdown",
                        )
                        return found
                    raise DesktopAutomationError(
                        "The active window after the countdown is not the ChatGPT browser window. "
                        f"Active window title: {title!r}. "
                        "Run again, then click inside the ChatGPT message box during the countdown."
                    )
                found = self._find_visible_chatgpt_window()
                if found is not None:
                    return found
                raise DesktopAutomationError(
                    "Could not find a usable ChatGPT browser window. "
                    f"Active window title: {title!r}."
                )
            if self.config.require_single_tab_window and not self._has_single_visible_browser_tab(window):
                title = self._window_title(window)
                tab_titles = self._visible_tab_titles(window)
                self._log(
                    "active ChatGPT window is not the generation window because "
                    f"it has {len(tab_titles)} visible tab(s): {tab_titles}"
                )
                if self.config.manual_composer_position is not None:
                    raise DesktopAutomationError(
                        "The active ChatGPT window does not match the generation-window rule. "
                        "Open or activate the ChatGPT generation window that has exactly one visible tab, "
                        f"then run again. Active window title: {title!r}. "
                        f"Visible tab titles: {tab_titles!r}."
                    )
                found = self._find_visible_chatgpt_window()
                if found is not None:
                    return found
                raise DesktopAutomationError(
                    "Could not find a ChatGPT generation window with exactly one visible tab. "
                    f"Active window title: {title!r}. Visible tab titles: {tab_titles!r}."
                )
            elif self.config.manual_composer_position is None:
                found = self._find_visible_chatgpt_window()
                if found is not None and self._chatgpt_window_score(found) > self._chatgpt_window_score(window):
                    self._log(
                        "selected a higher-priority ChatGPT window: "
                        f"{self._window_title(found)!r} over {self._window_title(window)!r}"
                    )
                    return found
            return window

        deadline = time.time() + self.config.startup_timeout_sec
        desktop = Desktop(backend="uia")
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                window = self._find_top_level_window(desktop)
                return window
            except Exception as exc:  # pragma: no cover - GUI specific
                last_error = exc
                time.sleep(1.0)
        raise DesktopAutomationError(
            f"Could not find a ChatGPT window matching '{self.config.window_title_re}'."
        ) from last_error

    def _window_from_handle(self, handle: int) -> BaseWrapper:
        if Desktop is None:
            raise DesktopAutomationError("Desktop automation dependencies are missing fixed-window support.")
        try:
            window = Desktop(backend="uia").window(handle=int(handle))
            window.wait("visible ready", timeout=1.0)
        except Exception as exc:
            raise DesktopAutomationError(
                f"Could not reuse the selected ChatGPT window handle: {handle}."
            ) from exc
        return window

    def _active_window(self) -> BaseWrapper:
        if Desktop is None:
            raise DesktopAutomationError("Desktop automation dependencies are missing active-window support.")
        handle = ctypes.windll.user32.GetForegroundWindow()
        if not handle:
            raise DesktopAutomationError("Could not get the active foreground window.")
        for window in Desktop(backend="uia").windows(visible_only=True):
            try:
                if window.handle == handle:
                    return window
            except Exception:
                continue
        raise DesktopAutomationError("Could not wrap the active foreground window for UI automation.")

    def _validate_expected_window(self, window: BaseWrapper) -> None:
        title = self._window_title(window)
        if not self._looks_like_chatgpt_content(window):
            raise DesktopAutomationError(
                "The active window is not the ChatGPT browser window. "
                f"Active window title: {title!r}. "
                "Start the batch again, then activate the ChatGPT window and click the message box during the countdown."
            )
        if self.config.require_single_tab_window and not self._has_single_visible_browser_tab(window):
            tab_titles = self._visible_tab_titles(window)
            raise DesktopAutomationError(
                "The selected ChatGPT window is not the dedicated generation window. "
                "This run requires a Chrome window with exactly one visible tab. "
                f"Selected window title: {title!r}. Visible tab titles: {tab_titles!r}."
            )

    def _window_title(self, window: BaseWrapper) -> str:
        try:
            return window.window_text()
        except Exception:
            return ""

    def _looks_like_chatgpt_window(self, window: BaseWrapper) -> bool:
        if not self._looks_like_chatgpt_content(window):
            return False
        if self.config.require_single_tab_window and not self._has_single_visible_browser_tab(window):
            return False
        return True

    def _looks_like_chatgpt_content(self, window: BaseWrapper) -> bool:
        title = self._window_title(window).casefold()
        if any(part in title for part in CHATGPT_WINDOW_EXCLUDED_TITLE_PARTS):
            return False
        if any(marker in title for marker in CHATGPT_WINDOW_TITLE_MARKERS):
            return True
        tab_re = self.config.browser_tab_title_re or DEFAULT_CHATGPT_TAB_TITLE_RE
        try:
            return self._find_tab(window, tab_re) is not None
        except Exception:
            return False

    def _visible_chatgpt_window_candidates(self) -> list[BaseWrapper]:
        if Desktop is None:
            return []
        candidates: list[BaseWrapper] = []
        for window in Desktop(backend="uia").windows(visible_only=True):
            try:
                if self._looks_like_chatgpt_window(window):
                    candidates.append(window)
            except Exception:
                continue
        return candidates

    def _find_visible_chatgpt_window(self) -> Optional[BaseWrapper]:
        candidates = self._visible_chatgpt_window_candidates()
        if not candidates:
            return None
        candidates.sort(key=lambda item: (self._chatgpt_window_score(item), item.rectangle().width() * item.rectangle().height()))
        self._log(
            "found ChatGPT window candidates: "
            f"{[(self._window_title(item), self._chatgpt_window_score(item), len(self._visible_tab_titles(item))) for item in candidates]}"
        )
        return candidates[-1]

    def _chatgpt_window_score(self, window: BaseWrapper) -> int:
        title = self._window_title(window).casefold()
        score = 0
        if "google chrome" in title:
            score += 10
        if "chatgpt" in title:
            score += 20
        if "new chat" in title:
            score += 60
        if "portrait request" in title:
            score += 40
        if "portrait generation" in title:
            score += 60
        if "watercolor portrait" in title or "pastel portrait" in title:
            score += 80
        if "restoration request" in title or "photo restoration" in title:
            score += 100
        if "modern color" in title:
            score += 100
        if "image editing request" in title:
            score += 100
        if "image generation request" in title:
            score += 100
        if "image transformation request" in title:
            score += 100
        if "image expansion request" in title or "image expansion" in title:
            score += 100
        if "face enlargement" in title or "scene expansion" in title:
            score += 100
        tab_count = len(self._visible_tab_titles(window))
        if tab_count == 1:
            score += 1000 if (self.config.prefer_single_tab_window or self.config.require_single_tab_window) else 25
        elif self.config.require_single_tab_window:
            score -= 1000
        return score

    def _return_focus_to_chatgpt(self) -> None:
        if self._window is None:
            return
        try:
            self._restore_foreground_window(self._window)
            self._log("returned focus to ChatGPT window")
        except Exception as exc:
            self._log(f"could not return focus to ChatGPT window: {exc}")

    def _find_top_level_window(self, desktop) -> BaseWrapper:
        windows = desktop.windows(title_re=self.config.window_title_re, visible_only=True)
        ready_windows = []
        for window in windows:
            try:
                window.wait("visible ready", timeout=0.5)
                ready_windows.append(window)
            except Exception:
                continue
        if not ready_windows:
            raise DesktopAutomationError(
                f"Could not find a visible window matching '{self.config.window_title_re}'."
            )

        titles = [window.window_text() for window in ready_windows]
        self._log(f"candidate windows: {titles}")
        if self.config.require_single_tab_window:
            single_tab_windows = [
                window for window in ready_windows if self._has_single_visible_browser_tab(window)
            ]
            if not single_tab_windows:
                tab_summary = [
                    (self._window_title(window), self._visible_tab_titles(window))
                    for window in ready_windows
                ]
                raise DesktopAutomationError(
                    "No matching Chrome window has exactly one visible tab. "
                    f"Candidates: {tab_summary!r}."
                )
            ready_windows = single_tab_windows
        if self.config.prefer_single_tab_window and len(ready_windows) > 1:
            ready_windows.sort(
                key=lambda item: (
                    self._has_single_visible_browser_tab(item),
                    self._chatgpt_window_score(item),
                    item.rectangle().width() * item.rectangle().height(),
                )
            )
            return ready_windows[-1]
        if len(ready_windows) == 1:
            return ready_windows[0]

        if self.config.browser_tab_title_re:
            title_pattern = re.compile(self.config.browser_tab_title_re)
            for window in ready_windows:
                try:
                    if title_pattern.search(window.window_text()):
                        self._log("selected window by title")
                        return window
                except Exception:
                    continue
            for window in ready_windows:
                try:
                    if self._find_tab(window, self.config.browser_tab_title_re) is not None:
                        self._log("selected window by ChatGPT tab")
                        return window
                except Exception:
                    continue

        raise DesktopAutomationError(
            "More than one matching Chrome window is open. Activate the ChatGPT window and run with "
            "--desktop-active-window, or narrow --desktop-window-title-re."
        )

    def _activate_browser_tab_if_needed(self, window: BaseWrapper) -> None:
        if not self.config.browser_tab_title_re and not self.config.target_url:
            return

        if self.config.browser_tab_title_re:
            self._log(f"looking for browser tab: {self.config.browser_tab_title_re}")
            tab = self._find_tab(window, self.config.browser_tab_title_re)
            if tab is not None:
                self._ensure_foreground_window(window, "activate ChatGPT browser tab")
                tab.click_input()
                time.sleep(1.0)
                self._log("browser tab activated")
                return

        if self.config.target_url:
            self._navigate_to_url(self.config.target_url, window)
            time.sleep(2.0)
            return

        raise DesktopAutomationError(
            f"Could not find a browser tab matching '{self.config.browser_tab_title_re}'."
        )

    def _new_chat_target_url(self) -> str:
        # Plain ChatGPT home. Temporary-chat URLs were tried for isolation but they force
        # a full page reload that blocks image paste on this setup; isolation between jobs
        # is handled by clicking the in-app New chat button instead.
        return self.config.target_url or "https://chatgpt.com/"

    def _find_new_chat_control(self, window: BaseWrapper) -> Optional[BaseWrapper]:
        # The ChatGPT "New chat" control can be a Button, a link, or a menu/list item,
        # so search several control types (not just Button). Restrict to the left part of
        # the window (the sidebar) and exclude lookalikes such as "New project".
        try:
            window_rect = window.rectangle()
            left_limit = window_rect.left + int(window_rect.width() * 0.45)
        except Exception:
            left_limit = None
        candidates: list[tuple[int, int, str, BaseWrapper]] = []
        for control_type in ("Button", "Hyperlink", "MenuItem", "ListItem", "TabItem", "Text"):
            for wrapper in self._safe_descendants(window, control_type=control_type):
                try:
                    if not wrapper.is_visible() or not wrapper.is_enabled():
                        continue
                    title = (wrapper.window_text() or "").strip()
                    name = (getattr(wrapper.element_info, "name", "") or "").strip()
                    searchable = " ".join(part for part in (title, name) if part).casefold()
                    if not searchable:
                        continue
                    if not any(pattern.casefold() in searchable for pattern in NEW_CHAT_BUTTON_PATTERNS):
                        continue
                    if any(bad in searchable for bad in NEW_CHAT_EXCLUDE_PARTS):
                        continue
                    rect = wrapper.rectangle()
                    if left_limit is not None and rect.left > left_limit:
                        continue
                    candidates.append((rect.top, rect.left, searchable, wrapper))
                except Exception:
                    continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        self._log(f"new-chat control candidate: {candidates[0][2]!r}")
        return candidates[0][3]

    def _log_sidebar_controls(self, window: BaseWrapper) -> None:
        if not self.config.verbose:
            return
        try:
            window_rect = window.rectangle()
            left_limit = window_rect.left + int(window_rect.width() * 0.45)
        except Exception:
            left_limit = None
        names: list[str] = []
        for control_type in ("Button", "Hyperlink", "MenuItem", "ListItem", "Text"):
            for wrapper in self._safe_descendants(window, control_type=control_type):
                try:
                    if not wrapper.is_visible():
                        continue
                    title = (wrapper.window_text() or "").strip()
                    name = (getattr(wrapper.element_info, "name", "") or "").strip()
                    label = title or name
                    if not label:
                        continue
                    rect = wrapper.rectangle()
                    if left_limit is not None and rect.left > left_limit:
                        continue
                    names.append(f"{control_type}:{label}")
                except Exception:
                    continue
        self._log(f"left-side controls (diagnostic for New chat): {names[:40]}")

    def _start_new_chat_via_button(self, window: BaseWrapper) -> bool:
        control = self._find_new_chat_control(window)
        if control is None:
            self._log("New chat control not found by accessible name")
            self._log_sidebar_controls(window)
            return False
        try:
            self._ensure_foreground_window(window, "click New chat control")
            if not self._click_wrapper_center(
                control,
                expected_window=window,
                purpose="click New chat control",
            ):
                control.click_input()
        except Exception as exc:
            self._log(f"New chat control click failed: {exc}")
            return False
        if self._wait_for_clean_new_chat_surface(
            window,
            timeout_sec=max(self.config.post_new_chat_delay_sec, 6.0),
        ):
            self._log("started a clean new chat via New chat button")
            return True
        return False

    def _open_new_chat(self, window: BaseWrapper) -> None:
        # Make sure ChatGPT actually finished loading before we try to reset/attach.
        # On a slow laptop the first job can otherwise race a still-blank page and fail
        # immediately. On later jobs the composer is already present and this returns at once.
        if self._wait_for_prompt_input(window, timeout_sec=25.0) is None:
            self._log("ChatGPT prompt input not visible yet; waiting a bit longer for the page to load")
            time.sleep(3.0)
        if self.config.force_new_chat_navigation and self.config.allow_new_chat_navigation:
            # Primary path: click the in-app "New chat" button. This resets the chat
            # client-side (no page reload, composer stays interactive, image paste keeps
            # working) and gives a brand-new empty chat with no prior result to leak.
            self._log("forcing a fresh ChatGPT chat before this job (New chat button)")
            if self._start_new_chat_via_button(window):
                return

            self._log("New chat button did not yield a clean chat; falling back to navigation")
            target_url = self._new_chat_target_url()
            self._navigate_to_url(target_url, window)
            time.sleep(1.6)
            self._close_chatgpt_overlay_if_needed(window)
            if self._wait_for_clean_new_chat_surface(
                window,
                timeout_sec=max(self.config.post_new_chat_delay_sec, 8.0),
            ):
                return
            if self._start_new_chat_via_button(window):
                return
            if self._url_points_to_conversation(window):
                self._log(
                    "forced navigation stayed on a conversation URL; opening a fresh tab for clean chat"
                )
                self._open_new_browser_tab(target_url, window)
                time.sleep(1.6)
                if self._wait_for_clean_new_chat_surface(
                    window,
                    timeout_sec=max(self.config.post_new_chat_delay_sec, 8.0),
                ):
                    return
                if self._start_new_chat_via_button(window):
                    return
            if self._can_continue_with_prompt_surface(window):
                self._log(
                    "could not prove a fully clean chat surface after forced navigation, "
                    "but prompt input is ready and no stale source attachments/results are visible; continuing"
                )
                return
            raise DesktopAutomationError("Could not confirm a clean ChatGPT chat after forced navigation.")

        if self.config.target_url and self.config.allow_new_chat_navigation:
            self._open_new_browser_tab(self.config.target_url, window)
            if self._wait_for_clean_new_chat_surface(
                window,
                timeout_sec=max(self.config.post_new_chat_delay_sec, 4.0),
            ):
                return
            raise DesktopAutomationError("Could not confirm a clean ChatGPT chat after opening a new browser tab.")

        if self._wait_for_clean_new_chat_surface(window, timeout_sec=1.0):
            self._log("selected ChatGPT window already shows a clean empty chat; continuing without New chat click.")
            return

        button = self._find_button(window, NEW_CHAT_BUTTON_PATTERNS)
        if button is not None:
            self._ensure_foreground_window(window, "click New chat button")
            button.click_input()
            if self._wait_for_clean_new_chat_surface(
                window,
                timeout_sec=max(self.config.post_new_chat_delay_sec, 3.0),
            ):
                return
            self._log("New chat control was clicked, but the page did not become a clean empty chat.")
        else:
            self._log("visible New chat control was not found in the selected ChatGPT window.")

        if not self.config.allow_new_chat_navigation:
            raise DesktopAutomationError(
                "Could not open a clean new ChatGPT chat using only the visible New chat control. "
                "Browser-address navigation is disabled for this work-window run."
            )

        self._log("trying verified browser-address navigation to a fresh temporary ChatGPT chat")
        self._navigate_to_url(self._new_chat_target_url(), window)
        time.sleep(0.8)
        if self._url_points_to_conversation(window):
            self._log(
                "browser navigation stayed on a conversation URL; opening a fresh tab for clean chat"
            )
            self._open_new_browser_tab(self._new_chat_target_url(), window)
            time.sleep(0.8)
            if self._url_points_to_conversation(window):
                raise DesktopAutomationError(
                    "Browser keeps returning to a conversation URL (/c/...) even after opening a fresh tab. "
                    "Stopping to avoid reusing prior image context."
                )
        if self._wait_for_clean_new_chat_surface(
            window,
            timeout_sec=max(self.config.post_new_chat_delay_sec, 8.0),
        ):
            return

        raise DesktopAutomationError(
            "Could not open a clean new ChatGPT chat using the visible New chat control "
            "or verified browser-address navigation."
        )

    def _can_continue_with_prompt_surface(self, window: BaseWrapper) -> bool:
        if self._find_prompt_input(window) is None:
            return False
        if self._attachment_remove_control_count(window) != 0:
            return False
        if self._find_result_images(window):
            self._log(
                "refusing to continue without a clean chat: a prior result image is still "
                "visible on the chat surface (would contaminate the next job)"
            )
            return False
        return True

    def _wait_for_clean_new_chat_surface(self, window: BaseWrapper, *, timeout_sec: float) -> bool:
        deadline = time.time() + max(0.5, timeout_sec)
        last_result_count: Optional[int] = None
        last_attachment_count: Optional[int] = None
        while time.time() < deadline:
            prompt_ready = self._find_prompt_input(window) is not None
            result_count = len(self._find_result_images(window))
            attachment_count = self._attachment_remove_control_count(window)
            if prompt_ready and result_count == 0 and attachment_count == 0:
                return True
            if result_count and result_count != last_result_count:
                self._log(
                    "new-chat surface still shows prior result image candidates: "
                    f"{result_count}"
                )
            if attachment_count and attachment_count != last_attachment_count:
                self._log(
                    "new-chat surface still shows prior source attachment controls: "
                    f"{attachment_count}"
                )
            last_result_count = result_count
            last_attachment_count = attachment_count
            time.sleep(0.5)
        return False

    def _enforce_clean_composer_surface(self, window: BaseWrapper) -> None:
        attempts = 4
        for attempt in range(1, attempts + 1):
            removed = self._remove_visible_attachment_controls(window)
            self._clear_prompt_input_text(window)
            time.sleep(0.35)
            attachment_count = self._attachment_remove_control_count(window)
            has_text = self._composer_still_has_text(window)
            if attachment_count == 0:
                if attempt > 1 or removed > 0:
                    self._log(
                        "confirmed clean composer surface after forced cleanup: "
                        f"attachments={attachment_count}, has_text={has_text}"
                    )
                return
            self._log(
                "composer cleanup retry required: "
                f"attempt {attempt}/{attempts}, attachments={attachment_count}, has_text={has_text}"
            )
            self._close_chatgpt_overlay_if_needed(window)
            time.sleep(0.5)
        raise DesktopAutomationError(
            "Could not clear stale attachment controls before the next image job. "
            "Stopping to avoid mixing source images across requests."
        )

    def _clear_prompt_input_text(self, window: BaseWrapper) -> None:
        if not self._focus_prompt_input_or_composer(window):
            return
        try:
            self._press_ctrl_key(window, VK_A)
            time.sleep(0.08)
            self._press_key(window, VK_BACK)
            time.sleep(0.08)
            self._press_key(window, VK_BACK)
        except Exception as exc:
            self._log(f"could not clear prompt input text: {exc}")

    def _remove_visible_attachment_controls(self, window: BaseWrapper) -> int:
        removed = 0
        for _ in range(10):
            candidates: list[tuple[int, int, BaseWrapper]] = []
            for wrapper in self._safe_descendants(window, control_type="Button"):
                try:
                    if not wrapper.is_visible() or not wrapper.is_enabled():
                        continue
                    search_text = self._control_search_text(wrapper).casefold()
                    if not any(pattern.casefold() in search_text for pattern in ATTACHMENT_CONFIRMATION_PATTERNS):
                        continue
                    rect = wrapper.rectangle()
                    candidates.append((rect.bottom, rect.right, wrapper))
                except Exception:
                    continue
            if not candidates:
                break
            candidates.sort()
            target = candidates[-1][2]
            if not self._click_wrapper_center(
                target,
                expected_window=window,
                purpose="remove stale attachment before next job",
            ):
                try:
                    self._ensure_foreground_window(window, "remove stale attachment before next job")
                    target.click_input()
                except Exception:
                    break
            removed += 1
            time.sleep(0.2)
        if removed > 0:
            self._log(f"removed stale attachment controls before next job: {removed}")
        return removed

    def _open_new_browser_tab(self, url: str, window: Optional[BaseWrapper] = None) -> None:
        target_window = window or self._window
        if target_window is not None:
            self._ensure_foreground_window(target_window, "open new browser tab")
            self._assert_foreground_window(target_window, "open new browser tab")
        send_keys("^t")
        time.sleep(0.4)
        self._navigate_to_url(url, target_window)

    def _navigate_to_url(self, url: str, window: Optional[BaseWrapper] = None) -> None:
        target_window = window or self._window
        if not self._focus_browser_address_bar_verified(target_window):
            raise DesktopAutomationError(
                "Browser address-bar focus could not be verified after Ctrl+L. "
                "Stopping before Enter so the key cannot activate page content."
            )
        pyperclip.copy(url)
        if target_window is not None:
            self._assert_foreground_window(target_window, "paste browser URL")
        send_keys("^v")
        time.sleep(0.1)
        if target_window is not None:
            self._assert_foreground_window(target_window, "submit browser URL")
        send_keys("{ENTER}")

    def _focus_browser_address_bar_verified(self, window: Optional[BaseWrapper]) -> bool:
        if window is not None:
            self._ensure_foreground_window(window, "focus browser address bar")
            self._assert_foreground_window(window, "focus browser address bar")

        # The laptop browser can be slow to hand keyboard focus to the address bar right
        # after a full page reload (fresh temporary chat), so retry the whole focus
        # sequence a few times instead of giving up after the first miss.
        for round_index in range(1, 4):
            for attempt_name, action in (
                ("raw Ctrl+L", lambda: self._press_ctrl_key_raw(VK_L)),
                ("pywinauto Ctrl+L", lambda: send_keys("^l")),
            ):
                label = attempt_name if round_index == 1 else f"{attempt_name} (round {round_index})"
                self._log(f"trying browser address-bar focus via {label}")
                action()
                time.sleep(0.35 + 0.25 * round_index)
                if window is not None:
                    self._assert_foreground_window(window, f"verify browser address bar selection after {label}")
                if self._browser_address_selection_is_verified():
                    return True

            if window is not None:
                for x_ratio, y_offset in (
                    (0.46, 50),
                    (0.54, 50),
                    (0.36, 50),
                    (0.46, 64),
                    (0.54, 64),
                ):
                    try:
                        rect = window.rectangle()
                        x = rect.left + int(rect.width() * x_ratio)
                        y = rect.top + y_offset
                        self._log(f"trying browser address-bar focus via toolbar click: x={x}, y={y}")
                        self._click_screen_point(
                            x,
                            y,
                            expected_window=window,
                            purpose="click browser address bar",
                        )
                        self._press_ctrl_key_raw(VK_A)
                        time.sleep(0.15)
                        if self._browser_address_selection_is_verified():
                            return True
                    except Exception as exc:
                        self._log(f"browser address-bar toolbar click focus failed: {exc}")
            time.sleep(0.5)
        return False

    def _browser_address_selection_is_verified(self) -> bool:
        sentinel = "__codex_address_probe__"
        copied = ""
        for read_attempt in range(3):
            try:
                pyperclip.copy(sentinel)
                self._press_ctrl_key_raw(VK_C)
                time.sleep(0.25 + 0.15 * read_attempt)
                copied = (pyperclip.paste() or "").strip()
            except Exception as exc:
                self._log(f"browser address-bar verification failed: {exc}")
                copied = ""
            if copied and copied != sentinel:
                break
        if not copied or copied == sentinel:
            self._log("browser address-bar verification copied no URL text")
            return False
        copied_cf = copied.casefold()
        verified = copied_cf.startswith(BROWSER_LOCATION_TEXT_PREFIXES)
        if verified:
            self._log(f"browser address-bar verification copied URL text: {copied[:120]!r}")
        else:
            self._log(f"browser address-bar verification copied unexpected text: {copied[:120]!r}")
        return verified

    def _read_browser_address_bar_text(self, window: Optional[BaseWrapper]) -> str:
        if window is not None:
            self._ensure_foreground_window(window, "read browser address bar")
            self._assert_foreground_window(window, "read browser address bar")
        sentinel = "__codex_address_probe_read__"
        try:
            pyperclip.copy(sentinel)
            self._press_ctrl_key_raw(VK_L)
            time.sleep(0.1)
            self._press_ctrl_key_raw(VK_C)
            time.sleep(0.15)
            copied = (pyperclip.paste() or "").strip()
        except Exception:
            return ""
        if not copied or copied == sentinel:
            return ""
        return copied

    def _url_points_to_conversation(self, window: Optional[BaseWrapper]) -> bool:
        copied_cf = self._read_browser_address_bar_text(window).casefold()
        if not copied_cf:
            return False
        return "/c/" in copied_cf or "chatgpt.com/c/" in copied_cf

    def _attach_image(self, window: BaseWrapper, image_path: Path) -> None:
        baseline_attachment_signatures = self._result_signatures(
            self._find_attachment_image_candidates(window)
        )
        baseline_attachment_text = self._collect_attachment_surface_text(window)
        if self.config.attach_via_clipboard:
            self._log("attaching image via Windows clipboard")
            last_error: Optional[DesktopAutomationError] = None
            max_attempts = (
                1
                if self.config.require_new_attachment_preview
                else 2 if self.config.force_new_chat_navigation else 1
            )
            for attempt in range(1, max_attempts + 1):
                self._paste_file_clipboard(window, image_path)
                time.sleep(self.config.post_attach_delay_sec)
                try:
                    self._wait_for_attached_source_image(
                        window,
                        image_path,
                        baseline_attachment_signatures,
                        baseline_attachment_text,
                    )
                    return
                except DesktopAutomationError as exc:
                    last_error = exc
                    if attempt >= max_attempts:
                        break
                    self._log(
                        "source image attachment was not confirmed after clipboard paste; "
                        "refocusing composer and retrying once"
                    )
                    time.sleep(1.5)
            if last_error is not None:
                if not self.config.allow_file_dialog_fallback:
                    raise last_error
                self._log(
                    "source image attachment was not confirmed after clipboard paste; "
                    "trying ChatGPT attach button/file dialog fallback"
                )
                try:
                    self._attach_image_via_file_dialog(
                        window,
                        image_path,
                        baseline_attachment_signatures,
                        baseline_attachment_text,
                    )
                    return
                except DesktopAutomationError as dialog_error:
                    raise DesktopAutomationError(
                        f"{last_error} File-dialog fallback also failed: {dialog_error}"
                    ) from dialog_error
            return

        self._attach_image_via_file_dialog(
            window,
            image_path,
            baseline_attachment_signatures,
            baseline_attachment_text,
        )

    def _attach_image_via_file_dialog(
        self,
        window: BaseWrapper,
        image_path: Path,
        baseline_attachment_signatures: list[ImageSignature],
        baseline_attachment_text: str,
    ) -> None:
        attach_button = self._wait_for_attach_button(window)
        if attach_button is None:
            raise DesktopAutomationError(
                "Could not find the attach/upload button in the ChatGPT desktop window."
            )
        self._ensure_foreground_window(window, "click ChatGPT attach button")
        if not self._click_wrapper_center(
            attach_button,
            expected_window=window,
            purpose="click ChatGPT attach button",
        ):
            attach_button.click_input()

        dialog = self._wait_for_dialog_or_attach_menu(window)
        self._fill_open_dialog(dialog, image_path)
        time.sleep(self.config.post_attach_delay_sec)
        self._wait_for_attached_source_image(
            window,
            image_path,
            baseline_attachment_signatures,
            baseline_attachment_text,
        )

    def _attach_images_via_clipboard(self, window: BaseWrapper, image_paths: tuple[Path, ...]) -> None:
        baseline_attachment_signatures = self._result_signatures(
            self._find_attachment_image_candidates(window)
        )
        baseline_attachment_text = self._collect_attachment_surface_text(window)
        self._log("attaching image pair via one Windows clipboard payload")
        self._paste_files_clipboard(window, image_paths)
        time.sleep(max(self.config.post_attach_delay_sec, 5.0))
        self._wait_for_attached_source_images(
            window,
            image_paths,
            baseline_attachment_signatures,
            baseline_attachment_text,
        )

    def _wait_for_attached_source_image(
        self,
        window: BaseWrapper,
        image_path: Path,
        baseline_signatures: list[ImageSignature],
        baseline_surface_text: str,
    ) -> None:
        timeout_sec = max(45.0, self.config.post_attach_delay_sec + 30.0)
        deadline = time.time() + timeout_sec
        baseline_set = set(baseline_signatures)
        baseline_digests = {
            digest
            for digest in (self._result_signature_digest(signature) for signature in baseline_signatures)
            if digest
        }
        baseline_remove_controls = self._attachment_remove_control_count_from_text(
            baseline_surface_text
        )
        baseline_text_cf = baseline_surface_text.casefold()
        last_log_at = 0.0
        first_seen_remove_control_at: Optional[float] = None
        first_seen_preview_at: dict[ImageSignature, float] = {}
        preview_stable_sec = 3.0 if self.config.require_new_attachment_preview else 0.0

        while time.time() < deadline:
            now = time.time()
            attachment_text = self._collect_attachment_surface_text(window)
            evidence = (
                self._attachment_file_name_evidence(
                    image_path=image_path,
                    current_text=attachment_text,
                    baseline_text_cf=baseline_text_cf,
                )
                if self.config.require_new_attachment_preview
                else self._attachment_text_evidence(
                    image_path=image_path,
                    current_text=attachment_text,
                    baseline_text_cf=baseline_text_cf,
                )
            )
            if evidence is not None:
                self._log(f"confirmed source image attachment by visible UI text: {evidence!r}")
                if self.config.require_new_attachment_preview:
                    self._log("waiting for source image upload to settle before prompt paste")
                    time.sleep(10.0)
                return

            # Reliable fallback for UI variants where filename text isn't exposed:
            # accept new remove-control only from a truly clean baseline.
            if self.config.require_new_attachment_preview and baseline_remove_controls == 0:
                remove_control_now = self._attachment_remove_control_count(window)
                if remove_control_now > 0:
                    if first_seen_remove_control_at is None:
                        first_seen_remove_control_at = now
                    if now - first_seen_remove_control_at >= 4.0:
                        self._log(
                            "confirmed source image attachment by remove-control fallback "
                            f"from clean baseline: {remove_control_now}"
                        )
                        time.sleep(2.0)
                        return
                else:
                    first_seen_remove_control_at = None

            candidates = self._find_attachment_image_candidates(window)
            new_candidates: list[BaseWrapper] = []
            for candidate in candidates:
                signature = self._result_signature(candidate)
                if signature is None or self._signature_matches_baseline(
                    signature,
                    baseline_set=baseline_set,
                    baseline_digests=baseline_digests,
                ):
                    continue
                new_candidates.append(candidate)
            if new_candidates:
                signature = self._result_signature(new_candidates[-1])
                if signature is not None:
                    first_seen_at = first_seen_preview_at.setdefault(signature, now)
                    if now - first_seen_at < preview_stable_sec:
                        if self.config.verbose and now - last_log_at >= 3.0:
                            self._log(
                                "source image preview appeared; waiting for it to stabilize "
                                "before prompt paste"
                            )
                            last_log_at = now
                        time.sleep(0.5)
                        continue
                self._log(
                    "confirmed source image attachment by new visible preview candidate: "
                    f"{self._wrapper_rect_text(new_candidates[-1])}"
                )
                return

            if self.config.verbose and now - last_log_at >= 5.0:
                self._log(
                    "waiting for ChatGPT to expose the attached source image before prompt paste"
                )
                last_log_at = now
            time.sleep(0.5)

        raise DesktopAutomationError(
            "The source image paste/open action completed, but ChatGPT never exposed a confirmed "
            "attachment preview. Stopping before prompt paste so the request cannot run without "
            f"the source image: {image_path.name}."
        )

    def _wait_for_attached_source_images(
        self,
        window: BaseWrapper,
        image_paths: tuple[Path, ...],
        baseline_signatures: list[ImageSignature],
        baseline_surface_text: str,
    ) -> None:
        timeout_sec = max(35.0, self.config.post_attach_delay_sec + 25.0)
        deadline = time.time() + timeout_sec
        expected_count = len(image_paths)
        baseline_set = set(baseline_signatures)
        baseline_digests = {
            digest
            for digest in (self._result_signature_digest(signature) for signature in baseline_signatures)
            if digest
        }
        baseline_text_cf = baseline_surface_text.casefold()
        last_log_at = 0.0

        while time.time() < deadline:
            attachment_text = self._collect_attachment_surface_text(window)
            confirmed_names = [
                image_path.name
                for image_path in image_paths
                if self._attachment_file_name_evidence(
                    image_path=image_path,
                    current_text=attachment_text,
                    baseline_text_cf=baseline_text_cf,
                )
                is not None
            ]
            if len(confirmed_names) >= expected_count:
                self._log(
                    "confirmed source image batch attachment by visible UI text: "
                    + ", ".join(confirmed_names)
                )
                return

            remove_control_delta = (
                self._attachment_remove_control_count(window)
                - self._attachment_remove_control_count_from_text(baseline_surface_text)
            )
            if remove_control_delta >= expected_count:
                self._log(
                    "confirmed source image batch attachment by remove controls: "
                    f"{remove_control_delta} new controls"
                )
                return

            candidates = self._find_attachment_image_candidates(window)
            new_candidates: list[BaseWrapper] = []
            for candidate in candidates:
                signature = self._result_signature(candidate)
                if signature is None or self._signature_matches_baseline(
                    signature,
                    baseline_set=baseline_set,
                    baseline_digests=baseline_digests,
                ):
                    continue
                new_candidates.append(candidate)
            if len(new_candidates) >= expected_count:
                self._log(
                    "confirmed source image batch attachment by new visible preview candidates: "
                    + ", ".join(self._wrapper_rect_text(candidate) for candidate in new_candidates[-expected_count:])
                )
                return

            now = time.time()
            if self.config.verbose and now - last_log_at >= 5.0:
                self._log(
                    "waiting for ChatGPT to expose both attached source images before prompt paste"
                )
                last_log_at = now
            time.sleep(0.5)

        names = ", ".join(image_path.name for image_path in image_paths)
        raise DesktopAutomationError(
            "The source image batch paste completed, but ChatGPT never exposed confirmed previews "
            f"for all {expected_count} source images. Stopping before prompt paste so the request "
            f"cannot run with an incomplete pair: {names}."
        )

    def _attachment_text_evidence(
        self,
        *,
        image_path: Path,
        current_text: str,
        baseline_text_cf: str,
    ) -> Optional[str]:
        current_text_cf = current_text.casefold()
        tokens = [
            image_path.name.casefold(),
            image_path.stem.casefold(),
        ]
        for token in tokens:
            if len(token) >= 3 and token in current_text_cf and token not in baseline_text_cf:
                return token
        for pattern in ATTACHMENT_CONFIRMATION_PATTERNS:
            normalized = pattern.casefold()
            if normalized in current_text_cf and normalized not in baseline_text_cf:
                return pattern
        return None

    def _attachment_file_name_evidence(
        self,
        *,
        image_path: Path,
        current_text: str,
        baseline_text_cf: str,
    ) -> Optional[str]:
        current_text_cf = current_text.casefold()
        for token in (image_path.name.casefold(), image_path.stem.casefold()):
            if len(token) >= 3 and token in current_text_cf and token not in baseline_text_cf:
                return token
        return None

    def _attachment_remove_control_count(self, window: BaseWrapper) -> int:
        count = 0
        for wrapper in self._safe_descendants(window, control_type="Button"):
            try:
                if not wrapper.is_visible():
                    continue
                search_text = self._control_search_text(wrapper).casefold()
            except Exception:
                continue
            if any(pattern.casefold() in search_text for pattern in ATTACHMENT_CONFIRMATION_PATTERNS):
                count += 1
        return count

    def _attachment_remove_control_count_from_text(self, text: str) -> int:
        text_cf = text.casefold()
        return sum(text_cf.count(pattern.casefold()) for pattern in ATTACHMENT_CONFIRMATION_PATTERNS)

    def _paste_file_clipboard(self, window: BaseWrapper, image_path: Path) -> None:
        if self._preserve_manual_composer_focus() and not self.config.click_composer_before_paste:
            self._click_manual_composer_position(window)
        elif not self._preserve_manual_composer_focus() or self.config.click_composer_before_paste:
            self._focus_prompt_input_or_composer(window)
        _copy_file_to_windows_clipboard(image_path)
        self._paste_from_clipboard(window)
        self._log("file paste shortcut sent")
        time.sleep(2.5)

    def _paste_files_clipboard(self, window: BaseWrapper, image_paths: tuple[Path, ...]) -> None:
        if self._preserve_manual_composer_focus() and not self.config.click_composer_before_paste:
            self._click_manual_composer_position(window)
        elif not self._preserve_manual_composer_focus() or self.config.click_composer_before_paste:
            self._focus_prompt_input_or_composer(window)
        _copy_files_to_windows_clipboard(image_paths)
        self._paste_from_clipboard(window)
        self._log(f"{len(image_paths)}-file paste shortcut sent")
        time.sleep(3.5)

    def _paste_prompt(self, window: BaseWrapper, prompt_text: str) -> None:
        self._close_unexpected_open_dialog_if_needed()
        if (
            len(self._source_image_paths()) > 1
            or not self._preserve_manual_composer_focus()
            or self.config.click_composer_before_paste
        ):
            self._focus_prompt_input_or_composer(window)
        pyperclip.copy(prompt_text)
        self._press_ctrl_key(window, VK_A)
        time.sleep(0.1)
        self._press_key(window, VK_BACK)
        time.sleep(0.1)
        self._paste_from_clipboard(window)
        self._log("prompt paste shortcut sent")
        time.sleep(self.config.post_paste_delay_sec)

    def _wait_for_prompt_after_multi_image_paste(self, window: BaseWrapper, prompt_text: str) -> None:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if self._composer_still_has_text(window):
                self._log("confirmed prompt text in composer after multi-image paste")
                return
            if self._prompt_text_anchor_is_visible(window, prompt_text):
                self._log("confirmed prompt text by visible UI after multi-image paste")
                return
            time.sleep(0.4)
        raise DesktopAutomationError(
            "Two source images were attached, but the prompt text was not confirmed in the composer. "
            "Stopping before submit and before the next pair so another pair image cannot be pasted into "
            "the same request."
        )

    def _ensure_prompt_after_paste(self, window: BaseWrapper, prompt_text: str) -> None:
        attempts = 3
        for attempt in range(1, attempts + 1):
            if self._composer_still_has_text(window):
                if attempt > 1:
                    self._log(f"confirmed prompt text in composer after retry {attempt}")
                return
            if self._prompt_text_anchor_is_visible(window, prompt_text):
                if attempt > 1:
                    self._log(f"confirmed prompt text by visible UI after retry {attempt}")
                return
            if attempt >= attempts:
                break
            self._log(
                "prompt text was not detected after paste; "
                f"retrying prompt paste ({attempt}/{attempts - 1})"
            )
            self._close_unexpected_open_dialog_if_needed()
            self._focus_prompt_input_or_composer(window)
            pyperclip.copy(prompt_text)
            self._paste_from_clipboard(window)
            time.sleep(max(self.config.post_paste_delay_sec, 0.5))
        raise DesktopAutomationError(
            "Prompt text was not confirmed in the ChatGPT composer after attachment. "
            "Stopping before submit so the request cannot run with an empty prompt."
        )

    def _prompt_text_anchor_is_visible(self, window: BaseWrapper, prompt_text: str) -> bool:
        anchors = self._prompt_text_anchors(prompt_text)
        if not anchors:
            return False
        try:
            visible_text = self._collect_visible_text(window=window).casefold()
        except Exception:
            return False
        return any(anchor in visible_text for anchor in anchors)

    def _prompt_text_anchors(self, prompt_text: str) -> list[str]:
        normalized = " ".join(prompt_text.casefold().split())
        if not normalized:
            return []
        anchors: list[str] = []
        for candidate in (
            normalized[:48],
            "construct a single",
            "cinematic",
            "do not respond with text",
        ):
            candidate = candidate.strip()
            if len(candidate) >= 12 and candidate in normalized and candidate not in anchors:
                anchors.append(candidate)
        return anchors

    def _focus_prompt_input_or_composer(self, window: BaseWrapper) -> bool:
        # After a full page reload (a fresh temporary chat) the composer can briefly
        # resolve to a stale/zero-size node, so retry fetching a fresh, interactive
        # prompt input before falling back to a fixed composer location.
        for attempt in range(1, 4):
            input_box = self._wait_for_prompt_input(window, timeout_sec=8.0)
            if input_box is not None:
                try:
                    self._ensure_foreground_window(window, "click detected ChatGPT prompt input")
                    input_box.click_input()
                    rect = input_box.rectangle()
                    if rect.width() <= 0 or rect.height() <= 0:
                        self._log(
                            "detected prompt input became invalid after click; "
                            f"retrying {attempt}/3: x={rect.left}, y={rect.top}, "
                            f"w={rect.width()}, h={rect.height()}"
                        )
                        time.sleep(0.6)
                        continue
                    self._log(
                        "clicked detected prompt input: "
                        f"x={rect.left}, y={rect.top}, w={rect.width()}, h={rect.height()}"
                    )
                    time.sleep(0.3)
                    return True
                except Exception as exc:
                    self._log(f"could not click detected prompt input: {exc}")
                    time.sleep(0.5)
            else:
                self._log("detected prompt input was not ready; retrying before composer fallback")
                time.sleep(0.6)
        self._log("using expected composer area after prompt input retries")
        return self._click_composer_area(window)

    def _wait_for_prompt_input(self, window: BaseWrapper, *, timeout_sec: float) -> Optional[BaseWrapper]:
        deadline = time.time() + max(0.1, timeout_sec)
        while time.time() < deadline:
            input_box = self._find_prompt_input(window)
            if input_box is not None:
                try:
                    rect = input_box.rectangle()
                    if (
                        input_box.is_visible()
                        and input_box.is_enabled()
                        and rect.width() > 0
                        and rect.height() > 0
                    ):
                        return input_box
                except Exception:
                    pass
            time.sleep(0.35)
        return None

    def _paste_from_clipboard(self, window: BaseWrapper) -> None:
        self._press_ctrl_key(window, VK_V)
        time.sleep(0.4)

    def _press_enter(self, window: BaseWrapper) -> None:
        self._press_key(window, VK_RETURN)

    def _close_chatgpt_overlay_if_needed(self, window: BaseWrapper) -> None:
        self._press_key(window, VK_ESCAPE)
        self._log("sent Esc to close any open ChatGPT image overlay")
        time.sleep(0.4)

    def _press_ctrl_enter(self, window: BaseWrapper) -> None:
        self._press_ctrl_key(window, VK_RETURN)

    def _press_alt_key(self, window: BaseWrapper, virtual_key: int) -> None:
        self._ensure_foreground_window(window, "press Alt shortcut")
        self._press_alt_key_raw(virtual_key)

    def _press_alt_key_raw(self, virtual_key: int) -> None:
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_MENU, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(virtual_key, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(virtual_key, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    def _press_ctrl_key(self, window: BaseWrapper, virtual_key: int) -> None:
        self._ensure_foreground_window(window, "press Ctrl shortcut")
        self._press_ctrl_key_raw(virtual_key)

    def _press_ctrl_key_raw(self, virtual_key: int) -> None:
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(virtual_key, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(virtual_key, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

    def _press_key(self, window: BaseWrapper, virtual_key: int) -> None:
        self._ensure_foreground_window(window, "press key")
        self._press_key_raw(virtual_key)

    def _press_key_raw(self, virtual_key: int) -> None:
        user32 = ctypes.windll.user32
        user32.keybd_event(virtual_key, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(virtual_key, 0, KEYEVENTF_KEYUP, 0)

    def _restore_foreground_window(self, window: BaseWrapper) -> None:
        try:
            ctypes.windll.user32.SetForegroundWindow(window.handle)
            time.sleep(0.15)
        except Exception as exc:
            self._log(f"could not restore foreground window: {exc}")

    def _window_handle(self, window: BaseWrapper) -> int:
        try:
            return int(window.handle)
        except Exception:
            return 0

    def _assert_foreground_window(self, window: BaseWrapper, action: str) -> None:
        expected_handle = self._window_handle(window)
        actual_handle, actual_title, actual_class = self._foreground_window_info()
        if expected_handle and actual_handle == expected_handle:
            return
        raise DesktopAutomationError(
            "Unsafe desktop input blocked before "
            f"{action}: expected selected window {self._window_title(window)!r} "
            f"(handle={expected_handle}), but foreground is {actual_title!r} "
            f"(class={actual_class!r}, handle={actual_handle})."
        )

    def _ensure_foreground_window(self, window: BaseWrapper, action: str) -> None:
        self._wait_for_mouse_idle(action)
        expected_handle = self._window_handle(window)
        if not expected_handle:
            raise DesktopAutomationError(f"Unsafe desktop input blocked before {action}: target window handle is unknown.")
        deadline = time.time() + 1.5
        while time.time() < deadline:
            self._restore_foreground_window(window)
            actual_handle, _actual_title, _actual_class = self._foreground_window_info()
            if actual_handle == expected_handle:
                return
            time.sleep(0.1)
        self._assert_foreground_window(window, action)

    def _wait_for_mouse_idle(self, action: str) -> None:
        idle_sec = max(0.0, float(self.config.mouse_idle_sec or 0.0))
        if idle_sec <= 0:
            return
        timeout_sec = max(idle_sec, float(self.config.mouse_idle_timeout_sec or idle_sec))
        deadline = time.monotonic() + timeout_sec
        last_position = self._cursor_position()
        stable_since = time.monotonic()
        logged = False
        while time.monotonic() < deadline:
            time.sleep(0.15)
            position = self._cursor_position()
            if position != last_position:
                last_position = position
                stable_since = time.monotonic()
                if not logged:
                    self._log(f"waiting for mouse idle before {action}")
                    logged = True
                continue
            if time.monotonic() - stable_since >= idle_sec:
                return
        raise DesktopAutomationError(
            f"Mouse did not stay idle for {idle_sec:g} seconds before {action}."
        )

    def _click_composer_area(self, window: BaseWrapper) -> bool:
        try:
            rect = window.rectangle()
            x = max(20, rect.width() // 2)
            y = max(80, rect.height() - 115)
            self._click_screen_point(
                rect.left + x,
                rect.top + y,
                expected_window=window,
                purpose="click ChatGPT composer area",
            )
            self._log(f"clicked expected composer area: x={rect.left + x}, y={rect.top + y}")
            time.sleep(0.3)
            return True
        except Exception as exc:
            self._log(f"could not click expected composer area: {exc}")
            return False

    def _cursor_position(self) -> Optional[tuple[int, int]]:
        point = POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return (int(point.x), int(point.y))

    def _click_manual_composer_position(self, window: BaseWrapper) -> bool:
        if self._manual_click_position is None:
            return False
        try:
            x, y = self._manual_click_position
            self._click_screen_point(x, y, expected_window=window, purpose="click remembered ChatGPT composer")
            self._log(f"clicked remembered composer position: x={x}, y={y}")
            time.sleep(0.3)
            return True
        except Exception as exc:
            self._log(f"could not click remembered composer position: {exc}")
            return False

    def _click_manual_send_button(self, window: BaseWrapper, target_x: int, target_y: int) -> bool:
        if self._manual_click_position is None:
            return False
        try:
            self._click_screen_point(target_x, target_y, expected_window=window, purpose="click ChatGPT send arrow")
            self._log(f"clicked ChatGPT send arrow: x={target_x}, y={target_y}")
            time.sleep(0.5)
            return True
        except Exception as exc:
            self._log(f"could not click ChatGPT send arrow: {exc}")
            return False

    def _manual_send_button_positions(self, window: BaseWrapper) -> list[tuple[int, int]]:
        if self._manual_send_position is not None:
            send_x, send_y = self._manual_send_position
            positions: list[tuple[int, int]] = []
            seen: set[tuple[int, int]] = set()

            def add_point(x: int, y: int) -> None:
                point = (int(x), int(y))
                if point in seen:
                    return
                seen.add(point)
                positions.append(point)

            add_point(send_x, send_y)
            for y_offset in (-20, 20, -40, 40):
                add_point(send_x, send_y + y_offset)
            for x_offset in (-20, 20, -40, 40):
                add_point(send_x + x_offset, send_y)
            return positions
        if self._manual_click_position is None:
            return []
        self._log("no valid send-arrow point captured; skipping estimated coordinate clicks")
        return []

    def _click_screen_point(
        self,
        x: int,
        y: int,
        *,
        expected_window: Optional[BaseWrapper] = None,
        purpose: str = "screen click",
        button: str = "left",
    ) -> None:
        if expected_window is not None:
            self._ensure_foreground_window(expected_window, purpose)
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.1)
        if expected_window is not None:
            self._assert_foreground_window(expected_window, purpose)
        if button == "right":
            down_event = MOUSEEVENTF_RIGHTDOWN
            up_event = MOUSEEVENTF_RIGHTUP
        else:
            down_event = MOUSEEVENTF_LEFTDOWN
            up_event = MOUSEEVENTF_LEFTUP
        user32.mouse_event(down_event, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(up_event, 0, 0, 0, 0)

    def _close_unexpected_open_dialog_if_needed(self) -> None:
        if self.config.attach_via_clipboard and not self.config.allow_file_dialog_fallback:
            self._log("skipping open-dialog scan in clipboard-only mode")
            return
        try:
            dialog = self._find_open_dialog()
        except BaseException as exc:
            self._log(f"skipping unexpected open-dialog cleanup due to UIA error: {exc}")
            return
        self._log("unexpected open-file dialog is still visible; closing it before prompt paste")
        try:
            self._press_key(dialog, VK_ESCAPE)
        except Exception as exc:
            self._log(f"could not close unexpected open-file dialog safely: {exc}")
        time.sleep(0.5)

    def _close_unexpected_file_dialogs_if_needed(self) -> None:
        if self.config.attach_via_clipboard and not self.config.allow_file_dialog_fallback:
            self._log("skipping file-dialog scan in clipboard-only mode")
            return
        self._close_unexpected_open_dialog_if_needed()
        try:
            dialog = self._find_save_dialog()
        except BaseException as exc:
            self._log(f"skipping unexpected save-dialog cleanup due to UIA error: {exc}")
            return
        self._log("unexpected save dialog is still visible; closing it before this job")
        try:
            self._press_key(dialog, VK_ESCAPE)
        except Exception as exc:
            self._log(f"could not close unexpected save dialog safely: {exc}")
        time.sleep(0.5)

    def _submit_prompt(self, window: BaseWrapper) -> None:
        if self._preserve_manual_composer_focus() and not self.config.click_composer_before_paste:
            self._restore_foreground_window(window)
            for target_x, target_y in self._manual_send_button_positions(window):
                self._click_manual_send_button(window, target_x, target_y)
                self._log("send arrow click attempted")
                if self._submission_started(window):
                    self._log("submission appears to have started after send-arrow click")
                    return
                self._log("send-arrow click did not start the request; trying next send-arrow position")
        for attempt in range(1, 6):
            send_button = self._wait_for_send_button(window)
            if send_button is None:
                break
            self._log(f"clicking send button candidate: {self._control_label(send_button)!r}")
            if not self._click_wrapper_center(
                send_button,
                expected_window=window,
                purpose="click ChatGPT send button",
            ):
                self._ensure_foreground_window(window, "click ChatGPT send button")
                send_button.click_input()
            if self._submission_started(window):
                self._log(f"submission appears to have started after send button click attempt {attempt}")
                return
            self._log(f"send button click attempt {attempt} did not start the request; retrying")
        raise DesktopAutomationError(
            "Could not find or activate the ChatGPT send button. "
            "Stopping before result wait so the batch does not wait forever after a missed send click."
        )

    def _submission_started(self, window: BaseWrapper) -> bool:
        deadline = time.time() + 7.0
        while time.time() < deadline:
            if not self._composer_still_has_text(window):
                return True
            if self._has_generation_running_indicator(window):
                return True
            time.sleep(0.5)
        return False

    def _has_generation_running_indicator(self, window: BaseWrapper) -> bool:
        patterns = [pattern.casefold() for pattern in GENERATION_RUNNING_PATTERNS]
        for wrapper in self._safe_descendants(window, control_type="Button"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                label = self._control_label(wrapper).casefold()
                if any(pattern in label for pattern in patterns):
                    return True
            except Exception:
                continue
        return False

    def _wait_for_send_button(self, window: BaseWrapper) -> Optional[BaseWrapper]:
        deadline = time.time() + 25.0
        geometry_candidate_logged = False
        while time.time() < deadline:
            button = self._find_button(window, SEND_BUTTON_PATTERNS)
            if button is not None:
                return button
            button = self._find_send_button_by_geometry(window)
            if button is not None:
                if not geometry_candidate_logged:
                    self._log(
                        "send button was not found by label; using geometric candidate: "
                        f"{self._wrapper_rect_text(button)}, title={self._control_label(button)!r}"
                    )
                    geometry_candidate_logged = True
                return button
            time.sleep(0.5)
        return None

    def _find_send_button_by_geometry(self, window: BaseWrapper) -> Optional[BaseWrapper]:
        prompt_input = self._find_prompt_input(window)
        try:
            prompt_rect = prompt_input.rectangle() if prompt_input is not None else None
            window_rect = window.rectangle()
        except Exception:
            return None

        candidates: list[tuple[float, BaseWrapper]] = []
        for wrapper in self._safe_descendants(window, control_type="Button"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                rect = wrapper.rectangle()
                if rect.width() < 12 or rect.height() < 12:
                    continue
                if rect.width() > 110 or rect.height() > 110:
                    continue
                if rect.top < window_rect.top + window_rect.height() * 0.18:
                    continue
                if not self._rect_center_inside(rect, window_rect):
                    continue

                label = self._control_label(wrapper).casefold()
                searchable = self._control_search_text(wrapper).casefold()
                if self._looks_like_voice_button(wrapper):
                    self._log(
                        "blocked voice/microphone candidate while looking for send button: "
                        f"{self._wrapper_rect_text(wrapper)}, label={self._control_label(wrapper)!r}, "
                        f"search={self._control_search_text(wrapper)!r}"
                    )
                    continue
                if label and not any(pattern.casefold() in searchable for pattern in SEND_BUTTON_PATTERNS):
                    if any(part in searchable for part in ("button", "кноп", "system")):
                        continue
                if any(
                    part in label
                    for part in (
                        "back",
                        "forward",
                        "reload",
                        "close",
                        "minimize",
                        "maximize",
                        "new project",
                        "attach",
                        "add photos",
                        "add files",
                        "добав",
                        "прикреп",
                        "новый проект",
                        "закрыть",
                        "свернуть",
                        "развернуть",
                    )
                ):
                    continue

                center_x = rect.left + rect.width() // 2
                center_y = rect.top + rect.height() // 2
                if prompt_rect is not None:
                    if center_x < prompt_rect.right - 60:
                        continue
                    if center_x > prompt_rect.right + 520:
                        continue
                    if center_y < prompt_rect.top - 80:
                        continue
                    if center_y > prompt_rect.bottom + 360:
                        continue
                    target_x = prompt_rect.right + 140
                    target_y = prompt_rect.bottom + 150
                    distance = abs(center_x - target_x) + abs(center_y - target_y)
                    score = 10_000 - distance
                    if center_x >= prompt_rect.right:
                        score += 500
                    if center_y >= prompt_rect.top:
                        score += 250
                else:
                    if center_y < window_rect.top + window_rect.height() * 0.45:
                        continue
                    if center_x < window_rect.left + window_rect.width() * 0.45:
                        continue
                    score = center_x + center_y

                # Prefer icon-sized square controls, which matches ChatGPT's send arrow.
                score -= abs(rect.width() - rect.height()) * 2
                candidates.append((score, wrapper))
            except Exception:
                continue

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        if self.config.verbose:
            self._log(
                "geometric send candidates: "
                f"{[(round(score, 1), self._wrapper_rect_text(wrapper), self._control_label(wrapper)) for score, wrapper in candidates[-5:]]}"
            )
        return candidates[-1][1]

    def _looks_like_voice_button(self, wrapper: BaseWrapper) -> bool:
        text = self._control_search_text(wrapper).casefold()
        return any(pattern.casefold() in text for pattern in VOICE_BUTTON_PATTERNS)

    def _control_label(self, wrapper: BaseWrapper) -> str:
        try:
            title = wrapper.window_text().strip()
        except Exception:
            title = ""
        try:
            name = (getattr(wrapper.element_info, "name", "") or "").strip()
        except Exception:
            name = ""
        return title or name

    def _control_search_text(self, wrapper: BaseWrapper) -> str:
        parts = [self._control_label(wrapper)]
        try:
            info = wrapper.element_info
            for attr in ("name", "automation_id", "class_name", "control_type"):
                value = getattr(info, attr, "") or ""
                if value:
                    parts.append(str(value))
        except Exception:
            pass
        return " ".join(part for part in parts if part)

    def _composer_still_has_text(self, window: BaseWrapper) -> bool:
        input_box = self._find_prompt_input(window)
        if input_box is None:
            return False
        try:
            text = input_box.window_text().strip()
            return bool(text)
        except Exception:
            return False

    def _collect_submission_baseline_signatures(self, window: BaseWrapper) -> list[ImageSignature]:
        seen: set[ImageSignature] = set()
        signatures: list[ImageSignature] = []
        wrappers = list(self._find_result_images(window))
        wrappers.extend(self._find_attachment_image_candidates(window))
        for wrapper in wrappers:
            signature = self._result_signature(wrapper)
            if signature is None or signature in seen:
                continue
            seen.add(signature)
            signatures.append(signature)
        return signatures

    def _save_result_image(self, window: BaseWrapper, baseline_signatures: list[ImageSignature]) -> None:
        _result_image, accepted_signature = self._wait_for_result_image(window, baseline_signatures)
        output_path = self.config.output_path
        if output_path is None:
            raise DesktopAutomationError("Output path for desktop result is not configured.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        live_result_image = self._find_live_result_wrapper(
            window,
            accepted_signature,
            baseline_signatures,
            resolve_timeout_sec=30.0,
        )
        self._save_wrapper_image_with_fallback(window, live_result_image, output_path)

    def _save_result_image_via_context_menu(
        self,
        window: BaseWrapper,
        baseline_signatures: list[ImageSignature],
    ) -> None:
        _result_image, accepted_signature = self._wait_for_result_image(window, baseline_signatures)
        output_path = self.config.output_path
        if output_path is None:
            raise DesktopAutomationError("Output path for desktop result is not configured.")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._open_save_dialog_from_image(
                window,
                accepted_signature,
                baseline_signatures,
                output_path,
            )
        except DesktopAutomationError as exc:
            self._log(f"context-menu save failed ({exc}); trying direct capture fallback")
            live_result_image = self._find_live_result_wrapper(
                window,
                accepted_signature,
                baseline_signatures,
                resolve_timeout_sec=60.0,
            )
            self._save_wrapper_image_with_fallback(window, live_result_image, output_path)
            self._log(f"result saved via direct capture fallback: {output_path}")
            return
        time.sleep(1.0)

    def _save_wrapper_image_with_fallback(
        self,
        window: BaseWrapper,
        wrapper: BaseWrapper,
        output_path: Path,
    ) -> None:
        last_error: Optional[Exception] = None
        try:
            wrapper.capture_as_image().save(output_path)
            if output_path.exists() and output_path.stat().st_size > 0:
                return
            raise DesktopAutomationError("Direct wrapper capture produced an empty output file.")
        except Exception as exc:  # pragma: no cover - GUI specific
            last_error = exc
            self._log(f"direct wrapper capture failed ({exc}); trying screen-region fallback")

        if ImageGrab is None:
            raise DesktopAutomationError(
                "Could not capture the generated image: PIL ImageGrab is unavailable."
            ) from last_error

        try:
            rect = wrapper.rectangle()
            raw_bbox = (rect.left, rect.top, rect.right, rect.bottom)
            bbox = self._clip_bbox_to_virtual_screen(raw_bbox)
            if bbox is None:
                self._log(
                    "result wrapper area is outside visible screen; falling back to ChatGPT window crop"
                )
                window_rect = window.rectangle()
                window_raw_bbox = (
                    window_rect.left,
                    window_rect.top,
                    window_rect.right,
                    window_rect.bottom,
                )
                bbox = self._clip_bbox_to_virtual_screen(window_raw_bbox)
            if bbox is None:
                raise DesktopAutomationError(
                    f"Invalid capture area after screen clipping. result_bbox={raw_bbox!r}"
                )
            self._ensure_foreground_window(window, "screen-region capture fallback")
            time.sleep(0.15)
            try:
                screenshot = ImageGrab.grab(bbox=bbox, all_screens=True)
            except TypeError:
                screenshot = ImageGrab.grab(bbox=bbox)
            screenshot.save(output_path)
            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise DesktopAutomationError("Screen-region fallback produced an empty output file.")
        except Exception as exc:  # pragma: no cover - GUI specific
            details = str(exc)
            if last_error is not None:
                details = f"direct={last_error}; fallback={exc}"
            raise DesktopAutomationError(
                "Could not save the generated result image via direct capture or screen-region fallback. "
                f"Details: {details}"
            ) from exc

    def _clip_bbox_to_virtual_screen(
        self,
        bbox: tuple[int, int, int, int],
    ) -> Optional[tuple[int, int, int, int]]:
        left, top, right, bottom = bbox
        if right <= left or bottom <= top:
            return None
        screen_left, screen_top, screen_right, screen_bottom = self._virtual_screen_bounds()
        clipped_left = max(left, screen_left)
        clipped_top = max(top, screen_top)
        clipped_right = min(right, screen_right)
        clipped_bottom = min(bottom, screen_bottom)
        if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
            return None
        return (clipped_left, clipped_top, clipped_right, clipped_bottom)

    def _virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if width <= 0 or height <= 0:
            left = 0
            top = 0
            width = int(user32.GetSystemMetrics(0))
            height = int(user32.GetSystemMetrics(1))
        return (left, top, left + max(1, width), top + max(1, height))

    def _open_save_dialog_from_image(
        self,
        window: BaseWrapper,
        accepted_signature: ImageSignature,
        baseline_signatures: list[ImageSignature],
        output_path: Path,
    ) -> None:
        attempts = (
            ("menu item", None),
        )
        last_error: Optional[Exception] = None
        for attempt_name, fallback_keys in attempts:
            self._log(f"opening browser image context menu: {attempt_name}")
            self._ensure_foreground_window(window, "open result image context menu")
            result_image = self._find_live_result_wrapper(
                window,
                accepted_signature,
                baseline_signatures,
                resolve_timeout_sec=15.0,
            )
            if not self._result_image_is_usable(window, result_image):
                raise DesktopAutomationError("Accepted result image is no longer visible inside the ChatGPT window.")
            self._log(f"right-clicking result image: {self._wrapper_rect_text(result_image)}")
            self._click_wrapper_center(
                result_image,
                expected_window=window,
                purpose="right-click generated result image",
                button="right",
            )
            time.sleep(0.6)
            if fallback_keys is None:
                if not self._activate_save_image_menu_item():
                    self._press_key(window, VK_ESCAPE)
                    time.sleep(0.3)
                    continue
            else:
                raise DesktopAutomationError("Keyboard context-menu fallback is disabled for input safety.")
            time.sleep(1.0)
            try:
                dialog = self._wait_for_save_dialog(timeout_sec=2.0)
                self._fill_save_dialog(dialog, output_path)
                return
            except Exception as exc:  # pragma: no cover - GUI specific
                last_error = exc
                self._log("save dialog is not visible to UIA; trying blind foreground save")
                if self._blind_save_foreground_dialog(output_path):
                    return
                self._press_key(window, VK_ESCAPE)
                time.sleep(0.5)
        raise DesktopAutomationError("Save-file dialog did not appear after image context-menu attempts.") from last_error

    def _save_response_text(self, window: BaseWrapper, baseline_text: str) -> None:
        response_text = self._wait_for_response_text(window, baseline_text)
        path = self.config.response_text_path
        if path is None:
            raise DesktopAutomationError("Response text path is not configured.")
        self._save_text_snapshot(path, response_text)

    def _wait_until_generation_settles(self, window: BaseWrapper, deadline: float) -> None:
        saw_running = False
        submit_grace_at = time.time() + 12.0
        last_log_at = 0.0
        while time.time() < deadline:
            now = time.time()
            if self._has_generation_running_indicator(window):
                saw_running = True
                if self.config.verbose and now - last_log_at >= 10.0:
                    self._log("ChatGPT generation still in progress; waiting for Stop control to clear")
                    last_log_at = now
                time.sleep(1.0)
                continue
            if saw_running:
                self._log("ChatGPT generation finished; searching for result image")
                time.sleep(3.0)
                return
            if now >= submit_grace_at:
                return
            time.sleep(0.5)

    def _wait_for_result_image(
        self,
        window: BaseWrapper,
        baseline_signatures: list[ImageSignature],
    ) -> tuple[BaseWrapper, ImageSignature]:
        start = time.time()
        deadline = start + self.config.result_timeout_sec
        self._wait_until_generation_settles(window, deadline)
        min_ready_at = start + max(0.0, self.config.min_result_wait_sec)
        stable_wait = max(0.0, self.config.result_stable_sec)
        early_ignore_sec = min(15.0, max(5.0, self.config.min_result_wait_sec / 6.0))
        early_ignore_until = start + early_ignore_sec
        baseline_set = set(baseline_signatures)
        baseline_digests = {
            digest
            for digest in (self._result_signature_digest(signature) for signature in baseline_signatures)
            if digest
        }
        baseline_count = len(baseline_signatures)
        first_seen: dict[ImageSignature, float] = {}
        stable_signature: Optional[ImageSignature] = None
        stable_since: Optional[float] = None
        generation_running_since: Optional[float] = None
        running_indicator_grace_sec = max(20.0, stable_wait + 8.0)
        last_candidate: Optional[BaseWrapper] = None
        last_log_at = 0.0
        while time.time() < deadline:
            now = time.time()
            running_now = self._has_generation_running_indicator(window)
            if running_now:
                if generation_running_since is None:
                    generation_running_since = now
                if self.config.verbose and now - last_log_at >= 10.0:
                    self._log("generation still running; postponing result acceptance")
                    last_log_at = now
            else:
                generation_running_since = None
            candidates = self._find_result_images(window)
            new_candidates: list[tuple[ImageSignature, BaseWrapper, float]] = []
            for candidate in candidates:
                signature = self._result_signature(candidate)
                if signature is None or self._signature_matches_baseline(
                    signature,
                    baseline_set=baseline_set,
                    baseline_digests=baseline_digests,
                ):
                    continue
                seen_at = first_seen.setdefault(signature, now)
                new_candidates.append((signature, candidate, seen_at))

            if self.config.verbose and now - last_log_at >= 15.0:
                elapsed = int(now - start)
                remaining_min = max(0, int(min_ready_at - now))
                self._log(
                    "waiting for generated result: "
                    f"{elapsed}s elapsed, {len(candidates)} image(s), "
                    f"{len(new_candidates)} new, min wait remaining {remaining_min}s"
                )
                last_log_at = now

            eligible = [
                item
                for item in new_candidates
                if now >= min_ready_at and item[2] >= early_ignore_until
            ]
            if not eligible and now >= min_ready_at and len(candidates) > baseline_count + 1:
                eligible = new_candidates
            if not eligible and now >= min_ready_at + stable_wait and new_candidates:
                eligible = [new_candidates[-1]]
                self._log(
                    "using fallback generated-image candidate after minimum wait; "
                    "no later candidate appeared"
                )

            if eligible:
                signature, candidate, _seen_at = eligible[-1]
                last_candidate = candidate
                if signature != stable_signature:
                    stable_signature = signature
                    stable_since = now
                    self._log(f"result candidate appeared; waiting {stable_wait:g}s for it to stabilize")
                elif stable_since is not None and now - stable_since >= stable_wait:
                    if running_now:
                        running_elapsed = (
                            now - generation_running_since
                            if generation_running_since is not None
                            else 0.0
                        )
                        if running_elapsed < running_indicator_grace_sec:
                            if self.config.verbose and now - last_log_at >= 10.0:
                                self._log(
                                    "result candidate is stable, but generation indicator is still visible; "
                                    f"waiting up to {int(running_indicator_grace_sec)}s before forced accept"
                                )
                                last_log_at = now
                            time.sleep(2.0)
                            continue
                        self._log(
                            "generation indicator appears stale; accepting stable result candidate anyway"
                        )
                    self._log(f"result image accepted after {int(now - start)}s")
                    return candidate, signature
            time.sleep(2.0)
        if last_candidate is not None:
            raise DesktopAutomationError(
                "A possible image result appeared, but it did not pass the generated-image wait checks."
            )
        raise DesktopAutomationError("Could not find a generated image in the ChatGPT desktop window.")

    def _find_result_wrapper_by_signature(
        self,
        window: BaseWrapper,
        accepted_signature: ImageSignature,
        *,
        baseline_set: set[ImageSignature],
        baseline_digests: set[int],
    ) -> Optional[BaseWrapper]:
        target_digest = self._result_signature_digest(accepted_signature)
        if not target_digest:
            return None
        best_match: Optional[BaseWrapper] = None
        best_area = 0
        for candidate in self._find_result_images(window):
            signature = self._result_signature(candidate)
            if signature is None or self._signature_matches_baseline(
                signature,
                baseline_set=baseline_set,
                baseline_digests=baseline_digests,
            ):
                continue
            if self._result_signature_digest(signature) != target_digest:
                continue
            try:
                area = candidate.rectangle().width() * candidate.rectangle().height()
            except Exception:
                area = 0
            if area >= best_area:
                best_match = candidate
                best_area = area
        return best_match

    def _find_live_result_wrapper(
        self,
        window: BaseWrapper,
        accepted_signature: ImageSignature,
        baseline_signatures: list[ImageSignature],
        *,
        resolve_timeout_sec: float,
    ) -> BaseWrapper:
        baseline_set = set(baseline_signatures)
        baseline_digests = {
            digest
            for digest in (self._result_signature_digest(signature) for signature in baseline_signatures)
            if digest
        }
        deadline = time.time() + max(1.0, resolve_timeout_sec)
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                match = self._find_result_wrapper_by_signature(
                    window,
                    accepted_signature,
                    baseline_set=baseline_set,
                    baseline_digests=baseline_digests,
                )
                if match is not None and self._result_image_is_usable(window, match):
                    return match
            except Exception as exc:  # pragma: no cover - GUI specific
                last_error = exc
            time.sleep(1.0)
        raise DesktopAutomationError(
            "Accepted result image is no longer visible inside the ChatGPT window."
        ) from last_error

    def _wait_for_response_text(self, window: BaseWrapper, baseline_text: str) -> str:
        deadline = time.time() + self.config.result_timeout_sec
        last_text = baseline_text
        while time.time() < deadline:
            current_text = self._collect_visible_text(window=window)
            if current_text and current_text != baseline_text and len(current_text) > len(baseline_text):
                return current_text
            if current_text:
                last_text = current_text
            time.sleep(2.0)
        if last_text and last_text != baseline_text:
            return last_text
        raise DesktopAutomationError("Could not capture a text response from the ChatGPT desktop window.")

    def _wait_for_dialog(self, timeout_sec: Optional[float] = None) -> BaseWrapper:
        deadline = time.time() + (self.config.dialog_timeout_sec if timeout_sec is None else timeout_sec)
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                dialog = self._find_open_dialog()
                return dialog
            except Exception as exc:  # pragma: no cover - GUI specific
                last_error = exc
                time.sleep(0.5)
        raise DesktopAutomationError("Open-file dialog did not appear.") from last_error

    def _find_open_dialog(self) -> BaseWrapper:
        desktop = Desktop(backend="uia")
        dialogs = []
        foreground = self._foreground_window()
        if foreground is not None and self._is_open_dialog_candidate(foreground, prefer_foreground=True):
            try:
                title = foreground.window_text()
            except Exception:
                title = ""
            self._log(f"accepted foreground open dialog: {title!r}")
            return foreground

        for dialog in self._safe_windows_query(desktop, title_re=OPEN_DIALOG_TITLE_RE, visible_only=True):
            try:
                title = dialog.window_text()
                if self._is_open_dialog_candidate(dialog, prefer_foreground=False):
                    dialog.wait("visible ready", timeout=0.5)
                    dialogs.append((title, dialog))
            except Exception:
                continue
        if not dialogs:
            for dialog in self._safe_windows_query(desktop, visible_only=True):
                try:
                    if not self._is_open_dialog_candidate(dialog, prefer_foreground=False):
                        continue
                    title = dialog.window_text()
                    self._log(f"accepted open dialog candidate by controls: {title!r}")
                    dialogs.append((title, dialog))
                except Exception:
                    continue
        if not dialogs and self.config.verbose:
            titles = []
            for window in self._safe_windows_query(desktop, visible_only=True):
                try:
                    titles.append(window.window_text())
                except Exception:
                    continue
            self._log(f"visible top-level windows while waiting for open dialog: {titles[:20]}")
        if not dialogs:
            raise DesktopAutomationError("Open-file dialog did not appear.")
        self._log(f"open dialogs: {[title for title, _ in dialogs]}")
        return dialogs[-1][1]

    def _safe_windows_query(self, desktop: Desktop, **kwargs) -> list[BaseWrapper]:
        try:
            return list(desktop.windows(**kwargs))
        except BaseException as exc:
            self._log(f"UIA windows query failed; returning empty list: {exc}")
            return []

    def _is_open_dialog_candidate(self, dialog: BaseWrapper, *, prefer_foreground: bool) -> bool:
        try:
            title = dialog.window_text().strip()
        except Exception:
            title = ""
        title_cf = title.casefold()
        if any(part in title_cf for part in SAVE_DIALOG_EXCLUDED_TITLE_PARTS):
            return False
        if "save" in title_cf or "сохран" in title_cf:
            return False

        class_name = self._window_class_name(dialog).casefold()
        title_looks_like_dialog = bool(re.search(OPEN_DIALOG_TITLE_RE, title, re.I))
        class_looks_like_dialog = class_name in {"#32770", "directuihwnd"} or "dialog" in class_name
        blank_foreground = prefer_foreground and not title
        if not (title_looks_like_dialog or class_looks_like_dialog or blank_foreground):
            return False

        edit = self._find_file_dialog_edit(dialog)
        action_button = self._find_dialog_action_button(dialog, OPEN_DIALOG_BUTTONS)
        if self.config.verbose:
            self._log(
                "open-dialog candidate check: "
                f"title={title!r}, class={self._window_class_name(dialog)!r}, "
                f"has_edit={edit is not None}, has_action={action_button is not None}, "
                f"foreground={prefer_foreground}"
            )
        if edit is None and action_button is None:
            return False
        return True

    def _wait_for_save_dialog(self, timeout_sec: Optional[float] = None) -> BaseWrapper:
        deadline = time.time() + (self.config.dialog_timeout_sec if timeout_sec is None else timeout_sec)
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                return self._find_save_dialog()
            except Exception as exc:  # pragma: no cover - GUI specific
                last_error = exc
                time.sleep(0.5)
        raise DesktopAutomationError("Save-file dialog did not appear.") from last_error

    def _activate_save_image_menu_item(self) -> bool:
        desktop = Desktop(backend="uia")
        menu_items = []
        for window in self._safe_windows_query(desktop, visible_only=True):
            try:
                descendants = window.descendants(control_type="MenuItem")
            except Exception:
                continue
            for item in descendants:
                try:
                    title = item.window_text().strip()
                    if not title or not item.is_visible() or not item.is_enabled():
                        continue
                    menu_items.append((title, item))
                except Exception:
                    continue
        if self.config.verbose and menu_items:
            self._log(f"context menu items: {[title for title, _ in menu_items][:20]}")
        for pattern in IMAGE_SAVE_MENU_PATTERNS:
            normalized = pattern.casefold()
            for title, item in menu_items:
                if normalized in title.casefold():
                    self._log(f"activating context menu item: {title!r}")
                    if self._invoke_wrapper(item):
                        return True
                    if self._click_wrapper_center(item):
                        return True
                    try:
                        item.click_input()
                        return True
                    except Exception as exc:
                        self._log(f"context menu item click_input failed: {exc}")
        return False

    def _find_save_dialog(self) -> BaseWrapper:
        desktop = Desktop(backend="uia")
        dialogs = []
        foreground = self._foreground_window()
        if foreground is not None and self._is_save_dialog_candidate(foreground, prefer_foreground=True):
            try:
                title = foreground.window_text()
            except Exception:
                title = ""
            self._log(f"accepted foreground save dialog: {title!r}")
            return foreground

        for dialog in self._safe_windows_query(desktop, title_re=SAVE_DIALOG_TITLE_RE, visible_only=True):
            try:
                title = dialog.window_text()
                if self._is_save_dialog_candidate(dialog, prefer_foreground=False):
                    dialogs.append((title, dialog))
            except Exception:
                continue
        if not dialogs:
            for dialog in self._safe_windows_query(desktop, visible_only=True):
                try:
                    if not self._is_save_dialog_candidate(dialog, prefer_foreground=False):
                        continue
                    title = dialog.window_text()
                    self._log(f"accepted save dialog candidate by controls: {title!r}")
                    dialogs.append((title, dialog))
                except Exception:
                    continue
        if not dialogs:
            if self.config.verbose:
                titles = []
                for window in self._safe_windows_query(desktop, visible_only=True):
                    try:
                        titles.append(window.window_text())
                    except Exception:
                        continue
                self._log(f"visible top-level windows while waiting for save dialog: {titles[:20]}")
            raise DesktopAutomationError("Save-file dialog did not appear.")
        self._log(f"save dialogs: {[title for title, _ in dialogs]}")
        return dialogs[-1][1]

    def _foreground_window(self) -> Optional[BaseWrapper]:
        try:
            handle = ctypes.windll.user32.GetForegroundWindow()
            if not handle:
                return None
            if Application is not None:
                return Application(backend="uia").connect(handle=handle).window(handle=handle)
            return Desktop(backend="uia").window(handle=handle)
        except Exception:
            return None

    def _is_save_dialog_candidate(self, dialog: BaseWrapper, *, prefer_foreground: bool) -> bool:
        try:
            title = dialog.window_text().strip()
        except Exception:
            title = ""
        title_cf = title.casefold()
        if any(part in title_cf for part in SAVE_DIALOG_EXCLUDED_TITLE_PARTS):
            return False

        class_name = self._window_class_name(dialog).casefold()
        title_looks_like_dialog = bool(re.search(SAVE_DIALOG_TITLE_RE, title, re.I))
        class_looks_like_dialog = class_name in {"#32770", "directuihwnd"} or "dialog" in class_name
        blank_foreground = prefer_foreground and not title
        blank_title = not title
        if not (title_looks_like_dialog or class_looks_like_dialog or blank_foreground or blank_title):
            return False

        edit = self._find_file_dialog_edit(dialog)
        action_button = self._find_dialog_action_button(dialog, SAVE_DIALOG_ACCEPT_BUTTONS)
        if self.config.verbose and blank_title:
            self._log(
                "blank save-dialog candidate check: "
                f"class={self._window_class_name(dialog)!r}, "
                f"has_edit={edit is not None}, has_action={action_button is not None}, "
                f"foreground={prefer_foreground}"
            )
        if edit is None:
            return False
        if action_button is None:
            return False
        if self.config.verbose:
            self._log(
                "save dialog candidate details: "
                f"title={title!r}, class={self._window_class_name(dialog)!r}, "
                f"foreground={prefer_foreground}"
            )
        return True

    def _window_class_name(self, window: BaseWrapper) -> str:
        try:
            return str(window.element_info.class_name or "")
        except Exception:
            return ""

    def _fill_save_dialog(self, dialog: BaseWrapper, output_path: Path) -> None:
        edit = self._find_file_dialog_edit(dialog)
        if edit is None:
            raise DesktopAutomationError("Could not find the filename field in the save-file dialog.")
        self._click_wrapper_center(edit, expected_window=dialog, purpose="click save filename field")
        time.sleep(0.2)
        self._press_ctrl_key(dialog, VK_A)
        time.sleep(0.1)
        self._press_key(dialog, VK_BACK)
        time.sleep(0.1)
        pyperclip.copy(str(output_path))
        self._paste_from_clipboard(dialog)
        time.sleep(0.5)
        self._log(f"save path pasted: {output_path}")
        self._submit_save_dialog(dialog)
        self._confirm_overwrite_if_needed()

    def _blind_save_foreground_dialog(self, output_path: Path) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        existing_files = {path.resolve() for path in output_path.parent.glob("*.png")}
        save_started_at = time.time()
        handle, title, class_name = self._foreground_window_info()
        self._log(f"blind save foreground: title={title!r}, class={class_name!r}, handle={handle}")
        if not self._is_safe_foreground_save_dialog(handle, title, class_name):
            self._log("blind save skipped because foreground is not a safe Save dialog")
            return False

        def foreground_still_safe(step: str) -> bool:
            current_handle, current_title, current_class = self._foreground_window_info()
            if current_handle != handle:
                self._log(
                    "blind save stopped before "
                    f"{step}: foreground changed to {current_title!r} "
                    f"(class={current_class!r}, handle={current_handle})"
                )
                return False
            if not self._is_safe_foreground_save_dialog(current_handle, current_title, current_class):
                self._log(
                    "blind save stopped before "
                    f"{step}: foreground is not a safe Save dialog anymore "
                    f"({current_title!r}, class={current_class!r})"
                )
                return False
            return True

        pyperclip.copy(str(output_path))
        if not foreground_still_safe("select filename"):
            return False
        self._press_ctrl_key_raw(VK_A)
        time.sleep(0.1)
        if not foreground_still_safe("clear filename"):
            return False
        self._press_key_raw(VK_BACK)
        time.sleep(0.1)
        if not foreground_still_safe("paste filename"):
            return False
        self._press_ctrl_key_raw(VK_V)
        time.sleep(0.3)

        for name, action in (
            ("Enter", lambda: self._press_key_raw(VK_RETURN)),
            ("Alt+S", lambda: self._press_alt_key_raw(VK_S)),
            ("Alt+O", lambda: self._press_alt_key_raw(VK_O)),
            ("Enter retry", lambda: self._press_key_raw(VK_RETURN)),
        ):
            self._log(f"blind save submit via {name}")
            if not foreground_still_safe(name):
                return False
            action()
            self._confirm_overwrite_if_needed()
            if self._wait_for_file_path(output_path, timeout_sec=5.0):
                self._log(f"blind save succeeded via {name}: {output_path}")
                return True
            if self._adopt_recent_saved_image(output_path, existing_files, save_started_at):
                self._log(f"blind save adopted browser filename via {name}: {output_path}")
                return True
            if not foreground_still_safe(f"post-{name} save-dialog check"):
                self._log(
                    "blind save stopped after submit because the Save dialog is no longer foreground; "
                    "not sending another shortcut into ChatGPT"
                )
                return self._wait_for_file_path(output_path, timeout_sec=2.0)
            time.sleep(0.5)
        return False

    def _adopt_recent_saved_image(
        self,
        output_path: Path,
        existing_files: set[Path],
        save_started_at: float,
    ) -> bool:
        try:
            candidates = []
            for path in output_path.parent.glob("*.png"):
                resolved = path.resolve()
                if resolved == output_path.resolve() or resolved in existing_files:
                    continue
                stat = path.stat()
                if stat.st_size <= 0 or stat.st_mtime < save_started_at - 2.0:
                    continue
                candidates.append((stat.st_mtime, path))
            if not candidates:
                return False
            candidates.sort()
            source = candidates[-1][1]
            source.replace(output_path)
            return self._wait_for_file_path(output_path, timeout_sec=1.0)
        except Exception as exc:
            self._log(f"could not adopt recent browser-saved image: {exc}")
            return False

    def _wait_for_file_path(self, path: Path, timeout_sec: float) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                if path.exists() and path.stat().st_size > 0:
                    return True
            except OSError:
                pass
            time.sleep(0.25)
        return False

    def _foreground_window_info(self) -> tuple[int, str, str]:
        try:
            user32 = ctypes.windll.user32
            handle = user32.GetForegroundWindow()
            if not handle:
                return 0, "", ""

            title_length = user32.GetWindowTextLengthW(handle)
            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(handle, title_buffer, title_length + 1)
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(handle, class_buffer, 256)
            return int(handle), title_buffer.value, class_buffer.value
        except Exception:
            return 0, "", ""

    def _is_safe_foreground_save_dialog(self, handle: int, title: str, class_name: str) -> bool:
        if not handle:
            return False
        title_cf = title.casefold()
        class_cf = class_name.casefold()
        if class_cf in {"shell_traywnd", "shell_secondarytraywnd", "progman"}:
            return False
        if class_name != "#32770" and not re.search(SAVE_DIALOG_TITLE_RE, title, re.I):
            return False
        if any(part in title_cf for part in SAVE_DIALOG_EXCLUDED_TITLE_PARTS):
            return False
        return True

    def _submit_save_dialog(self, dialog: BaseWrapper) -> None:
        save_button = self._find_dialog_action_button(dialog, SAVE_DIALOG_ACCEPT_BUTTONS)
        attempts: list[tuple[str, object]] = [("Enter", None)]
        if save_button is not None:
            self._log(f"save button candidate: {save_button.window_text()!r}")
            attempts.extend(
                [
                    ("button center click", save_button),
                    ("button invoke", save_button),
                    ("button click_input", save_button),
                ]
            )
        attempts.extend([("Alt+S", None), ("Alt+C", None), ("Alt+O", None), ("Enter retry", None)])

        for name, target in attempts:
            self._log(f"submitting save dialog via {name}")
            try:
                if name == "Enter" or name == "Enter retry":
                    self._press_enter(dialog)
                elif name == "Alt+S":
                    self._press_alt_key(dialog, VK_S)
                elif name == "Alt+C":
                    self._press_alt_key(dialog, VK_C)
                elif name == "Alt+O":
                    self._press_alt_key(dialog, VK_O)
                elif name == "button center click" and target is not None:
                    self._click_wrapper_center(  # type: ignore[arg-type]
                        target,
                        expected_window=dialog,
                        purpose="click Save dialog button",
                    )
                elif name == "button invoke" and target is not None:
                    self._invoke_wrapper(target)  # type: ignore[arg-type]
                elif name == "button click_input" and target is not None:
                    self._ensure_foreground_window(dialog, "click Save dialog button")
                    target.click_input()  # type: ignore[attr-defined]
                time.sleep(0.8)
                self._confirm_overwrite_if_needed()
                if self._wait_for_wrapper_to_close(dialog, timeout_sec=1.5):
                    self._log(f"save dialog closed after {name}")
                    return
                if not self._dialog_still_visible(dialog):
                    self._log(
                        "save dialog is no longer visible after submit; "
                        "stopping retries so no later shortcut can hit ChatGPT"
                    )
                    return
            except Exception as exc:
                self._log(f"save dialog submit attempt failed via {name}: {exc}")
        raise DesktopAutomationError("Save-file dialog stayed open after all Save attempts.")

    def _dialog_still_visible(self, dialog: BaseWrapper) -> bool:
        try:
            return bool(dialog.is_visible())
        except Exception:
            return False

    def _find_file_dialog_edit(self, dialog: BaseWrapper) -> Optional[BaseWrapper]:
        candidates = []
        for wrapper in self._safe_descendants(dialog, control_type="Edit"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                rect = wrapper.rectangle()
                if rect.width() <= 40 or rect.height() <= 10:
                    continue
                candidates.append(wrapper)
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.rectangle().bottom, item.rectangle().width() * item.rectangle().height()))
        return candidates[-1]

    def _find_dialog_action_button(
        self,
        dialog: BaseWrapper,
        patterns: Iterable[str],
    ) -> Optional[BaseWrapper]:
        matches = []
        normalized_patterns = [pattern.casefold() for pattern in patterns]
        for wrapper in self._safe_descendants(dialog, control_type="Button"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                title = wrapper.window_text().strip()
                if not title:
                    continue
                if not any(pattern in title.casefold() for pattern in normalized_patterns):
                    continue
                rect = wrapper.rectangle()
                matches.append((rect.bottom, rect.right, title, wrapper))
            except Exception:
                continue
        if not matches:
            return None
        matches.sort()
        return matches[-1][3]

    def _wait_for_wrapper_to_close(self, wrapper: BaseWrapper, timeout_sec: float = 3.0) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                if not wrapper.is_visible():
                    return True
            except Exception:
                return True
            time.sleep(0.2)
        return False

    def _invoke_wrapper(self, wrapper: BaseWrapper) -> bool:
        try:
            wrapper.invoke()
            time.sleep(0.4)
            return True
        except Exception as exc:
            self._log(f"invoke failed: {exc}")
            return False

    def _click_wrapper_center(
        self,
        wrapper: BaseWrapper,
        *,
        expected_window: Optional[BaseWrapper] = None,
        purpose: str = "click wrapper center",
        button: str = "left",
    ) -> bool:
        try:
            rect = wrapper.rectangle()
            x = rect.left + max(1, rect.width() // 2)
            y = rect.top + max(1, rect.height() // 2)
            self._click_screen_point(
                x,
                y,
                expected_window=expected_window,
                purpose=purpose,
                button=button,
            )
            self._log(f"clicked wrapper center: x={x}, y={y}, title={wrapper.window_text()!r}")
            time.sleep(0.4)
            return True
        except Exception as exc:
            self._log(f"wrapper center click failed: {exc}")
            return False

    def _confirm_overwrite_if_needed(self) -> None:
        try:
            desktop = Desktop(backend="uia")
            for dialog in self._safe_windows_query(desktop, visible_only=True):
                title = dialog.window_text()
                if not re.search(r"(Confirm|Replace|Подтверж|Замен|Сохран)", title, re.I):
                    continue
                button = self._find_button(dialog, ("Yes", "Replace", "Да", "Заменить"))
                if button is not None:
                    self._ensure_foreground_window(dialog, "confirm overwrite dialog")
                    button.click_input()
                    return
        except Exception:
            return

    def _wait_for_dialog_or_attach_menu(self, window: BaseWrapper) -> BaseWrapper:
        deadline = time.time() + self.config.dialog_timeout_sec
        last_error: Optional[Exception] = None
        activated_menu_items: set[str] = set()
        attach_button_retries = 0
        while time.time() < deadline:
            try:
                return self._wait_for_dialog(timeout_sec=1.0)
            except Exception as exc:  # pragma: no cover - GUI specific
                last_error = exc

            if self._activate_attach_menu_item(window, activated_menu_items):
                try:
                    return self._wait_for_dialog(timeout_sec=3.0)
                except Exception as exc:  # pragma: no cover - GUI specific
                    last_error = exc

            menu_button = self._find_page_button(window, ATTACH_UPLOAD_MENU_PATTERNS)
            if menu_button is not None:
                self._ensure_foreground_window(window, "click ChatGPT attachment menu item")
                if not self._click_wrapper_center(
                    menu_button,
                    expected_window=window,
                    purpose="click ChatGPT attachment menu item",
                ):
                    menu_button.click_input()
                time.sleep(0.5)
                try:
                    return self._wait_for_dialog(timeout_sec=3.0)
                except Exception as exc:  # pragma: no cover - GUI specific
                    last_error = exc

            if attach_button_retries < 2:
                attach_button = self._find_page_button(window, ATTACH_BUTTON_PATTERNS)
                if attach_button is not None:
                    attach_button_retries += 1
                    self._log(f"retrying ChatGPT attach plus button: attempt {attach_button_retries}")
                    self._ensure_foreground_window(window, "retry click ChatGPT attach plus button")
                    if not self._click_wrapper_center(
                        attach_button,
                        expected_window=window,
                        purpose="retry click ChatGPT attach plus button",
                    ):
                        attach_button.click_input()
                    try:
                        return self._wait_for_dialog(timeout_sec=3.0)
                    except Exception as exc:  # pragma: no cover - GUI specific
                        last_error = exc
            time.sleep(0.5)
        raise DesktopAutomationError("Open-file dialog did not appear.") from last_error

    def _activate_attach_menu_item(self, window: BaseWrapper, activated_items: set[str]) -> bool:
        desktop = Desktop(backend="uia")
        menu_items: list[tuple[str, BaseWrapper]] = []
        window_rect = window.rectangle()
        anchor_x, anchor_y = self._attach_menu_anchor_point(window)
        for popup in desktop.windows(visible_only=True):
            try:
                popup_title = popup.window_text().casefold()
                popup_class = self._window_class_name(popup).casefold()
                if self._is_non_chatgpt_attach_popup_text(popup_title) or "consolewindowclass" in popup_class:
                    continue
                popup_rect = popup.rectangle()
                if not self._rects_overlap(popup_rect, window_rect, margin=20):
                    continue
            except Exception:
                continue
            for control_type in ("MenuItem", "Button", "Text"):
                try:
                    descendants = popup.descendants(control_type=control_type)
                except Exception:
                    continue
                for item in descendants:
                    try:
                        if not item.is_visible():
                            continue
                        rect = item.rectangle()
                        if not self._is_compact_attach_menu_item_rect(rect):
                            continue
                        if not self._rect_center_inside(rect, window_rect):
                            if self.config.verbose:
                                self._log(
                                    "skipping attach popup item outside selected ChatGPT window: "
                                    f"{self._wrapper_rect_text(item)}, title={self._control_label(item)!r}"
                                )
                            continue
                        title = self._control_search_text(item).strip()
                        if not title:
                            continue
                        if self._is_non_chatgpt_attach_popup_text(title):
                            continue
                        menu_items.append((title, item))
                    except Exception:
                        continue
        if self.config.verbose and menu_items:
            self._log(f"attach popup items: {[title for title, _ in menu_items][:20]}")
        excluded = (
            "add files and more",
            "добавляйте файлы",
            "bookmark",
            "заклад",
            "extension",
            "расшир",
            "chrome",
            "powershell",
            "command prompt",
            "terminal",
            "console",
            "copyright",
            "directory:",
            "can't open file",
            "python.exe",
            "claude",
            "api key",
        )
        matches: list[tuple[float, str, BaseWrapper]] = []
        for pattern in ATTACH_UPLOAD_MENU_PATTERNS:
            normalized = pattern.casefold()
            for title, item in menu_items:
                title_cf = title.casefold()
                if normalized not in title_cf:
                    continue
                if any(token in title_cf for token in excluded):
                    continue
                try:
                    rect = item.rectangle()
                    if not self._is_compact_attach_menu_item_rect(rect):
                        continue
                    center_x = rect.left + rect.width() // 2
                    center_y = rect.top + rect.height() // 2
                    distance = abs(center_x - anchor_x) + abs(center_y - anchor_y)
                    if "open file" in title_cf or "access open file" in title_cf:
                        distance -= 1000
                    if center_y < window_rect.top + window_rect.height() * 0.16:
                        distance += 10000
                    matches.append((float(distance), title, item))
                except Exception:
                    continue
        matches.sort(key=lambda candidate: candidate[0])
        if self.config.verbose and matches:
            self._log(
                "attach upload menu matches: "
                f"{[(round(score, 1), self._wrapper_rect_text(item), title[:80]) for score, title, item in matches[:8]]}"
            )
        for score, title, item in matches:
            try:
                rect = item.rectangle()
                if not self._is_compact_attach_menu_item_rect(rect):
                    self._log(
                        "skipping stale attach menu item with invalid geometry: "
                        f"{self._wrapper_rect_text(item)}, title={title[:80]!r}"
                    )
                    continue
                rect_key = f"{rect.left},{rect.top},{rect.right},{rect.bottom}"
            except Exception:
                continue
            key = f"{' '.join(title.casefold().split())}|{rect_key}"
            if key in activated_items:
                continue
            activated_items.add(key)
            self._log(
                f"activating attach menu item: {title[:120]!r} "
                f"at {self._wrapper_rect_text(item)} score={score:.1f}"
            )
            if self._activate_attach_menu_item_by_keyboard(window, title):
                return True
            if self._click_exact_open_file_menu_item(window, title, item):
                return True
            if self._click_attach_menu_row(window, item):
                return True
            try:
                self._ensure_foreground_window(window, "click ChatGPT attach menu item")
                item.click_input()
                time.sleep(0.5)
                try:
                    self._find_open_dialog()
                    return True
                except Exception:
                    pass
            except Exception as exc:
                self._log(f"attach menu item click_input failed: {exc}")
        return False

    def _is_non_chatgpt_attach_popup_text(self, text: str) -> bool:
        text_cf = text.casefold()
        if "\r" in text_cf or "\n" in text_cf or len(text_cf) > 260:
            return True
        blocked = (
            "windows powershell",
            "powershell",
            "command prompt",
            "terminal",
            "copyright",
            "directory:",
            "can't open file",
            "python.exe",
            "api key",
            "claude api",
        )
        return any(token in text_cf for token in blocked)

    def _is_compact_attach_menu_item_rect(self, rect) -> bool:
        width = rect.width()
        height = rect.height()
        if width <= 0 or height <= 0:
            return False
        return height <= 90 and width <= 760

    def _activate_attach_menu_item_by_keyboard(self, window: BaseWrapper, _title: str) -> bool:
        title_cf = _title.casefold()
        attempts: list[tuple[str, str]] = []
        if "control u" in title_cf or "ctrl+u" in title_cf or "контролировать u" in title_cf:
            attempts.append(("Ctrl+U", "^u"))

        for label, keys in attempts:
            try:
                self._ensure_foreground_window(window, f"activate ChatGPT attach menu item with {label}")
                self._log(f"trying attach menu keyboard activation: {label}")
                send_keys(keys)
                time.sleep(0.7)
                self._find_open_dialog()
                return True
            except Exception as exc:
                self._log(f"attach menu keyboard activation {label} did not open dialog: {exc}")
        return False

    def _click_exact_open_file_menu_item(self, window: BaseWrapper, title: str, item: BaseWrapper) -> bool:
        normalized = " ".join(title.casefold().replace(".", " ").split())
        if normalized not in {"open file", "open files", "access open file"}:
            return False
        try:
            self._ensure_foreground_window(window, "click exact Open file menu item")
            self._log("clicking exact Open file menu item via UIA click_input")
            item.click_input()
            time.sleep(0.7)
            self._find_open_dialog()
            return True
        except Exception as exc:
            self._log(f"exact Open file menu item click did not open dialog: {exc}")
            return False

    def _attach_menu_anchor_point(self, window: BaseWrapper) -> tuple[int, int]:
        try:
            button = self._find_page_button(window, ATTACH_BUTTON_PATTERNS)
            if button is not None:
                rect = button.rectangle()
                return (rect.left + rect.width() // 2, rect.top + rect.height() // 2)
        except Exception:
            pass
        try:
            prompt_input = self._find_prompt_input(window)
            if prompt_input is not None:
                rect = prompt_input.rectangle()
                return (rect.left, rect.top + rect.height() // 2)
        except Exception:
            pass
        rect = window.rectangle()
        return (rect.left + rect.width() // 2, rect.bottom - 120)

    def _click_attach_menu_row(self, window: BaseWrapper, item: BaseWrapper) -> bool:
        try:
            rect = item.rectangle()
            window_rect = window.rectangle()
            y = rect.top + max(1, rect.height() // 2)
            x_candidates = [
                rect.left + max(1, rect.width() // 2),
                rect.left + 20,
                rect.left - 30,
                rect.left - 80,
                rect.left - 140,
                rect.right + 30,
            ]
            for x in x_candidates:
                if x <= 0 or y <= 0:
                    continue
                if not self._point_inside_rect(int(x), int(y), window_rect):
                    self._log(
                        "skipping attach menu row click outside selected ChatGPT window: "
                        f"x={int(x)}, y={int(y)}, title={item.window_text()!r}"
                    )
                    continue
                self._click_screen_point(
                    int(x),
                    int(y),
                    expected_window=window,
                    purpose="click ChatGPT attach menu row",
                )
                self._log(
                    "clicked attach menu row candidate: "
                    f"x={int(x)}, y={int(y)}, title={item.window_text()!r}"
                )
                time.sleep(0.5)
                try:
                    self._find_open_dialog()
                    return True
                except Exception:
                    pass
            return False
        except Exception as exc:
            self._log(f"attach menu row click failed: {exc}")
            return False

    def _fill_open_dialog(self, dialog: BaseWrapper, image_path: Path) -> None:
        edit = self._find_descendant(dialog, control_type="Edit")
        if edit is None:
            self._log("open filename field was not visible to UIA; trying blind open dialog fill")
            self._blind_fill_open_dialog(dialog, image_path)
            return
        try:
            self._click_wrapper_center(edit, expected_window=dialog, purpose="click open filename field")
            self._press_ctrl_key(dialog, VK_A)
            time.sleep(0.1)
            self._press_key(dialog, VK_BACK)
            pyperclip.copy(str(image_path))
            self._paste_from_clipboard(dialog)
            time.sleep(0.2)

            open_button = self._find_button(dialog, OPEN_DIALOG_BUTTONS)
            if open_button is not None:
                self._click_wrapper_center(dialog, expected_window=dialog, purpose="focus open dialog")
                self._click_wrapper_center(open_button, expected_window=dialog, purpose="click Open dialog button")
            else:
                self._press_enter(dialog)
            if self._wait_for_wrapper_to_close(dialog, timeout_sec=3.0):
                return
            self._log("open dialog stayed visible after normal fill; trying blind open dialog fill")
        except Exception as exc:
            self._log(f"normal open dialog fill failed: {exc}; trying blind open dialog fill")
        self._blind_fill_open_dialog(dialog, image_path)

    def _blind_fill_open_dialog(self, dialog: BaseWrapper, image_path: Path) -> None:
        self._ensure_foreground_window(dialog, "blind fill open dialog")
        pyperclip.copy(str(image_path))
        attempts = [
            ("Alt+N paste Enter", lambda: (self._press_alt_key(dialog, VK_N), time.sleep(0.2), self._paste_from_clipboard(dialog), self._press_enter(dialog))),
            ("Ctrl+L paste Enter", lambda: (send_keys("^l"), time.sleep(0.2), self._paste_from_clipboard(dialog), self._press_enter(dialog))),
            ("filename paste Enter", lambda: (self._paste_from_clipboard(dialog), self._press_enter(dialog))),
            ("Alt+O", lambda: self._press_alt_key(dialog, VK_O)),
            ("Enter retry", lambda: self._press_enter(dialog)),
        ]
        for name, action in attempts:
            self._log(f"open dialog blind submit via {name}")
            try:
                self._ensure_foreground_window(dialog, f"open dialog {name}")
                action()
                time.sleep(1.0)
                if self._wait_for_wrapper_to_close(dialog, timeout_sec=2.0):
                    self._log(f"open dialog closed after {name}")
                    return
            except Exception as exc:
                self._log(f"open dialog blind attempt failed via {name}: {exc}")
        raise DesktopAutomationError("Open-file dialog stayed open after all file path submit attempts.")

    def _safe_descendants(self, window: BaseWrapper, *, control_type: Optional[str] = None) -> list[BaseWrapper]:
        try:
            return list(window.descendants(control_type=control_type))
        except Exception as exc:
            label = control_type or "any"
            self._log(f"could not enumerate UIA descendants ({label}); ignoring unstable Chrome element: {exc}")
            return []

    def _find_prompt_input(self, window: BaseWrapper) -> Optional[BaseWrapper]:
        candidates = []
        window_rect = window.rectangle()
        for control_type in ("Edit", "Document"):
            for wrapper in self._safe_descendants(window, control_type=control_type):
                try:
                    rect = wrapper.rectangle()
                    if rect.width() <= 0 or rect.height() <= 0:
                        continue
                    if not wrapper.is_visible() or not wrapper.is_enabled():
                        continue
                    if rect.top < window_rect.top + window_rect.height() * 0.12:
                        continue
                    if rect.width() < max(180, window_rect.width() * 0.06):
                        continue
                    if rect.width() >= window_rect.width() * 0.95 and rect.height() >= window_rect.height() * 0.45:
                        continue
                    text = " ".join(
                        part
                        for part in (
                            wrapper.window_text(),
                            getattr(wrapper.element_info, "name", "") or "",
                        )
                        if part
                    ).casefold()
                    score = rect.bottom / 1000.0 + (rect.width() * rect.height()) / 1_000_000.0
                    if control_type == "Edit":
                        score += 100.0
                    if any(marker in text for marker in ("message", "ask", "prompt", "спрос", "сообщ")):
                        score += 50.0
                    if rect.height() > 220:
                        score -= 20.0
                    candidates.append((score, wrapper))
                except Exception:
                    continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    def _collect_visible_text(self, window: BaseWrapper) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for control_type in ("Text", "Document", "Edit"):
            for wrapper in self._safe_descendants(window, control_type=control_type):
                try:
                    if not wrapper.is_visible():
                        continue
                    # Avoid expensive rich-text calls that can hang on unstable UIA nodes.
                    text = (getattr(wrapper.element_info, "name", "") or "").strip()
                    if not text:
                        continue
                    if text in seen:
                        continue
                    seen.add(text)
                    lines.append(text)
                except Exception:
                    continue
        return "\n".join(lines)

    def _collect_attachment_surface_text(self, window: BaseWrapper) -> str:
        lines = [self._collect_visible_text(window)]
        seen = {line for line in lines if line}
        for control_type in ("Button", "Image"):
            for wrapper in self._safe_descendants(window, control_type=control_type):
                try:
                    if not wrapper.is_visible():
                        continue
                    text = self._control_search_text(wrapper).strip()
                    if not text or text in seen:
                        continue
                    seen.add(text)
                    lines.append(text)
                except Exception:
                    continue
        return "\n".join(line for line in lines if line)

    def _save_text_snapshot(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _find_result_image(self, window: BaseWrapper) -> Optional[BaseWrapper]:
        candidates = self._find_result_images(window)
        if not candidates:
            return None
        return candidates[-1]

    def _find_result_images(self, window: BaseWrapper) -> list[BaseWrapper]:
        candidates = []
        window_rect = window.rectangle()
        for wrapper in self._safe_descendants(window, control_type="Image"):
            try:
                rect = wrapper.rectangle()
                if rect.width() < 128 or rect.height() < 128:
                    continue
                if not wrapper.is_visible():
                    continue
                if not self._rect_center_inside(rect, window_rect):
                    continue
                candidates.append(wrapper)
            except Exception:
                continue
        candidates.sort(key=lambda item: (item.rectangle().bottom, item.rectangle().width() * item.rectangle().height()))
        return candidates

    def _find_attachment_image_candidates(self, window: BaseWrapper) -> list[BaseWrapper]:
        candidates = []
        window_rect = window.rectangle()
        for wrapper in self._safe_descendants(window, control_type="Image"):
            try:
                rect = wrapper.rectangle()
                if rect.width() < 24 or rect.height() < 24:
                    continue
                if not wrapper.is_visible():
                    continue
                if not self._rect_center_inside(rect, window_rect):
                    continue
                candidates.append(wrapper)
            except Exception:
                continue
        candidates.sort(key=lambda item: (item.rectangle().bottom, item.rectangle().width() * item.rectangle().height()))
        return candidates

    def _result_image_is_usable(self, window: BaseWrapper, wrapper: BaseWrapper) -> bool:
        try:
            if not wrapper.is_visible():
                return False
            rect = wrapper.rectangle()
            if rect.width() < 128 or rect.height() < 128:
                return False
            window_rect = window.rectangle()
            if rect.top < window_rect.top + window_rect.height() * 0.12:
                return False
            return self._rect_center_inside(rect, window_rect)
        except Exception:
            return False

    def _rect_center_inside(self, rect, container_rect) -> bool:
        center_x = rect.left + rect.width() // 2
        center_y = rect.top + rect.height() // 2
        return (
            container_rect.left <= center_x <= container_rect.right
            and container_rect.top <= center_y <= container_rect.bottom
        )

    def _point_inside_rect(self, x: int, y: int, rect) -> bool:
        return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom

    def _rects_overlap(self, first, second, *, margin: int = 0) -> bool:
        return not (
            first.right < second.left - margin
            or first.left > second.right + margin
            or first.bottom < second.top - margin
            or first.top > second.bottom + margin
        )

    def _wrapper_rect_text(self, wrapper: BaseWrapper) -> str:
        try:
            rect = wrapper.rectangle()
            return f"x={rect.left}, y={rect.top}, w={rect.width()}, h={rect.height()}"
        except Exception as exc:
            return f"<unavailable: {exc}>"

    def _result_signatures(self, wrappers: Iterable[BaseWrapper]) -> list[ImageSignature]:
        signatures: list[ImageSignature] = []
        for wrapper in wrappers:
            signature = self._result_signature(wrapper)
            if signature is not None:
                signatures.append(signature)
        return signatures

    def _result_signature_digest(self, signature: ImageSignature) -> int:
        return int(signature[-1]) if signature else 0

    def _signature_matches_baseline(
        self,
        signature: ImageSignature,
        *,
        baseline_set: set[ImageSignature],
        baseline_digests: set[int],
    ) -> bool:
        if signature in baseline_set:
            return True
        digest = self._result_signature_digest(signature)
        return bool(digest and digest in baseline_digests)

    def _result_signature(self, wrapper: Optional[BaseWrapper]) -> Optional[ImageSignature]:
        if wrapper is None:
            return None
        rect = wrapper.rectangle()
        digest = 0
        try:
            image = wrapper.capture_as_image()
            thumb = image.convert("RGB").resize((16, 16))
            digest = zlib.adler32(thumb.tobytes()) & 0xFFFFFFFF
        except Exception as exc:
            self._log(f"could not hash result image content: {exc}")
        return (rect.left, rect.top, rect.right, rect.bottom, digest)

    def _find_button(self, window: BaseWrapper, patterns: Iterable[str]) -> Optional[BaseWrapper]:
        buttons = []
        for wrapper in self._safe_descendants(window, control_type="Button"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                title = wrapper.window_text().strip()
                name = (getattr(wrapper.element_info, "name", "") or "").strip()
                label = title or name
                searchable = " ".join(part for part in (title, name) if part)
                buttons.append((label, searchable, wrapper))
            except Exception:
                continue
        if self.config.verbose:
            self._log(f"visible buttons: {[label for label, _, _ in buttons if label][:30]}")

        for pattern in patterns:
            normalized = pattern.casefold()
            for _, searchable, wrapper in buttons:
                if normalized in searchable.casefold():
                    return wrapper
        return None

    def _wait_for_attach_button(self, window: BaseWrapper) -> Optional[BaseWrapper]:
        deadline = time.time() + 20.0
        while time.time() < deadline:
            button = self._find_page_button(window, ATTACH_BUTTON_PATTERNS)
            if button is not None:
                return button
            time.sleep(0.5)
        return None

    def _find_page_button(self, window: BaseWrapper, patterns: Iterable[str]) -> Optional[BaseWrapper]:
        window_rect = window.rectangle()
        prompt_input = self._find_prompt_input(window)
        prompt_rect = None
        try:
            prompt_rect = prompt_input.rectangle() if prompt_input is not None else None
        except Exception:
            prompt_rect = None
        excluded = (
            "bookmark",
            "bookmarks",
            "extension",
            "extensions",
            "chrome",
            "google keep",
            "account",
            "profile",
            "plan",
            "sergey",
            "freidman",
            "tab",
            "tabs",
            "page",
            "аккаунт",
            "профил",
            "изображение профиля",
            "план",
            "заклад",
            "расшир",
            "страниц",
            "вклад",
            "групп",
            "keep",
        )
        normalized_patterns = [pattern.casefold() for pattern in patterns]
        candidates: list[tuple[float, BaseWrapper]] = []
        for wrapper in self._safe_descendants(window, control_type="Button"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                rect = wrapper.rectangle()
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                if rect.top < window_rect.top + window_rect.height() * 0.16:
                    continue
                if not self._rect_center_inside(rect, window_rect):
                    continue
                searchable = self._control_search_text(wrapper).casefold()
                if any(token in searchable for token in excluded):
                    continue
                if not any(pattern in searchable for pattern in normalized_patterns):
                    continue
                score = rect.bottom / 100.0
                if prompt_rect is not None:
                    horizontal_near = (
                        prompt_rect.left - 180 <= rect.left <= prompt_rect.right + 180
                        or prompt_rect.left - 180 <= rect.right <= prompt_rect.right + 180
                    )
                    vertical_gap = min(abs(rect.bottom - prompt_rect.top), abs(rect.top - prompt_rect.bottom))
                    if horizontal_near and vertical_gap <= 220:
                        score += 1000.0
                    else:
                        score -= 500.0
                candidates.append((score, wrapper))
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        if self.config.verbose:
            self._log(
                "page attach/menu candidates: "
                f"{[(round(score, 1), self._wrapper_rect_text(wrapper), self._control_label(wrapper)) for score, wrapper in candidates[-5:]]}"
            )
        return candidates[-1][1]

    def _find_tab(self, window: BaseWrapper, title_re: str) -> Optional[BaseWrapper]:
        pattern = re.compile(title_re)
        candidates = []
        for wrapper in self._safe_descendants(window, control_type="TabItem"):
            try:
                title = wrapper.window_text().strip()
                if not title:
                    continue
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                if pattern.search(title):
                    candidates.append(wrapper)
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.rectangle().left)
        return candidates[-1]

    def _visible_tab_titles(self, window: BaseWrapper) -> list[str]:
        candidates: list[tuple[int, str]] = []
        for wrapper in self._safe_descendants(window, control_type="TabItem"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                title = wrapper.window_text().strip()
                if not title:
                    continue
                candidates.append((wrapper.rectangle().left, title))
            except Exception:
                continue
        candidates.sort(key=lambda item: item[0])
        return [title for _, title in candidates]

    def _has_single_visible_browser_tab(self, window: BaseWrapper) -> bool:
        return len(self._visible_tab_titles(window)) == 1

    def _find_descendant(self, window: BaseWrapper, *, control_type: str) -> Optional[BaseWrapper]:
        for wrapper in self._safe_descendants(window, control_type=control_type):
            try:
                if wrapper.is_visible() and wrapper.is_enabled():
                    return wrapper
            except Exception:
                continue
        return None
