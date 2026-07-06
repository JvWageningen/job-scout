"""Tests for multi-user config loading and path helpers."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

from job_scout.models import Config


def _reload_config(monkeypatch: pytest.MonkeyPatch, data_dir: Path):
    """Point config module at data_dir and reload."""
    monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(data_dir))
    import job_scout.config as cfg

    importlib.reload(cfg)
    return cfg


def test_global_and_user_fields_partition_all_fields() -> None:
    """Every Config field is in exactly one of GLOBAL, USER, or SECRET."""
    from job_scout.config import GLOBAL_FIELDS, SECRET_FIELDS, USER_FIELDS

    all_fields = frozenset(Config.model_fields)
    union = GLOBAL_FIELDS | USER_FIELDS | SECRET_FIELDS
    assert union == all_fields, f"Uncovered fields: {all_fields - union}"
    # Disjoint
    assert not (GLOBAL_FIELDS & USER_FIELDS)
    assert not (GLOBAL_FIELDS & SECRET_FIELDS)
    assert not (USER_FIELDS & SECRET_FIELDS)


def test_secret_fields_not_in_global_or_user() -> None:
    """Secret fields are never in GLOBAL_FIELDS or USER_FIELDS."""
    from job_scout.config import GLOBAL_FIELDS, SECRET_FIELDS, USER_FIELDS

    for f in SECRET_FIELDS:
        assert f not in GLOBAL_FIELDS
        assert f not in USER_FIELDS


def test_user_dir_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """user_dir returns data/users/<name>/."""
    cfg = _reload_config(monkeypatch, tmp_path)
    assert cfg.user_dir("alice") == tmp_path / "users" / "alice"


def test_user_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """user_config_path returns data/users/<name>/config.yaml."""
    cfg = _reload_config(monkeypatch, tmp_path)
    assert cfg.user_config_path("alice") == tmp_path / "users" / "alice" / "config.yaml"


def test_user_db_path_with_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """user_db_path(name) returns data/users/<name>/jobs.db."""
    cfg = _reload_config(monkeypatch, tmp_path)
    assert cfg.user_db_path("alice") == tmp_path / "users" / "alice" / "jobs.db"


def test_user_db_path_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """user_db_path(None) returns the global jobs.db path."""
    cfg = _reload_config(monkeypatch, tmp_path)
    assert cfg.user_db_path(None) == tmp_path / "jobs.db"


def test_list_users_empty_when_no_users_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list_users returns [] when data/users/ does not exist."""
    cfg = _reload_config(monkeypatch, tmp_path)
    assert cfg.list_users() == []


def test_list_users_returns_sorted_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list_users returns sorted directory names under data/users/."""
    cfg = _reload_config(monkeypatch, tmp_path)
    for name in ("bob", "alice", "charlie"):
        (tmp_path / "users" / name).mkdir(parents=True)
    assert cfg.list_users() == ["alice", "bob", "charlie"]


def test_build_effective_config_merges_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build_effective_config: user overrides global; secrets overlay both."""
    cfg = _reload_config(monkeypatch, tmp_path)

    # Write global config
    (tmp_path / "config.yaml").write_text(
        yaml.dump({"llm_provider": "zai", "max_jobs_per_source": 10}),
        encoding="utf-8",
    )
    # Write user config
    user_dir = tmp_path / "users" / "alice"
    user_dir.mkdir(parents=True)
    (user_dir / "config.yaml").write_text(
        yaml.dump({"ntfy_topic": "alice-alerts", "max_jobs_per_source": 99}),
        encoding="utf-8",
    )
    # Write secrets
    (tmp_path / "secrets.yaml").write_text(
        yaml.dump({"zai_api_key": "sk-secret"}),
        encoding="utf-8",
    )

    config = cfg.build_effective_config("alice")
    assert config.llm_provider == "zai"  # from global
    assert config.ntfy_topic == "alice-alerts"  # from user
    assert config.max_jobs_per_source == 99  # user overrides global
    assert config.zai_api_key == "sk-secret"  # from secrets


def test_build_effective_config_env_secret_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env var JOB_SCOUT_ZAI_API_KEY overrides secrets.yaml."""
    cfg = _reload_config(monkeypatch, tmp_path)
    (tmp_path / "secrets.yaml").write_text(
        yaml.dump({"zai_api_key": "from-file"}), encoding="utf-8"
    )
    (tmp_path / "users" / "alice").mkdir(parents=True)
    monkeypatch.setenv("JOB_SCOUT_ZAI_API_KEY", "from-env")

    config = cfg.build_effective_config("alice")
    assert config.zai_api_key == "from-env"


def test_set_config_value_rejects_secret_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_config_value raises ValueError for SECRET_FIELDS keys."""
    cfg = _reload_config(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="secret"):
        cfg.set_config_value("zai_api_key", "leak")


def test_set_config_value_routes_global_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_config_value writes a GLOBAL_FIELDS key to data/config.yaml."""
    cfg = _reload_config(monkeypatch, tmp_path)
    cfg.set_config_value("max_jobs_per_source", "42")
    data = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert data["max_jobs_per_source"] == 42


def test_set_config_value_routes_user_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_config_value writes a USER_FIELDS key to the user config."""
    cfg = _reload_config(monkeypatch, tmp_path)
    (tmp_path / "users" / "alice").mkdir(parents=True)
    cfg.set_config_value("ntfy_topic", "alice-topic", user="alice")
    data = yaml.safe_load((tmp_path / "users" / "alice" / "config.yaml").read_text())
    assert data["ntfy_topic"] == "alice-topic"
