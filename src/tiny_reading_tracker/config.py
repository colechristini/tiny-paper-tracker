"""Explicit local configuration with environment and CLI overrides."""

import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    db: Path
    vault: Path | None
    translator_url: str = "http://127.0.0.1:1969"
    timeout: float = 20.0
    workers: int = 4


def load_config(
    config_path: Path | None = None, db: Path | None = None, vault: Path | None = None
) -> Config:
    explicit = config_path is not None or bool(os.environ.get("LIT_CONFIG"))
    path = config_path or Path(
        os.environ.get("LIT_CONFIG")
        or Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")) / "tiny-reading-tracker/config.toml"
    )
    path = path.expanduser().resolve()
    data = {}
    if path.exists():
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    elif explicit:
        raise ValueError(f"Config file does not exist: {path}")
    unknown = data.keys() - {"db", "vault", "translator_url", "timeout", "workers"}
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")

    def configured_path(key: str, override: Path | None, default: str | None) -> Path | None:
        value = override or os.environ.get(f"LIT_{key.upper()}") or data.get(key) or default
        if value is None:
            return None
        if not isinstance(value, (str, Path)):
            raise ValueError(f"{key} must be a filesystem path")
        result = Path(value).expanduser()
        if (
            not result.is_absolute()
            and override is None
            and key in data
            and not os.environ.get(f"LIT_{key.upper()}")
        ):
            result = path.parent / result
        return result.resolve()

    db_path = configured_path(
        "db",
        db,
        str(
            Path(os.environ.get("XDG_DATA_HOME", "~/.local/share"))
            / "tiny-reading-tracker/library.db"
        ),
    )
    url = os.environ.get("LIT_TRANSLATOR_URL") or data.get(
        "translator_url", "http://127.0.0.1:1969"
    )
    if not isinstance(url, str):
        raise ValueError("translator_url must be an HTTP(S) URL")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.query or parts.fragment:
        raise ValueError("translator_url must be an HTTP(S) URL without query or fragment")
    timeout = data.get("timeout", 20.0)
    workers = data.get("workers", 4)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= 120
    ):
        raise ValueError("timeout must be a number greater than 0 and at most 120 seconds")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 16:
        raise ValueError("workers must be an integer from 1 to 16")
    assert db_path is not None
    return Config(
        db_path, configured_path("vault", vault, None), url.rstrip("/"), float(timeout), workers
    )
