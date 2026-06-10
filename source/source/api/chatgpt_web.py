from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
except ModuleNotFoundError:
    BrowserContext = object  # type: ignore[assignment,misc]
    Page = object  # type: ignore[assignment,misc]
    PlaywrightTimeoutError = Exception  # type: ignore[assignment]
    sync_playwright = None  # type: ignore[assignment]


class ChatGPTWebError(RuntimeError):
    pass


@dataclass
class ChatGPTWebConfig:
    prompt_text: str
    image_path: Optional[Path] = None
    output_path: Optional[Path] = None
    response_text_path: Optional[Path] = None
    profile_dir: Path = Path(".browser-profile/chatgpt-web")
    target_url: str = "https://chatgpt.com/"
    executable_path: Optional[Path] = None
    launch_timeout_ms: int = 60_000
    result_timeout_ms: int = 180_000
    submit: bool = True


class ChatGPTWebAgent:
    def __init__(self, config: ChatGPTWebConfig) -> None:
        self.config = config

    def run(self) -> None:
        self._ensure_dependencies()
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            launch_kwargs: dict[str, object] = {
                "user_data_dir": str(self.config.profile_dir),
                "headless": False,
                "args": ["--start-maximized"],
            }
            if self.config.executable_path is not None:
                launch_kwargs["executable_path"] = str(self.config.executable_path)
            else:
                launch_kwargs["channel"] = "chrome"

            context = playwright.chromium.launch_persistent_context(**launch_kwargs)
            try:
                page = self._get_page(context)
                self._prepare_chat(page)
                baseline_text = self._assistant_text(page)
                baseline_image_count = self._assistant_image_count(page)
                if self.config.image_path is not None:
                    self._attach_image(page, self.config.image_path)
                self._fill_prompt(page, self.config.prompt_text)
                if self.config.submit:
                    self._submit(page)
                    if self.config.response_text_path is not None:
                        response_text = self._wait_for_response_text(page, baseline_text)
                        self.config.response_text_path.parent.mkdir(parents=True, exist_ok=True)
                        self.config.response_text_path.write_text(response_text, encoding="utf-8")
                    if self.config.output_path is not None:
                        self._save_generated_image(page, baseline_image_count, self.config.output_path)
            finally:
                context.close()

    def _ensure_dependencies(self) -> None:
        if sync_playwright is None:
            raise ChatGPTWebError("Playwright is not installed. Run 'python -m pip install playwright'.")

    def _get_page(self, context: BrowserContext) -> Page:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(self.config.target_url, wait_until="domcontentloaded", timeout=self.config.launch_timeout_ms)
        self._wait_for_chat_ready(page)
        return page

    def _prepare_chat(self, page: Page) -> None:
        if "login" in page.url.lower():
            raise ChatGPTWebError(
                "ChatGPT Web is not logged in for the automation profile. "
                f"Open {self.config.target_url} once with profile '{self.config.profile_dir}' and sign in."
            )
        page.goto(self.config.target_url, wait_until="domcontentloaded", timeout=self.config.launch_timeout_ms)
        self._wait_for_chat_ready(page)

    def _attach_image(self, page: Page, image_path: Path) -> None:
        if not image_path.exists():
            raise FileNotFoundError(f"Input image was not found: {image_path}")
        file_input = page.locator("input[type='file']").first
        try:
            file_input.set_input_files(str(image_path), timeout=10_000)
            return
        except PlaywrightTimeoutError:
            pass

        attach_button = page.get_by_role("button", name=re.compile(r"(files|photos|добавляйте|файлы|upload)", re.I)).first
        try:
            attach_button.click(timeout=5_000)
        except PlaywrightTimeoutError as exc:
            raise ChatGPTWebError("Could not find the file attach control on ChatGPT Web.") from exc

        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(str(image_path), timeout=10_000)

    def _fill_prompt(self, page: Page, prompt_text: str) -> None:
        box = self._prompt_locator(page)
        try:
            box.click(timeout=10_000)
            box.fill(prompt_text, timeout=10_000)
            return
        except Exception:
            pass

        editable = page.locator("[contenteditable='true']").last
        try:
            editable.click(timeout=10_000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.insert_text(prompt_text)
            return
        except Exception as exc:
            raise ChatGPTWebError("Could not find the ChatGPT Web prompt input.") from exc

    def _submit(self, page: Page) -> None:
        send_button = page.get_by_role("button", name=re.compile(r"(send|отправить)", re.I)).first
        try:
            send_button.click(timeout=5_000)
            return
        except Exception:
            pass
        box = self._prompt_locator(page)
        box.press("Enter")

    def _wait_for_response_text(self, page: Page, baseline_text: str) -> str:
        deadline = time.time() + self.config.result_timeout_ms / 1000
        last_text = baseline_text
        while time.time() < deadline:
            current_text = self._assistant_text(page)
            if current_text and current_text != baseline_text and len(current_text) > len(baseline_text):
                return current_text
            if current_text:
                last_text = current_text
            time.sleep(2.0)
        if last_text and last_text != baseline_text:
            return last_text
        raise ChatGPTWebError("Timed out while waiting for a text response from ChatGPT Web.")

    def _save_generated_image(self, page: Page, baseline_image_count: int, output_path: Path) -> None:
        deadline = time.time() + self.config.result_timeout_ms / 1000
        while time.time() < deadline:
            images = page.locator("main img")
            count = images.count()
            if count > baseline_image_count:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                images.nth(count - 1).screenshot(path=str(output_path))
                return
            time.sleep(2.0)
        raise ChatGPTWebError("Timed out while waiting for a generated image on ChatGPT Web.")

    def _assistant_text(self, page: Page) -> str:
        candidates = []
        for selector in (
            "[data-message-author-role='assistant']",
            "[data-testid='conversation-turn-assistant']",
            "main article",
        ):
            locator = page.locator(selector)
            count = locator.count()
            for idx in range(count):
                text = locator.nth(idx).inner_text().strip()
                if text:
                    candidates.append(text)
            if candidates:
                return "\n\n".join(candidates)
        return ""

    def _assistant_image_count(self, page: Page) -> int:
        return page.locator("main img").count()

    def _prompt_locator(self, page: Page):
        selectors = [
            "textarea",
            "[contenteditable='true']",
            "[data-testid='composer-text-input']",
        ]
        for selector in selectors:
            locator = page.locator(selector).last
            try:
                locator.wait_for(state="visible", timeout=3_000)
                return locator
            except Exception:
                continue
        raise ChatGPTWebError("Could not locate the ChatGPT Web prompt field.")

    def _wait_for_chat_ready(self, page: Page) -> None:
        if "login" in page.url.lower():
            raise ChatGPTWebError(
                "ChatGPT Web is not logged in for the automation profile. "
                f"Open {self.config.target_url} once with profile '{self.config.profile_dir}' and sign in."
            )

        selectors = [
            "[data-testid='composer-text-input']",
            "textarea",
            "[contenteditable='true']",
            "input[type='file']",
            "button[aria-label*='ChatGPT']",
            "text=Ask ChatGPT",
            "text=Спросите ChatGPT",
        ]
        last_error: Optional[Exception] = None
        for selector in selectors:
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=5_000)
                return
            except Exception as exc:
                last_error = exc
                continue

        deadline = time.time() + self.config.launch_timeout_ms / 1000
        while time.time() < deadline:
            if "login" in page.url.lower():
                raise ChatGPTWebError(
                    "ChatGPT Web is not logged in for the automation profile. "
                    f"Open {self.config.target_url} once with profile '{self.config.profile_dir}' and sign in."
                )
            if page.locator("main").count() > 0:
                return
            time.sleep(1.0)

        raise ChatGPTWebError("ChatGPT Web UI did not become ready.") from last_error
