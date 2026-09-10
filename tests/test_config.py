from pathlib import Path

import pytest

from tiny_reading_tracker.config import load_config


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    for name in ("LIT_CONFIG", "LIT_DB", "LIT_VAULT", "LIT_NOTES_DIR", "LIT_TRANSLATOR_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_defaults_and_missing_explicit(tmp_path):
    cfg = load_config()
    assert cfg.db == tmp_path / "data/tiny-reading-tracker/library.db"
    assert cfg.vault is None
    assert cfg.notes_dir == tmp_path / "data/tiny-reading-tracker/notes"
    with pytest.raises(ValueError, match="does not exist"):
        load_config(tmp_path / "missing.toml")


def test_precedence_and_relative_paths(monkeypatch, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('db = "library.db"\nvault = "notes"\nworkers = 2\n')
    cfg = load_config(path)
    assert cfg.db == tmp_path / "library.db"
    assert cfg.vault == tmp_path / "notes"
    assert cfg.notes_dir == (tmp_path / "notes/Reading").resolve()
    assert cfg.workers == 2
    monkeypatch.setenv("LIT_DB", str(tmp_path / "env.db"))
    assert load_config(path).db == tmp_path / "env.db"
    assert load_config(path, db=Path("/tmp/cli.db")).db == Path("/tmp/cli.db").resolve()


def test_notes_dir_precedence_and_relative_paths(monkeypatch, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('vault = "vault"\nnotes_dir = "configured-notes"\n')
    cfg = load_config(path)
    assert cfg.notes_dir == tmp_path / "configured-notes"
    monkeypatch.setenv("LIT_NOTES_DIR", str(tmp_path / "env-notes"))
    assert load_config(path).notes_dir == (tmp_path / "env-notes").resolve()
    assert load_config(path, notes_dir=Path("cli-notes")).notes_dir == Path("cli-notes").resolve()


def test_explicit_notes_dir_wins_over_vault(monkeypatch, tmp_path):
    monkeypatch.setenv("LIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("LIT_NOTES_DIR", str(tmp_path / "notes"))
    assert load_config().notes_dir == (tmp_path / "notes").resolve()


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
