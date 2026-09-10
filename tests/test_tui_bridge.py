from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import tiny_reading_tracker.tui_bridge as tui_bridge
from tiny_reading_tracker.config import Config
from tiny_reading_tracker.db import Database
from tiny_reading_tracker.ingest import ResolutionError
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


def test_bridge_list_note_filters_combine_with_status_group_title_and_sections(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "library.db") as library:
        root = library.create_group("Root")
        child = library.create_group("Child", parent=root["id"])
        with_note, _ = library.add(
            ResolvedItem(
                title="Match child",
                url="https://child.example/",
                identifiers=[("url", "https://child.example/")],
            ),
            groups=[child["id"]],
        )
        library.set_note_path(with_note["id"], "Reading/child.md")
        library.set_status(with_note["id"], "reading")
        without_note, _ = library.add(
            ResolvedItem(
                title="Match root",
                url="https://root.example/",
                identifiers=[("url", "https://root.example/")],
            ),
            groups=[root["id"]],
        )
        empty_note, _ = library.add(
            ResolvedItem(
                title="Match empty",
                url="https://empty.example/",
                identifiers=[("url", "https://empty.example/")],
            ),
            groups=[child["id"]],
        )
        library.connection.execute("UPDATE items SET note_path='' WHERE id=?", (empty_note["id"],))
        library.connection.commit()

        has_note = _handle(
            {
                "version": 1,
                "op": "list",
                "status": "reading",
                "group": root["id"],
                "query": "Match",
                "note_filter": "has_note",
            },
            library,
        )
        assert [item["id"] for item in has_note["items"]] == [with_note["id"]]
        assert [section["item_ids"] for section in has_note["sections"]] == [[], [with_note["id"]]]

        no_note = _handle(
            {
                "version": 1,
                "op": "list",
                "group": root["id"],
                "query": "Match",
                "note_filter": "no_note",
            },
            library,
        )
        assert {item["id"] for item in no_note["items"]} == {without_note["id"], empty_note["id"]}
        assert [section["item_ids"] for section in no_note["sections"]] == [
            [without_note["id"]],
            [empty_note["id"]],
        ]

        omitted = _handle(
            {"version": 1, "op": "list", "group": root["id"], "query": "Match"}, library
        )
        assert len(omitted["items"]) == 3


def test_bridge_rejects_unknown_protocol_operation(tmp_path: Path) -> None:
    with Database(tmp_path / "library.db") as library:
        response = _handle({"version": 99, "op": "list"}, library, {})
    assert response["ok"] is False
    assert response["error"]["kind"] == "protocol"


