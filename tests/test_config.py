"""Tests for configuration management."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_scout.models import Config


def test_config_serialisation_round_trip() -> None:
    """Config round-trips through model_dump and reconstruction."""
    config = Config(ntfy_topic="my-alerts", max_travel_car=45)
    data = config.model_dump()
    restored = Config(**data)
    assert restored.ntfy_topic == "my-alerts"
    assert restored.max_travel_car == 45


def test_load_config_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: E501
    """load_config returns defaults when config.yaml does not exist."""
    monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(tmp_path / "nonexistent"))

    # Re-import to pick up env var change
    import importlib

    import job_scout.config as cfg_module

    importlib.reload(cfg_module)
    config = cfg_module.load_config()
    assert config.ntfy_topic == "job-scout-alerts"


def test_save_and_load_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save_config writes YAML; load_config reads it back correctly."""
    monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(tmp_path))

    import importlib

    import job_scout.config as cfg_module

    importlib.reload(cfg_module)

    original = Config(ntfy_topic="saved-topic", max_travel_pt=90)
    cfg_module.save_config(original)

    loaded = cfg_module.load_config()
    assert loaded.ntfy_topic == "saved-topic"
    assert loaded.max_travel_pt == 90


def test_set_config_value_int(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """set_config_value converts string to int for integer fields."""
    monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(tmp_path))

    import importlib

    import job_scout.config as cfg_module

    importlib.reload(cfg_module)
    cfg_module.save_config(Config())
    cfg_module.set_config_value("max_travel_car", "45")

    loaded = cfg_module.load_config()
    assert loaded.max_travel_car == 45


def test_set_config_value_unknown_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: E501
    """set_config_value raises ValueError for unknown keys."""
    monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(tmp_path))

    import importlib

    import job_scout.config as cfg_module

    importlib.reload(cfg_module)
    cfg_module.save_config(Config())

    with pytest.raises(ValueError, match="Unknown config key"):
        cfg_module.set_config_value("nonexistent_key", "value")


def test_set_config_value_string_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_config_value stores a plain string value for string fields."""
    monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(tmp_path))

    import importlib

    import job_scout.config as cfg_module

    importlib.reload(cfg_module)
    cfg_module.save_config(Config())
    cfg_module.set_config_value("ntfy_topic", "my-custom-topic")

    loaded = cfg_module.load_config()
    assert loaded.ntfy_topic == "my-custom-topic"


def test_coerce_value_bool_true() -> None:
    """_coerce_value converts 'true'/'yes'/'1' to True for bool fields."""
    from job_scout.config import _coerce_value

    assert _coerce_value(True, "true") is True
    assert _coerce_value(True, "yes") is True
    assert _coerce_value(True, "1") is True


def test_coerce_value_bool_false() -> None:
    """_coerce_value converts other strings to False for bool fields."""
    from job_scout.config import _coerce_value

    assert _coerce_value(True, "false") is False
    assert _coerce_value(True, "0") is False
    assert _coerce_value(True, "no") is False


def test_coerce_value_list_from_json() -> None:
    """_coerce_value parses a JSON array string for list fields."""
    from job_scout.config import _coerce_value

    result = _coerce_value([], '["python", "java"]')
    assert result == ["python", "java"]


def test_coerce_value_empty_string_returns_none() -> None:
    """_coerce_value returns None when raw is empty string for str fields."""
    from job_scout.config import _coerce_value

    assert _coerce_value("some string", "") is None


def test_save_config_creates_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_config creates the parent directory if it does not exist."""
    deep_dir = tmp_path / "a" / "b" / "c"
    monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(deep_dir))

    import importlib

    import job_scout.config as cfg_module

    importlib.reload(cfg_module)
    cfg_module.save_config(Config())

    assert (deep_dir / "config.yaml").exists()
