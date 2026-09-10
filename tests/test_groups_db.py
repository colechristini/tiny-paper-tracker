from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tiny_reading_tracker.db import Database, DatabaseError, GroupNotFoundError, ItemNotFoundError
from tiny_reading_tracker.models import ResolvedItem


def article(title: str, identifier: str) -> ResolvedItem:
    return ResolvedItem(
        title=title,
        url=f"https://example.test/{identifier}",
        kind="journalArticle",
        authors=["Ada Lovelace"],
        venue="Database Journal",
        published_at="2025-01-02",
        source="test",
        metadata={"abstract": "Useful"},
        identifiers=[("doi", identifier)],
    )


def create_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            kind TEXT NOT NULL,
            authors TEXT NOT NULL,
            venue TEXT,
            published_at TEXT,
            source TEXT NOT NULL,
            metadata TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('unread', 'read')),
            added_at TEXT NOT NULL,
            read_at TEXT,
            note_path TEXT
        );
        CREATE TABLE identifiers (
            scheme TEXT NOT NULL,
            value TEXT NOT NULL,
            item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            PRIMARY KEY (scheme, value)
        );
        CREATE TABLE tags (name TEXT PRIMARY KEY);
        CREATE TABLE item_tags (
            item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            tag TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE,
            PRIMARY KEY (item_id, tag)
        );
        CREATE VIRTUAL TABLE item_search USING fts5(
            item_id UNINDEXED, title, authors, venue, tags
        );
        PRAGMA user_version = 1;
        """
    )
    connection.execute(
        """
        INSERT INTO items VALUES (
            'lit_existing', 'Existing Paper', 'https://example.test/existing',
            'journalArticle', ?, 'Old Journal', '2024-01-01', 'test', ?,
            'read', '2024-02-01T00:00:00+00:00', '2024-02-02T00:00:00+00:00',
            'Reading/existing.md'
        )
        """,
        (json.dumps(["Existing Author"]), json.dumps({"legacy": True})),
    )
    connection.execute("INSERT INTO identifiers VALUES ('doi', 'legacy', 'lit_existing')")
    connection.execute("INSERT INTO tags VALUES ('preserved')")
    connection.execute("INSERT INTO item_tags VALUES ('lit_existing', 'preserved')")
    connection.execute(
        "INSERT INTO item_search VALUES (?, ?, ?, ?, ?)",
        ("lit_existing", "Existing Paper", "Existing Author", "Old Journal", "preserved"),
    )
    connection.commit()
    connection.close()


def create_v2_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
            kind TEXT NOT NULL, authors TEXT NOT NULL, venue TEXT,
            published_at TEXT, source TEXT NOT NULL, metadata TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('unread', 'read')),
            added_at TEXT NOT NULL, read_at TEXT, note_path TEXT
        );
        CREATE TABLE identifiers (scheme TEXT NOT NULL, value TEXT NOT NULL,
            item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            PRIMARY KEY (scheme, value));
        CREATE TABLE tags (name TEXT PRIMARY KEY);
        CREATE TABLE item_tags (item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            tag TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE, PRIMARY KEY (item_id, tag));
        CREATE VIRTUAL TABLE item_search USING fts5(item_id UNINDEXED, title, authors, venue, tags);
        CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL);
        CREATE TABLE item_groups (group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE, PRIMARY KEY (group_id, item_id));
        CREATE INDEX item_groups_item_id_idx ON item_groups(item_id);
        PRAGMA user_version = 2;
        """
    )
    connection.execute(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "lit_v2",
            "V2 Paper",
            "https://example.test/v2",
            "webpage",
            json.dumps(["Author"]),
            "Venue",
            "2025-01-01",
            "test",
            json.dumps({"keep": "yes"}),
            "read",
            "2025-01-01T00:00:00+00:00",
            "2025-01-02T00:00:00+00:00",
            "Reading/v2.md",
        ),
    )
    connection.execute("INSERT INTO identifiers VALUES ('doi', 'v2', 'lit_v2')")
    connection.execute("INSERT INTO tags VALUES ('tag-v2')")
    connection.execute("INSERT INTO item_tags VALUES ('lit_v2', 'tag-v2')")
    connection.execute(
        "INSERT INTO item_search VALUES ('lit_v2', 'V2 Paper', 'Author', 'Venue', 'tag-v2')"
    )
    connection.execute("INSERT INTO groups VALUES ('grp_v2', 'Archive', 'archive', '2025-01-01')")
    connection.execute("INSERT INTO item_groups VALUES ('grp_v2', 'lit_v2')")
    connection.commit()
    connection.close()


