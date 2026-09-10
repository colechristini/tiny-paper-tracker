from pathlib import Path

import pytest

from tiny_reading_tracker.config import load_config


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    for name in ("LIT_CONFIG", "LIT_DB", "LIT_VAULT", "LIT_TRANSLATOR_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_defaults_and_missing_explicit(tmp_path):
    cfg = load_config()
    assert cfg.db == tmp_path / "data/tiny-reading-tracker/library.db"
    assert cfg.vault is None
    with pytest.raises(ValueError, match="does not exist"):
        load_config(tmp_path / "missing.toml")


def test_precedence_and_relative_paths(monkeypatch, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('db = "library.db"\nvault = "notes"\nworkers = 2\n')
    cfg = load_config(path)
    assert cfg.db == tmp_path / "library.db"
    assert cfg.vault == tmp_path / "notes"
    assert cfg.workers == 2
    monkeypatch.setenv("LIT_DB", str(tmp_path / "env.db"))
    assert load_config(path).db == tmp_path / "env.db"
    assert load_config(path, db=Path("/tmp/cli.db")).db == Path("/tmp/cli.db").resolve()


@pytest.mark.parametrize(
    "content",
    [
        "workers = 0",
        "workers = true",
        "timeout = nan",
        "timeout = -1",
        'translator_url = "file:///etc/passwd"',
        'unexpected = "x"',
        "db = 123",
    ],
)
def test_invalid_config(tmp_path, content):
    path = tmp_path / "settings.toml"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_config(path)
