"""Configuration loading, saving, and multi-user path helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from job_scout.models import Config

DATA_DIR = Path(os.environ.get("JOB_SCOUT_DATA_DIR", "data"))
CONFIG_PATH = DATA_DIR / "config.yaml"

GLOBAL_FIELDS: frozenset[str] = frozenset(
    {
        "llm_provider",
        "claude_evaluation_model",
        "claude_screening_model",
        "zai_base_url",
        "zai_model",
        "zai_screening_model",
        "zai_screening_batch_size",
        "zai_quick_eval_model",
        "kilo_evaluation_model",
        "kilo_screening_model",
        "kilo_quick_eval_model",
        "local_base_url",
        "local_model",
        "local_screening_model",
        "local_quick_eval_model",
        "local_keywords_model",
        "local_evaluation_timeout",
        "local_screening_timeout",
        "quick_eval_provider",
        "screening_provider",
        "evaluation_provider",
        "keywords_provider",
        "ntfy_server",
        "max_jobs_per_source",
        "llm_max_attempts",
        "llm_retry_base_delay",
    }
)

SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "zai_api_key",
        "ors_api_key",
        "ns_api_key",
        "local_api_key",
        "dashboard_token",
    }
)

USER_FIELDS: frozenset[str] = (
    frozenset(Config.model_fields) - GLOBAL_FIELDS - SECRET_FIELDS
)


# -- Path helpers -------------------------------------------------------------


def get_data_dir() -> Path:
    """Return and create the data directory.

    Returns:
        Path to data directory.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def get_config_path() -> Path:
    """Return the global config file path.

    Returns:
        Path to data/config.yaml.
    """
    return CONFIG_PATH


def user_dir(name: str) -> Path:
    """Return the per-user data directory path.

    Args:
        name: User name.

    Returns:
        Path to data/users/<name>/.
    """
    return DATA_DIR / "users" / name


def user_config_path(name: str) -> Path:
    """Return the config file path for a specific user.

    Args:
        name: User name.

    Returns:
        Path to data/users/<name>/config.yaml.
    """
    return user_dir(name) / "config.yaml"


def user_db_path(name: str | None) -> Path:
    """Return the database path for a user or the global default.

    Args:
        name: User name, or None for the global (backward-compat) database.

    Returns:
        Path to the jobs database.
    """
    if name is None:
        return get_data_dir() / "jobs.db"
    return user_dir(name) / "jobs.db"


def user_logs_dir(name: str | None) -> Path:
    """Return the logs directory for a user or the global default.

    Args:
        name: User name, or None for the global (backward-compat) logs dir.

    Returns:
        Path to the logs directory.
    """
    if name is None:
        return get_data_dir() / "logs"
    return user_dir(name) / "logs"


def list_users() -> list[str]:
    """Return sorted list of initialized user names.

    Returns:
        Names of directories under data/users/, sorted alphabetically.
    """
    users_dir = DATA_DIR / "users"
    if not users_dir.exists():
        return []
    return sorted(p.name for p in users_dir.iterdir() if p.is_dir())


def secrets_path() -> Path:
    """Return the path to the gitignored secrets file.

    Returns:
        Path to data/secrets.yaml.
    """
    return DATA_DIR / "secrets.yaml"


# -- Loaders ------------------------------------------------------------------


def load_config() -> Config:
    """Load the global config.yaml and return a Config instance.

    Returns:
        Populated Config (defaults for missing keys).
    """
    path = get_config_path()
    if not path.exists():
        return Config()
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(**data)


def load_global_config() -> dict[str, Any]:
    """Return the raw dict from data/config.yaml.

    Returns:
        Parsed YAML dict, or empty dict if the file does not exist.
    """
    path = get_config_path()
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_user_config(name: str) -> dict[str, Any]:
    """Return the raw dict from data/users/<name>/config.yaml.

    Args:
        name: User name.

    Returns:
        Parsed YAML dict, or empty dict if the file does not exist.
    """
    path = user_config_path(name)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_secrets() -> dict[str, Any]:
    """Load API secrets from env vars (priority) or data/secrets.yaml.

    Env var names: JOB_SCOUT_ZAI_API_KEY, JOB_SCOUT_ORS_API_KEY,
    JOB_SCOUT_NS_API_KEY, JOB_SCOUT_LOCAL_API_KEY, JOB_SCOUT_DASHBOARD_TOKEN.

    Returns:
        Dict containing only SECRET_FIELDS keys that have values.
    """
    data: dict[str, Any] = {}
    path = secrets_path()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    env_map = {
        "JOB_SCOUT_ZAI_API_KEY": "zai_api_key",
        "JOB_SCOUT_ORS_API_KEY": "ors_api_key",
        "JOB_SCOUT_NS_API_KEY": "ns_api_key",
        "JOB_SCOUT_LOCAL_API_KEY": "local_api_key",
        "JOB_SCOUT_DASHBOARD_TOKEN": "dashboard_token",
    }
    for env_key, field in env_map.items():
        val = os.environ.get(env_key)
        if val:
            data[field] = val
    return {k: v for k, v in data.items() if k in SECRET_FIELDS}


