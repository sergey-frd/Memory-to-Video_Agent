from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

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
    "Attach",
    "Upload",
    "Open file picker",
    "Plus",
    "Добавить",
    "Прикрепить",
    "Загрузить",
)
OPEN_DIALOG_TITLE_RE = ".*(Open|Open File|Открыть|Открытие|Выбор файла).*"
OPEN_DIALOG_BUTTONS = ("Open", "Открыть")
SEND_BUTTON_PATTERNS = ("Send", "Submit", "Отправить")


class DesktopAutomationError(RuntimeError):
    pass


@dataclass
class DesktopAgentConfig:
    image_path: Optional[Path]
    prompt_text: str
    output_path: Optional[Path] = None
    response_text_path: Optional[Path] = None
    executable_path: Optional[Path] = None
    window_title_re: str = WINDOW_TITLE_RE
    startup_timeout_sec: float = 30.0
    dialog_timeout_sec: float = 15.0
    result_timeout_sec: float = 120.0
    post_attach_delay_sec: float = 1.0
    post_paste_delay_sec: float = 0.5
    submit: bool = True


class ChatGPTDesktopAgent:
    def __init__(self, config: DesktopAgentConfig) -> None:
        self.config = config
        self._window: Optional[BaseWrapper] = None

    def run(self) -> None:
        self._ensure_dependencies()
        self._window = self._launch_or_connect()
        self._window.set_focus()
        if self.config.image_path is not None:
            self._attach_image(self._window, self.config.image_path)
        self._paste_prompt(self._window, self.config.prompt_text)
        if self.config.submit:
            baseline_signature = self._result_signature(self._find_result_image(self._window))
            baseline_text = self._collect_visible_text(window=self._window)
            self._submit_prompt(self._window)
            if self.config.output_path is not None:
                self._save_result_image(self._window, baseline_signature)
            if self.config.response_text_path is not None:
                self._save_response_text(self._window, baseline_text)
        elif self.config.response_text_path is not None:
            self._save_text_snapshot(self.config.response_text_path, self._collect_visible_text(window=self._window))

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

        deadline = time.time() + self.config.startup_timeout_sec
        desktop = Desktop(backend="uia")
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                window = desktop.window(title_re=self.config.window_title_re)
                window.wait("visible ready", timeout=1)
                return window
            except Exception as exc:  # pragma: no cover - GUI specific
                last_error = exc
                time.sleep(1.0)
        raise DesktopAutomationError(
            f"Could not find a ChatGPT window matching '{self.config.window_title_re}'."
        ) from last_error

    def _attach_image(self, window: BaseWrapper, image_path: Path) -> None:
        attach_button = self._find_button(window, ATTACH_BUTTON_PATTERNS)
        if attach_button is None:
            raise DesktopAutomationError(
                "Could not find the attach/upload button in the ChatGPT desktop window."
            )
        attach_button.click_input()

        dialog = self._wait_for_dialog()
        self._fill_open_dialog(dialog, image_path)
        time.sleep(self.config.post_attach_delay_sec)

    def _paste_prompt(self, window: BaseWrapper, prompt_text: str) -> None:
        input_box = self._find_prompt_input(window)
        if input_box is None:
            raise DesktopAutomationError(
                "Could not find the prompt input box in the ChatGPT desktop window."
            )
        input_box.click_input()
        pyperclip.copy(prompt_text)
        send_keys("^a{BACKSPACE}")
        time.sleep(0.1)
        send_keys("^v")
        time.sleep(self.config.post_paste_delay_sec)

    def _submit_prompt(self, window: BaseWrapper) -> None:
        send_button = self._find_button(window, SEND_BUTTON_PATTERNS)
        if send_button is not None:
            send_button.click_input()
            return
        send_keys("^~")

    def _save_result_image(self, window: BaseWrapper, baseline_signature: Optional[tuple[int, int, int, int]]) -> None:
        result_image = self._wait_for_result_image(window, baseline_signature)
        output_path = self.config.output_path
        if output_path is None:
            raise DesktopAutomationError("Output path for desktop result is not configured.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result_image.capture_as_image().save(output_path)

    def _save_response_text(self, window: BaseWrapper, baseline_text: str) -> None:
        response_text = self._wait_for_response_text(window, baseline_text)
        path = self.config.response_text_path
        if path is None:
            raise DesktopAutomationError("Response text path is not configured.")
        self._save_text_snapshot(path, response_text)

    def _wait_for_result_image(
        self,
        window: BaseWrapper,
        baseline_signature: Optional[tuple[int, int, int, int]],
    ) -> BaseWrapper:
        deadline = time.time() + self.config.result_timeout_sec
        last_candidate: Optional[BaseWrapper] = None
        while time.time() < deadline:
            candidate = self._find_result_image(window)
            if candidate is not None:
                last_candidate = candidate
                signature = self._result_signature(candidate)
                if signature != baseline_signature:
                    return candidate
            time.sleep(2.0)
        if last_candidate is None:
            raise DesktopAutomationError("Could not find a generated image in the ChatGPT desktop window.")
        return last_candidate

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
        desktop = Desktop(backend="uia")
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                dialog = desktop.window(title_re=OPEN_DIALOG_TITLE_RE)
                dialog.wait("visible ready", timeout=1)
                return dialog
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
        for control_type in ("Edit", "Document"):
            for wrapper in window.descendants(control_type=control_type):
                try:
                    rect = wrapper.rectangle()
                    if rect.width() <= 0 or rect.height() <= 0:
                        continue
                    if not wrapper.is_visible() or not wrapper.is_enabled():
                        continue
                    candidates.append(wrapper)
                except Exception:
                    continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.rectangle().bottom, item.rectangle().width() * item.rectangle().height()))
        return candidates[-1]

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
        candidates = []
        for wrapper in window.descendants(control_type="Image"):
            try:
                rect = wrapper.rectangle()
                if rect.width() < 128 or rect.height() < 128:
                    continue
                if not wrapper.is_visible():
                    continue
                candidates.append(wrapper)
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.rectangle().bottom, item.rectangle().width() * item.rectangle().height()))
        return candidates[-1]

    def _result_signature(self, wrapper: Optional[BaseWrapper]) -> Optional[tuple[int, int, int, int]]:
        if wrapper is None:
            return None
        rect = wrapper.rectangle()
        return (rect.left, rect.top, rect.right, rect.bottom)

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

        for pattern in patterns:
            normalized = pattern.casefold()
            for title, wrapper in buttons:
                if normalized in title.casefold():
                    return wrapper
        return None

    def _find_descendant(self, window: BaseWrapper, *, control_type: str) -> Optional[BaseWrapper]:
        for wrapper in window.descendants(control_type=control_type):
            try:
                if wrapper.is_visible() and wrapper.is_enabled():
                    return wrapper
            except Exception:
                continue
        return None
