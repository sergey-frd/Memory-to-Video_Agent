from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

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
    debug_port: Optional[int] = None
    launch_timeout_ms: int = 60_000
    result_timeout_ms: int = 180_000
    manual_verification_timeout_ms: int = 0
    submit: bool = True
    open_new_chat_before_run: bool = False


class ChatGPTWebAgent:
    def __init__(self, config: ChatGPTWebConfig) -> None:
        self.config = config
        self._connected_browser = None
        self._connected_over_cdp = False

    def run(self) -> Optional[Path]:
        self._ensure_dependencies()
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = self._open_browser_context(playwright)
            try:
                return self.run_in_context(context)
            finally:
                self._close_browser_context(context)

    def run_in_context(self, context: BrowserContext) -> Optional[Path]:
        page = self._get_page(context)
        self._prepare_chat(page)
        if self.config.open_new_chat_before_run:
            self._open_new_chat(page)
        baseline_text = self._assistant_text(page)
        if self.config.image_path is not None:
            self._attach_image(page, self.config.image_path)
        self._fill_prompt(page, self.config.prompt_text)
        baseline_image_count = self._assistant_image_count(page)
        baseline_page_image_count = self._page_image_count(page)
        if self.config.submit:
            self._submit(page)
            if self.config.response_text_path is not None:
                response_text = self._wait_for_response_text(page, baseline_text)
                self.config.response_text_path.parent.mkdir(parents=True, exist_ok=True)
                self.config.response_text_path.write_text(response_text, encoding="utf-8")
            if self.config.output_path is not None:
                self._save_generated_image(
                    page,
                    baseline_image_count,
                    baseline_page_image_count,
                    self.config.output_path,
                )
        return self.config.output_path

    def _ensure_dependencies(self) -> None:
        if sync_playwright is None:
            raise ChatGPTWebError("Playwright is not installed. Run 'python -m pip install playwright'.")

    def _launch_context(self, playwright) -> BrowserContext:
        launch_kwargs: dict[str, object] = {
            "user_data_dir": str(self.config.profile_dir),
            "headless": False,
            "args": ["--start-maximized"],
        }
        if self.config.executable_path is not None:
            launch_kwargs["executable_path"] = str(self.config.executable_path)
        else:
            launch_kwargs["channel"] = "chrome"
        return playwright.chromium.launch_persistent_context(**launch_kwargs)

    def _open_browser_context(self, playwright) -> BrowserContext:
        if self.config.debug_port is not None:
            return self._connect_context(playwright)
        return self._launch_context(playwright)

    def _connect_context(self, playwright) -> BrowserContext:
        endpoint = f"http://127.0.0.1:{self.config.debug_port}"
        try:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=self.config.launch_timeout_ms)
        except Exception as exc:
            raise ChatGPTWebError(
                "Could not connect to the already opened ChatGPT Chrome session. "
                f"Make sure login_chatgpt_debug_profile.bat is still open and listening on {endpoint}."
            ) from exc
        self._connected_browser = browser
        self._connected_over_cdp = True
        if browser.contexts:
            return browser.contexts[0]
        raise ChatGPTWebError("Connected to Chrome debugging port, but no browser context is available.")

    def _close_browser_context(self, context: BrowserContext) -> None:
        if self._connected_over_cdp:
            self._connected_browser = None
            self._connected_over_cdp = False
            return
        context.close()

    def _get_page(self, context: BrowserContext) -> Page:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(self.config.target_url, wait_until="domcontentloaded", timeout=self.config.launch_timeout_ms)
        self._wait_for_chat_ready(page)
        return page

    def _prepare_chat(self, page: Page) -> None:
        if "login" in page.url.lower():
            if self.config.manual_verification_timeout_ms > 0:
                self._wait_for_manual_access(page)
                return
            raise ChatGPTWebError(
                "ChatGPT Web is not logged in for the automation profile. "
                f"Open {self.config.target_url} once with profile '{self.config.profile_dir}' and sign in."
            )
        page.goto(self.config.target_url, wait_until="domcontentloaded", timeout=self.config.launch_timeout_ms)
        self._wait_for_chat_ready(page)

    def _wait_for_manual_access(self, page: Page) -> None:
        page.bring_to_front()
        print(
            "Manual ChatGPT access is required. Complete sign-in or the human verification "
            "in the opened Chrome window; automation will continue when the prompt box appears.",
            flush=True,
        )
        self._wait_for_chat_ready(page, allow_manual_wait=True)

    def _open_new_chat(self, page: Page) -> None:
        new_chat_url = self._new_chat_url()
        page.goto(new_chat_url, wait_until="domcontentloaded", timeout=self.config.launch_timeout_ms)
        self._wait_for_chat_ready(page)

        controls = [
            page.get_by_role("link", name=re.compile(r"(new chat|new|новый чат|создать чат)", re.I)).first,
            page.get_by_role("button", name=re.compile(r"(new chat|new|новый чат|создать чат)", re.I)).first,
            page.locator("a[href='/']").first,
        ]
        for control in controls:
            try:
                if control.is_visible(timeout=1_000):
                    control.click(timeout=2_000)
                    self._wait_for_chat_ready(page)
                    return
            except Exception:
                continue

    def _new_chat_url(self) -> str:
        parts = urlsplit(self.config.target_url)
        if parts.scheme and parts.netloc:
            return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        return self.config.target_url

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

    def _save_generated_image(
        self,
        page: Page,
        baseline_image_count: int,
        baseline_page_image_count: int,
        output_path: Path,
    ) -> None:
        deadline = time.time() + self.config.result_timeout_ms / 1000
        while time.time() < deadline:
            assistant_images = self._assistant_images(page)
            if assistant_images is not None:
                count = assistant_images.count()
                if count > baseline_image_count:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    assistant_images.nth(count - 1).screenshot(path=str(output_path))
                    return

            page_images = page.locator("main img")
            count = page_images.count()
            if count > baseline_page_image_count:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                page_images.nth(count - 1).screenshot(path=str(output_path))
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
        images = self._assistant_images(page)
        return images.count() if images is not None else 0

    def _assistant_images(self, page: Page):
        for selector in (
            "[data-message-author-role='assistant'] img",
            "[data-testid='conversation-turn-assistant'] img",
            "main article img",
        ):
            locator = page.locator(selector)
            if locator.count() > 0:
                return locator
        return None

    def _page_image_count(self, page: Page) -> int:
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

    def _wait_for_chat_ready(self, page: Page, allow_manual_wait: bool = False) -> None:
        if "login" in page.url.lower():
            if allow_manual_wait or self.config.manual_verification_timeout_ms > 0:
                page.bring_to_front()
            else:
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
            "text=Ð¡Ð¿Ñ€Ð¾ÑÐ¸Ñ‚Ðµ ChatGPT",
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
        if allow_manual_wait or self.config.manual_verification_timeout_ms > 0:
            deadline = max(deadline, time.time() + self.config.manual_verification_timeout_ms / 1000)
        manual_notice_shown = False
        while time.time() < deadline:
            if "login" in page.url.lower() and not (allow_manual_wait or self.config.manual_verification_timeout_ms > 0):
                raise ChatGPTWebError(
                    "ChatGPT Web is not logged in for the automation profile. "
                    f"Open {self.config.target_url} once with profile '{self.config.profile_dir}' and sign in."
                )
            if self._manual_verification_visible(page) and self.config.manual_verification_timeout_ms > 0:
                if not manual_notice_shown:
                    page.bring_to_front()
                    print(
                        "ChatGPT is showing a human verification check. Please complete it manually "
                        "in the opened Chrome window; automation will resume automatically.",
                        flush=True,
                    )
                    manual_notice_shown = True
            for selector in selectors:
                try:
                    if page.locator(selector).first.is_visible(timeout=500):
                        return
                except Exception:
                    continue
            if page.locator("main").count() > 0 and not self._manual_verification_visible(page):
                return
            time.sleep(1.0)

        raise ChatGPTWebError("ChatGPT Web UI did not become ready.") from last_error

    def _manual_verification_visible(self, page: Page) -> bool:
        try:
            current_url = page.url.lower()
            if any(marker in current_url for marker in ("challenge", "captcha", "verify")):
                return True
            body_text = page.locator("body").inner_text(timeout=1_000).lower()
            return any(
                marker in body_text
                for marker in (
                    "verify you are human",
                    "confirm you are human",
                    "checking if the site connection is secure",
                    "human verification",
                    "captcha",
                    "подтвердите, что вы человек",
                    "подтвердите что вы человек",
                    "проверка",
                )
            )
        except Exception:
            return False

    def _unused_wait_for_chat_ready_old(self, page: Page) -> None:
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


