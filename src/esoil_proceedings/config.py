from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """The conference configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ConferenceConfig:
    base_url: str
    event: str
    include_states: tuple[str, ...]
    include_submission_types: tuple[str, ...]


def load_config(path: Path) -> ConferenceConfig:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(document, dict):
        raise ConfigError("Configuration root must be a mapping")
    pretalx = _mapping(document.get("pretalx", {}), "pretalx")
    proceedings = _mapping(document.get("proceedings", {}), "proceedings")
    base_url = _nonempty_string(
        os.environ.get("PRETALX_BASE_URL") or pretalx.get("base_url"),
        "PRETALX_BASE_URL (or pretalx.base_url)",
    )
    event = _nonempty_string(
        os.environ.get("PRETALX_EVENT") or pretalx.get("event"),
        "PRETALX_EVENT (or pretalx.event)",
    )
    include_states_value = proceedings.get("include_states", ["confirmed"])
    if not isinstance(include_states_value, list) or not include_states_value:
        raise ConfigError("proceedings.include_states must be a non-empty list")
    include_states = tuple(
        _nonempty_string(value, "proceedings.include_states item")
        for value in include_states_value
    )
    include_types_value = proceedings.get("include_submission_types", [])
    if not isinstance(include_types_value, list):
        raise ConfigError("proceedings.include_submission_types must be a list")
    include_submission_types = tuple(
        _nonempty_string(value, "proceedings.include_submission_types item")
        for value in include_types_value
    )
    return ConferenceConfig(
        base_url=base_url.rstrip("/"),
        event=event,
        include_states=include_states,
        include_submission_types=include_submission_types,
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value.strip()
