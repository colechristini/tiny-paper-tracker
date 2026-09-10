from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tiny_reading_tracker.db import Database
from tiny_reading_tracker.models import ResolvedItem
from tiny_reading_tracker.tui_bridge import _handle


def test_bridge_lists_title_sorted_and_mutates_status(tmp_path: Path) -> None:
    with Database(tmp_path / "library.db") as library:
        documents = {}
        second, _ = library.add(
            ResolvedItem(title="Zeta", url="https://z", source="test", identifiers=[("url", "z")])
        )
        first, _ = library.add(
            ResolvedItem(title="Alpha", url="https://a", source="test", identifiers=[("url", "a")])
        )
        response = _handle({"version": 1, "op": "list", "status": "all"}, library, documents)
        assert [item["id"] for item in response["items"]] == [first["id"], second["id"]]
        changed = _handle(
            {"version": 1, "op": "set_status", "id": first["id"], "status": "reading"},
            library,
            documents,
        )
        assert changed["ok"] and changed["item"]["status"] == "reading"


def test_bridge_rejects_unknown_protocol_operation(tmp_path: Path) -> None:
    with Database(tmp_path / "library.db") as library:
        response = _handle({"version": 99, "op": "list"}, library, {})
        assert response["ok"] is False
        assert response["error"]["kind"] == "protocol"


def test_bridge_note_open_save_and_conflict(tmp_path: Path, monkeypatch) -> None:
    notes_dir = tmp_path / "notes"
    monkeypatch.setenv("LIT_TUI_NOTES_DIR", str(notes_dir))
    with Database(tmp_path / "library.db") as library:
        item, _ = library.add(
            ResolvedItem(
                title="Editable",
                url="https://editable",
                source="test",
                identifiers=[("url", "editable")],
            )
        )
        documents = {}
        opened = _handle({"version": 1, "op": "note_open", "id": item["id"]}, library, documents)
        assert opened["ok"]
        note = opened["note"]
        saved = _handle(
            {
                "version": 1,
                "op": "note_save",
                "id": item["id"],
                "text": note["text"] + "\nlocal",
                "revision": note["revision"],
            },
            library,
            documents,
        )
        assert saved["ok"] and saved["note"]["text"].endswith("\nlocal")
        Path(saved["note"]["path"]).write_text("external")
        conflict = _handle(
            {
                "version": 1,
                "op": "note_save",
                "id": item["id"],
                "text": "overwrite",
                "revision": saved["note"]["revision"],
            },
            library,
            documents,
        )
        assert conflict["ok"] is False
        assert conflict["error"]["kind"] == "conflict"


def test_bridge_note_recover_writes_unique_copy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LIT_TUI_NOTES_DIR", str(tmp_path / "notes"))
    with Database(tmp_path / "library.db") as library:
        item, _ = library.add(
            ResolvedItem(
                title="Recoverable",
                url="https://recoverable",
                source="test",
                identifiers=[("url", "recoverable")],
            )
        )
        dirty = "# local buffer\n"
        result = _handle(
            {"version": 1, "op": "note_recover", "id": item["id"], "text": dirty},
            library,
            {},
        )
        assert result["ok"]
        recovery = Path(result["path"])
        assert recovery.read_text() == dirty
        assert library.get(item["id"])["note_path"] is None


def test_bridge_process_handles_multiple_requests(tmp_path: Path) -> None:
    db_path = tmp_path / "library.db"
    with Database(db_path) as library:
        item, _ = library.add(
            ResolvedItem(
                title="Process item",
                url="https://process",
                source="test",
                identifiers=[("url", "process")],
            )
        )
    environment = dict(os.environ, LIT_TUI_DB=str(db_path), PYTHONPATH="src")
    process = subprocess.Popen(
        [sys.executable, "-m", "tiny_reading_tracker.tui_bridge"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write('{"version":1,"op":"list","status":"all"}\n')
    process.stdin.flush()
    listed = json.loads(process.stdout.readline())
    process.stdin.write(
        json.dumps({"version": 1, "op": "set_status", "id": item["id"], "status": "reading"}) + "\n"
    )
    process.stdin.flush()
    changed = json.loads(process.stdout.readline())
    process.stdin.close()
    process.wait(timeout=5)
    assert listed["ok"] and listed["items"][0]["id"] == item["id"]
    assert changed["ok"] and changed["item"]["status"] == "reading"