class ChatGPTWebSessionRunner:
    def __init__(self) -> None:
        self._playwright_cm = None
        self._playwright = None
        self._context: BrowserContext | None = None
        self._profile_dir: Path | None = None
        self._target_url: str | None = None
        self._executable_path: Path | None = None
        self._debug_port: int | None = None
        self._connected_over_cdp = False

    def run(self, config: ChatGPTWebConfig) -> Optional[Path]:
        agent = ChatGPTWebAgent(config)
        agent._ensure_dependencies()
        config.profile_dir.mkdir(parents=True, exist_ok=True)
        if self._context is None:
            self._playwright_cm = sync_playwright()
            self._playwright = self._playwright_cm.start()
            try:
                self._context = agent._open_browser_context(self._playwright)
            except Exception:
                self.close()
                raise
            self._profile_dir = config.profile_dir
            self._target_url = config.target_url
            self._executable_path = config.executable_path
            self._debug_port = config.debug_port
            self._connected_over_cdp = agent._connected_over_cdp
        elif (
            self._profile_dir != config.profile_dir
            or self._target_url != config.target_url
            or self._executable_path != config.executable_path
            or self._debug_port != config.debug_port
        ):
            raise ChatGPTWebError("All ChatGPT batch items must use the same profile, target URL, Chrome executable, and debug port.")
        return agent.run_in_context(self._context)

    def close(self) -> None:
        if self._context is not None:
            try:
                if not self._connected_over_cdp:
                    self._context.close()
            finally:
                self._context = None
        if self._playwright_cm is not None:
            try:
                self._playwright_cm.stop()
            finally:
                self._playwright_cm = None
                self._playwright = None
                self._profile_dir = None
                self._target_url = None
                self._executable_path = None
                self._debug_port = None
                self._connected_over_cdp = False
