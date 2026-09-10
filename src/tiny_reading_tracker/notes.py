"""Portable Markdown notes with safe paths and optimistic file saves."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class NoteError(ValueError):
    """A note could not be read or written safely."""


class NoteConflictError(NoteError):
    """The note changed or disappeared after it was loaded."""


@dataclass(frozen=True)
class NoteVersion:
    """The file identity and content observed when a note was loaded."""

    content_hash: str
    fingerprint: tuple[int, int, int, int]


@dataclass(frozen=True)
class NoteDocument:
    """A note buffer and the version it was read from."""

    item_id: str
    path: Path
    content: str
    version: NoteVersion
    created_at: str | None
    modified_at: str

    @property
    def revision(self) -> str:
        """Stable content revision suitable for the JSON bridge."""

        return self.version.content_hash


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _component(value: str) -> str:
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "Untitled"


def _truncate_utf8(value: str, maximum: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    return encoded[:maximum].decode("utf-8", errors="ignore").rstrip(" .")


def _yaml(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _template(item: dict[str, Any]) -> str:
    item_id = str(item.get("id", ""))
    if not item_id.startswith("lit_"):
        raise NoteError("invalid item ID")
    return "\n".join(
        (
            "---",
            f"lit_id: {_yaml(item_id)}",
            f"created_at: {_yaml(_now())}",
            f"url: {_yaml(str(item.get('url', '')))}",
            "---",
            "",
            f"# {str(item.get('title', 'Untitled'))}",
            "",
            "## Summary",
            "",
            "## Notes",
            "",
        )
    )


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _stat(path: Path) -> tuple[int, int, int, int]:
    try:
        value = path.stat()
    except FileNotFoundError:
        raise NoteConflictError(f"note disappeared: {path}") from None
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _fingerprint(path: Path, content: str | None = None) -> NoteVersion:
    value = _stat(path)
    if content is None:
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise NoteConflictError(f"note disappeared: {path}") from None
    return NoteVersion(_hash(content), value)


def _read_snapshot(path: Path) -> tuple[str, NoteVersion]:
    """Read content and metadata from one stable filesystem version."""

    for _ in range(2):
        before = _stat(path)
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise NoteConflictError(f"note disappeared: {path}") from None
        after = _stat(path)
        if before == after:
            return content, NoteVersion(_hash(content), after)
    raise NoteConflictError(f"note changed while reading: {path}")


def _modified_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _frontmatter(content: str) -> dict[str, str]:
    if not content.startswith("---\n"):
        return {}
    end = content.find("\n---", 4)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    for line in content[4:end].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = str(json.loads(value))
            except json.JSONDecodeError:
                pass
        values[key.strip()] = value
    return values


def _validate_identity(path: Path, item_id: str) -> None:
    values = _frontmatter(path.read_text(encoding="utf-8"))
    if values.get("lit_id") != item_id:
        raise NoteError(f"existing note does not belong to {item_id}: {path}")


def _root(path: Path) -> Path:
    path = Path(path).expanduser()
    if path.exists() and path.is_symlink():
        raise NoteError(f"notes directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise NoteError(f"notes directory is not a directory: {path}")
    return resolved


def _registered_path(item: dict[str, Any], vault: Path | None) -> Path | None:
    recorded = item.get("note_path")
    if not recorded:
        return None
    path = Path(str(recorded)).expanduser()
    if not path.is_absolute():
        if vault is None:
            raise NoteError("a vault is required to resolve a relative registered note path")
        path = Path(vault).expanduser() / path
    if path.is_symlink():
        raise NoteError(f"registered note must not be a symlink: {path}")
    if not path.exists():
        raise NoteError(f"recorded note is missing: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise NoteError(f"recorded note is not a file: {path}")
    return resolved


def note_path(item: dict[str, Any], notes_dir: Path, vault: Path | None = None) -> Path:
    """Resolve a registered note or the stable path for a new note."""

    registered = _registered_path(item, vault)
    if registered is not None:
        return registered
    root = _root(notes_dir)
    item_id = _component(str(item.get("id", "")))
    if not item_id.startswith("lit_"):
        raise NoteError("invalid item ID")
    suffix = f"-{item_id}.md"
    title = _truncate_utf8(
        _component(str(item.get("title", ""))), max(1, 240 - len(suffix.encode()))
    )
    path = root / f"{title}{suffix}"
    if not _inside(path, root):
        raise NoteError("note path escapes notes directory")
    return path


def _document(
    item_id: str, path: Path, content: str, version: NoteVersion | None = None
) -> NoteDocument:
    values = _frontmatter(content)
    return NoteDocument(
        item_id=item_id,
        path=path,
        content=content,
        version=version or _fingerprint(path, content),
        created_at=values.get("created_at"),
        modified_at=_modified_at(path),
    )


def load_note(item: dict[str, Any], notes_dir: Path, vault: Path | None = None) -> NoteDocument:
    """Load an existing note, or create a validated new note for an item."""

    item_id = str(item.get("id", ""))
    registered = _registered_path(item, vault)
    path = registered or note_path(item, notes_dir, vault)
    if path.exists():
        if path.is_symlink():
            raise NoteError(f"note must not be a symlink: {path}")
        if not path.is_file():
            raise NoteError(f"note path is not a file: {path}")
        if registered is None:
            _validate_identity(path, item_id)
        content, version = _read_snapshot(path)
        return _document(item_id, path, content, version)

    if registered is not None:
        raise NoteError(f"recorded note is missing: {path}")

    content = _template(item)
    root = path.parent.resolve(strict=True)
    if not _inside(root, Path(notes_dir).expanduser().resolve()):
        raise NoteError("note path escapes notes directory")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise NoteError("existing note is unsafe") from None
        _validate_identity(path, item_id)
        content, version = _read_snapshot(path)
        return _document(item_id, path, content, version)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    except OSError as error:
        try:
            path.unlink()
        except OSError:
            pass
        raise NoteError(f"could not create note: {error}") from error
    return _document(item_id, path.resolve(strict=True), content)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_note(note: NoteDocument, content: str) -> NoteDocument:
    """Atomically save a buffer if the note has not changed since loading."""

    path = note.path
    if path.is_symlink() or not path.is_file():
        raise NoteConflictError(f"note was replaced or removed: {path}")
    current_content, current_version = _read_snapshot(path)
    if current_version != note.version:
        raise NoteConflictError(f"note changed outside this editor: {path}")
    mode = path.stat().st_mode & 0o777
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _, latest_version = _read_snapshot(path)
        if latest_version != note.version:
            raise NoteConflictError(f"note changed outside this editor: {path}")
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    except (OSError, UnicodeError) as error:
        if descriptor is not None:
            os.close(descriptor)
        raise NoteError(f"could not save note: {error}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return _document(note.item_id, path, content)


def append_note(
    item: dict[str, Any], notes_dir: Path, text: str, vault: Path | None = None
) -> NoteDocument:
    """Append text to a note using the same conflict-safe save protocol."""

    note = load_note(item, notes_dir, vault)
    addition = str(text)
    if not addition:
        return note
    separator = "" if note.content.endswith("\n\n") else "\n"
    return save_note(
        note, note.content + separator + addition + ("" if addition.endswith("\n") else "\n")
    )
