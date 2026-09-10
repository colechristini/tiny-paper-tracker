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


def test_bridge_delete_requires_full_id_and_evicts_cached_document(tmp_path: Path) -> None:
    with Database(tmp_path / "library.db") as library:
        item, _ = library.add(ResolvedItem(title="Delete", url="https://delete", source="test"))
        documents = {item["id"]: object()}
        rejected = _handle(
            {"version": 1, "op": "delete_item", "id": item["id"][:10]}, library, documents
        )
        assert not rejected["ok"]
        deleted = _handle({"version": 1, "op": "delete_item", "id": item["id"]}, library, documents)
        assert deleted["ok"] and deleted["deleted"] == item["id"]
        assert item["id"] not in documents


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


def test_link_search_ignores_current_status_and_group_filters(tmp_path: Path) -> None:
    with Database(tmp_path / "library.db") as library:
        group = library.create_group("One group")
        first, _ = library.add(
            ResolvedItem(
                title="Attention unread",
                url="https://one",
                source="test",
                identifiers=[("url", "one")],
            ),
            groups=[group["id"]],
        )
        second, _ = library.add(
            ResolvedItem(
                title="Attention read",
                url="https://two",
                source="test",
                identifiers=[("url", "two")],
            )
        )
        library.set_status(second["id"], "read")
        result = _handle({"version": 1, "op": "link_search", "query": "ATTENTION"}, library, {})
        assert result["ok"]
        assert [entry["id"] for entry in result["items"]] == [second["id"], first["id"]]


def test_note_link_escapes_title_and_preserves_active_baseline(tmp_path: Path, monkeypatch) -> None:
    notes_dir = tmp_path / "notes"
    monkeypatch.setenv("LIT_TUI_NOTES_DIR", str(notes_dir))
    with Database(tmp_path / "library.db") as library:
        first_group = library.create_group("First")
        second_group = library.create_group("Second")
        current, _ = library.add(
            ResolvedItem(
                title="Current",
                url="https://current",
                source="test",
                identifiers=[("url", "current")],
            )
        )
        target, _ = library.add(
            ResolvedItem(
                title="Target [x] \\ path\nline # (v) 🧪",
                url="https://target",
                source="test",
                identifiers=[("url", "target")],
            )
        )
        library.add_to_group(first_group["id"], [target["id"]])
        library.add_to_group(second_group["id"], [target["id"]])
        documents = {}
        opened = _handle({"version": 1, "op": "note_open", "id": current["id"]}, library, documents)
        active_before = documents[current["id"]]
        result = _handle(
            {"version": 1, "op": "note_link", "id": current["id"], "target_id": target["id"]},
            library,
            documents,
        )
        assert result["ok"]
        assert result["markdown"].startswith(r"[Target \[x\] \\ path line # (v) 🧪](<")
        assert result["markdown"].endswith(">)")
        assert documents[current["id"]] == active_before
        assert library.get(target["id"])["note_path"] == result["target_path"]
        assert opened["note"]["revision"] == active_before.revision


def test_note_link_preserves_legacy_registered_path_outside_notes_dir(
    tmp_path: Path, monkeypatch
) -> None:
    notes_dir = tmp_path / "notes"
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("LIT_TUI_NOTES_DIR", str(notes_dir))
    monkeypatch.setenv("LIT_TUI_VAULT", str(vault))
    legacy = vault / "Legacy # note (old).md"
    legacy.write_text('---\nlit_id: "placeholder"\n---\n')
    with Database(tmp_path / "library.db") as library:
        current, _ = library.add(
            ResolvedItem(
                title="Current",
                url="https://current",
                source="test",
                identifiers=[("url", "current")],
            )
        )
        target, _ = library.add(
            ResolvedItem(
                title="Legacy", url="https://legacy", source="test", identifiers=[("url", "legacy")]
            )
        )
        legacy.write_text(f'---\nlit_id: "{target["id"]}"\n---\n')
        library.set_note_path(target["id"], str(legacy))
        documents = {}
        _handle({"version": 1, "op": "note_open", "id": current["id"]}, library, documents)
        result = _handle(
            {"version": 1, "op": "note_link", "id": current["id"], "target_id": target["id"]},
            library,
            documents,
        )
        assert result["ok"]
        assert result["markdown"] == "[Legacy](<../vault/Legacy%20%23%20note%20%28old%29.md>)"
        assert library.get(target["id"])["note_path"] == str(legacy)


def test_note_link_unknown_target_does_not_mutate_active_note(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LIT_TUI_NOTES_DIR", str(tmp_path / "notes"))
    with Database(tmp_path / "library.db") as library:
        current, _ = library.add(
            ResolvedItem(
                title="Current",
                url="https://current",
                source="test",
                identifiers=[("url", "current")],
            )
        )
        documents = {}
        _handle({"version": 1, "op": "note_open", "id": current["id"]}, library, documents)
        baseline = documents[current["id"]]
        result = _handle(
            {"version": 1, "op": "note_link", "id": current["id"], "target_id": "lit_missing"},
            library,
            documents,
        )
        assert result["ok"] is False
        assert documents[current["id"]] == baseline
