from __future__ import annotations

import ctypes
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from api.chatgpt_desktop_v2 import (
    BaseWrapper,
    ChatGPTDesktopAgent,
    Desktop,
    DesktopAgentConfig,
    DesktopAutomationError,
    NEW_CHAT_BUTTON_PATTERNS,
    SAVE_DIALOG_EXCLUDED_TITLE_PARTS,
    SAVE_DIALOG_TITLE_RE,
    SEND_BUTTON_PATTERNS,
    VK_A,
    VK_BACK,
    VK_ESCAPE,
    VK_RETURN,
)


GEMINI_APP_URL = "https://gemini.google.com/app"
DEFAULT_GEMINI_BROWSER_TAB_TITLE_RE = ".*(Gemini|Google Gemini).*"
GEMINI_WINDOW_TITLE_MARKERS = (
    "gemini",
    "google gemini",
)
GEMINI_CONTENT_MARKERS = (
    "gemini",
    "ask gemini",
    "talk live with gemini",
    "google apps",
)
GEMINI_WINDOW_EXCLUDED_TITLE_PARTS = (
    "command prompt",
    "powershell",
    "total commander",
    "faststone",
    "codex",
    "taskbar",
    "program manager",
    "youtube",
    "google photos",
    "photoshop",
    "premiere",
    "notepad",
)
GEMINI_SUBMISSION_RUNNING_TEXT_PATTERNS = (
    "creating image",
    "generating image",
    "generating",
    "\u0441\u043e\u0437\u0434\u0430\u044e \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435",
    "\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u044e",
    "\u0438\u0434\u0435\u0442 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0435",
)
GEMINI_NEW_CHAT_BUTTON_PATTERNS = (
    *NEW_CHAT_BUTTON_PATTERNS,
    "\u041d\u043e\u0432\u044b\u0439 \u0447\u0430\u0442",
    "\u041d\u043e\u0432\u044b\u0439 \u0437\u0430\u043f\u0440\u043e\u0441",
    "\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0447\u0430\u0442",
)
GEMINI_FULL_SIZE_DOWNLOAD_PATTERNS = (
    "download full size",
    "download full-size",
    "download image",
    "download",
    "\u0441\u043a\u0430\u0447\u0430\u0442\u044c \u0432 \u043f\u043e\u043b\u043d\u043e\u043c \u0440\u0430\u0437\u043c\u0435\u0440\u0435",
    "\u0441\u043a\u0430\u0447\u0430\u0442\u044c",
)
GEMINI_DOWNLOAD_EXCLUDED_PATTERNS = (
    "downloads",
    "downloaded",
    "file downloaded",
    "\u0441\u043a\u0430\u0447\u0430\u043d\u043d\u044b\u0435",
    "\u0444\u0430\u0439\u043b \u0441\u043a\u0430\u0447\u0430\u043d",
    "share",
    "\u043f\u043e\u0434\u0435\u043b\u0438\u0442\u044c\u0441\u044f",
)
DOWNLOAD_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PARTIAL_DOWNLOAD_SUFFIXES = {".crdownload", ".tmp"}
GEMINI_SEND_BUTTON_WAIT_SEC = 90.0
WM_CLOSE = 0x0010


