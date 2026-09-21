from __future__ import annotations

from pathlib import Path

import pytest

from esoil_proceedings.config import ConfigError, load_config


def test_pretalx_location_comes_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "conference.yaml"
    path.write_text("proceedings:\n  include_states: [confirmed]\n", encoding="utf-8")
    monkeypatch.setenv("PRETALX_BASE_URL", "https://next.example/")
    monkeypatch.setenv("PRETALX_EVENT", "pgb2027")

    config = load_config(path)

    assert config.base_url == "https://next.example"
    assert config.event == "pgb2027"
    assert config.include_states == ("confirmed",)
    assert config.include_submission_types == ()


def test_missing_pretalx_location_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "conference.yaml"
    path.write_text("proceedings:\n  include_states: [confirmed]\n", encoding="utf-8")
    monkeypatch.delenv("PRETALX_BASE_URL", raising=False)
    monkeypatch.delenv("PRETALX_EVENT", raising=False)

    with pytest.raises(ConfigError, match="PRETALX_BASE_URL"):
        load_config(path)
