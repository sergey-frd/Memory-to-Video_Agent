from __future__ import annotations

from pathlib import Path

import pytest

try:
    from api.chatgpt_desktop_v2 import (
        ChatGPTDesktopAgent,
        DesktopAgentConfig,
        DesktopAutomationError,
    )
except PermissionError as exc:  # pragma: no cover - host-specific comtypes cache permissions
    pytest.skip(f"Desktop automation import is unavailable in this host Python: {exc}", allow_module_level=True)


class _FakeWindow:
    def __init__(self, title: str) -> None:
        self._title = title

    def window_text(self) -> str:
        return self._title


class _FakeButton:
    def __init__(self) -> None:
        self.clicked = False

    def click_input(self) -> None:
        self.clicked = True


def _config() -> DesktopAgentConfig:
    return DesktopAgentConfig(
        image_path=None,
        prompt_text="prompt",
        use_active_window=True,
        attach_via_clipboard=True,
        require_single_tab_window=True,
        manual_composer_position=(100, 200),
    )


def test_reactivates_only_visible_chatgpt_window_after_countdown_focus_drift(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    command_window = _FakeWindow("Administrator: Command Prompt")
    chatgpt_window = _FakeWindow("ChatGPT - Google Chrome")
    ensured: list[tuple[object, str]] = []

    monkeypatch.setattr(agent, "_active_window", lambda: command_window)
    monkeypatch.setattr(agent, "_looks_like_chatgpt_content", lambda window: window is chatgpt_window)
    monkeypatch.setattr(agent, "_visible_chatgpt_window_candidates", lambda: [chatgpt_window])
    monkeypatch.setattr(agent, "_ensure_foreground_window", lambda window, action: ensured.append((window, action)))

    selected = agent._launch_or_connect()

    assert selected is chatgpt_window
    assert ensured == [
        (
            chatgpt_window,
            "reactivate sole visible ChatGPT generation window after countdown",
        )
    ]


def test_still_stops_when_countdown_focus_drift_has_multiple_chatgpt_candidates(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    command_window = _FakeWindow("Administrator: Command Prompt")
    first_chatgpt = _FakeWindow("ChatGPT - Google Chrome")
    second_chatgpt = _FakeWindow("Watercolor Portrait Request - Google Chrome")

    monkeypatch.setattr(agent, "_active_window", lambda: command_window)
    monkeypatch.setattr(agent, "_looks_like_chatgpt_content", lambda window: False)
    monkeypatch.setattr(
        agent,
        "_visible_chatgpt_window_candidates",
        lambda: [first_chatgpt, second_chatgpt],
    )

    with pytest.raises(DesktopAutomationError, match="active window after the countdown"):
        agent._launch_or_connect()


def test_wait_for_clean_new_chat_surface_requires_old_result_images_to_disappear(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    window = _FakeWindow("ChatGPT - Google Chrome")
    image_batches = [[object()], []]

    monkeypatch.setattr(agent, "_find_prompt_input", lambda _window: object())
    monkeypatch.setattr(agent, "_find_result_images", lambda _window: image_batches.pop(0))
    monkeypatch.setattr("api.chatgpt_desktop_v2.time.sleep", lambda _seconds: None)

    assert agent._wait_for_clean_new_chat_surface(window, timeout_sec=1.0) is True


def test_wait_for_clean_new_chat_surface_rejects_page_with_lingering_result_images(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    window = _FakeWindow("ChatGPT - Google Chrome")
    timestamps = iter([0.0, 0.1, 0.2, 1.1])

    monkeypatch.setattr(agent, "_find_prompt_input", lambda _window: object())
    monkeypatch.setattr(agent, "_find_result_images", lambda _window: [object()])
    monkeypatch.setattr("api.chatgpt_desktop_v2.time.time", lambda: next(timestamps))
    monkeypatch.setattr("api.chatgpt_desktop_v2.time.sleep", lambda _seconds: None)

    assert agent._wait_for_clean_new_chat_surface(window, timeout_sec=1.0) is False


def test_signature_digest_blocks_reusing_same_image_after_layout_shift() -> None:
    agent = ChatGPTDesktopAgent(_config())
    original_signature = (100, 200, 300, 400, 123456)
    shifted_signature = (120, 220, 320, 420, 123456)

    assert agent._signature_matches_baseline(
        shifted_signature,
        baseline_set={original_signature},
        baseline_digests={123456},
    )


def test_wait_for_attached_source_image_accepts_new_preview_candidate(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    window = _FakeWindow("ChatGPT - Google Chrome")
    preview = object()

    monkeypatch.setattr(agent, "_collect_attachment_surface_text", lambda _window: "")
    monkeypatch.setattr(agent, "_find_attachment_image_candidates", lambda _window: [preview])
    monkeypatch.setattr(agent, "_result_signature", lambda _wrapper: (10, 20, 30, 40, 54321))

    agent._wait_for_attached_source_image(
        window,
        Path("family_photo.jpg"),
        baseline_signatures=[],
        baseline_surface_text="",
    )


def test_wait_for_attached_source_image_stops_before_prompt_when_no_preview_appears(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    window = _FakeWindow("ChatGPT - Google Chrome")
    timestamps = iter([0.0, 21.0])

    monkeypatch.setattr(agent, "_collect_attachment_surface_text", lambda _window: "")
    monkeypatch.setattr(agent, "_find_attachment_image_candidates", lambda _window: [])
    monkeypatch.setattr("api.chatgpt_desktop_v2.time.time", lambda: next(timestamps))

    with pytest.raises(DesktopAutomationError, match="never exposed a confirmed attachment preview"):
        agent._wait_for_attached_source_image(
            window,
            Path("family_photo.jpg"),
            baseline_signatures=[],
            baseline_surface_text="",
        )


def test_dialog_visibility_helper_returns_false_when_wrapper_is_gone() -> None:
    agent = ChatGPTDesktopAgent(_config())

    class _ClosedDialog:
        def is_visible(self) -> bool:
            raise RuntimeError("dialog handle is gone")

    assert agent._dialog_still_visible(_ClosedDialog()) is False


def test_open_new_chat_prefers_button_and_never_navigates_old_page(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    window = _FakeWindow("ChatGPT - Google Chrome")
    button = _FakeButton()

    clean_surface_checks = iter([False, True])
    monkeypatch.setattr(agent, "_find_button", lambda _window, _patterns: button)
    monkeypatch.setattr(agent, "_ensure_foreground_window", lambda _window, _action: None)
    monkeypatch.setattr(agent, "_wait_for_clean_new_chat_surface", lambda _window, timeout_sec: next(clean_surface_checks))
    monkeypatch.setattr(agent, "_navigate_to_url", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected navigation")))

    agent._open_new_chat(window)

    assert button.clicked is True


def test_open_new_chat_accepts_already_clean_empty_surface_without_click(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    window = _FakeWindow("ChatGPT - Google Chrome")

    monkeypatch.setattr(agent, "_wait_for_clean_new_chat_surface", lambda _window, timeout_sec: True)
    monkeypatch.setattr(
        agent,
        "_find_button",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected New chat lookup")),
    )

    agent._open_new_chat(window)


def test_open_new_chat_falls_back_to_verified_url_navigation_when_new_chat_control_is_missing(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    window = _FakeWindow("ChatGPT - Google Chrome")
    navigations: list[str] = []
    clean_surface_checks = iter([False, True])

    monkeypatch.setattr(agent, "_find_button", lambda _window, _patterns: None)
    monkeypatch.setattr(agent, "_wait_for_clean_new_chat_surface", lambda _window, timeout_sec: next(clean_surface_checks))
    monkeypatch.setattr(agent, "_navigate_to_url", lambda url, _window: navigations.append(url))

    agent._open_new_chat(window)

    assert navigations == ["https://chatgpt.com/"]


def test_browser_address_selection_verification_accepts_copied_url(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    clipboard = {"value": ""}

    monkeypatch.setattr("api.chatgpt_desktop_v2.pyperclip.copy", lambda value: clipboard.__setitem__("value", value))
    monkeypatch.setattr("api.chatgpt_desktop_v2.pyperclip.paste", lambda: "https://chatgpt.com/c/example")
    monkeypatch.setattr(agent, "_press_ctrl_key_raw", lambda _key: None)
    monkeypatch.setattr("api.chatgpt_desktop_v2.time.sleep", lambda _seconds: None)

    assert agent._browser_address_selection_is_verified() is True
    assert clipboard["value"] == "__codex_address_probe__"


def test_browser_address_selection_verification_rejects_non_url_copy(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())

    monkeypatch.setattr("api.chatgpt_desktop_v2.pyperclip.copy", lambda _value: None)
    monkeypatch.setattr("api.chatgpt_desktop_v2.pyperclip.paste", lambda: "Сформированное изображение")
    monkeypatch.setattr(agent, "_press_ctrl_key_raw", lambda _key: None)
    monkeypatch.setattr("api.chatgpt_desktop_v2.time.sleep", lambda _seconds: None)

    assert agent._browser_address_selection_is_verified() is False


def test_focus_browser_address_bar_falls_back_to_toolbar_click(monkeypatch) -> None:
    agent = ChatGPTDesktopAgent(_config())
    window = _FakeWindow("ChatGPT - Google Chrome")
    attempts = iter([False, False, True])
    clicks: list[tuple[int, int]] = []

    class _Rect:
        left = 100
        top = 20

        def width(self) -> int:
            return 1000

    window.rectangle = lambda: _Rect()  # type: ignore[attr-defined]
    monkeypatch.setattr(agent, "_ensure_foreground_window", lambda _window, _action: None)
    monkeypatch.setattr(agent, "_assert_foreground_window", lambda _window, _action: None)
    monkeypatch.setattr(agent, "_press_ctrl_key_raw", lambda _key: None)
    monkeypatch.setattr("api.chatgpt_desktop_v2.send_keys", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("api.chatgpt_desktop_v2.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(agent, "_browser_address_selection_is_verified", lambda: next(attempts))
    monkeypatch.setattr(
        agent,
        "_click_screen_point",
        lambda x, y, **_kwargs: clicks.append((x, y)),
    )

    assert agent._focus_browser_address_bar_verified(window) is True
    assert clicks == [(560, 70)]