def test_bridge_add_item_resolves_and_attaches_exact_group(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_resolve(value, translator_url, timeout):
        calls.append((value, translator_url, timeout))
        return ResolvedItem(
            title="Resolved paper",
            url="https://resolved.example/paper",
            identifiers=[("url", "https://resolved.example/paper")],
        )

    monkeypatch.setattr(tui_bridge, "resolve", fake_resolve)
    config = Config(tmp_path / "db.sqlite", None, "https://translator.example", 7.5)
    with Database(config.db) as library:
        group = library.create_group("Reading")
        response = _handle(
            {
                "version": 1,
                "op": "add_item",
                "value": "https://input.example",
                "group_id": group["id"],
            },
            library,
            config=config,
        )
        assert response["ok"] and response["created"] is True
        assert response["item"]["groups"] == [{"id": group["id"], "name": "Reading"}]
        assert calls == [("https://input.example", "https://translator.example", 7.5)]


def test_bridge_add_item_duplicate_skips_network_and_preserves_state(
    tmp_path: Path, monkeypatch
) -> None:
    with Database(tmp_path / "db.sqlite") as library:
        item, _ = library.add(
            ResolvedItem(
                title="Existing title",
                url="https://existing.example/",
                identifiers=[("url", "https://existing.example/")],
            )
        )
        library.set_status(item["id"], "read")
        library.set_note_path(item["id"], "Reading/existing.md")
        group = library.create_group("Reading")

        def no_network(*args):
            raise AssertionError("duplicate add must not resolve through the network")

        monkeypatch.setattr(tui_bridge, "resolve", no_network)
        result = _handle(
            {
                "version": 1,
                "op": "add_item",
                "value": "https://existing.example/",
                "group_id": group["id"],
            },
            library,
        )
        assert result["ok"] and result["created"] is False
        assert result["item"]["id"] == item["id"]
        assert result["item"]["title"] == "Existing title"
        assert result["item"]["status"] == "read"
        assert result["item"]["note_path"] == "Reading/existing.md"
        assert result["item"]["groups"] == [{"id": group["id"], "name": "Reading"}]


def test_bridge_add_item_resolution_failure_does_not_mutate(tmp_path: Path, monkeypatch) -> None:
    with Database(tmp_path / "db.sqlite") as library:
        group = library.create_group("Reading")

        def failing_resolve(*args):
            assert not library.connection.in_transaction
            raise ResolutionError("lookup failed")

        monkeypatch.setattr(tui_bridge, "resolve", failing_resolve)
        result = _handle(
            {
                "version": 1,
                "op": "add_item",
                "value": "https://new.example/",
                "group_id": group["id"],
            },
            library,
        )
        assert result == {
            "version": 1,
            "ok": False,
            "error": {"kind": "request", "message": "lookup failed"},
        }
        assert library.list_items(None) == []
        assert library.connection.execute("SELECT COUNT(*) FROM item_groups").fetchone()[0] == 0


def test_bridge_add_item_invalid_input_or_group_does_not_mutate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        tui_bridge, "resolve", lambda *args: (_ for _ in ()).throw(AssertionError())
    )
    with Database(tmp_path / "db.sqlite") as library:
        before = library.list_items(None)
        bad_input = _handle(
            {"version": 1, "op": "add_item", "value": "not-an-input", "group_id": None}, library
        )
        bad_group = _handle(
            {"version": 1, "op": "add_item", "value": "https://new.example", "group_id": "missing"},
            library,
        )
        assert not bad_input["ok"] and not bad_group["ok"]
        assert library.list_items(None) == before


def test_bridge_rename_item_validates_and_returns_item(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as library:
        item, _ = library.add(
            ResolvedItem(
                title="Before", url="https://before", source="test", identifiers=[("url", "before")]
            )
        )
        response = _handle(
            {"version": 1, "op": "rename_item", "id": item["id"], "title": "After"}, library
        )
        assert response["ok"] is True
        assert response["item"]["title"] == "After"
        invalid = _handle(
            {"version": 1, "op": "rename_item", "id": item["id"][:8], "title": "Nope"}, library
        )
        assert invalid["ok"] is False
        blank = _handle(
            {"version": 1, "op": "rename_item", "id": item["id"], "title": " "}, library
        )
        assert blank["ok"] is False


def test_bridge_set_item_groups_replaces_direct_groups(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as library:
        root = library.create_group("Root")
        child = library.create_group("Child", parent=root["id"])
        item, _ = library.add(
            ResolvedItem(
                title="Paper", url="https://paper", source="test", identifiers=[("url", "paper")]
            ),
            groups=[root["id"]],
        )
        response = _handle(
            {"version": 1, "op": "set_item_groups", "id": item["id"], "group_ids": [child["id"]]},
            library,
        )
        assert response["ok"] is True
        assert response["item"]["groups"] == [{"id": child["id"], "name": "Child"}]
        invalid = _handle(
            {"version": 1, "op": "set_item_groups", "id": item["id"], "group_ids": ["missing"]},
            library,
        )
        assert invalid["ok"] is False
        assert library.get(item["id"])["groups"] == [{"id": child["id"], "name": "Child"}]


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
