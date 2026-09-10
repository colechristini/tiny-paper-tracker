"""Versioned JSON-lines backend for the Rust terminal interface."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .db import Database, DatabaseError
from .notes import NoteConflictError, NoteDocument, NoteError, load_note, save_note

PROTOCOL_VERSION = 1
_STATUSES = {"all", "unread", "reading", "read"}


def _response(**payload: Any) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "ok": True, **payload}


def _error(message: str, kind: str = "error") -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "ok": False, "error": {"kind": kind, "message": message}}


def _note_payload(note: NoteDocument) -> dict[str, Any]:
    return {
        "path": str(note.path),
        "text": note.content,
        "revision": note.revision,
        "created_at": note.created_at,
        "modified_at": note.modified_at,
    }


def _notes_root() -> tuple[Path, Path | None]:
    value = os.environ.get("LIT_TUI_NOTES_DIR")
    if not value:
        value = str(
            Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")) / "tiny-reading-tracker/notes"
        )
    vault = os.environ.get("LIT_TUI_VAULT")
    return Path(value), Path(vault) if vault else None


def _write_recovery(notes_dir: Path, item_id: str, text: str) -> Path:
    notes_dir = notes_dir.expanduser()
    if notes_dir.exists() and notes_dir.is_symlink():
        raise NoteError(f"notes directory must not be a symlink: {notes_dir}")
    notes_dir.mkdir(parents=True, exist_ok=True)
    root = notes_dir.resolve(strict=True)
    path = root / f"recovery-{item_id}-{uuid.uuid4().hex}.md"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            path.unlink()
        except OSError:
            pass
        raise NoteError(f"could not write recovery note: {exc}") from exc
    return path


def _escape_link_label(value: str) -> str:
    """Escape the Markdown characters that can terminate link text."""

    return (
        value.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _link_search(request: dict[str, Any], library: Database) -> dict[str, Any]:
    query = request.get("query")
    if not isinstance(query, str):
        return _error("link_search requires a string query", "request")
    folded = query.strip().casefold()
    if not folded:
        return _response(items=[])
    items = [item for item in library.list_items(None) if folded in item["title"].casefold()]
    items.sort(key=lambda item: (item["title"].casefold(), item["id"]))
    return _response(items=[{"id": item["id"], "title": item["title"]} for item in items[:20]])


def _list(request: dict[str, Any], library: Database) -> dict[str, Any]:
    status = request.get("status", "all")
    if status not in _STATUSES:
        raise DatabaseError(f"invalid status: {status}")
    group = request.get("group")
    query = str(request.get("query", "")).strip().casefold()
    items = library.list_items(None if status == "all" else status, group=group)
    if query:
        items = [item for item in items if query in item["title"].casefold()]
    items.sort(key=lambda item: (item["title"].casefold(), item["id"]))
    groups = library.list_groups()
    selected = library.get_group(group) if group is not None else None

    def direct_ids(group_id: str) -> set[str]:
        return {r[0] for r in library.connection.execute(
            "SELECT item_id FROM item_groups WHERE group_id=?", (group_id,)
        )}

    visible_ids = {i["id"] for i in items}
    def section(title: str, ids: set[str], sid: str | None = None) -> dict[str, Any]:
        ids &= visible_ids
        return {"id": sid, "title": title, "item_ids": sorted(ids, key=lambda x: (next((i["title"].casefold() for i in items if i["id"] == x), ""), x))}

    sections: list[dict[str, Any]] = []
    if selected is not None:
        if selected["parent_id"] is not None:
            sections = [section(selected["name"], direct_ids(selected["id"]), selected["id"])]
        else:
            children = sorted((g for g in groups if g["parent_id"] == selected["id"]), key=lambda g: (g["name"].casefold(), g["id"]))
            child_ids = set().union(*(direct_ids(c["id"]) for c in children)) if children else set()
            sections = [section("", direct_ids(selected["id"]) - child_ids)]
            sections += [section(c["name"], direct_ids(c["id"]), c["id"]) for c in children]
    elif any(g["parent_id"] is not None for g in groups):
        roots = sorted((g for g in groups if g["parent_id"] is None), key=lambda g: (g["name"].casefold(), g["id"]))
        assigned: set[str] = set()
        root_sections: list[dict[str, Any]] = []
        for root in roots:
            children = sorted((g for g in groups if g["parent_id"] == root["id"]), key=lambda g: (g["name"].casefold(), g["id"]))
            root_direct = direct_ids(root["id"])
            child_union = set().union(*(direct_ids(c["id"]) for c in children)) if children else set()
            root_sections.append(section(root["name"], root_direct - child_union, root["id"]))
            assigned |= root_direct | child_union
            root_sections += [section(f"{root['name']} / {c['name']}", direct_ids(c["id"]), c["id"]) for c in children]
        unassigned = {i["id"] for i in items} - assigned
        if unassigned:
            sections.append(section("", unassigned))
        sections += root_sections
    else:
        sections = [section("Reading", {i["id"] for i in items})]
    return _response(items=items, groups=groups, sections=sections)


def _handle(
    request: dict[str, Any],
    library: Database,
    documents: dict[str, NoteDocument] | None = None,
) -> dict[str, Any]:
    if documents is None:
        documents = {}
    if request.get("version") != PROTOCOL_VERSION:
        return _error("unsupported protocol version", "protocol")
    operation = request.get("op")
    if operation == "list":
        return _list(request, library)
    if operation == "set_status":
        item_id = request.get("id")
        status = request.get("status")
        if not isinstance(item_id, str) or not isinstance(status, str):
            return _error("set_status requires string id and status", "request")
        return _response(item=library.set_status(item_id, status))
    if operation == "delete_item":
        item_id = request.get("id")
        if not isinstance(item_id, str) or not item_id:
            return _error("delete_item requires a full item ID", "request")
        try:
            library.delete_item(item_id)
        except DatabaseError as exc:
            return _error(str(exc), "request")
        documents.pop(item_id, None)
        return _response(deleted=item_id)
    if operation == "link_search":
        return _link_search(request, library)
    if operation in {"note_open", "note_save"}:
        item_id = request.get("id")
        if not isinstance(item_id, str) or not item_id:
            return _error("note operation requires a full item ID", "request")
        item = library.get(item_id)
        if item["id"] != item_id:
            return _error("note operation requires a full item ID", "request")
        notes_dir, vault = _notes_root()
        if operation == "note_open":
            note = load_note(item, notes_dir, vault)
            if not item.get("note_path"):
                library.set_note_path(item_id, str(note.path))
            documents.clear()
            documents[item_id] = note
            return _response(note=_note_payload(note))
        note = documents.get(item_id)
        if note is None:
            return _error("note must be opened before saving", "request")
        revision = request.get("revision")
        text = request.get("text")
        if not isinstance(revision, str) or not isinstance(text, str):
            return _error("note_save requires string text and revision", "request")
        if revision != note.revision:
            return _error("note baseline is stale; reopen the note", "conflict")
        try:
            updated = save_note(note, text)
        except NoteConflictError as exc:
            return _error(str(exc), "conflict")
        except NoteError as exc:
            return _error(str(exc), "note")
        documents[item_id] = updated
        return _response(note=_note_payload(updated))
    if operation == "note_recover":
        item_id = request.get("id")
        text = request.get("text")
        if not isinstance(item_id, str) or not isinstance(text, str):
            return _error("note_recover requires string id and text", "request")
        item = library.get(item_id)
        if item["id"] != item_id:
            return _error("note operation requires a full item ID", "request")
        try:
            path = _write_recovery(_notes_root()[0], item_id, text)
        except NoteError as exc:
            return _error(str(exc), "note")
        return _response(path=str(path))
    if operation == "note_link":
        item_id = request.get("id")
        target_id = request.get("target_id")
        if not isinstance(item_id, str) or not isinstance(target_id, str):
            return _error("note_link requires full string item IDs", "request")
        try:
            current = library.get(item_id)
            target = library.get(target_id)
        except DatabaseError as exc:
            return _error(str(exc), "request")
        if current["id"] != item_id or target["id"] != target_id:
            return _error("note_link requires full item IDs", "request")
        active = documents.get(item_id)
        if active is None:
            return _error("current note must be opened before linking", "request")
        notes_dir, vault = _notes_root()
        try:
            target_note = load_note(target, notes_dir, vault)
            if not target.get("note_path"):
                library.set_note_path(target_id, str(target_note.path))
        except NoteError as exc:
            return _error(str(exc), "note")
        relative = os.path.relpath(target_note.path, start=active.path.parent)
        encoded = quote(Path(relative).as_posix(), safe="/")
        markdown = f"[{_escape_link_label(target['title'])}](<{encoded}>)"
        return _response(markdown=markdown, target_path=str(target_note.path))
    return _error(f"unknown operation: {operation}", "request")


def main() -> int:
    db = os.environ.get("LIT_TUI_DB")
    if not db:
        print(json.dumps(_error("LIT_TUI_DB is not set", "configuration")), flush=True)
        return 2
    try:
        with Database(Path(db)) as library:
            documents: dict[str, NoteDocument] = {}
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("request must be a JSON object")
                    result = _handle(request, library, documents)
                except (ValueError, TypeError, DatabaseError, OSError, sqlite3.Error) as exc:
                    result = _error(str(exc), "backend")
                print(json.dumps(result, ensure_ascii=False), flush=True)
    except (ValueError, OSError) as exc:
        print(json.dumps(_error(str(exc), "configuration")), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