def create_v3_database(path: Path, orphan_membership: bool = False) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
            kind TEXT NOT NULL, authors TEXT NOT NULL, venue TEXT,
            published_at TEXT, source TEXT NOT NULL, metadata TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('unread', 'reading', 'read')),
            added_at TEXT NOT NULL, read_at TEXT, note_path TEXT
        );
        CREATE TABLE identifiers (scheme TEXT NOT NULL, value TEXT NOT NULL,
            item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            PRIMARY KEY (scheme, value));
        CREATE TABLE tags (name TEXT PRIMARY KEY);
        CREATE TABLE item_tags (item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            tag TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE, PRIMARY KEY (item_id, tag));
        CREATE VIRTUAL TABLE item_search USING fts5(item_id UNINDEXED, title, authors, venue, tags);
        CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL);
        CREATE TABLE item_groups (group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE, PRIMARY KEY (group_id, item_id));
        CREATE INDEX item_groups_item_id_idx ON item_groups(item_id);
        PRAGMA user_version = 3;
        """
    )
    connection.execute(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "lit_v3",
            "V3 Paper",
            "https://example.test/v3",
            "webpage",
            json.dumps(["Author"]),
            "Venue",
            "2025-01-01",
            "test",
            json.dumps({"keep": True}),
            "read",
            "2025-01-01T00:00:00+00:00",
            "2025-01-02T00:00:00+00:00",
            "Reading/v3.md",
        ),
    )
    connection.execute("INSERT INTO identifiers VALUES ('doi', 'v3', 'lit_v3')")
    connection.execute("INSERT INTO tags VALUES ('legacy')")
    connection.execute("INSERT INTO item_tags VALUES ('lit_v3', 'legacy')")
    connection.execute(
        "INSERT INTO item_search VALUES ('lit_v3', 'V3 Paper', 'Author', 'Venue', 'legacy')"
    )
    connection.execute("INSERT INTO groups VALUES ('grp_v3', 'Archive', 'archive', '2025-01-01')")
    connection.execute("INSERT INTO item_groups VALUES ('grp_v3', 'lit_v3')")
    if orphan_membership:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("INSERT INTO item_groups VALUES ('grp_missing', 'lit_v3')")
    connection.commit()
    connection.close()


def test_v1_migration_preserves_all_item_state_and_fts(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite"
    create_v1_database(path)

    with Database(path) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        item = db.get("lit_existing")
        assert item["authors"] == ["Existing Author"]
        assert item["metadata"] == {"legacy": True}
        assert item["identifiers"] == [("doi", "legacy")]
        assert item["tags"] == ["preserved"]
        assert item["status"] == "read"
        assert item["read_at"] == "2024-02-02T00:00:00+00:00"
        assert item["note_path"] == "Reading/existing.md"
        assert item["groups"] == []
        assert [found["id"] for found in db.search('"Existing Paper"')] == ["lit_existing"]
        assert db.list_groups() == []


def test_v2_migration_preserves_groups_and_all_item_state(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite"
    create_v2_database(path)
    with Database(path) as db:
        item = db.get("lit_v2")
        assert item["metadata"] == {"keep": "yes"}
        assert item["tags"] == ["tag-v2"]
        assert item["note_path"] == "Reading/v2.md"
        assert item["groups"] == [{"id": "grp_v2", "name": "Archive"}]
        assert [found["id"] for found in db.search('"V2 Paper"')] == ["lit_v2"]


def test_v3_migration_preserves_groups_memberships_and_fts(tmp_path: Path) -> None:
    path = tmp_path / "v3.sqlite"
    create_v3_database(path)
    with Database(path) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.get_group("Archive")["id"] == "grp_v3"
        item = db.get("lit_v3")
        assert item["groups"] == [{"id": "grp_v3", "name": "Archive"}]
        assert item["note_path"] == "Reading/v3.md"
        assert item["metadata"] == {"keep": True}
        assert [entry["id"] for entry in db.search("V3 Paper")] == ["lit_v3"]


def test_v3_group_migration_rolls_back_on_invalid_membership(tmp_path: Path) -> None:
    path = tmp_path / "v3-invalid.sqlite"
    create_v3_database(path, orphan_membership=True)
    with pytest.raises(DatabaseError, match="foreign-key validation"):
        Database(path)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='groups'").fetchone()
    assert connection.execute("SELECT COUNT(*) FROM item_groups").fetchone()[0] == 2
    connection.close()


def test_v2_migration_rolls_back_on_invalid_status(tmp_path: Path) -> None:
    path = tmp_path / "v2-invalid.sqlite"
    create_v2_database(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA ignore_check_constraints = ON")
    connection.execute("UPDATE items SET status = 'corrupt' WHERE id = 'lit_v2'")
    connection.commit()
    connection.close()
    with pytest.raises(sqlite3.IntegrityError):
        Database(path)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert (
        connection.execute("SELECT status FROM items WHERE id = 'lit_v2'").fetchone()[0]
        == "corrupt"
    )
    connection.close()


def test_concurrent_v1_migration_is_serialized(tmp_path: Path) -> None:
    path = tmp_path / "concurrent-v1.sqlite"
    create_v1_database(path)

    def open_database(_: int) -> int:
        with Database(path) as db:
            return db.connection.execute("PRAGMA user_version").fetchone()[0]

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(open_database, range(8))) == [4] * 8


def test_new_schema_and_future_schema_rejection(tmp_path: Path) -> None:
    path = tmp_path / "new.sqlite"
    with Database(path) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        indexes = {
            row["name"]
            for row in db.connection.execute("PRAGMA index_list('item_groups')").fetchall()
        }
        assert "item_groups_item_id_idx" in indexes

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 5")
    connection.close()
    with pytest.raises(DatabaseError, match="unsupported database schema version 5"):
        Database(path)


def test_group_names_are_trimmed_unicode_casefolded_and_exact(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        group = db.create_group("  Straße  ")
        assert group["id"].startswith("grp_")
        assert group["name"] == "Straße"
        assert group["item_count"] == 0
        assert db.get_group("STRASSE") == group
        assert db.get_group(group["id"]) == group
        with pytest.raises(DatabaseError, match="already exists"):
            db.create_group("strasse")
        other = db.create_group("Other")
        with pytest.raises(DatabaseError, match="conflicts with an existing group ID"):
            db.create_group(group["id"])
        with pytest.raises(DatabaseError, match="conflicts with an existing group ID"):
            db.rename_group(other["id"], group["id"])
        with pytest.raises(GroupNotFoundError):
            db.get_group("Stra")
        with pytest.raises(DatabaseError, match="must not be empty"):
            db.create_group("   ")


def test_memberships_are_multiple_idempotent_and_rename_is_live(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        first_group = db.create_group("Methods")
        second_group = db.create_group("Favorites")
        first, _ = db.add(article("First", "first"), groups=[first_group["id"], "favorites"])
        second, _ = db.add(article("Second", "second"))

        assert first["groups"] == [
            {"id": second_group["id"], "name": "Favorites"},
            {"id": first_group["id"], "name": "Methods"},
        ]
        result = db.add_to_group("METHODS", [first["id"], second["id"], first["id"]])
        assert result["item_count"] == 2
        assert db.add_to_group(first_group["id"], [second["id"]])["item_count"] == 2

        renamed = db.rename_group("methods", "Core Methods")
        assert renamed["id"] == first_group["id"]
        assert renamed["item_count"] == 2
        assert {entry["id"]: entry["name"] for entry in db.get(first["id"])["groups"]}[
            first_group["id"]
        ] == "Core Methods"
        with pytest.raises(GroupNotFoundError):
            db.get_group("Methods")


def test_membership_changes_validate_all_items_before_writing(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        db.create_group("Queue")
        first, _ = db.add(article("First", "first"))
        second, _ = db.add(article("Second", "second"))

        with pytest.raises(ItemNotFoundError):
            db.add_to_group("Queue", [first["id"], "lit_missing"])
        assert db.get_group("Queue")["item_count"] == 0

        db.add_to_group("Queue", [first["id"], second["id"]])
        with pytest.raises(ItemNotFoundError):
            db.remove_from_group("Queue", [first["id"], "lit_missing"])
        assert db.get_group("Queue")["item_count"] == 2
        assert db.remove_from_group("Queue", [first["id"], first["id"]])["item_count"] == 1
        assert db.remove_from_group("Queue", [first["id"]])["item_count"] == 1


def test_group_filters_status_dedup_and_delete_preserve_items(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        group = db.create_group("Reading Group")
        first, _ = db.add(article("Systems Paper", "systems"), ["database"], ["reading group"])
        db.set_status(first["id"], "read")
        db.set_note_path(first["id"], "Reading/systems.md")
        duplicate, created = db.add(
            article("Replacement", "systems"), groups=[group["id"], "READING GROUP"]
        )
        second, _ = db.add(article("Other Systems", "other"))

        assert not created
        assert duplicate["id"] == first["id"]
        assert db.get_group("Reading Group")["item_count"] == 1
        assert db.list_items(status=None, group="READING GROUP") == [duplicate]
        assert db.list_items(status="unread", group=group["id"]) == []
        assert [item["id"] for item in db.search("Systems", group="Reading Group")] == [first["id"]]
        assert db.search("Systems", status="unread", group="Reading Group") == []
        with pytest.raises(GroupNotFoundError):
            db.list_items(status=None, group="missing")
        with pytest.raises(GroupNotFoundError):
            db.search("Systems", group="missing")

        deleted = db.delete_group(group["id"])
        assert deleted["item_count"] == 1
        assert db.list_groups() == []
        preserved = db.get(first["id"])
        assert preserved["status"] == "read"
        assert preserved["note_path"] == "Reading/systems.md"
        assert preserved["tags"] == ["database"]
        assert preserved["groups"] == []
        assert db.get(second["id"])["title"] == "Other Systems"


def test_two_level_groups_lookup_membership_and_delete(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        root = db.create_group("Root")
        other = db.create_group("Other")
        child = db.create_group("Child", parent=root["id"])
        other_child = db.create_group("Child", parent=other["id"])
        with pytest.raises(DatabaseError, match="at most one parent"):
            db.create_group("Grandchild", parent=child["id"])
        with pytest.raises(DatabaseError, match="ambiguous group"):
            db.get_group("Child")
        assert db.get_group("Root/Child")["id"] == child["id"]
        assert db.get_group("Root / Child")["id"] == child["id"]
        with pytest.raises(sqlite3.IntegrityError, match="at most one parent"):
            db.connection.execute(
                "INSERT INTO groups(id,name,name_key,parent_id,created_at) VALUES ('x','X','x',?,?)",
                (child["id"], "now"),
            )
        first, _ = db.add(article("First", "first"), groups=[child["id"]])
        second, _ = db.add(article("Second", "second"), groups=[root["id"]])
        assert db.get_group(root["id"])["item_count"] == 2
        assert {i["id"] for i in db.list_items(None, group=root["id"])} == {
            first["id"],
            second["id"],
        }
        db.remove_from_group(root["id"], [first["id"]])
        assert db.get_group(child["id"])["item_count"] == 0
        db.add_to_group(child["id"], [first["id"]])
        db.delete_group(root["id"])
        assert db.get(first["id"])["groups"] == []
        assert db.get_group(other_child["id"])["name"] == "Child"
