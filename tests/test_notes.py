import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tiny_reading_tracker.notes import (
    NoteConflictError,
    NoteError,
    append_note,
    load_note,
    save_note,
)


def item(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "lit_12345678-1234-1234-1234-123456789abc",
        "title": "A / title: with Unicode 🧪",
        "url": "https://example.test/",
        "note_path": None,
    }
    value.update(updates)
    return value


def test_create_load_save_and_append(tmp_path: Path) -> None:
    note = load_note(item(), tmp_path)
    assert note.path.parent == tmp_path.resolve()
    assert "## Summary\n" in note.content
    assert "## Notes\n" in note.content
    assert 'lit_id: "lit_12345678-1234-1234-1234-123456789abc"' in note.content
    saved = save_note(note, note.content + "A thought.\n")
    assert "A thought." in saved.path.read_text()
    appended = append_note(item(), tmp_path, "Another thought.")
    assert appended.content.endswith("Another thought.\n")
    assert appended.created_at == note.created_at
    assert appended.modified_at


def test_existing_unknown_file_is_not_adopted(tmp_path: Path) -> None:
    path = tmp_path / "A - title- with Unicode 🧪-lit_12345678-1234-1234-1234-123456789abc.md"
    path.write_text("# Someone else's note\n")
    with pytest.raises(NoteError, match="does not belong"):
        load_note(item(), tmp_path)
    assert path.read_text() == "# Someone else's note\n"


def test_registered_absolute_and_relative_paths_are_preserved(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    path = vault / "elsewhere.md"
    path.write_text('---\nlit_id: "lit_12345678-1234-1234-1234-123456789abc"\n---\n\nBody\n')
    absolute = load_note(item(note_path=str(path)), tmp_path / "new-notes", vault)
    relative = load_note(item(note_path="elsewhere.md"), tmp_path / "new-notes", vault)
    assert absolute.path == relative.path == path


def test_registered_note_without_frontmatter_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "handwritten.md"
    path.write_text("# Existing writing\n")
    note = load_note(item(note_path=str(path)), tmp_path / "new-notes")
    assert note.content == "# Existing writing\n"


def test_registered_missing_note_is_never_recreated(tmp_path: Path) -> None:
    path = tmp_path / "missing.md"
    with pytest.raises(NoteError, match="missing"):
        load_note(item(note_path=str(path)), tmp_path)
    assert not path.exists()


def test_symlink_targets_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    link = tmp_path / "link.md"
    link.symlink_to(outside)
    with pytest.raises(NoteError, match="symlink"):
        load_note(item(note_path=str(link)), tmp_path)


def test_external_edit_and_delete_conflict_without_overwrite(tmp_path: Path) -> None:
    note = load_note(item(), tmp_path)
    original = note.path.read_text()
    note.path.write_text(original + "external\n")
    with pytest.raises(NoteConflictError):
        save_note(note, "local replacement\n")
    assert "external" in note.path.read_text()
    refreshed = load_note(item(), tmp_path)
    refreshed.path.unlink()
    with pytest.raises(NoteConflictError):
        save_note(refreshed, "local replacement\n")


def test_failed_atomic_replace_preserves_original(tmp_path: Path, monkeypatch) -> None:
    note = load_note(item(), tmp_path)
    original = note.path.read_text()
    note.path.chmod(0o640)
    original_mode = note.path.stat().st_mode & 0o777

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("tiny_reading_tracker.notes.os.replace", fail_replace)
    with pytest.raises(NoteError, match="could not save"):
        save_note(note, "replacement\n")
    assert note.path.read_text() == original
    assert note.path.stat().st_mode & 0o777 == original_mode


def test_cooperating_concurrent_saves_have_one_winner(tmp_path: Path) -> None:
    note = load_note(item(), tmp_path)
    start = threading.Barrier(2)

    def save(value: str):
        start.wait()
        try:
            return save_note(note, value)
        except NoteConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ("first\n", "second\n")))
    assert sum(result is not None for result in results) == 1
    assert note.path.read_text() in {"first\n", "second\n"}


def test_unicode_filename_is_bounded(tmp_path: Path) -> None:
    note = load_note(item(title="界🧪" * 200), tmp_path)
    assert len(note.path.name.encode()) <= 240


def test_relative_registered_path_requires_vault(tmp_path: Path) -> None:
    with pytest.raises(NoteError, match="vault"):
        load_note(item(note_path="Reading/note.md"), tmp_path)
