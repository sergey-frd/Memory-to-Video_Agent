from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2, rmtree
from typing import Optional
from uuid import uuid4

from PIL import Image, ImageChops, ImageOps, ImageStat

try:
    from playwright.sync_api import Browser, BrowserContext, Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
except ModuleNotFoundError:
    Browser = object  # type: ignore[assignment,misc]
    BrowserContext = object  # type: ignore[assignment,misc]
    Locator = object  # type: ignore[assignment,misc]
    Page = object  # type: ignore[assignment,misc]
    PlaywrightTimeoutError = Exception  # type: ignore[assignment]
    sync_playwright = None  # type: ignore[assignment]

try:
    from playwright._impl._errors import TargetClosedError
except Exception:
    TargetClosedError = RuntimeError  # type: ignore[assignment]


INTERFERING_OVERLAY_MARKER_PATTERNS = (
    re.compile(r"share template", re.I),
    re.compile(r"disable ads?", re.I),
    re.compile(r"goldski\w*", re.I),
    re.compile(r"shirty\s*pride", re.I),
    re.compile(r"\b(sponsored|advertisement|promoted)\b", re.I),
    re.compile(r"поделиться шаблоном", re.I),
    re.compile(r"отключить рекламу", re.I),
)

INTERFERING_CANDIDATE_PATTERNS = INTERFERING_OVERLAY_MARKER_PATTERNS + (
    re.compile(r"\b(shop now|learn more|open app|install app|download app|install)\b", re.I),
    re.compile(r"\b(share|поделиться)\b.{0,24}\b(template|шаблон)\b", re.I),
)

OVERLAY_DISMISS_CONTROL_PATTERNS = (
    re.compile(r"^(disable ads?|disable ad|hide ads?|hide ad|dismiss ads?|dismiss ad|close ads?|close ad|remove ads?|remove ad|skip ads?|skip ad|turn off ads?)$", re.I),
    re.compile(r"^(отключить рекламу|скрыть рекламу|убрать рекламу|закрыть рекламу|пропустить рекламу)$", re.I),
    re.compile(r"^(close|dismiss|not now|no thanks|cancel|закрыть|не сейчас|нет, спасибо|отмена)$", re.I),
)


class GrokWebError(RuntimeError):
    pass


@dataclass
class GrokWebConfig:
    prompt_text: str
    image_path: Optional[Path]
    output_path: Path
    profile_dir: Path = Path(".browser-profile/grok-web")
    target_url: str = "https://grok.com/imagine"
    executable_path: Optional[Path] = None
    debug_port: Optional[int] = None
    launch_timeout_ms: int = 60_000
    upload_timeout_ms: int = 180_000
    result_timeout_ms: int = 600_000
    submit: bool = True
    generation_mode: str = "video"
    aspect_ratio: Optional[str] = None
    orientation: Optional[str] = None
    allow_profile_clone_fallback: bool = False
    save_debug_artifacts: bool = False


