from __future__ import annotations

from pathlib import Path

import pytest

from tiny_reading_tracker.obsidian import NoteError, write_note


def item(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "lit_12345678-1234-1234-1234-123456789abc",
        "title": 'A / title: with "YAML" and Unicode 🧪',
        "url": "https://example.test/?a=1&b=two",
        "kind": "webpage",
        "authors": ["A: Author", "B Author"],
        "venue": None,
        "published_at": None,
        "source": "test",
        "status": "unread",
        "added_at": "2026-01-01T00:00:00+00:00",
        "read_at": None,
        "tags": ["to-read"],
        "note_path": None,
    }
    value.update(updates)
    return value


def test_note_creation_is_safe_yaml_and_repeat_preserves_content(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    note = write_note(item(), tmp_path, "My first thought.")
    assert note.parent == tmp_path / "Reading"
    assert "/" not in note.name
    initial = note.read_text()
    assert 'lit_id: "lit_12345678-1234-1234-1234-123456789abc"' in initial
    assert 'url: "https://example.test/?a=1&b=two"' in initial
    assert "status:" not in initial
    assert "## Summary\n" in initial
    assert "## Takeaways\n" in initial
    assert "## Thoughts\n" in initial
    assert "## Connections\n" in initial
    assert "My first thought." in initial

    same = write_note(item(), tmp_path, "Second thought.")
    final = same.read_text()
    assert same == note
    assert initial in final
    assert final.count("---") == 2
    assert "Second thought." in final


def test_recorded_missing_note_fails_instead_of_recreating(tmp_path: Path) -> None:
    reading = tmp_path / "Reading"
    reading.mkdir()
    with pytest.raises(NoteError, match="missing"):
        write_note(item(note_path="Reading/deleted.md"), tmp_path)
    assert not (reading / "deleted.md").exists()


def test_long_unicode_filename_fits_common_filesystem_limit(tmp_path: Path) -> None:
    note = write_note(item(title="界🧪" * 200), tmp_path)
    assert len(note.name.encode("utf-8")) <= 240
    assert note.exists()


def test_vault_must_already_exist(tmp_path: Path) -> None:
    missing = tmp_path / "typo"
    with pytest.raises(NoteError, match="does not exist"):
        write_note(item(), missing)
    assert not missing.exists()


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    (vault / "Reading").symlink_to(outside, target_is_directory=True)
    with pytest.raises(NoteError, match="outside"):
        write_note(item(), vault)


def test_recorded_path_must_stay_in_reading(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("do not touch")
    with pytest.raises(NoteError, match="outside"):
        write_note(item(note_path=str(outside)), tmp_path, "bad")
    assert outside.read_text() == "do not touch"