class GeminiDesktopAgent(ChatGPTDesktopAgent):
    """Desktop automation for a dedicated Gemini Chrome generation window.

    Gemini intentionally reuses the proven ChatGPT desktop automation primitives:
    clipboard file paste, prompt paste, foreground-window guards, result detection,
    and browser context-menu saving. The service-specific layer only changes
    window recognition and "new chat" navigation.
    """

    def __init__(self, config: DesktopAgentConfig) -> None:
        if not config.target_url:
            config.target_url = GEMINI_APP_URL
        super().__init__(config)

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(f"[desktop] {self._compact_log_message(message)}", flush=True)

    def _compact_log_message(self, message: str) -> str:
        compact = re.sub(r"Generate the .{160,}? - Google Gemini - Google Chrome", "Gemini generation tab - Google Chrome", message)
        if len(compact) > 900:
            compact = compact[:900] + "... <truncated>"
        return compact

    def _validate_expected_window(self, window: BaseWrapper) -> None:
        title = self._window_title(window)
        if not self._looks_like_chatgpt_content(window):
            raise DesktopAutomationError(
                "The active window is not the Gemini browser window. "
                f"Active window title: {title!r}. "
                "Start the batch again, then activate the dedicated Gemini window and click the message box."
            )
        if self.config.require_single_tab_window and not self._has_single_visible_browser_tab(window):
            tab_titles = self._visible_tab_titles(window)
            raise DesktopAutomationError(
                "The selected Gemini window is not the dedicated generation window. "
                "This run requires a Chrome window with exactly one visible tab. "
                f"Selected window title: {title!r}. Visible tab titles: {tab_titles!r}."
            )

    def _looks_like_chatgpt_content(self, window: BaseWrapper) -> bool:
        title = self._window_title(window).casefold()
        if any(part in title for part in GEMINI_WINDOW_EXCLUDED_TITLE_PARTS):
            return False
        if any(marker in title for marker in GEMINI_WINDOW_TITLE_MARKERS):
            return True

        tab_re = self.config.browser_tab_title_re or DEFAULT_GEMINI_BROWSER_TAB_TITLE_RE
        try:
            if self._find_tab(window, tab_re) is not None:
                return True
        except Exception:
            pass

        try:
            visible_text = self._collect_visible_text(window=window).casefold()
        except Exception:
            visible_text = ""
        return any(marker in visible_text for marker in GEMINI_CONTENT_MARKERS)

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
        candidate_summary = [
            (self._short_title(self._window_title(item)), self._chatgpt_window_score(item), len(self._visible_tab_titles(item)))
            for item in candidates
        ]
        self._log(f"found Gemini window candidates: {candidate_summary}")
        return candidates[-1]

    def _short_title(self, title: str, limit: int = 96) -> str:
        if len(title) <= limit:
            return title
        return title[:limit] + "..."

    def _chatgpt_window_score(self, window: BaseWrapper) -> int:
        title = self._window_title(window).casefold()
        score = 0
        if "google chrome" in title:
            score += 10
        if "gemini" in title:
            score += 120
        if "portrait" in title:
            score += 40
        if "image generation" in title or "image editing" in title:
            score += 60
        if "image expansion" in title:
            score += 80
        try:
            visible_text = self._collect_visible_text(window=window).casefold()
            if any(marker in visible_text for marker in GEMINI_CONTENT_MARKERS):
                score += 150
        except Exception:
            pass
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
            self._log("returned focus to Gemini window")
        except Exception as exc:
            self._log(f"could not return focus to Gemini window: {exc}")

    def _close_chatgpt_overlay_if_needed(self, window: BaseWrapper) -> None:
        super()._close_chatgpt_overlay_if_needed(window)
        self._log("sent Esc to close any open Gemini image overlay")

    def _open_new_chat(self, window: BaseWrapper) -> None:
        self._close_lingering_save_dialogs_before_request(window)
        target_url = self.config.target_url or GEMINI_APP_URL
        self._log("navigating current tab to Gemini app")
        self._navigate_to_url(target_url, window)
        time.sleep(max(self.config.post_new_chat_delay_sec, 4.0))
        if self._find_prompt_input(window) is not None:
            return

        button = self._find_button(window, GEMINI_NEW_CHAT_BUTTON_PATTERNS)
        if button is not None:
            self._ensure_foreground_window(window, "click Gemini new chat button")
            button.click_input()
            time.sleep(self.config.post_new_chat_delay_sec)
            if self._find_prompt_input(window) is not None:
                return

        self._log("Gemini prompt input was not confirmed after new-chat navigation; continuing to attach step")
        return

    def _activate_browser_tab_if_needed(self, window: BaseWrapper) -> None:
        if not self.config.browser_tab_title_re and not self.config.target_url:
            return

        if self.config.browser_tab_title_re:
            self._log(f"looking for Gemini browser tab: {self.config.browser_tab_title_re}")
            tab = self._find_tab(window, self.config.browser_tab_title_re)
            if tab is not None:
                self._ensure_foreground_window(window, "activate Gemini browser tab")
                tab.click_input()
                time.sleep(1.0)
                self._log("Gemini browser tab activated")
                return

        if self.config.target_url:
            self._navigate_to_url(self.config.target_url, window)
            time.sleep(2.0)
            return

        raise DesktopAutomationError(
            f"Could not find a Gemini browser tab matching '{self.config.browser_tab_title_re}'."
        )

    def _attach_image(self, window: BaseWrapper, image_path: Path) -> None:
        self._close_lingering_save_dialogs_before_request(window)
        self._clear_composer_before_new_request(window)
        super()._attach_image(window, image_path)

    def _close_lingering_save_dialogs_before_request(self, window: BaseWrapper) -> None:
        self._close_save_dialogs_and_restore(
            window,
            "closing leftover Gemini Save As before new request",
            timeout_sec=12.0,
            quiet_sec=1.5,
        )

    def _close_save_dialogs_and_restore(
        self,
        window: BaseWrapper,
        reason: str,
        *,
        timeout_sec: float,
        quiet_sec: float,
    ) -> bool:
        deadline = time.time() + timeout_sec
        quiet_since: Optional[float] = None
        closed_any = False
        while time.time() < deadline:
            if self._close_any_visible_save_dialog(reason):
                closed_any = True
                quiet_since = None
                time.sleep(0.5)
                continue
            if quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since >= quiet_sec:
                break
            time.sleep(0.2)
        if closed_any:
            try:
                self._restore_foreground_window(window)
                time.sleep(0.3)
            except Exception as exc:
                self._log(f"could not restore Gemini window after closing Save As: {exc}")
        return closed_any

    def _clear_composer_before_new_request(self, window: BaseWrapper) -> None:
        self._close_unexpected_open_dialog_if_needed()
        if not self._focus_prompt_input_or_composer(window):
            self._log("Gemini composer was not found before attach; continuing without pre-clear")
            return
        for attempt in range(1, 3):
            self._log(f"clearing Gemini composer before attach attempt {attempt}")
            self._press_ctrl_key(window, VK_A)
            time.sleep(0.1)
            self._press_key(window, VK_BACK)
            time.sleep(0.4)
            if not self._composer_still_has_text(window):
                return
        self._log("Gemini composer still appears to contain text after pre-clear")

    def _submit_prompt(self, window: BaseWrapper) -> None:
        self._close_save_dialogs_and_restore(
            window,
            "closing delayed Gemini Save As before submit",
            timeout_sec=12.0,
            quiet_sec=1.5,
        )
        baseline_signatures = set(self._result_signatures(self._find_result_images(window)))
        for attempt in range(1, 4):
            self._close_save_dialogs_and_restore(
                window,
                f"closing delayed Gemini Save As before send attempt {attempt}",
                timeout_sec=4.0,
                quiet_sec=0.8,
            )
            send_button = self._wait_for_send_button(window)
            if send_button is None:
                break
            self._log(f"clicking Gemini send button candidate: {self._control_label(send_button)!r}")
            if not self._click_wrapper_center(
                send_button,
                expected_window=window,
                purpose="click Gemini send button",
            ):
                self._ensure_foreground_window(window, "click Gemini send button")
                send_button.click_input()
            if self._gemini_submission_started(window, baseline_signatures):
                self._log(f"Gemini submission appears to have started after send attempt {attempt}")
                return
            self._log(f"Gemini send attempt {attempt} did not start the request; retrying")
        if self._try_keyboard_submit_from_prompt_input(window, baseline_signatures):
            return
        raise DesktopAutomationError(
            "Gemini did not accept the request after send-button attempts. "
            "Stopping before the next job so the same composer is not filled repeatedly."
        )

    def _wait_for_send_button(self, window: BaseWrapper) -> Optional[BaseWrapper]:
        deadline = time.time() + GEMINI_SEND_BUTTON_WAIT_SEC
        geometry_candidate_logged = False
        last_wait_log = 0.0
        while time.time() < deadline:
            if self._close_any_visible_save_dialog("closing Gemini Save As while waiting for send button"):
                self._restore_foreground_window(window)
                time.sleep(0.5)
                continue
            button = self._find_button(window, SEND_BUTTON_PATTERNS)
            if button is not None:
                return button
            button = self._find_send_button_by_geometry(window)
            if button is not None:
                if not geometry_candidate_logged:
                    self._log(
                        "Gemini send button was not found by label; using geometric candidate: "
                        f"{self._wrapper_rect_text(button)}, title={self._control_label(button)!r}"
                    )
                    geometry_candidate_logged = True
                return button
            now = time.time()
            if self.config.verbose and now - last_wait_log >= 15.0:
                last_wait_log = now
                self._log("Gemini send button is not ready yet; only waiting, not clicking microphone")
            time.sleep(0.5)
        return None

    def _try_keyboard_submit_from_prompt_input(
        self,
        window: BaseWrapper,
        baseline_signatures: set[tuple[int, ...]],
    ) -> bool:
        if not self._composer_still_has_text(window):
            return False
        self._close_save_dialogs_and_restore(
            window,
            "closing delayed Gemini Save As before keyboard submit",
            timeout_sec=4.0,
            quiet_sec=0.8,
        )
        if not self._focus_prompt_input_or_composer(window):
            self._log("Gemini keyboard submit skipped because prompt input was not found")
            return False
        self._log("Gemini send button was unavailable; trying Ctrl+Enter from focused prompt input")
        self._press_ctrl_key(window, VK_RETURN)
        if self._gemini_submission_started(window, baseline_signatures):
            self._log("Gemini submission appears to have started after Ctrl+Enter")
            return True
        self._log("Gemini Ctrl+Enter did not start the request")
        return False

    def _gemini_submission_started(self, window: BaseWrapper, baseline_signatures: set[tuple[int, ...]]) -> bool:
        deadline = time.time() + 18.0
        baseline_digests = {
            digest
            for digest in (self._result_signature_digest(signature) for signature in baseline_signatures)
            if digest
        }
        while time.time() < deadline:
            if self._has_generation_running_indicator(window):
                return True
            if self._has_gemini_running_text(window):
                return True
            current_signatures = set(self._result_signatures(self._find_result_images(window)))
            if any(
                not self._signature_matches_baseline(
                    signature,
                    baseline_set=baseline_signatures,
                    baseline_digests=baseline_digests,
                )
                for signature in current_signatures
            ):
                return True
            time.sleep(0.5)
        return False

    def _has_gemini_running_text(self, window: BaseWrapper) -> bool:
        try:
            visible_text = self._collect_visible_text(window=window).casefold()
        except Exception:
            return False
        return any(pattern in visible_text for pattern in GEMINI_SUBMISSION_RUNNING_TEXT_PATTERNS)

    def _save_result_image_via_context_menu(
        self,
        window: BaseWrapper,
        baseline_signatures: list[tuple[int, ...]],
    ) -> None:
        result_image = self._wait_for_result_image(window, baseline_signatures)
        output_path = self.config.output_path
        if output_path is None:
            raise DesktopAutomationError("Output path for desktop result is not configured.")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if self._save_result_image_via_full_size_download(window, result_image, output_path):
            self._settle_after_gemini_save(output_path, timeout_sec=35.0)
            return

        self._log("Gemini full-size download did not complete; falling back to browser image context menu")
        self._open_save_dialog_from_image(window, result_image, output_path)
        self._settle_after_gemini_save(output_path, timeout_sec=20.0)
        time.sleep(1.0)

    def _save_result_image_via_full_size_download(
        self,
        window: BaseWrapper,
        result_image: BaseWrapper,
        output_path: Path,
    ) -> bool:
        download_dirs = self._download_search_dirs(output_path)
        baseline = self._snapshot_download_files(download_dirs)
        start_time = time.time()

        button = self._find_full_size_download_button(window, result_image)
        if button is None:
            self._hover_result_image_top_right(window, result_image)
            button = self._find_full_size_download_button(window, result_image)
        if button is None:
            self._log("Gemini full-size download button was not found")
            return False

        self._log(f"clicking Gemini full-size download button: {self._control_label(button)!r}")
        if not self._click_wrapper_center(
            button,
            expected_window=window,
            purpose="click Gemini full-size download button",
        ):
            return False

        time.sleep(0.8)
        if self._blind_save_foreground_dialog_when_available(output_path, timeout_sec=8.0):
            return True

        try:
            dialog = self._wait_for_save_dialog(timeout_sec=1.5)
            try:
                self._fill_save_dialog(dialog, output_path)
                self._dismiss_duplicate_save_dialog_if_output_exists(output_path, timeout_sec=3.0)
                return True
            except Exception as exc:
                self._log(f"UIA save dialog submit failed after Gemini download click: {exc}")
                if self._blind_save_foreground_dialog_when_available(output_path, timeout_sec=3.0):
                    return True
        except Exception:
            if self._blind_save_foreground_dialog_when_available(output_path, timeout_sec=3.0):
                return True

        if self._save_any_visible_save_dialog(output_path, "saving Gemini Save As before download wait"):
            return True

        return self._wait_for_downloaded_image(output_path, download_dirs, baseline, start_time)

    def _blind_save_foreground_dialog_when_available(self, output_path: Path, timeout_sec: float) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            handle, title, class_name = self._foreground_window_info()
            if self._is_safe_foreground_save_dialog(handle, title, class_name):
                self._log(
                    "Gemini full-size download opened Save As; "
                    "using blind foreground save"
                )
                saved = self._blind_save_foreground_dialog(output_path)
                if saved or self._wait_for_file_path(output_path, timeout_sec=1.0):
                    self._dismiss_duplicate_save_dialog_if_output_exists(output_path, timeout_sec=10.0)
                    return True
                return False
            time.sleep(0.25)
        return False

    def _save_any_visible_save_dialog(self, output_path: Path, reason: str) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self._blind_save_foreground_dialog_when_available(output_path, timeout_sec=0.5):
            return True

        for dialog in self._visible_save_dialogs():
            try:
                dialog_title = dialog.window_text()
                dialog_class = self._window_class_name(dialog)
            except Exception:
                dialog_title = ""
                dialog_class = ""
            self._log(f"{reason}: UIA title={dialog_title!r}, class={dialog_class!r}")
            try:
                dialog.set_focus()
                time.sleep(0.2)
                self._fill_save_dialog(dialog, output_path)
                if self._wait_for_file_path(output_path, timeout_sec=2.0):
                    self._dismiss_duplicate_save_dialog_if_output_exists(output_path, timeout_sec=5.0)
                    return True
            except Exception as exc:
                self._log(f"UIA Gemini Save As fill failed: {exc}")
                if self._blind_save_foreground_dialog_when_available(output_path, timeout_sec=1.5):
                    return True

        for handle, title, class_name in self._win32_visible_save_dialogs():
            self._log(f"{reason}: win32 title={title!r}, class={class_name!r}")
            try:
                ctypes.windll.user32.SetForegroundWindow(int(handle))
                time.sleep(0.3)
                if self._blind_save_foreground_dialog(output_path):
                    self._dismiss_duplicate_save_dialog_if_output_exists(output_path, timeout_sec=5.0)
                    return True
            except Exception as exc:
                self._log(f"win32 Gemini Save As fill failed: {exc}")
        return False

    def _dismiss_duplicate_save_dialog_if_output_exists(self, output_path: Path, timeout_sec: float) -> None:
        if not self._wait_for_file_path(output_path, timeout_sec=1.0):
            return
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            closed = self._close_any_visible_save_dialog(
                "closing duplicate Gemini Save As after output file already exists"
            )
            if not closed:
                return
            time.sleep(0.5)

    def _settle_after_gemini_save(self, output_path: Path, timeout_sec: float) -> None:
        if not self._wait_for_file_path(output_path, timeout_sec=1.0):
            return
        deadline = time.time() + timeout_sec
        quiet_since: Optional[float] = None
        while time.time() < deadline:
            if self._close_any_visible_save_dialog("closing late Gemini Save As before moving to the next job"):
                quiet_since = None
                time.sleep(1.0)
                continue
            if quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since >= 8.0:
                return
            time.sleep(0.25)

    def _close_any_visible_save_dialog(self, reason: str) -> bool:
        handle, title, class_name = self._foreground_window_info()
        if self._is_safe_foreground_save_dialog(handle, title, class_name):
            self._log(f"{reason}: foreground title={title!r}, class={class_name!r}")
            self._press_key_raw(VK_ESCAPE)
            time.sleep(0.5)
            if handle and self._win32_window_is_visible(handle):
                ctypes.windll.user32.PostMessageW(int(handle), WM_CLOSE, 0, 0)
                time.sleep(0.5)
            return True

        for dialog in self._visible_save_dialogs():
            try:
                dialog_title = dialog.window_text()
                dialog_class = self._window_class_name(dialog)
            except Exception:
                dialog_title = ""
                dialog_class = ""
            self._log(f"{reason}: visible title={dialog_title!r}, class={dialog_class!r}")
            try:
                dialog.set_focus()
                time.sleep(0.2)
                self._press_key(dialog, VK_ESCAPE)
            except Exception:
                self._press_key_raw(VK_ESCAPE)
            time.sleep(0.5)
            return True
        return self._close_any_win32_save_dialog(reason)

    def _visible_save_dialogs(self) -> list[BaseWrapper]:
        if Desktop is None:
            return []
        dialogs: list[BaseWrapper] = []
        for window in Desktop(backend="uia").windows(visible_only=True):
            try:
                if self._is_save_dialog_candidate(window, prefer_foreground=False):
                    dialogs.append(window)
            except Exception:
                continue
        return dialogs

    def _close_any_win32_save_dialog(self, reason: str) -> bool:
        for handle, title, class_name in self._win32_visible_save_dialogs():
            self._log(f"{reason}: win32 title={title!r}, class={class_name!r}")
            user32 = ctypes.windll.user32
            try:
                user32.PostMessageW(int(handle), WM_CLOSE, 0, 0)
                time.sleep(0.5)
                if not self._win32_window_is_visible(handle):
                    return True
                user32.SetForegroundWindow(int(handle))
                time.sleep(0.2)
                self._press_key_raw(VK_ESCAPE)
                time.sleep(0.5)
                return True
            except Exception as exc:
                self._log(f"win32 save-dialog close failed: {exc}")
                return False
        return False

    def _win32_visible_save_dialogs(self) -> list[tuple[int, str, str]]:
        user32 = ctypes.windll.user32
        dialogs: list[tuple[int, str, str]] = []
        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = self._win32_window_text(hwnd)
            class_name = self._win32_class_name(hwnd)
            if self._looks_like_win32_save_dialog(title, class_name):
                dialogs.append((int(hwnd), title, class_name))
            return True

        enum_proc = enum_proc_type(callback)
        user32.EnumWindows(enum_proc, 0)
        return dialogs

    def _looks_like_win32_save_dialog(self, title: str, class_name: str) -> bool:
        if not title:
            return False
        title_cf = title.casefold()
        class_cf = class_name.casefold()
        if class_cf in {"shell_traywnd", "shell_secondarytraywnd", "progman"}:
            return False
        if any(part in title_cf for part in SAVE_DIALOG_EXCLUDED_TITLE_PARTS):
            return False
        return bool(re.search(SAVE_DIALOG_TITLE_RE, title, re.I))

    def _win32_window_is_visible(self, handle: int) -> bool:
        user32 = ctypes.windll.user32
        return bool(user32.IsWindow(int(handle))) and bool(user32.IsWindowVisible(int(handle)))

    def _win32_window_text(self, handle: int) -> str:
        user32 = ctypes.windll.user32
        handle = int(handle)
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value

    def _win32_class_name(self, handle: int) -> str:
        user32 = ctypes.windll.user32
        handle = int(handle)
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, buffer, len(buffer))
        return buffer.value

    def _find_full_size_download_button(
        self,
        window: BaseWrapper,
        result_image: BaseWrapper,
    ) -> Optional[BaseWrapper]:
        try:
            image_rect = result_image.rectangle()
            window_rect = window.rectangle()
        except Exception:
            return None

        candidates: list[tuple[float, BaseWrapper]] = []
        for wrapper in window.descendants(control_type="Button"):
            try:
                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue
                searchable = self._control_search_text(wrapper).casefold()
                if not searchable:
                    continue
                if any(pattern in searchable for pattern in GEMINI_DOWNLOAD_EXCLUDED_PATTERNS):
                    continue
                if not any(pattern in searchable for pattern in GEMINI_FULL_SIZE_DOWNLOAD_PATTERNS):
                    continue
                rect = wrapper.rectangle()
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                center_x = rect.left + rect.width() // 2
                center_y = rect.top + rect.height() // 2
                if not self._rect_center_inside(rect, window_rect):
                    continue
                score = 100.0
                if "full" in searchable or "\u043f\u043e\u043b\u043d" in searchable:
                    score += 700.0
                if image_rect.left - 120 <= center_x <= image_rect.right + 180:
                    score += 250.0
                if image_rect.top - 120 <= center_y <= image_rect.top + 220:
                    score += 250.0
                target_x = image_rect.right - 30
                target_y = image_rect.top + 35
                score -= (abs(center_x - target_x) + abs(center_y - target_y)) / 4.0
                candidates.append((score, wrapper))
            except Exception:
                continue

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        if self.config.verbose:
            self._log(
                "Gemini download button candidates: "
                f"{[(round(score, 1), self._wrapper_rect_text(wrapper), self._control_label(wrapper)) for score, wrapper in candidates[-5:]]}"
            )
        return candidates[-1][1]

    def _hover_result_image_top_right(self, window: BaseWrapper, result_image: BaseWrapper) -> None:
        try:
            rect = result_image.rectangle()
            x = rect.right - 28
            y = rect.top + 28
            self._ensure_foreground_window(window, "hover Gemini result image")
            ctypes.windll.user32.SetCursorPos(int(x), int(y))
            time.sleep(0.7)
        except Exception as exc:
            self._log(f"could not hover Gemini result image controls: {exc}")

    def _download_search_dirs(self, output_path: Path) -> list[Path]:
        dirs: list[Path] = []
        for candidate in (
            Path.home() / "Downloads",
            Path(os.environ.get("USERPROFILE", "")) / "Downloads" if os.environ.get("USERPROFILE") else None,
            output_path.parent,
        ):
            if candidate is None:
                continue
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if resolved not in dirs and resolved.exists():
                dirs.append(resolved)
        return dirs

    def _snapshot_download_files(self, dirs: list[Path]) -> dict[Path, tuple[int, int]]:
        snapshot: dict[Path, tuple[int, int]] = {}
        for folder in dirs:
            try:
                paths = list(folder.iterdir())
            except Exception:
                continue
            for path in paths:
                try:
                    if not path.is_file():
                        continue
                    stat = path.stat()
                    snapshot[path] = (int(stat.st_mtime_ns), int(stat.st_size))
                except Exception:
                    continue
        return snapshot

    def _wait_for_downloaded_image(
        self,
        output_path: Path,
        download_dirs: list[Path],
        baseline: dict[Path, tuple[int, int]],
        start_time: float,
    ) -> bool:
        deadline = time.time() + 90.0
        last_candidate: Optional[Path] = None
        last_size: Optional[int] = None
        stable_since: Optional[float] = None
        while time.time() < deadline:
            if self._save_any_visible_save_dialog(output_path, "saving Gemini Save As during download wait"):
                self._log(f"Gemini Save As saved with configured output path: {output_path}")
                return True
            candidate = self._latest_completed_download(download_dirs, baseline, start_time)
            if candidate is not None:
                try:
                    size = candidate.stat().st_size
                except Exception:
                    size = -1
                if candidate != last_candidate or size != last_size:
                    last_candidate = candidate
                    last_size = size
                    stable_since = time.time()
                elif stable_since is not None and time.time() - stable_since >= 1.5 and size > 0:
                    self._move_downloaded_image(candidate, output_path)
                    self._log(f"Gemini full-size download saved: {output_path}")
                    return True
            time.sleep(0.5)
        self._log("Gemini full-size download file was not detected before timeout")
        return False

    def _latest_completed_download(
        self,
        download_dirs: list[Path],
        baseline: dict[Path, tuple[int, int]],
        start_time: float,
    ) -> Optional[Path]:
        candidates: list[Path] = []
        for folder in download_dirs:
            try:
                paths = list(folder.iterdir())
            except Exception:
                continue
            partial_names = {
                path.name.removesuffix(path.suffix)
                for path in paths
                if path.is_file() and path.suffix.casefold() in PARTIAL_DOWNLOAD_SUFFIXES
            }
            for path in paths:
                try:
                    if not path.is_file():
                        continue
                    if path.suffix.casefold() not in DOWNLOAD_IMAGE_SUFFIXES:
                        continue
                    if path.name in partial_names or path.stem in partial_names:
                        continue
                    stat = path.stat()
                    previous = baseline.get(path)
                    if previous is not None and previous == (int(stat.st_mtime_ns), int(stat.st_size)):
                        continue
                    if stat.st_mtime < start_time - 5:
                        continue
                    candidates.append(path)
                except Exception:
                    continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.stat().st_mtime)
        return candidates[-1]

    def _move_downloaded_image(self, source_path: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() == output_path.resolve():
            return
        if output_path.exists():
            output_path.unlink()
        shutil.move(str(source_path), str(output_path))
