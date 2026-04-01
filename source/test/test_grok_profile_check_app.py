from argparse import Namespace
from pathlib import Path

import main_grok_profile_check

from api.grok_web import GrokWebAgent, GrokWebConfig


def test_build_config_creates_non_submitting_auth_check() -> None:
    args = Namespace(
        profile_dir=Path(".browser-profile/grok-web"),
        target_url="https://grok.com/imagine",
        chrome_exe=Path("C:/Chrome/chrome.exe"),
        chrome_debug_port=9222,
        launch_timeout=45.0,
    )

    config = main_grok_profile_check.build_config(args)

    assert config.prompt_text == "Grok profile authentication check."
    assert config.image_path is None
    assert config.profile_dir == Path(".browser-profile/grok-web")
    assert config.target_url == "https://grok.com/imagine"
    assert config.executable_path == Path("C:/Chrome/chrome.exe")
    assert config.debug_port == 9222
    assert config.launch_timeout_ms == 45_000
    assert config.submit is False


def test_main_grok_profile_check_runs_agent_and_prints_success(monkeypatch, capsys) -> None:
    observed: dict[str, GrokWebConfig] = {}

    def fake_check(self: GrokWebAgent) -> None:
        observed["config"] = self.config

    monkeypatch.setattr(
        "sys.argv",
        [
            "main_grok_profile_check.py",
            "--profile-dir",
            "profile-dir",
            "--target-url",
            "https://grok.com/imagine",
        ],
    )
    monkeypatch.setattr(GrokWebAgent, "check_authentication", fake_check)

    main_grok_profile_check.main()

    assert observed["config"].profile_dir == Path("profile-dir")
    assert observed["config"].target_url == "https://grok.com/imagine"
    assert "Grok profile authentication is active" in capsys.readouterr().out


def test_check_authentication_in_context_reuses_page_probe(monkeypatch) -> None:
    agent = GrokWebAgent(
        GrokWebConfig(
            prompt_text="check",
            image_path=None,
            output_path=Path("unused.txt"),
        )
    )
    observed: list[object] = []

    monkeypatch.setattr(agent, "_get_page", lambda context: observed.append(context) or object())

    agent.check_authentication_in_context("context")

    assert observed == ["context"]