class GrokWebAgent:
    def __init__(self, config: GrokWebConfig) -> None:
        self.config = config
        self._runtime_profile_dir: Path | None = None
        self._connected_over_cdp = False
        self._connected_browser: Browser | None = None
        self._managed_browser_process: subprocess.Popen | None = None
        self._managed_debug_port: int | None = None

    def _log(self, message: str) -> None:
        print(message, flush=True)

    def _launch_kwargs(self) -> dict[str, object]:
        launch_kwargs: dict[str, object] = {
            "user_data_dir": str(self.config.profile_dir),
            "headless": False,
            "accept_downloads": True,
            "args": ["--start-maximized"],
        }
        if self.config.executable_path is not None:
            launch_kwargs["executable_path"] = str(self.config.executable_path)
        else:
            launch_kwargs["channel"] = "chrome"
        return launch_kwargs

    def run(self) -> Path:
        self._ensure_dependencies()
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            if self.config.debug_port is None:
                self._close_lingering_login_browser(playwright)
                self._terminate_profile_processes()
            if self.config.debug_port is not None:
                try:
                    context = self._connect_context(playwright)
                except GrokWebError:
                    self._log("chrome debug-port connect failed; falling back to profile launch")
                    context = self._launch_managed_context(playwright)
            else:
                context = self._launch_managed_context(playwright)
            try:
                return self.run_in_context(context)
            finally:
                used_managed_browser = self._managed_browser_process is not None
                if self._connected_over_cdp and self._managed_browser_process is not None and self._connected_browser is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                    try:
                        self._connected_browser.close()
                    except Exception:
                        pass
                if not self._connected_over_cdp:
                    context.close()
                self._connected_browser = None
                self._connected_over_cdp = False
                self._cleanup_managed_browser_process()
                if used_managed_browser:
                    self._terminate_profile_processes()
                    self._clear_profile_restore_artifacts()
                self._cleanup_runtime_profile_dir()

    def _close_lingering_login_browser(self, playwright) -> None:
        if self.config.debug_port is not None:
            return
        endpoint = "http://127.0.0.1:9222"
        try:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=1_500)
        except Exception:
            return

        try:
            self._log("closing lingering Grok login browser on debug port 9222")
            browser.close()
            time.sleep(2.0)
        except Exception:
            try:
                browser.close()
            except Exception:
                pass

    def _terminate_profile_processes(self) -> None:
        if os.name != "nt":
            return
        try:
            profile_dir = str(self.config.profile_dir.resolve())
        except OSError:
            profile_dir = str(self.config.profile_dir)
        escaped_profile_dir = profile_dir.replace("'", "''")
        script = (
            f"$profileDir = '{escaped_profile_dir}'.ToLower(); "
            "$procs = Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($profileDir) }; "
            "$ids = @($procs | ForEach-Object { $_.ProcessId }); "
            "foreach ($id in $ids) { "
            "try { $proc = Get-Process -Id $id -ErrorAction Stop; if ($proc.MainWindowHandle -ne 0) { [void]$proc.CloseMainWindow() } } catch { } "
            "} "
            "Start-Sleep -Milliseconds 1500; "
            "foreach ($id in $ids) { "
            "try { Stop-Process -Id $id -ErrorAction SilentlyContinue } catch { } "
            "} "
            "Start-Sleep -Milliseconds 500; "
            "foreach ($id in $ids) { "
            "try { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue } catch { } "
            "}"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return
        if completed.returncode == 0:
            self._log("terminated lingering Chrome processes for the Grok automation profile")
            time.sleep(1.5)

    def _clear_profile_restore_artifacts(self) -> None:
        sessions_dir = self.config.profile_dir / "Default" / "Sessions"
        if not sessions_dir.exists():
            return
        candidates = [path for path in sessions_dir.iterdir() if path.is_file()]
        for candidate in candidates:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                if os.name == "nt":
                    try:
                        subprocess.run(
                            ["cmd", "/c", "del", "/F", "/Q", str(candidate)],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                        )
                    except Exception:
                        pass

    def _resolve_chrome_executable(self) -> str:
        if self.config.executable_path is not None:
            return str(self.config.executable_path)
        if os.name == "nt":
            candidates = [
                Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LocalAppData", "")) / "Google/Chrome/Application/chrome.exe",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
        return "chrome"

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            return int(sock.getsockname()[1])

    def _launch_managed_context(self, playwright) -> BrowserContext:
        chrome_executable = self._resolve_chrome_executable()
        try:
            profile_dir = str(self.config.profile_dir.resolve())
        except OSError:
            profile_dir = str(self.config.profile_dir)
        self._clear_profile_restore_artifacts()

        last_error: Exception | None = None
        for attempt in range(3):
            debug_port = self._find_free_port()
            command = [
                chrome_executable,
                "--new-window",
                f"--remote-debugging-port={debug_port}",
                "--disable-background-mode",
                "--disable-hang-monitor",
                "--hide-crash-restore-bubble",
                "--no-first-run",
                "--disable-sync",
                f"--user-data-dir={profile_dir}",
                "about:blank",
            ]
            try:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                raise GrokWebError(f"Could not start Chrome for Grok automation: {chrome_executable}") from exc

            endpoint = f"http://127.0.0.1:{debug_port}"
            deadline = time.time() + (self.config.launch_timeout_ms / 1000)
            while time.time() < deadline:
                try:
                    browser = playwright.chromium.connect_over_cdp(endpoint, timeout=1_500)
                    self._connected_browser = browser
                    self._connected_over_cdp = True
                    self._managed_browser_process = process
                    self._managed_debug_port = debug_port
                    if browser.contexts:
                        return browser.contexts[0]
                    raise GrokWebError("Managed Chrome started, but no browser context is available.")
                except Exception as exc:
                    last_error = exc
                    if process.poll() is not None:
                        break
                    time.sleep(1.0)

            self._managed_browser_process = process
            self._cleanup_managed_browser_process()
            if attempt < 2:
                self._log(f"managed chrome launch retry {attempt + 1}/2")
                time.sleep(2.0 + attempt)

        raise GrokWebError(
            "Chrome started for Grok automation but the automation session could not attach to it."
        ) from last_error

    def _cleanup_managed_browser_process(self) -> bool:
        process = self._managed_browser_process
        self._managed_browser_process = None
        self._managed_debug_port = None
        if process is None:
            return False
        forced_termination = False
        try:
            if process.poll() is None:
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=10,
                        )
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            subprocess.run(
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                check=False,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=10,
                            )
                            forced_termination = True
                            process.wait(timeout=5)
                    else:
                        process.terminate()
                        process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                forced_termination = True
            except Exception:
                pass
        return forced_termination

    def _connect_context(self, playwright) -> BrowserContext:
        endpoint = f"http://127.0.0.1:{self.config.debug_port}"
        try:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=self.config.launch_timeout_ms)
        except Exception as exc:
            raise GrokWebError(
                "Could not connect to the already opened Chrome automation session. "
                f"Make sure login_grok_profile.bat is still open and listening on {endpoint}."
            ) from exc

        self._connected_browser = browser
        self._connected_over_cdp = True
        if browser.contexts:
            return browser.contexts[0]
        raise GrokWebError(
            "Connected to Chrome debugging port, but no browser context is available. "
            "Open Grok in the login automation window and try again."
        )

    def run_in_context(self, context: BrowserContext) -> Path:
        page = self._get_page(context)
        baseline_video_count = self._video_count(page)
        baseline_video_signatures = self._video_signatures(page)
        baseline_image_count = self._image_count(page)
        baseline_image_signatures = self._image_signatures(page)
        baseline_downloads = self._downloads_snapshot()
        if self.config.generation_mode == "image":
            self._configure_image_generation(page)
        else:
            self._configure_video_generation(page)
        if self.config.image_path is not None:
            self._attach_image(page, self.config.image_path)
        self._fill_prompt(page, self.config.prompt_text)
        if self.config.generation_mode == "image":
            baseline_image_count = self._image_count(page)
            baseline_image_signatures = self._image_signatures(page)
        if self.config.submit:
            self._submit(page)
            if self.config.generation_mode == "image":
                self._save_generated_image(
                    page,
                    baseline_image_count,
                    baseline_image_signatures,
                    baseline_downloads,
                    self.config.output_path,
                )
            else:
                self._save_generated_video(
                    page,
                    baseline_video_count,
                    baseline_video_signatures,
                    baseline_downloads,
                    self.config.output_path,
                )
        return self.config.output_path

    def _launch_context(self, playwright, launch_kwargs: dict[str, object]) -> BrowserContext:
        if self.config.profile_dir.exists():
            self._remove_profile_lock_files(self.config.profile_dir)
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                return playwright.chromium.launch_persistent_context(**launch_kwargs)
            except TargetClosedError as exc:
                last_error = exc
                if attempt < 3:
                    self._log(f"chrome launch retry {attempt + 1}/3")
                    time.sleep(2.0 + attempt)
                    continue
                break
        if self.config.allow_profile_clone_fallback:
            cloned_context = self._launch_with_cloned_profile(playwright, launch_kwargs, last_error)
            if cloned_context is not None:
                return cloned_context
        if last_error is not None:
            self._log("Grok profile is busy; close the automation Chrome window and retry")
            raise GrokWebError(
                "Chrome did not start with the Grok automation profile. "
                "Most often this means Chrome is already open with the same profile directory, "
                "or the previous automation run has not fully released the profile yet. "
                "Profile-clone fallback is intentionally disabled here because it can lose Grok authorization "
                "and open the Sign up page instead of the logged-in session. "
                f"Close all Chrome windows that use '{self.config.profile_dir}', wait a few seconds, then run the command again."
            ) from last_error
        raise GrokWebError("Chrome automation launch failed.") from last_error

    def _launch_with_cloned_profile(self, playwright, launch_kwargs: dict[str, object], original_error: Exception | None) -> BrowserContext | None:
        source_profile = self.config.profile_dir
        if not source_profile.exists():
            return None

        runtime_profile = source_profile.parent / f"{source_profile.name}-runtime-{uuid4().hex}"
        runtime_profile.mkdir(parents=True, exist_ok=False)
        try:
            self._clone_profile_tree(source_profile, runtime_profile)
            self._remove_profile_lock_files(runtime_profile)
            cloned_kwargs = dict(launch_kwargs)
            cloned_kwargs["user_data_dir"] = str(runtime_profile)
            context = playwright.chromium.launch_persistent_context(**cloned_kwargs)
            self._runtime_profile_dir = runtime_profile
            self._log(f"chrome profile clone fallback active: {runtime_profile}")
            return context
        except Exception:
            self._cleanup_path(runtime_profile)
            return None

    def _clone_profile_tree(self, source_profile: Path, runtime_profile: Path) -> None:
        ignored_dir_names = {
            "Cache",
            "Code Cache",
            "GPUCache",
            "GrShaderCache",
            "GraphiteDawnCache",
            "DawnCache",
            "Crashpad",
            "BrowserMetrics",
            "Safe Browsing",
            "optimization_guide_hint_cache_store",
            "pnacl",
        }
        ignored_file_names = {"lockfile", "LOCK"}
        ignored_prefixes = ("Singleton",)
        ignored_suffixes = (".tmp", ".temp", ".lock")

        runtime_profile.mkdir(parents=True, exist_ok=True)
        for root, dir_names, file_names in os.walk(source_profile):
            root_path = Path(root)
            relative_root = root_path.relative_to(source_profile)
            dir_names[:] = [
                name
                for name in dir_names
                if name not in ignored_dir_names
                and not name.startswith(ignored_prefixes)
                and not name.endswith(ignored_suffixes)
            ]
            destination_root = runtime_profile / relative_root
            destination_root.mkdir(parents=True, exist_ok=True)

            for file_name in file_names:
                if (
                    file_name in ignored_file_names
                    or file_name.startswith(ignored_prefixes)
                    or file_name.endswith(ignored_suffixes)
                ):
                    continue
                source_path = root_path / file_name
                destination_path = destination_root / file_name
                try:
                    copy2(source_path, destination_path)
                except OSError:
                    continue

    def _remove_profile_lock_files(self, profile_dir: Path) -> None:
        for pattern in ("Singleton*", "*.lock", "lockfile", "LOCK"):
            for path in profile_dir.rglob(pattern):
                if path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        continue

    def _cleanup_runtime_profile_dir(self) -> None:
        if self._runtime_profile_dir is None:
            return
        self._cleanup_path(self._runtime_profile_dir)
        self._runtime_profile_dir = None

    def _cleanup_path(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            rmtree(path)
        except OSError:
            pass

    def _ensure_dependencies(self) -> None:
        if sync_playwright is None:
            raise GrokWebError("Playwright is not installed. Run 'python -m pip install playwright'.")


    def _get_page(self, context: BrowserContext) -> Page:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(self.config.target_url, wait_until="domcontentloaded", timeout=self.config.launch_timeout_ms)
        self._ensure_imagine_mode(page)
        self._wait_for_ready(page)
        self._dismiss_interfering_overlay(page)
        return page

    def _ensure_imagine_mode(self, page: Page) -> None:
        if "/imagine" in page.url:
            self._raise_if_auth_required(page)
            return
        imagine_link = page.locator("a[href='/imagine']").first
        try:
            imagine_link.click(timeout=5_000)
            page.wait_for_load_state("domcontentloaded", timeout=self.config.launch_timeout_ms)
        except Exception:
            pass
        self._raise_if_auth_required(page)

    def _raise_if_auth_required(self, page: Page) -> None:
        sign_in = page.locator("a[href='/sign-in']")
        sign_up = page.locator("a[href='/sign-up']")
        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=2_000)
        except Exception:
            body_text = ""
        if sign_in.count() and sign_up.count() and "Sign in" in body_text and "Sign up" in body_text:
            raise GrokWebError(
                "Grok Web is not authenticated for video generation in the current Chrome profile. "
                f"Open {self.config.target_url} with profile '{self.config.profile_dir}', sign in manually, "
                "then run the command again."
            )

    def _wait_for_ready(self, page: Page) -> None:
        selectors = [
            "textarea",
            "[contenteditable='true']",
            "input[type='file']",
            "main",
        ]
        last_error: Optional[Exception] = None
        for selector in selectors:
            try:
                page.locator(selector).first.wait_for(state="attached", timeout=5_000)
                if selector in {"textarea", "[contenteditable='true']", "input[type='file']"}:
                    return
            except Exception as exc:
                last_error = exc
                continue

        deadline = time.time() + self.config.launch_timeout_ms / 1000
        while time.time() < deadline:
            self._raise_if_auth_required(page)
            if page.locator("textarea").count() or page.locator("[contenteditable='true']").count():
                return
            if "login" in page.url.lower() or "sign-in" in page.url.lower():
                raise GrokWebError(
                    "Grok Web is not logged in for the automation profile. "
                    f"Open {self.config.target_url} once with profile '{self.config.profile_dir}' and sign in."
                )
            time.sleep(1.0)
        raise GrokWebError("Grok Web UI did not become ready.") from last_error

    def _configure_image_generation(self, page: Page) -> None:
        if not self._click_named_control(
            page,
            [
                re.compile(r"^(image|images|picture|photo|art)$", re.I),
                re.compile(r"(image|images|picture|photo|artwork|illustration)", re.I),
            ],
        ):
            raise GrokWebError("Could not switch Grok Imagine to image generation mode.")

        if self.config.aspect_ratio:
            if not self._set_aspect_ratio(page, self.config.aspect_ratio):
                raise GrokWebError(f"Could not set Grok image aspect ratio to {self.config.aspect_ratio}.")

        if self.config.orientation:
            if not self._set_orientation(page, self.config.orientation):
                if self._orientation_is_implied():
                    self._log(
                        f"orientation control not found; continuing because {self.config.aspect_ratio} already implies {self.config.orientation}"
                    )
                else:
                    raise GrokWebError(f"Could not set Grok image orientation to {self.config.orientation}.")

    def _configure_video_generation(self, page: Page) -> None:
        self._click_named_control(
            page,
            [
                re.compile(r"^(video|videos|movie|clip|animation)$", re.I),
                re.compile(r"(video|videos|movie|clip|animation)", re.I),
            ],
        )

    def _click_named_control(self, page: Page, patterns: list[re.Pattern[str]]) -> bool:
        for pattern in patterns:
            candidate_groups = [
                page.get_by_role("button", name=pattern),
                page.get_by_role("tab", name=pattern),
                page.get_by_role("radio", name=pattern),
                page.get_by_role("option", name=pattern),
                page.get_by_text(pattern),
            ]
            for group in candidate_groups:
                try:
                    count = group.count()
                except Exception:
                    count = 0
                for index in range(count):
                    candidate = group.nth(index)
                    try:
                        candidate.wait_for(state="visible", timeout=1_000)
                        candidate.click(timeout=2_000)
                        return True
                    except Exception:
                        continue
        return False

    def _interfering_overlay_markers(self, page: Page) -> list[str]:
        body_text = self._safe_body_text(page)
        if not body_text:
            return []
        markers: list[str] = []
        for pattern in INTERFERING_OVERLAY_MARKER_PATTERNS:
            match = pattern.search(body_text)
            if match:
                markers.append(match.group(0))
        return markers

    def _dismiss_interfering_overlay(self, page: Page) -> bool:
        markers = self._interfering_overlay_markers(page)
        if not markers:
            return False

        for pattern in OVERLAY_DISMISS_CONTROL_PATTERNS:
            if self._click_named_control(page, [pattern]):
                self._log(f"dismissed interfering Grok overlay ({markers[0]})")
                time.sleep(0.5)
                return True

        try:
            page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception:
            return False

        if not self._interfering_overlay_markers(page):
            self._log(f"dismissed interfering Grok overlay with Escape ({markers[0]})")
            return True
        return False

    def _set_aspect_ratio(self, page: Page, aspect_ratio: str) -> bool:
        direct_patterns = [
            re.compile(rf"^{re.escape(aspect_ratio)}$", re.I),
            re.compile(rf"(aspect|ratio|size|format).+{re.escape(aspect_ratio)}", re.I),
        ]
        if self._click_named_control(page, direct_patterns):
            return True

        if self._click_named_control(page, [re.compile(r"(aspect|ratio|size|format)", re.I)]):
            return self._click_named_control(page, [re.compile(rf"^{re.escape(aspect_ratio)}$", re.I)])
        return False

    def _set_orientation(self, page: Page, orientation: str) -> bool:
        patterns = [
            re.compile(r"(horizontal|landscape|wide|panorama|widescreen)", re.I),
            re.compile(re.escape(orientation), re.I),
        ]
        if self._click_named_control(page, patterns):
            return True

        if self._click_named_control(page, [re.compile(r"(orientation|layout|rotate|direction)", re.I)]):
            return self._click_named_control(page, patterns)
        return False

    def _orientation_is_implied(self) -> bool:
        orientation = (self.config.orientation or "").strip().lower()
        aspect_ratio = (self.config.aspect_ratio or "").strip().lower()
        if orientation != "horizontal":
            return False
        return aspect_ratio in {"16:9", "21:9", "4:3", "3:2", "5:3", "2:1"}

    def _attach_image(self, page: Page, image_path: Path) -> None:
        if not image_path.exists():
            raise FileNotFoundError(f"Input image was not found: {image_path}")

        self._dismiss_interfering_overlay(page)
        self._log("upload started")
        file_input = page.locator("input[type='file']").first
        try:
            file_input.set_input_files(str(image_path), timeout=5_000)
            return
        except Exception:
            pass

        attach_button = page.get_by_role(
            "button",
            name=re.compile(r"(attach|upload|image|photo|file|add|plus|clip|paperclip|загруз|файл|изображ)", re.I),
        ).first
        try:
            attach_button.click(timeout=5_000)
        except Exception as exc:
            raise GrokWebError("Could not find the file attach control on Grok Web.") from exc

        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(str(image_path), timeout=10_000)

    def _fill_prompt(self, page: Page, prompt_text: str) -> None:
        self._dismiss_interfering_overlay(page)
        box = self._prompt_locator(page)
        try:
            box.click(timeout=10_000)
            box.fill(prompt_text, timeout=10_000)
            if self._prompt_matches(box, prompt_text):
                return
        except Exception:
            pass

        editable = page.locator("[contenteditable='true']").last
        try:
            editable.click(timeout=10_000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.insert_text(prompt_text)
            if self._prompt_matches(editable, prompt_text):
                return
        except Exception as exc:
            raise GrokWebError("Could not find the Grok prompt input.") from exc
        raise GrokWebError("Grok prompt field did not retain the background/video prompt text.")

    def _prompt_matches(self, locator: Locator, prompt_text: str) -> bool:
        actual_text = self._read_prompt_value(locator)
        expected = self._normalize_prompt_text(prompt_text)
        actual = self._normalize_prompt_text(actual_text)
        if not expected:
            return not actual
        if actual == expected:
            return True
        return len(actual) >= min(len(expected), 80) and actual.startswith(expected[: min(len(expected), 80)])

    @staticmethod
    def _normalize_prompt_text(text: str) -> str:
        return " ".join(text.lower().split())

    def _read_prompt_value(self, locator: Locator) -> str:
        try:
            payload = locator.evaluate(
                """(element) => {
                    if (!element) return '';
                    if (typeof element.value === 'string' && element.value) return element.value;
                    if (typeof element.innerText === 'string' && element.innerText) return element.innerText;
                    if (typeof element.textContent === 'string' && element.textContent) return element.textContent;
                    return '';
                }"""
            )
        except Exception:
            return ""
        return str(payload or "")

    def _click_nearest_prompt_submit_button(self, page: Page) -> bool:
        try:
            selector = page.evaluate(
                """() => {
                    const visible = (element) => {
                        if (!element) return false;
                        const style = window.getComputedStyle(element);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return false;
                        }
                        const rect = element.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };

                    const promptCandidates = Array.from(
                        document.querySelectorAll("textarea, [contenteditable='true'], [role='textbox']")
                    ).filter(visible);
                    const prompt = promptCandidates[promptCandidates.length - 1];
                    if (!prompt) {
                        return '';
                    }

                    const promptRect = prompt.getBoundingClientRect();
                    const sendPattern = /(send|generate|create|submit|go|arrow|up|plane|paper|отправ|сгенер|создат)/i;
                    let best = null;

                    Array.from(document.querySelectorAll('button,[role="button"]')).forEach((button, index) => {
                        if (!visible(button)) return;
                        if (button.disabled || button.getAttribute('aria-disabled') === 'true') return;

                        const rect = button.getBoundingClientRect();
                        const label = [
                            button.innerText || '',
                            button.getAttribute('aria-label') || '',
                            button.getAttribute('title') || '',
                            button.getAttribute('data-testid') || '',
                            typeof button.className === 'string' ? button.className : '',
                        ]
                            .join(' ')
                            .replace(/\\s+/g, ' ')
                            .trim();

                        const horizontalGap = Math.abs(rect.left - promptRect.right);
                        const verticalGap = Math.abs((rect.top + rect.height / 2) - (promptRect.top + promptRect.height / 2));
                        const keywordMatch = sendPattern.test(label);
                        const nearbyPrompt = horizontalGap <= 260 && verticalGap <= 120 && rect.left >= promptRect.left - 40;

                        if (!keywordMatch && !nearbyPrompt) {
                            return;
                        }

                        const score = (keywordMatch ? 10_000 : 0) - horizontalGap - verticalGap - index;
                        if (!best || score > best.score) {
                            const candidateId = button.getAttribute('data-codex-submit-candidate') || `codex-submit-${Math.random().toString(36).slice(2, 10)}`;
                            button.setAttribute('data-codex-submit-candidate', candidateId);
                            best = {
                                score,
                                selector: `[data-codex-submit-candidate="${candidateId}"]`,
                            };
                        }
                    });

                    return best ? best.selector : '';
                }"""
            )
        except Exception:
            return False

        if not isinstance(selector, str) or not selector:
            return False

        candidate = page.locator(selector).first
        try:
            candidate.click(timeout=5_000)
            return True
        except Exception:
            pass

        try:
            return bool(
                page.evaluate(
                    """(buttonSelector) => {
                        const element = document.querySelector(buttonSelector);
                        if (!element) {
                            return false;
                        }
                        element.click();
                        return true;
                    }""",
                    selector,
                )
            )
        except Exception:
            return False

    def _submit_button_state(self, page: Page) -> dict[str, object] | None:
        try:
            payload = page.evaluate(
                """() => {
                    const visible = (element) => {
                        if (!element) return false;
                        const style = window.getComputedStyle(element);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return false;
                        }
                        const rect = element.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };

                    const promptCandidates = Array.from(
                        document.querySelectorAll("textarea, [contenteditable='true'], [role='textbox']")
                    ).filter(visible);
                    const prompt = promptCandidates[promptCandidates.length - 1];
                    const promptRect = prompt ? prompt.getBoundingClientRect() : null;
                    const submitPattern = /(submit|send|generate|create|go|arrow|up|plane|paper|отправ|создат|сгенер)/i;

                    const candidates = Array.from(document.querySelectorAll('button,[role="button"]'))
                        .filter(visible)
                        .map((button, index) => {
                            const rect = button.getBoundingClientRect();
                            const label = [
                                button.innerText || '',
                                button.getAttribute('aria-label') || '',
                                button.getAttribute('title') || '',
                                button.getAttribute('data-testid') || '',
                                typeof button.className === 'string' ? button.className : '',
                            ]
                                .join(' ')
                                .replace(/\\s+/g, ' ')
                                .trim();

                            let score = 0;
                            if ((button.getAttribute('type') || '').toLowerCase() === 'submit') score += 100000;
                            if (submitPattern.test(label)) score += 10000;
                            if (promptRect) {
                                const horizontalGap = Math.abs(rect.left - promptRect.right);
                                const verticalGap = Math.abs((rect.top + rect.height / 2) - (promptRect.bottom - 20));
                                if (horizontalGap <= 120 && verticalGap <= 120) {
                                    score += 5000 - horizontalGap - verticalGap;
                                }
                            }
                            if (score <= 0) return null;

                            const existing = button.getAttribute('data-codex-submit-id');
                            const submitId = existing || `codex-submit-${Math.random().toString(36).slice(2, 10)}`;
                            if (!existing) {
                                button.setAttribute('data-codex-submit-id', submitId);
                            }

                            return {
                                selector: `[data-codex-submit-id="${submitId}"]`,
                                disabled: !!button.disabled || button.getAttribute('aria-disabled') === 'true',
                                text: (button.innerText || '').trim(),
                                ariaLabel: button.getAttribute('aria-label') || '',
                                score,
                                index,
                            };
                        })
                        .filter(Boolean)
                        .sort((left, right) => right.score - left.score || left.index - right.index);

                    return candidates[0] || null;
                }"""
            )
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _click_submit_button(self, page: Page, submit_state: dict[str, object]) -> bool:
        selector = str(submit_state.get("selector", "")).strip()
        if not selector:
            return False

        candidate = page.locator(selector).first
        try:
            candidate.click(timeout=5_000)
            return True
        except Exception:
            pass

        try:
            return bool(
                page.evaluate(
                    """(buttonSelector) => {
                        const element = document.querySelector(buttonSelector);
                        if (!element) {
                            return false;
                        }
                        element.click();
                        return true;
                    }""",
                    selector,
                )
            )
        except Exception:
            return False

    def _nudge_prompt_submit_controls(self, page: Page) -> bool:
        try:
            nudged = page.evaluate(
                """() => {
                    const visible = (element) => {
                        if (!element) return false;
                        const style = window.getComputedStyle(element);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return false;
                        }
                        const rect = element.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };

                    const promptCandidates = Array.from(
                        document.querySelectorAll("textarea, [contenteditable='true'], [role='textbox']")
                    ).filter(visible);
                    const prompt = promptCandidates[promptCandidates.length - 1];
                    if (!prompt) {
                        return false;
                    }
                    const promptRect = prompt.getBoundingClientRect();

                    const candidates = Array.from(document.querySelectorAll('button[role="radio"], button'))
                        .filter(visible)
                        .map((button, index) => {
                            const rect = button.getBoundingClientRect();
                            const label = (button.innerText || '').trim();
                            const selected = button.getAttribute('aria-checked') === 'true';
                            let score = 0;
                            if (selected) score += 10000;
                            if (label === '6s' || label === '5s') score += 1000;
                            if (label === 'Video') score += 800;
                            if (label === '480p' || label === '720p') score += 600;
                            const belowPrompt = rect.top >= promptRect.bottom - 8 && rect.top <= promptRect.bottom + 56;
                            if (belowPrompt) score += 500;
                            if (rect.left >= promptRect.left - 12 && rect.right <= promptRect.right + 12) score += 300;
                            if (score <= 0) return null;
                            return { button, score, index };
                        })
                        .filter(Boolean)
                        .sort((left, right) => right.score - left.score || right.index - left.index);

                    if (!candidates.length) {
                        return false;
                    }

                    candidates[0].button.click();
                    return true;
                }"""
            )
        except Exception:
            return False
        return bool(nudged)

    def _wait_for_submit_enabled(self, page: Page) -> dict[str, object] | None:
        deadline = time.time() + 12.0
        nudge_attempts = 0

        while time.time() < deadline:
            self._dismiss_interfering_overlay(page)
            submit_state = self._submit_button_state(page)
            if submit_state and not bool(submit_state.get("disabled")):
                return submit_state

            if nudge_attempts < 3 and self._nudge_prompt_submit_controls(page):
                nudge_attempts += 1

            time.sleep(0.5)

        submit_state = self._submit_button_state(page)
        if submit_state and not bool(submit_state.get("disabled")):
            return submit_state
        return submit_state

    def _submit(self, page: Page) -> None:
        self._dismiss_interfering_overlay(page)
        if self.config.image_path is not None:
            self._wait_for_upload_ready(page)
        submit_state = self._wait_for_submit_enabled(page)
        if submit_state and not bool(submit_state.get("disabled")) and self._click_submit_button(page, submit_state):
            self._log("submit clicked")
            return
        send_patterns = [
            re.compile(r"(send|generate|create|submit|go|отправ|создат|сгенер)", re.I),
            re.compile(r"(arrow up|submit prompt)", re.I),
        ]
        for pattern in send_patterns:
            button = page.get_by_role("button", name=pattern).first
            try:
                button.click(timeout=5_000)
                self._log("submit clicked")
                return
            except Exception:
                continue

        if self._click_nearest_prompt_submit_button(page):
            self._log("submit clicked")
            return

        box = self._prompt_locator(page)
        try:
            box.press("Control+Enter")
            self._log("submit clicked")
            return
        except Exception:
            pass
        box.press("Enter")
        self._log("submit clicked")

    def _wait_for_upload_ready(self, page: Page) -> None:
        deadline = time.time() + self.config.upload_timeout_ms / 1000
        stable_polls = 0
        last_error: Optional[Exception] = None

        while time.time() < deadline:
            self._raise_if_auth_required(page)
            self._dismiss_interfering_overlay(page)
            try:
                state = self._upload_state(page)
            except Exception as exc:
                last_error = exc
                time.sleep(1.0)
                continue

            looks_ready = (
                not state["uploadBusyText"]
                and (
                    state["sendReady"]
                    or state["attachmentDetected"]
                    or state["fileNameDetected"]
                )
            )
            if looks_ready and (state["busyCount"] == 0 or stable_polls >= 1):
                stable_polls += 1
                if stable_polls >= 2:
                    self._log("upload ready")
                    return
            else:
                stable_polls = 0

            time.sleep(1.0)

        try:
            final_state = self._upload_state(page)
        except Exception as exc:
            final_state = None
            last_error = last_error or exc

        if final_state and not final_state["uploadBusyText"] and (
            final_state["attachmentDetected"] or final_state["fileNameDetected"] or final_state["sendControlsPresent"]
        ):
            self._log("upload ready")
            return

        debug_paths = self._write_debug_snapshot(page, self.config.output_path, "upload-wait-timeout")
        raise GrokWebError(
            "Image upload on Grok Web did not reach a ready state before submit. "
            f"Debug artifacts saved to: {debug_paths['screenshot']}, {debug_paths['html']}, {debug_paths['json']}"
        ) from last_error

    def _upload_state(self, page: Page) -> dict[str, object]:
        if self.config.image_path is None:
            return {
                "busyCount": 0,
                "uploadBusyText": False,
                "sendReady": True,
                "sendControlsPresent": True,
                "attachmentDetected": False,
                "fileNameDetected": False,
            }
        filename = self.config.image_path.name.lower()
        stem = self.config.image_path.stem.lower()
        state = page.evaluate(
            """(payload) => {
                const visible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return false;
                    }
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const busySelectors = [
                    'progress',
                    '[role="progressbar"]',
                    '[aria-busy="true"]',
                    '[data-testid*="progress"]',
                    '[class*="progress"]',
                    '[class*="spinner"]',
                    '[class*="loading"]'
                ];

                const busyCount = busySelectors
                    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                    .filter(visible).length;

                const bodyText = (document.body?.innerText || '').toLowerCase();
                const uploadBusyText = /(uploading|processing image|processing file|preparing image|preparing file|loading image|loading file|analyzing image|analyzing file)/i.test(bodyText);

                const sendPattern = /(send|generate|create|submit|go|arrow up|submit prompt)/i;
                const sendControls = Array.from(document.querySelectorAll('button,[role="button"]')).filter((element) => {
                    if (!visible(element)) return false;
                    const label = [
                        element.innerText || '',
                        element.getAttribute('aria-label') || '',
                        element.getAttribute('title') || ''
                    ].join(' ');
                    return sendPattern.test(label);
                });

                const sendReady = sendControls.some((element) => {
                    return !element.disabled && element.getAttribute('aria-disabled') !== 'true';
                });

                const attachmentSelectors = [
                    'img',
                    '[data-testid*="attachment"]',
                    '[data-testid*="upload"]',
                    '[class*="attachment"]',
                    '[class*="upload"]',
                    '[class*="preview"]'
                ];

                const attachmentDetected = attachmentSelectors
                    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                    .some((element) => {
                        if (!visible(element)) return false;
                        const text = [
                            element.innerText || '',
                            element.getAttribute('aria-label') || '',
                            element.getAttribute('title') || '',
                            element.getAttribute('alt') || '',
                            element.getAttribute('src') || ''
                        ].join(' ').toLowerCase();
                        return text.includes(payload.filename) || text.includes(payload.stem) || element.tagName.toLowerCase() === 'img';
                    });

                return {
                    busyCount,
                    uploadBusyText,
                    sendReady,
                    sendControlsPresent: sendControls.length > 0,
                    attachmentDetected,
                    fileNameDetected: bodyText.includes(payload.filename) || bodyText.includes(payload.stem),
                };
            }""",
            {"filename": filename, "stem": stem},
        )
        if not isinstance(state, dict):
            raise GrokWebError("Could not inspect Grok upload state.")
        return state

    def _prompt_locator(self, page: Page):
        selectors = [
            "textarea[aria-label*='Grok']",
            "textarea[placeholder*='help']",
            "textarea",
            "[contenteditable='true']",
            "[role='textbox']",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            count = locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=1_500)
                    return candidate
                except Exception:
                    continue
        raise GrokWebError("Could not locate the Grok prompt field.")

    def _video_count(self, page: Page) -> int:
        return page.locator("video").count()

    def _video_signatures(self, page: Page) -> list[str]:
        signatures: list[str] = []
        videos = page.locator("video")
        count = videos.count()
        for index in range(count):
            try:
                signature = videos.nth(index).evaluate(
                    """(video) => {
                        return video.currentSrc || video.src || (video.querySelector('source') ? video.querySelector('source').src : '');
                    }"""
                )
            except Exception:
                signature = ""
            if isinstance(signature, str) and signature:
                signatures.append(signature)
        return signatures

    def _image_count(self, page: Page) -> int:
        return len(self._image_metadata(page))

    def _image_signatures(self, page: Page) -> list[str]:
        return [item["signature"] for item in self._image_metadata(page)]

    def _image_metadata(self, page: Page) -> list[dict[str, object]]:
        try:
            payload = page.evaluate(
                """() => {
                    const visible = (element) => {
                        if (!element) return false;
                        const style = window.getComputedStyle(element);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return false;
                        }
                        const rect = element.getBoundingClientRect();
                        return rect.width > 120 && rect.height > 120;
                    };
                    const ensureCandidateId = (element, kind, index) => {
                        const existing = element.getAttribute('data-codex-candidate-id');
                        if (existing) {
                            return existing;
                        }
                        const generated = `codex-${kind}-${index}-${Math.random().toString(36).slice(2, 10)}`;
                        element.setAttribute('data-codex-candidate-id', generated);
                        return generated;
                    };
                    const collectContext = (element) => {
                        const pieces = [];
                        let current = element;
                        for (let depth = 0; current && depth < 4; depth += 1, current = current.parentElement) {
                            const tag = (current.tagName || '').toLowerCase();
                            if (tag === 'body' || tag === 'html' || tag === 'main') {
                                break;
                            }
                            const className = typeof current.className === 'string' ? current.className : '';
                            const payload = [
                                current.innerText || '',
                                current.getAttribute('aria-label') || '',
                                current.getAttribute('title') || '',
                                current.getAttribute('alt') || '',
                                current.getAttribute('data-testid') || '',
                                current.getAttribute('href') || '',
                                className,
                            ]
                                .join(' ')
                                .replace(/\\s+/g, ' ')
                                .trim();
                            if (payload && payload.length <= 260) {
                                pieces.push(payload);
                            }
                        }
                        return pieces.join(' ').slice(0, 420);
                    };
                    const adPattern = /(share template|disable ads?|goldski\\w*|shirty\\s*pride|sponsored|advertisement|promoted|shop now|learn more|open app|install app|download app|поделиться шаблоном|отключить рекламу)/i;

                    const images = Array.from(document.images)
                        .filter(visible)
                        .map((img, index) => {
                            const rect = img.getBoundingClientRect();
                            const contextText = collectContext(img);
                            const href = img.closest('a[href]')?.href || '';
                            return {
                                kind: 'img',
                                index,
                                candidateId: ensureCandidateId(img, 'img', index),
                                src: img.currentSrc || img.src || '',
                                width: img.naturalWidth || img.width || 0,
                                height: img.naturalHeight || img.height || 0,
                                renderedWidth: rect.width || 0,
                                renderedHeight: rect.height || 0,
                                top: rect.top || 0,
                                signature: `img|${img.currentSrc || img.src || ''}|${img.naturalWidth || img.width || 0}x${img.naturalHeight || img.height || 0}`,
                                contextText,
                                href,
                                isLikelyAd: adPattern.test(`${contextText} ${href}`),
                            };
                        });

                    const canvases = Array.from(document.querySelectorAll('canvas'))
                        .filter(visible)
                        .map((canvas, index) => {
                            const rect = canvas.getBoundingClientRect();
                            let contentKey = `${canvas.width || rect.width || 0}x${canvas.height || rect.height || 0}`;
                            try {
                                contentKey = canvas.toDataURL('image/png').slice(0, 128);
                            } catch (error) {
                                // ignore canvas export failures
                            }
                            const contextText = collectContext(canvas);
                            const href = canvas.closest('a[href]')?.href || '';
                            return {
                                kind: 'canvas',
                                index,
                                candidateId: ensureCandidateId(canvas, 'canvas', index),
                                src: '',
                                width: canvas.width || rect.width || 0,
                                height: canvas.height || rect.height || 0,
                                renderedWidth: rect.width || 0,
                                renderedHeight: rect.height || 0,
                                top: rect.top || 0,
                                signature: `canvas|${contentKey}`,
                                contextText,
                                href,
                                isLikelyAd: adPattern.test(`${contextText} ${href}`),
                            };
                        });

                    const backgroundElements = Array.from(document.querySelectorAll('div, figure, section, article, a'))
                        .filter((element) => {
                            if (!visible(element)) return false;
                            const style = window.getComputedStyle(element);
                            const backgroundImage = style.backgroundImage || '';
                            if (!backgroundImage || backgroundImage === 'none' || !backgroundImage.includes('url(')) {
                                return false;
                            }
                            const rect = element.getBoundingClientRect();
                            return rect.width > 220 && rect.height > 220;
                        })
                        .map((element, index) => {
                            const rect = element.getBoundingClientRect();
                            const style = window.getComputedStyle(element);
                            const contextText = collectContext(element);
                            const href = element.closest('a[href]')?.href || element.getAttribute('href') || '';
                            return {
                                kind: 'background',
                                index,
                                candidateId: ensureCandidateId(element, 'background', index),
                                src: style.backgroundImage || '',
                                width: rect.width || 0,
                                height: rect.height || 0,
                                renderedWidth: rect.width || 0,
                                renderedHeight: rect.height || 0,
                                top: rect.top || 0,
                                signature: `background|${style.backgroundImage || ''}|${Math.round(rect.width || 0)}x${Math.round(rect.height || 0)}`,
                                contextText,
                                href,
                                isLikelyAd: adPattern.test(`${contextText} ${href}`),
                            };
                        });

                    return [...images, ...canvases, ...backgroundElements];
                }"""
            )
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _candidate_locator(self, page: Page, item: dict[str, object]) -> Locator:
        candidate_id = str(item.get("candidateId", "")).strip()
        if candidate_id:
            return page.locator(f'[data-codex-candidate-id="{candidate_id}"]').first

        kind = str(item.get("kind", "img")).strip().lower()
        selector = {
            "canvas": "canvas",
            "background": "div, figure, section, article, a",
        }.get(kind, "img")
        index = int(item.get("index", 0))
        return page.locator(selector).nth(index)

    def _candidate_priority_score(self, item: dict[str, object]) -> float:
        kind = str(item.get("kind", "img")).strip().lower()
        kind_priority = {
            "background": 4.0,
            "canvas": 3.0,
            "img": 2.0,
        }.get(kind, 1.0)
        rendered_area = float(item.get("renderedWidth", 0)) * float(item.get("renderedHeight", 0))
        natural_area = float(item.get("width", 0)) * float(item.get("height", 0))
        top = float(item.get("top", 0))
        return (kind_priority * 1_000_000_000.0) + (top * 1_000_000.0) + rendered_area + (natural_area / 10.0)

    def _candidate_looks_like_ad_or_template(self, item: dict[str, object]) -> bool:
        if bool(item.get("isLikelyAd")):
            return True
        haystack = " ".join(
            str(item.get(key, "")).strip()
            for key in ("contextText", "href", "signature")
            if str(item.get(key, "")).strip()
        )
        if not haystack:
            return False
        return any(pattern.search(haystack) for pattern in INTERFERING_CANDIDATE_PATTERNS)

    def _recover_open_page(self, page: Page) -> Page | None:
        try:
            context = page.context
        except Exception:
            return None
        try:
            pages = list(context.pages)
        except Exception:
            return None
        for candidate in reversed(pages):
            try:
                if not candidate.is_closed():
                    return candidate
            except Exception:
                continue
        return None

    def _save_generated_video(
        self,
        page: Page,
        baseline_video_count: int,
        baseline_video_signatures: list[str],
        baseline_downloads: dict[str, float],
        output_path: Path,
    ) -> None:
        deadline = time.time() + self.config.result_timeout_ms / 1000
        last_error: Optional[Exception] = None
        active_page = page
        while time.time() < deadline:
            try:
                self._raise_if_auth_required(active_page)
                self._dismiss_interfering_overlay(active_page)
                try:
                    if self._download_from_controls(active_page, output_path):
                        return
                except Exception as exc:
                    last_error = exc

                try:
                    if self._capture_download_from_folder(baseline_downloads, output_path):
                        return
                except Exception as exc:
                    last_error = exc

                videos = active_page.locator("video")
                count = videos.count()
                current_signatures = self._video_signatures(active_page)
                has_new_video = count > baseline_video_count or any(
                    signature for signature in current_signatures if signature not in baseline_video_signatures
                )
                if count and (has_new_video or not baseline_video_signatures):
                    for index in range(count - 1, -1, -1):
                        locator = videos.nth(index)
                        try:
                            self._save_video_from_locator(active_page, locator, output_path)
                            return
                        except Exception as exc:
                            last_error = exc
            except TargetClosedError as exc:
                last_error = exc
                if output_path.exists():
                    return
                try:
                    if self._capture_download_from_folder(baseline_downloads, output_path):
                        return
                except Exception as download_exc:
                    last_error = download_exc
                recovered_page = self._recover_open_page(active_page)
                if recovered_page is not None:
                    active_page = recovered_page
                    time.sleep(1.0)
                    continue
            time.sleep(3.0)
        debug_paths = self._write_debug_snapshot(active_page, output_path, "timeout")
        raise GrokWebError(
            "Timed out while waiting for a generated video on Grok Web. "
            f"Debug artifacts saved to: {debug_paths['screenshot']}, {debug_paths['html']}, {debug_paths['json']}"
        ) from last_error

    def _save_generated_image(
        self,
        page: Page,
        baseline_image_count: int,
        baseline_image_signatures: list[str],
        baseline_downloads: dict[str, float],
        output_path: Path,
    ) -> None:
        deadline = time.time() + self.config.result_timeout_ms / 1000
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            self._raise_if_auth_required(page)
            self._dismiss_interfering_overlay(page)
            try:
                image_busy = self._image_generation_busy(page)
            except Exception as exc:
                image_busy = True
                last_error = exc

            if image_busy:
                time.sleep(3.0)
                continue

            image_metadata = self._image_metadata(page)
            current_signatures = [str(item.get("signature", "")) for item in image_metadata]
            has_new_image = len(image_metadata) > baseline_image_count or any(
                signature for signature in current_signatures if signature and signature not in baseline_image_signatures
            )
            if image_metadata and (has_new_image or not baseline_image_signatures):
                preferred_items = [
                    item
                    for item in image_metadata
                    if str(item.get("signature", "")) not in baseline_image_signatures
                ] or image_metadata
                ranked_items = sorted(
                    preferred_items,
                    key=self._candidate_priority_score,
                    reverse=True,
                )
                ranked_items = [
                    item
                    for item in ranked_items
                    if not self._candidate_looks_like_ad_or_template(item)
                ]
                if not ranked_items:
                    last_error = GrokWebError("Only ad/template image candidates are visible on Grok Web.")
                    time.sleep(3.0)
                    continue
                if self.config.generation_mode == "image":
                    if self._save_best_image_candidate(page, ranked_items, output_path):
                        return
                for item in ranked_items:
                    try:
                        locator = self._candidate_locator(page, item)
                        self._save_image_from_locator(page, locator, output_path)
                        return
                    except Exception as exc:
                        last_error = exc

            try:
                if self._download_from_controls(page, output_path):
                    return
            except Exception as exc:
                last_error = exc

            try:
                if self._capture_download_from_folder(baseline_downloads, output_path, suffixes={".png", ".jpg", ".jpeg", ".webp"}):
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(3.0)

        debug_paths = self._write_debug_snapshot(page, output_path, "image-timeout")
        raise GrokWebError(
            "Timed out while waiting for a generated image on Grok Web. "
            f"Debug artifacts saved to: {debug_paths['screenshot']}, {debug_paths['html']}, {debug_paths['json']}"
        ) from last_error

    def _save_best_image_candidate(self, page: Page, ranked_items: list[dict[str, object]], output_path: Path) -> bool:
        candidate_scores: list[tuple[float, Path, dict[str, object]]] = []
        temporary_candidates: list[Path] = []
        for position, item in enumerate(ranked_items[:4]):
            if self.config.save_debug_artifacts:
                candidate_path = output_path.with_name(f"{output_path.stem}.candidate_{position}{output_path.suffix}")
            else:
                candidate_path = output_path.with_name(f".{output_path.stem}.candidate_{position}{output_path.suffix}")
                temporary_candidates.append(candidate_path)
            try:
                locator = self._candidate_locator(page, item)
                self._save_image_from_locator(page, locator, candidate_path)
                score = -self._candidate_priority_score(item)
                candidate_scores.append((score, candidate_path, item))
            except Exception:
                try:
                    candidate_path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue

        if not candidate_scores:
            self._cleanup_temporary_candidates(temporary_candidates)
            return False

        best_score, best_path, _best_item = min(candidate_scores, key=lambda item: item[0])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        copy2(best_path, output_path)
        if self.config.save_debug_artifacts:
            self._write_image_candidate_report(output_path, candidate_scores, best_path, best_score)
        self._cleanup_temporary_candidates(temporary_candidates)
        return True

    def _cleanup_temporary_candidates(self, candidate_paths: list[Path]) -> None:
        for candidate_path in candidate_paths:
            for attempt in range(5):
                try:
                    candidate_path.unlink(missing_ok=True)
                except OSError:
                    time.sleep(0.1 * (attempt + 1))
                if not candidate_path.exists():
                    break

    def _write_image_candidate_report(
        self,
        output_path: Path,
        candidate_scores: list[tuple[float, Path, dict[str, object]]],
        best_path: Path,
        best_score: float,
    ) -> None:
        report_path = output_path.with_name(f"{output_path.stem}_candidates.json")
        payload = {
            "selected_output": output_path.name,
            "selected_candidate": best_path.name,
            "selected_score": best_score,
            "candidates": [
                {
                    "file": candidate_path.name,
                    "score": score,
                    "kind": item.get("kind"),
                    "index": item.get("index"),
                    "candidateId": item.get("candidateId"),
                    "signature": item.get("signature"),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "renderedWidth": item.get("renderedWidth"),
                    "renderedHeight": item.get("renderedHeight"),
                    "top": item.get("top"),
                }
                for score, candidate_path, item in candidate_scores
            ],
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _image_candidate_similarity_score(self, candidate_path: Path) -> float:
        if self.config.image_path is None:
            return float("inf")
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        with (
            Image.open(self.config.image_path).convert("RGB") as source_image,
            Image.open(candidate_path).convert("RGB") as candidate_image,
        ):
            fitted_source = ImageOps.fit(source_image, candidate_image.size, method=resampling)
            diff = ImageChops.difference(fitted_source, candidate_image)
            stat = ImageStat.Stat(diff)
        rms = [float(value) for value in stat.rms]
        return max(rms)

    def _image_generation_busy(self, page: Page) -> bool:
        state = page.evaluate(
            """() => {
                const visible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return false;
                    }
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const busySelectors = [
                    'progress',
                    '[role="progressbar"]',
                    '[aria-busy="true"]',
                    '[data-testid*="progress"]',
                    '[class*="progress"]',
                    '[class*="spinner"]',
                    '[class*="loading"]'
                ];

                const busyCount = busySelectors
                    .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                    .filter(visible).length;

                const bodyText = (document.body?.innerText || '').toLowerCase();
                const busyText = /(generating|rendering|creating image|creating artwork|processing image|processing photo|loading image|\\b\\d{1,3}%\\b)/i.test(bodyText);
                return busyCount > 0 || busyText;
            }"""
        )
        return bool(state)

    def _download_from_controls(self, page: Page, output_path: Path) -> bool:
        download_candidates = [
            page.locator("a[download]").first,
            page.locator("a[href*='.mp4']").first,
            page.get_by_role("button", name=re.compile(r"(download|save|export|save video)", re.I)).first,
            page.get_by_role("link", name=re.compile(r"(download|save|export)", re.I)).first,
        ]

        for candidate in download_candidates:
            try:
                if candidate.count() == 0:
                    continue
                candidate.wait_for(state="visible", timeout=1_000)
            except Exception:
                continue

            output_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with page.expect_download(timeout=5_000) as download_info:
                    candidate.click(timeout=5_000)
                download = download_info.value
                download.save_as(str(output_path))
                return True
            except Exception:
                href = None
                try:
                    href = candidate.get_attribute("href", timeout=1_000)
                except Exception:
                    href = None
                if href and href.startswith("http"):
                    response = page.context.request.get(str(href), timeout=30_000)
                    if response.ok:
                        output_path.write_bytes(response.body())
                        return True
        return False

    def _downloads_snapshot(self) -> dict[str, float]:
        downloads_dir = Path.home() / "Downloads"
        if not downloads_dir.exists():
            return {}
        snapshot: dict[str, float] = {}
        for path in downloads_dir.iterdir():
            if not path.is_file():
                continue
            try:
                snapshot[str(path)] = path.stat().st_mtime
            except OSError:
                continue
        return snapshot

    def _capture_download_from_folder(
        self,
        baseline_downloads: dict[str, float],
        output_path: Path,
        *,
        suffixes: set[str] | None = None,
    ) -> bool:
        downloads_dir = Path.home() / "Downloads"
        if not downloads_dir.exists():
            return False

        allowed_suffixes = suffixes or {".mp4"}
        candidates: list[Path] = []
        for path in downloads_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            baseline_mtime = baseline_downloads.get(str(path))
            if baseline_mtime is None or stat.st_mtime > baseline_mtime:
                candidates.append(path)

        if not candidates:
            return False

        newest = max(candidates, key=lambda item: item.stat().st_mtime)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(newest.read_bytes())
        return True

    def _write_debug_snapshot(self, page: Page, output_path: Path, reason: str) -> dict[str, str]:
        if not self.config.save_debug_artifacts:
            return {
                "screenshot": "debug artifacts disabled",
                "html": "debug artifacts disabled",
                "json": "debug artifacts disabled",
            }
        base = output_path.with_suffix("")
        screenshot_path = base.with_name(f"{base.name}_grok_debug.png")
        html_path = base.with_name(f"{base.name}_grok_debug.html")
        json_path = base.with_name(f"{base.name}_grok_debug.json")

        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            pass

        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass

        debug_payload = {
            "reason": reason,
            "url": page.url,
            "body_excerpt": self._safe_body_text(page),
            "overlay_markers": self._interfering_overlay_markers(page),
            "image_candidates": self._image_metadata(page)[:12],
            "video_signatures": self._video_signatures(page),
            "download_controls": self._collect_candidate_controls(page),
        }
        try:
            json_path.write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return {
            "screenshot": str(screenshot_path),
            "html": str(html_path),
            "json": str(json_path),
        }

    def _safe_body_text(self, page: Page) -> str:
        try:
            return page.locator("body").inner_text(timeout=3_000)[:5000]
        except Exception:
            return ""

    def _collect_candidate_controls(self, page: Page) -> list[dict[str, str]]:
        selectors = [
            "button",
            "a",
            "video",
        ]
        controls: list[dict[str, str]] = []
        for selector in selectors:
            locator = page.locator(selector)
            count = min(locator.count(), 30)
            for index in range(count):
                item = locator.nth(index)
                try:
                    controls.append(
                        {
                            "selector": selector,
                            "text": (item.inner_text(timeout=500) or "")[:200],
                            "aria_label": item.get_attribute("aria-label", timeout=500) or "",
                            "href": item.get_attribute("href", timeout=500) or "",
                            "src": item.get_attribute("src", timeout=500) or "",
                            "download": item.get_attribute("download", timeout=500) or "",
                        }
                    )
                except Exception:
                    continue
        return controls

    def _save_video_from_locator(self, page: Page, locator: Locator, output_path: Path) -> None:
        src = locator.evaluate(
            """(video) => {
                return video.currentSrc || video.src || (video.querySelector('source') ? video.querySelector('source').src : '');
            }"""
        )
        if not src:
            raise GrokWebError("Generated video element does not contain a source URL.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, str) and src.startswith("blob:"):
            payload = locator.evaluate(
                """async (video) => {
                    const src = video.currentSrc || video.src || (video.querySelector('source') ? video.querySelector('source').src : '');
                    const response = await fetch(src);
                    const blob = await response.blob();
                    const arrayBuffer = await blob.arrayBuffer();
                    const bytes = Array.from(new Uint8Array(arrayBuffer));
                    return { bytes };
                }"""
            )
            output_path.write_bytes(bytes(payload["bytes"]))
            return

        response = page.context.request.get(str(src), timeout=self.config.result_timeout_ms)
        if not response.ok:
            raise GrokWebError(f"Could not download generated video from Grok source URL: {src}")
        output_path.write_bytes(response.body())

    def _save_image_from_source(self, page: Page, source: str, output_path: Path) -> bool:
        if not source:
            return False

        if source.startswith("data:image/"):
            header, encoded = source.split(",", 1)
            import base64

            output_path.write_bytes(base64.b64decode(encoded))
            return True

        if source.startswith("blob:"):
            payload = page.evaluate(
                """async (src) => {
                    const response = await fetch(src);
                    const blob = await response.blob();
                    const arrayBuffer = await blob.arrayBuffer();
                    const bytes = Array.from(new Uint8Array(arrayBuffer));
                    return { bytes };
                }""",
                source,
            )
            output_path.write_bytes(bytes(payload["bytes"]))
            return True

        response = page.context.request.get(str(source), timeout=self.config.result_timeout_ms)
        if response.ok:
            output_path.write_bytes(response.body())
            return True
        return False

    def _nested_visual_source(self, locator: Locator) -> dict[str, object] | None:
        try:
            payload = locator.evaluate(
                """(element) => {
                    const visible = (node) => {
                        if (!node) return false;
                        const style = window.getComputedStyle(node);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return false;
                        }
                        const rect = node.getBoundingClientRect();
                        return rect.width > 60 && rect.height > 60;
                    };

                    const backgroundUrl = (node) => {
                        const style = window.getComputedStyle(node);
                        const backgroundImage = style.backgroundImage || '';
                        const match = backgroundImage.match(/url\\((['"]?)(.*?)\\1\\)/i);
                        return match ? match[2] : '';
                    };

                    const pushCandidate = (list, node, kind, src) => {
                        if (!visible(node)) return;
                        const rect = node.getBoundingClientRect();
                        const existing = node.getAttribute('data-codex-nested-visual-id');
                        const nestedId = existing || `codex-nested-${Math.random().toString(36).slice(2, 10)}`;
                        if (!existing) {
                            node.setAttribute('data-codex-nested-visual-id', nestedId);
                        }
                        list.push({
                            kind,
                            src,
                            nestedId,
                            area: (rect.width || 0) * (rect.height || 0),
                            width: rect.width || 0,
                            height: rect.height || 0,
                        });
                    };

                    const candidates = [];
                    const nodes = [element, ...Array.from(element.querySelectorAll('img, canvas, div, figure, section, article, picture, span'))];

                    nodes.forEach((node) => {
                        const tag = (node.tagName || '').toLowerCase();
                        if (tag === 'img') {
                            pushCandidate(candidates, node, 'img', node.currentSrc || node.src || '');
                            return;
                        }
                        if (tag === 'canvas') {
                            try {
                                pushCandidate(candidates, node, 'canvas', node.toDataURL('image/png'));
                                return;
                            } catch (error) {
                                // ignore canvas export failures
                            }
                        }
                        const background = backgroundUrl(node);
                        if (background) {
                            pushCandidate(candidates, node, 'background', background);
                        }
                    });

                    if (!candidates.length) {
                        return null;
                    }

                    const kindPriority = { img: 3, background: 2, canvas: 1 };
                    candidates.sort((left, right) => {
                        const priorityDelta = (kindPriority[right.kind] || 0) - (kindPriority[left.kind] || 0);
                        if (priorityDelta !== 0) return priorityDelta;
                        return (right.area || 0) - (left.area || 0);
                    });
                    return candidates[0];
                }"""
            )
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _save_image_from_locator(self, page: Page, locator: Locator, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        nested_payload = self._nested_visual_source(locator)
        if nested_payload is not None:
            nested_src = str(nested_payload.get("src", "")).strip()
            if self._save_image_from_source(page, nested_src, output_path):
                return
            nested_id = str(nested_payload.get("nestedId", "")).strip()
            if nested_id:
                try:
                    nested_locator = locator.locator(f'[data-codex-nested-visual-id="{nested_id}"]').first
                    nested_locator.screenshot(path=str(output_path))
                    return
                except Exception:
                    pass

        try:
            background_src = locator.evaluate(
                """(element) => {
                    const style = window.getComputedStyle(element);
                    const backgroundImage = style.backgroundImage || '';
                    const match = backgroundImage.match(/url\\((['"]?)(.*?)\\1\\)/i);
                    return match ? match[2] : '';
                }"""
            )
        except Exception:
            background_src = ""

        if isinstance(background_src, str) and self._save_image_from_source(page, background_src, output_path):
            return

        try:
            src = locator.get_attribute("src")
        except Exception:
            src = None
        if isinstance(src, str) and self._save_image_from_source(page, src, output_path):
            return

        try:
            locator.screenshot(path=str(output_path))
            return
        except Exception as exc:
            raise GrokWebError("Generated image element does not contain a source URL.") from exc


class GrokWebSessionRunner:
    def __init__(self) -> None:
        self._playwright_cm = None
        self._playwright = None
        self._context: BrowserContext | None = None
        self._connected_over_cdp = False
        self._profile_dir: str | None = None
        self._executable_path: str | None = None
        self._debug_port: int | None = None
        self._launcher_agent: GrokWebAgent | None = None

    def run(self, config: GrokWebConfig) -> Path:
        agent = GrokWebAgent(config)
        agent._ensure_dependencies()
        config.profile_dir.mkdir(parents=True, exist_ok=True)

        profile_dir = str(config.profile_dir)
        executable_path = str(config.executable_path) if config.executable_path is not None else None
        debug_port = config.debug_port

        if self._playwright is None:
            self._playwright_cm = sync_playwright()
            self._playwright = self._playwright_cm.start()
        if self._context is None:
            if debug_port is None:
                agent._close_lingering_login_browser(self._playwright)
                agent._terminate_profile_processes()
                self._context = agent._launch_managed_context(self._playwright)
                self._connected_over_cdp = True
                self._launcher_agent = agent
            else:
                try:
                    self._context = agent._connect_context(self._playwright)
                    self._connected_over_cdp = True
                except GrokWebError:
                    agent._log("chrome debug-port connect failed; falling back to managed Chrome launch")
                    self._context = agent._launch_managed_context(self._playwright)
                    self._connected_over_cdp = True
                    self._launcher_agent = agent
            self._profile_dir = profile_dir
            self._executable_path = executable_path
            self._debug_port = debug_port
        else:
            if (
                self._profile_dir != profile_dir
                or self._executable_path != executable_path
                or self._debug_port != debug_port
            ):
                raise GrokWebError("All Grok batch items must use the same Chrome profile, executable, and debug-port settings.")

        return agent.run_in_context(self._context)

    def close_stage_session(self) -> None:
        if self._context is not None:
            if not self._connected_over_cdp:
                try:
                    self._context.close()
                except Exception:
                    pass
            elif self._launcher_agent is not None and self._launcher_agent._connected_browser is not None and self._launcher_agent._managed_browser_process is not None:
                try:
                    self._context.close()
                except Exception:
                    pass
                try:
                    self._launcher_agent._connected_browser.close()
                except Exception:
                    pass
            self._context = None
        self._profile_dir = None
        self._executable_path = None
        self._debug_port = None
        self._connected_over_cdp = False
        if self._launcher_agent is not None:
            self._launcher_agent._cleanup_managed_browser_process()
            self._launcher_agent._terminate_profile_processes()
            self._launcher_agent._clear_profile_restore_artifacts()
            self._launcher_agent._cleanup_runtime_profile_dir()
            self._launcher_agent = None

    def close(self) -> None:
        self.close_stage_session()
        if self._playwright_cm is not None:
            try:
                self._playwright_cm.stop()
            except Exception:
                pass
            self._playwright_cm = None
            self._playwright = None

