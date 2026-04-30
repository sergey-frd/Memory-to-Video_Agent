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


WINDOW_TITLE_RE = ".*ChatGPT.*"
ATTACH_BUTTON_PATTERNS = (
    "Add photos and files",
    "Add files",
    "Add photos",
    "Add files and more",
    "Attach",
    "Upload",
    "Open file picker",
    "Plus",
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
    "Choose file",
    "Add photos and files",
    "Add files",
    "Фото и файлы",
    "Загрузить с компьютера",
    "Загрузить файл",
    "Выбрать файл",
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
)
SEND_BUTTON_PATTERNS = ("Send", "Submit", "Отправить")
CHATGPT_WINDOW_TITLE_MARKERS = (
    "chatgpt",
    "portrait",
    "portrait generation",
    "portrait request",
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
)
CHATGPT_WINDOW_EXCLUDED_TITLE_PARTS = (
    "command prompt",
    "powershell",
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
NEW_CHAT_BUTTON_PATTERNS = (
    "New chat",
    "New Chat",
    "Start new chat",
    "Новый чат",
    "Создать чат",
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
VK_MENU = 0x12
VK_O = 0x4F
VK_RETURN = 0x0D
VK_S = 0x53
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


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
    path_text = str(image_path.resolve())
    payload = (path_text + "\0\0").encode("utf-16le")
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
    use_active_window: bool = False
    attach_via_clipboard: bool = False
    skip_capture_result: bool = False
    save_result_via_context_menu: bool = False
    click_composer_before_paste: bool = False
    manual_composer_position: Optional[tuple[int, int]] = None
    manual_send_position: Optional[tuple[int, int]] = None
    manual_send_capture_delay_sec: float = 0.0
    manual_send_min_distance_px: int = 50
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
        if self.config.open_new_chat_before_run:
            self._log("opening a new chat")
            self._open_new_chat(self._window)
        if self.config.image_path is not None:
            self._log(f"attaching image: {self.config.image_path.name}")
            self._attach_image(self._window, self.config.image_path)
            self._log("image attached")
        self._log("pasting prompt")
        self._paste_prompt(self._window, self.config.prompt_text)
        if self.config.submit:
            self._capture_manual_send_position_after_paste()
            self._log("capturing baseline state")
            baseline_signatures = self._result_signatures(self._find_result_images(self._window))
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
            if not self._looks_like_chatgpt_window(window):
                title = window.window_text()
                self._log(f"active window is not ChatGPT: {title!r}; searching ChatGPT window")
                if self.config.manual_composer_position is not None:
                    found = self._find_visible_chatgpt_window()
                    found_titles = [] if found is None else [self._window_title(found)]
                    if found_titles:
                        self._log(f"visible ChatGPT window candidates: {found_titles}")
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
        if self._looks_like_chatgpt_window(window):
            return
        raise DesktopAutomationError(
            "The active window is not the ChatGPT browser window. "
            f"Active window title: {title!r}. "
            "Start the batch again, then activate the ChatGPT window and click the message box during the countdown."
        )

    def _window_title(self, window: BaseWrapper) -> str:
        try:
            return window.window_text()
        except Exception:
            return ""

    def _looks_like_chatgpt_window(self, window: BaseWrapper) -> bool:
        title = self._window_title(window).casefold()
        if any(part in title for part in CHATGPT_WINDOW_EXCLUDED_TITLE_PARTS):
            return False
        if any(marker in title for marker in CHATGPT_WINDOW_TITLE_MARKERS):
            return True
        tab_re = self.config.browser_tab_title_re or ".*(ChatGPT|Portrait Request).*"
        try:
            return self._find_tab(window, tab_re) is not None
        except Exception:
            return False

    def _find_visible_chatgpt_window(self) -> Optional[BaseWrapper]:
        if Desktop is None:
            return None
        candidates = []
        for window in Desktop(backend="uia").windows(visible_only=True):
            try:
                if self._looks_like_chatgpt_window(window):
                    candidates.append(window)
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: (self._chatgpt_window_score(item), item.rectangle().width() * item.rectangle().height()))
        self._log(
            "found ChatGPT window candidates: "
            f"{[(self._window_title(item), self._chatgpt_window_score(item)) for item in candidates]}"
        )
        return candidates[-1]

    def _chatgpt_window_score(self, window: BaseWrapper) -> int:
        title = self._window_title(window).casefold()
        score = 0
        if "google chrome" in title:
            score += 10
        if "chatgpt" in title:
            score += 20
        if "portrait request" in title:
            score += 40
        if "portrait generation" in title:
            score += 60
        if "watercolor portrait" in title or "pastel portrait" in title:
            score += 80
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
                tab.click_input()
                time.sleep(1.0)
                self._log("browser tab activated")
                return

        if self.config.target_url:
            self._navigate_to_url(self.config.target_url)
            time.sleep(2.0)
            return

        raise DesktopAutomationError(
            f"Could not find a browser tab matching '{self.config.browser_tab_title_re}'."
        )

    def _open_new_chat(self, window: BaseWrapper) -> None:
        if self.config.target_url:
            self._open_new_browser_tab(self.config.target_url)
            time.sleep(self.config.post_new_chat_delay_sec)
            return

        button = self._find_button(window, NEW_CHAT_BUTTON_PATTERNS)
        if button is not None:
            button.click_input()
            time.sleep(self.config.post_new_chat_delay_sec)
            return

        baseline_text = self._collect_visible_text(window=window)
        send_keys("^+o")
        deadline = time.time() + self.config.new_chat_timeout_sec
        while time.time() < deadline:
            current_text = self._collect_visible_text(window=window)
            if current_text != baseline_text:
                time.sleep(self.config.post_new_chat_delay_sec)
                return
            time.sleep(0.5)
        raise DesktopAutomationError("Could not open a new chat in the ChatGPT desktop window.")

    def _open_new_browser_tab(self, url: str) -> None:
        send_keys("^t")
        time.sleep(0.4)
        self._navigate_to_url(url)

    def _navigate_to_url(self, url: str) -> None:
        send_keys("^l")
        time.sleep(0.2)
        pyperclip.copy(url)
        send_keys("^v")
        time.sleep(0.1)
        send_keys("{ENTER}")

    def _attach_image(self, window: BaseWrapper, image_path: Path) -> None:
        if self.config.attach_via_clipboard:
            self._log("attaching image via Windows clipboard")
            self._paste_file_clipboard(window, image_path)
            time.sleep(self.config.post_attach_delay_sec)
            return

        attach_button = self._find_button(window, ATTACH_BUTTON_PATTERNS)
        if attach_button is None:
            raise DesktopAutomationError(
                "Could not find the attach/upload button in the ChatGPT desktop window."
            )
        attach_button.click_input()

        dialog = self._wait_for_dialog_or_attach_menu(window)
        self._fill_open_dialog(dialog, image_path)
        time.sleep(self.config.post_attach_delay_sec)

    def _paste_file_clipboard(self, window: BaseWrapper, image_path: Path) -> None:
        if self._preserve_manual_composer_focus() and not self.config.click_composer_before_paste:
            self._click_manual_composer_position(window)
        elif not self._preserve_manual_composer_focus() or self.config.click_composer_before_paste:
            self._focus_prompt_input_or_composer(window)
        _copy_file_to_windows_clipboard(image_path)
        self._paste_from_clipboard(window)
        self._log("file paste shortcut sent")
        time.sleep(2.5)

    def _paste_prompt(self, window: BaseWrapper, prompt_text: str) -> None:
        self._close_unexpected_open_dialog_if_needed()
        if not self._preserve_manual_composer_focus() or self.config.click_composer_before_paste:
            self._focus_prompt_input_or_composer(window)
        pyperclip.copy(prompt_text)
        if not self.config.attach_via_clipboard:
            send_keys("^a")
            time.sleep(0.1)
            self._press_key(window, VK_BACK)
            time.sleep(0.1)
        self._paste_from_clipboard(window)
        self._log("prompt paste shortcut sent")
        time.sleep(self.config.post_paste_delay_sec)

    def _focus_prompt_input_or_composer(self, window: BaseWrapper) -> bool:
        input_box = self._find_prompt_input(window)
        if input_box is not None:
            try:
                input_box.click_input()
                rect = input_box.rectangle()
                self._log(
                    "clicked detected prompt input: "
                    f"x={rect.left}, y={rect.top}, w={rect.width()}, h={rect.height()}"
                )
                time.sleep(0.3)
                return True
            except Exception as exc:
                self._log(f"could not click detected prompt input: {exc}")
        return self._click_composer_area(window)

    def _paste_from_clipboard(self, window: BaseWrapper) -> None:
        self._restore_foreground_window(window)
        self._press_ctrl_key(window, VK_V)
        time.sleep(0.4)

    def _press_enter(self, window: BaseWrapper) -> None:
        self._restore_foreground_window(window)
        self._press_key(window, VK_RETURN)

    def _close_chatgpt_overlay_if_needed(self, window: BaseWrapper) -> None:
        self._restore_foreground_window(window)
        self._press_key_raw(VK_ESCAPE)
        self._log("sent Esc to close any open ChatGPT image overlay")
        time.sleep(0.4)

    def _press_ctrl_enter(self, window: BaseWrapper) -> None:
        self._restore_foreground_window(window)
        self._press_ctrl_key(window, VK_RETURN)

    def _press_alt_key(self, window: BaseWrapper, virtual_key: int) -> None:
        self._restore_foreground_window(window)
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
        self._restore_foreground_window(window)
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
        self._restore_foreground_window(window)
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
            self._log(f"could not restore ChatGPT foreground window: {exc}")

    def _click_composer_area(self, window: BaseWrapper) -> bool:
        try:
            rect = window.rectangle()
            x = max(20, rect.width() // 2)
            y = max(80, rect.height() - 115)
            window.click_input(coords=(x, y))
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
            self._restore_foreground_window(window)
            x, y = self._manual_click_position
            self._click_screen_point(x, y)
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
            self._restore_foreground_window(window)
            self._click_screen_point(target_x, target_y)
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

    def _click_screen_point(self, x: int, y: int) -> None:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.1)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def _close_unexpected_open_dialog_if_needed(self) -> None:
        try:
            dialog = self._find_open_dialog()
        except Exception:
            return
        self._log("unexpected open-file dialog is still visible; closing it before prompt paste")
        try:
            dialog.type_keys("{ESC}")
        except Exception:
            send_keys("{ESC}")
        time.sleep(0.5)

    def _submit_prompt(self, window: BaseWrapper) -> None:
        if self._preserve_manual_composer_focus() and not self.config.click_composer_before_paste:
            self._restore_foreground_window(window)
            for target_x, target_y in self._manual_send_button_positions(window):
                self._click_manual_send_button(window, target_x, target_y)
                self._log("send arrow click attempted")
                time.sleep(1.2)
                if not self._composer_still_has_text(window):
                    self._log("composer text is gone or not visible after send arrow click")
                    return
                self._log("composer still has text after send arrow click; trying next send-arrow position")
            self._log("trying keyboard submit without changing focus")
            self._press_ctrl_enter(window)
            time.sleep(1.0)
            if not self._composer_still_has_text(window):
                self._log("composer text is gone or not visible after Ctrl+Enter")
                return
            self._log(
                "composer text is still visible after Ctrl+Enter; continuing to result wait "
                "because ChatGPT may keep stale composer text while generation is already running"
            )
            return
        send_button = self._find_button(window, SEND_BUTTON_PATTERNS)
        if send_button is not None:
            send_button.click_input()
            time.sleep(0.8)
            return
        self._log("send button not found; pressing Enter")
        self._press_enter(window)
        time.sleep(0.8)
        if self._composer_still_has_text(window):
            self._log("composer still has text after Enter; pressing Ctrl+Enter")
            self._press_ctrl_enter(window)
            time.sleep(0.8)
            if self._composer_still_has_text(window):
                raise DesktopAutomationError(
                    "The prompt still appears to be in the composer after Enter and Ctrl+Enter. "
                    "The paste/submit focus is probably not inside the ChatGPT message box."
                )
        else:
            self._log("composer text is gone or not visible after Enter")

    def _composer_still_has_text(self, window: BaseWrapper) -> bool:
        input_box = self._find_prompt_input(window)
        if input_box is None:
            return False
        try:
            text = input_box.window_text().strip()
            return bool(text)
        except Exception:
            return False

    def _save_result_image(self, window: BaseWrapper, baseline_signatures: list[ImageSignature]) -> None:
        result_image = self._wait_for_result_image(window, baseline_signatures)
        output_path = self.config.output_path
        if output_path is None:
            raise DesktopAutomationError("Output path for desktop result is not configured.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result_image.capture_as_image().save(output_path)

    def _save_result_image_via_context_menu(
        self,
        window: BaseWrapper,
        baseline_signatures: list[ImageSignature],
    ) -> None:
        result_image = self._wait_for_result_image(window, baseline_signatures)
        output_path = self.config.output_path
        if output_path is None:
            raise DesktopAutomationError("Output path for desktop result is not configured.")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self._open_save_dialog_from_image(window, result_image, output_path)
        time.sleep(1.0)

    def _open_save_dialog_from_image(self, window: BaseWrapper, result_image: BaseWrapper, output_path: Path) -> None:
        attempts = (
            ("menu item", None),
            ("keyboard first item", "{DOWN}{ENTER}"),
            ("keyboard second item", "{DOWN 2}{ENTER}"),
            ("keyboard third item", "{DOWN 3}{ENTER}"),
        )
        last_error: Optional[Exception] = None
        for attempt_name, fallback_keys in attempts:
            self._log(f"opening browser image context menu: {attempt_name}")
            self._restore_foreground_window(window)
            if not self._result_image_is_usable(window, result_image):
                raise DesktopAutomationError("Accepted result image is no longer visible inside the ChatGPT window.")
            self._log(f"right-clicking result image: {self._wrapper_rect_text(result_image)}")
            result_image.click_input(button="right")
            time.sleep(0.6)
            if fallback_keys is None:
                if not self._activate_save_image_menu_item():
                    send_keys("{ESC}")
                    time.sleep(0.3)
                    continue
            else:
                send_keys(fallback_keys)
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
                send_keys("{ESC}")
                time.sleep(0.5)
        raise DesktopAutomationError("Save-file dialog did not appear after image context-menu attempts.") from last_error

    def _save_response_text(self, window: BaseWrapper, baseline_text: str) -> None:
        response_text = self._wait_for_response_text(window, baseline_text)
        path = self.config.response_text_path
        if path is None:
            raise DesktopAutomationError("Response text path is not configured.")
        self._save_text_snapshot(path, response_text)

    def _wait_for_result_image(
        self,
        window: BaseWrapper,
        baseline_signatures: list[ImageSignature],
    ) -> BaseWrapper:
        start = time.time()
        deadline = start + self.config.result_timeout_sec
        min_ready_at = start + max(0.0, self.config.min_result_wait_sec)
        stable_wait = max(0.0, self.config.result_stable_sec)
        early_ignore_sec = min(15.0, max(5.0, self.config.min_result_wait_sec / 6.0))
        early_ignore_until = start + early_ignore_sec
        baseline_set = set(baseline_signatures)
        baseline_count = len(baseline_signatures)
        first_seen: dict[ImageSignature, float] = {}
        stable_signature: Optional[ImageSignature] = None
        stable_since: Optional[float] = None
        last_candidate: Optional[BaseWrapper] = None
        last_log_at = 0.0
        while time.time() < deadline:
            now = time.time()
            candidates = self._find_result_images(window)
            new_candidates: list[tuple[ImageSignature, BaseWrapper, float]] = []
            for candidate in candidates:
                signature = self._result_signature(candidate)
                if signature is None or signature in baseline_set:
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
                    self._log(f"result image accepted after {int(now - start)}s")
                    return candidate
            time.sleep(2.0)
        if last_candidate is not None:
            raise DesktopAutomationError(
                "A possible image result appeared, but it did not pass the generated-image wait checks."
            )
        raise DesktopAutomationError("Could not find a generated image in the ChatGPT desktop window.")

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

    def _wait_for_dialog(self) -> BaseWrapper:
        deadline = time.time() + self.config.dialog_timeout_sec
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
        for dialog in desktop.windows(title_re=OPEN_DIALOG_TITLE_RE, visible_only=True):
            try:
                title = dialog.window_text()
                dialog.wait("visible ready", timeout=0.5)
                dialogs.append((title, dialog))
            except Exception:
                continue
        if not dialogs:
            raise DesktopAutomationError("Open-file dialog did not appear.")
        self._log(f"open dialogs: {[title for title, _ in dialogs]}")
        return dialogs[-1][1]

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
        for window in desktop.windows(visible_only=True):
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

        for dialog in desktop.windows(title_re=SAVE_DIALOG_TITLE_RE, visible_only=True):
            try:
                title = dialog.window_text()
                if self._is_save_dialog_candidate(dialog, prefer_foreground=False):
                    dialogs.append((title, dialog))
            except Exception:
                continue
        if not dialogs:
            for dialog in desktop.windows(visible_only=True):
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
                for window in desktop.windows(visible_only=True):
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
            for window in Desktop(backend="uia").windows(visible_only=True):
                try:
                    if window.handle == handle:
                        return window
                except Exception:
                    continue
        except Exception:
            return None
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
        self._restore_foreground_window(dialog)
        edit.click_input()
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
        handle, title, class_name = self._foreground_window_info()
        title_cf = title.casefold()
        class_cf = class_name.casefold()
        self._log(f"blind save foreground: title={title!r}, class={class_name!r}, handle={handle}")
        if not handle:
            return False
        if class_cf in {"shell_traywnd", "shell_secondarytraywnd"}:
            return False
        if any(part in title_cf for part in SAVE_DIALOG_EXCLUDED_TITLE_PARTS if part not in {"google chrome"}):
            return False
        if "google chrome" in title_cf or "chatgpt" in title_cf:
            return False

        pyperclip.copy(str(output_path))
        self._press_ctrl_key_raw(VK_A)
        time.sleep(0.1)
        self._press_key_raw(VK_BACK)
        time.sleep(0.1)
        self._press_ctrl_key_raw(VK_V)
        time.sleep(0.3)

        for name, action in (
            ("Enter", lambda: self._press_key_raw(VK_RETURN)),
            ("Alt+S", lambda: self._press_alt_key_raw(VK_S)),
            ("Alt+O", lambda: self._press_alt_key_raw(VK_O)),
            ("Enter retry", lambda: self._press_key_raw(VK_RETURN)),
        ):
            self._log(f"blind save submit via {name}")
            action()
            self._confirm_overwrite_if_needed()
            if self._wait_for_file_path(output_path, timeout_sec=5.0):
                self._log(f"blind save succeeded via {name}: {output_path}")
                return True
            time.sleep(0.5)
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
                    self._click_wrapper_center(target)  # type: ignore[arg-type]
                elif name == "button invoke" and target is not None:
                    self._invoke_wrapper(target)  # type: ignore[arg-type]
                elif name == "button click_input" and target is not None:
                    target.click_input()  # type: ignore[attr-defined]
                time.sleep(0.8)
                self._confirm_overwrite_if_needed()
                if self._wait_for_wrapper_to_close(dialog, timeout_sec=1.5):
                    self._log(f"save dialog closed after {name}")
                    return
            except Exception as exc:
                self._log(f"save dialog submit attempt failed via {name}: {exc}")
        raise DesktopAutomationError("Save-file dialog stayed open after all Save attempts.")

    def _find_file_dialog_edit(self, dialog: BaseWrapper) -> Optional[BaseWrapper]:
        candidates = []
        for wrapper in dialog.descendants(control_type="Edit"):
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
        for wrapper in dialog.descendants(control_type="Button"):
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

    def _click_wrapper_center(self, wrapper: BaseWrapper) -> bool:
        try:
            rect = wrapper.rectangle()
            x = rect.left + max(1, rect.width() // 2)
            y = rect.top + max(1, rect.height() // 2)
            self._click_screen_point(x, y)
            self._log(f"clicked wrapper center: x={x}, y={y}, title={wrapper.window_text()!r}")
            time.sleep(0.4)
            return True
        except Exception as exc:
            self._log(f"wrapper center click failed: {exc}")
            return False

    def _confirm_overwrite_if_needed(self) -> None:
        try:
            desktop = Desktop(backend="uia")
            for dialog in desktop.windows(visible_only=True):
                title = dialog.window_text()
                if not re.search(r"(Confirm|Replace|Подтверж|Замен|Сохран)", title, re.I):
                    continue
                button = self._find_button(dialog, ("Yes", "Replace", "Да", "Заменить"))
                if button is not None:
                    button.click_input()
                    return
        except Exception:
            return

    def _wait_for_dialog_or_attach_menu(self, window: BaseWrapper) -> BaseWrapper:
        deadline = time.time() + self.config.dialog_timeout_sec
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                return self._wait_for_dialog()
            except Exception as exc:  # pragma: no cover - GUI specific
                last_error = exc

            menu_button = self._find_button(window, ATTACH_MENU_PATTERNS)
            if menu_button is not None:
                menu_button.click_input()
                time.sleep(0.5)
                try:
                    return self._wait_for_dialog()
                except Exception as exc:  # pragma: no cover - GUI specific
                    last_error = exc
            time.sleep(0.5)
        raise DesktopAutomationError("Open-file dialog did not appear.") from last_error

    def _fill_open_dialog(self, dialog: BaseWrapper, image_path: Path) -> None:
        edit = self._find_descendant(dialog, control_type="Edit")
        if edit is None:
            raise DesktopAutomationError("Could not find the filename field in the open-file dialog.")
        edit.click_input()
        send_keys("^a{BACKSPACE}")
        pyperclip.copy(str(image_path))
        send_keys("^v")
        time.sleep(0.2)

        open_button = self._find_button(dialog, OPEN_DIALOG_BUTTONS)
        if open_button is not None:
            open_button.click_input()
            return
        send_keys("{ENTER}")

    def _find_prompt_input(self, window: BaseWrapper) -> Optional[BaseWrapper]:
        candidates = []
        window_rect = window.rectangle()
        for control_type in ("Edit", "Document"):
            for wrapper in window.descendants(control_type=control_type):
                try:
                    rect = wrapper.rectangle()
                    if rect.width() <= 0 or rect.height() <= 0:
                        continue
                    if not wrapper.is_visible() or not wrapper.is_enabled():
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
            for wrapper in window.descendants(control_type=control_type):
                try:
                    if not wrapper.is_visible():
                        continue
                    text = wrapper.window_text().strip()
                    if not text:
                        continue
                    if text in seen:
                        continue
                    seen.add(text)
                    lines.append(text)
                except Exception:
                    continue
        return "\n".join(lines)

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
        for wrapper in window.descendants(control_type="Image"):
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

    def _result_image_is_usable(self, window: BaseWrapper, wrapper: BaseWrapper) -> bool:
        try:
            if not wrapper.is_visible():
                return False
            rect = wrapper.rectangle()
            if rect.width() < 128 or rect.height() < 128:
                return False
            window_rect = window.rectangle()
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
        for wrapper in window.descendants(control_type="Button"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                title = wrapper.window_text().strip()
                buttons.append((title, wrapper))
            except Exception:
                continue
        if self.config.verbose:
            self._log(f"visible buttons: {[title for title, _ in buttons if title][:30]}")

        for pattern in patterns:
            normalized = pattern.casefold()
            for title, wrapper in buttons:
                if normalized in title.casefold():
                    return wrapper
        return None

    def _find_tab(self, window: BaseWrapper, title_re: str) -> Optional[BaseWrapper]:
        pattern = re.compile(title_re)
        candidates = []
        for wrapper in window.descendants(control_type="TabItem"):
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

    def _find_descendant(self, window: BaseWrapper, *, control_type: str) -> Optional[BaseWrapper]:
        for wrapper in window.descendants(control_type=control_type):
            try:
                if wrapper.is_visible() and wrapper.is_enabled():
                    return wrapper
            except Exception:
                continue
        return None