def build_effective_config(name: str) -> Config:
    """Merge global + user + secrets into a Config for the given user.

    Args:
        name: User name.

    Returns:
        Effective Config with global defaults, user overrides, and secrets.
    """
    global_data = load_global_config()
    user_data = load_user_config(name)
    secrets = load_secrets()
    return Config(**{**global_data, **user_data, **secrets})


def load_llm_config() -> Config:
    """Load global config + secrets for LLM client construction.

    This is user-agnostic -- only fields needed to build an LLMClient.

    Returns:
        Config with global settings and secret API keys overlaid.
    """
    data = load_global_config()
    secrets = load_secrets()
    return Config(**{**data, **secrets})


# -- Writers ------------------------------------------------------------------


def save_config(config: Config) -> None:
    """Persist the full Config to data/config.yaml.

    Args:
        config: Configuration to save.
    """
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, allow_unicode=True)
    logger.debug(f"Config saved to {path}")


def save_user_config(name: str, data: dict[str, Any]) -> None:
    """Persist a user config dict to data/users/<name>/config.yaml.

    Args:
        name: User name.
        data: Raw config dict to write (partial or full).
    """
    path = user_config_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    logger.debug(f"User config saved to {path}")


def write_global_config(data: dict[str, Any]) -> None:
    """Write a raw dict to data/config.yaml (overwrites).

    Args:
        data: Dict to write as global config.
    """
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    logger.debug(f"Global config written to {path}")


def update_secrets(new_vals: dict[str, Any]) -> None:
    """Merge new secret values into data/secrets.yaml.

    Args:
        new_vals: Keys from SECRET_FIELDS to persist.
    """
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}
    existing.update(new_vals)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True)
    logger.debug(f"Secrets updated at {path}")


def set_config_value(key: str, value: str, *, user: str | None = None) -> None:
    """Set a single configuration value and save.

    Args:
        key: Configuration key name.
        value: String value (auto-converted to the field's type).
        user: If given, write user-scoped fields to that user's config.

    Raises:
        ValueError: If key is unknown, is a secret, or is routed incorrectly.
    """
    all_fields = set(Config.model_fields)
    if key not in all_fields:
        raise ValueError(
            f"Unknown config key: {key!r}. Valid keys: {', '.join(sorted(all_fields))}"
        )
    if key in SECRET_FIELDS:
        raise ValueError(
            f"{key!r} is a secret field. "
            f"Set it via the JOB_SCOUT_{key.upper()}_API_KEY env var "
            f"or data/secrets.yaml -- never in a tracked config file."
        )
    if user is None:
        config = load_config()
        data = config.model_dump()
        data[key] = _coerce_value(data.get(key), value)
        save_config(Config(**data))
        return
    if key in GLOBAL_FIELDS:
        raise ValueError(
            f"{key!r} is a global field and cannot be set per-user. "
            f"Omit --user to set it globally."
        )
    user_data = load_user_config(user)
    default_val = Config.model_fields[key].default
    existing_val = user_data.get(key, default_val)
    user_data[key] = _coerce_value(existing_val, value)
    save_user_config(user, user_data)


def apply_user_init(name: str, fields: dict[str, Any]) -> None:
    """Apply initial configuration values for a user.

    This function takes a dict of config fields (from interactive prompts or API)
    and splits them into user-scoped, global, and secret parts, saving each to
    the appropriate location. Reused by both CLI's _init_user and web's POST /api/users.

    Args:
        name: User name.
        fields: Configuration fields (typically from Config.model_dump()).

    Raises:
        ValueError: If name is invalid.
    """
    if name == "all":
        raise ValueError("Cannot create a user named 'all'")

    # Ensure user directory exists
    user_dir(name).mkdir(parents=True, exist_ok=True)

    # Split fields by their category
    user_data = {k: v for k, v in fields.items() if k in USER_FIELDS}
    user_data["name"] = name
    global_data = {k: v for k, v in fields.items() if k in GLOBAL_FIELDS}
    secret_data = {k: str(v) for k, v in fields.items() if k in SECRET_FIELDS and v}

    # Save each part
    save_user_config(name, user_data)
    if global_data:
        # Merge with existing global config to avoid overwriting other users' settings
        existing_global = load_global_config()
        existing_global.update(global_data)
        write_global_config(existing_global)
    if secret_data:
        update_secrets(secret_data)

    logger.info(f"User '{name}' initialized")


def _coerce_value(existing_val: object, raw: str) -> object:
    """Convert a raw string to the type of existing_val.

    Args:
        existing_val: Existing value used to infer target type.
        raw: Raw string input.

    Returns:
        Coerced value.
    """
    if isinstance(existing_val, bool):
        return raw.lower() in ("true", "1", "yes")
    if isinstance(existing_val, int):
        return int(raw)
    if isinstance(existing_val, float):
        return float(raw)
    if not raw:
        return None
    if isinstance(existing_val, list):
        stripped = raw.strip()
        if stripped.startswith("["):
            return json.loads(stripped)
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return raw
