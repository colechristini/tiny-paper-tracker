from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tiny_reading_tracker.db import (
    AmbiguousItemError,
    Database,
    DatabaseError,
    IdentityConflictError,
    ItemNotFoundError,
)
from tiny_reading_tracker.models import ResolvedItem


def article(
    title: str = "Practical SQLite",
    identifiers: list[tuple[str, str]] | None = None,
) -> ResolvedItem:
    return ResolvedItem(
        title=title,
        url="https://example.test/paper",
        kind="journalArticle",
        authors=["Ada Lovelace", "Grace Hopper"],
        venue="Database Journal",
        published_at="2025-01-02",
        source="test",
        metadata={"abstract": "Useful"},
        identifiers=identifiers or [("doi", "10.1234/example")],
    )


def test_schema_durability_and_context_close(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "library.sqlite3"
    with Database(path) as db:
        saved, created = db.add(article(), tags=["databases", "reference"])
        assert created
        assert saved["id"].startswith("lit_")
        assert saved["authors"] == ["Ada Lovelace", "Grace Hopper"]
        assert saved["metadata"] == {"abstract": "Useful"}
        assert saved["status"] == "unread"
        assert saved["tags"] == ["databases", "reference"]
        connection = db.connection
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

    with Database(path) as reopened:
        assert reopened.get(saved["id"])["title"] == "Practical SQLite"
        assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert reopened.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert reopened.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_duplicate_adds_aliases_and_tags_but_preserves_fields(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        original, _ = db.add(article(), tags=["first"])
        read = db.set_status(original["id"], "read")
        db.set_note_path(original["id"], "Reading/original.md")
        duplicate = article("Replacement title", [("doi", "10.1234/example"), ("pmid", "42")])
        duplicate.url = "https://wrong.test/replacement"
        result, created = db.add(duplicate, tags=["second"])

        assert not created
        assert result["id"] == original["id"]
        assert result["title"] == original["title"]
        assert result["url"] == original["url"]
        assert result["status"] == "read"
        assert result["read_at"] == read["read_at"]
        assert result["note_path"] == "Reading/original.md"
        assert result["tags"] == ["first", "second"]
        assert db.find_identifiers([("PMID", "42")])["id"] == original["id"]


def test_identity_bridge_rolls_back_all_aliases_and_tags(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        first, _ = db.add(article("One", [("doi", "one")]))
        second, _ = db.add(article("Two", [("doi", "two")]))
        with pytest.raises(IdentityConflictError):
            db.add(article("Bridge", [("doi", "one"), ("doi", "two"), ("pmid", "new")]), ["bad"])
        assert db.find_identifiers([("pmid", "new")]) is None
        assert db.get(first["id"])["tags"] == []
        assert db.get(second["id"])["tags"] == []


def test_filters_fts_status_and_read_timestamp_semantics(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        first, _ = db.add(article(), ["systems"])
        second, _ = db.add(article("Biology Notes", [("url", "bio")]), ["genomics"])
        assert [x["id"] for x in db.search('"Practical SQLite"')] == [first["id"]]
        assert [x["id"] for x in db.search("syst*")] == [first["id"]]
        assert [x["id"] for x in db.list_items(tag="genomics")] == [second["id"]]
        assert len(db.list_items(kind="journalArticle")) == 2
        assert len(db.list_items(no_note=True)) == 2

        marked = db.set_status(first["id"], "read")
        marked_again = db.set_status(first["id"], "read")
        assert marked_again["read_at"] == marked["read_at"]
        assert db.search("SQLite", status="unread") == []
        unread = db.set_status(first["id"], "unread")
        assert unread["read_at"] is None
        with pytest.raises(DatabaseError):
            db.set_status(first["id"], "later")
        with pytest.raises(DatabaseError, match="FTS5"):
            db.search('"unterminated')


def test_get_resolution_and_ambiguity(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        first, _ = db.add(article("A Shared Subject", [("url", "one")]))
        db.add(article("Another Shared Subject", [("url", "two")]))
        assert db.get(first["id"])["id"] == first["id"]
        assert db.get(first["id"][:15])["id"] == first["id"]
        with pytest.raises(AmbiguousItemError, match=first["id"]):
            db.get("Shared Subject")
        with pytest.raises(ItemNotFoundError):
            db.get("missing")
        with pytest.raises(ItemNotFoundError):
            db.get("%")
