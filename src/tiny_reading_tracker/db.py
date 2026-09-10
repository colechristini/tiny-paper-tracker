"""Durable SQLite storage for the reading tracker."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .models import ResolvedItem

SCHEMA_VERSION = 1
VALID_STATUSES = {"unread", "read"}


class DatabaseError(ValueError):
    """Base class for errors that are safe to present to a CLI user."""


class IdentityConflictError(DatabaseError):
    """Raised when a set of aliases would join two distinct library items."""


class ItemNotFoundError(DatabaseError):
    """Raised when no item matches a lookup."""


class AmbiguousItemError(DatabaseError):
    """Raised when a lookup matches more than one item."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _identifiers(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for scheme, value in values:
        pair = (str(scheme).strip().lower(), str(value).strip())
        if not pair[0] or not pair[1]:
            raise DatabaseError("identifier scheme and value must not be empty")
        if pair not in seen:
            result.append(pair)
            seen.add(pair)
    return result


def _tags(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = str(value).strip()
        if not tag:
            raise DatabaseError("tags must not be empty")
        if tag not in seen:
            result.append(tag)
            seen.add(tag)
    return result


class Database:
    """A small, synchronous SQLite repository.

    Each instance owns one connection.  Separate instances can safely write to
    the same database: ``BEGIN IMMEDIATE`` serializes identity resolution with
    alias insertion, closing the otherwise common check-then-insert race.
    """

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self.path, timeout=10.0, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._create_schema()
        except Exception:
            self.close()
            raise

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DatabaseError("database is closed")
        return self._connection

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _create_schema(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, SCHEMA_VERSION):
            raise DatabaseError(
                f"unsupported database schema version {version}; expected {SCHEMA_VERSION}"
            )
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
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
            CREATE TABLE IF NOT EXISTS identifiers (
                scheme TEXT NOT NULL,
                value TEXT NOT NULL,
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                PRIMARY KEY (scheme, value)
            );
            CREATE TABLE IF NOT EXISTS tags (
                name TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS item_tags (
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                tag TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE,
                PRIMARY KEY (item_id, tag)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS item_search USING fts5(
                item_id UNINDEXED, title, authors, venue, tags
            );
            PRAGMA user_version = 1;
            """
        )

    def _begin(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def _matching_ids(self, identifiers: list[tuple[str, str]]) -> set[str]:
        found: set[str] = set()
        for scheme, value in identifiers:
            row = self.connection.execute(
                "SELECT item_id FROM identifiers WHERE scheme = ? AND value = ?",
                (scheme, value),
            ).fetchone()
            if row is not None:
                found.add(str(row[0]))
        return found

    def add(self, item: ResolvedItem, tags: list[str] | None = None) -> tuple[dict[str, Any], bool]:
        title = item.title.strip()
        url = item.url.strip()
        kind = item.kind.strip()
        source = item.source.strip()
        if not title or not url or not kind or not source:
            raise DatabaseError("title, URL, kind, and source must not be empty")
        identifiers = _identifiers(item.identifiers)
        tag_names = _tags(tags or [])
        try:
            self._begin()
            matching = self._matching_ids(identifiers)
            if len(matching) > 1:
                raise IdentityConflictError(
                    "identifiers belong to different existing items; refusing to merge them"
                )
            created = not matching
            if created:
                item_id = f"lit_{uuid.uuid4()}"
                self.connection.execute(
                    """
                    INSERT INTO items (
                        id, title, url, kind, authors, venue, published_at, source,
                        metadata, status, added_at, read_at, note_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unread', ?, NULL, NULL)
                    """,
                    (
                        item_id,
                        title,
                        url,
                        kind,
                        json.dumps(list(item.authors), ensure_ascii=False),
                        item.venue,
                        item.published_at,
                        source,
                        json.dumps(item.metadata, ensure_ascii=False, sort_keys=True),
                        _now(),
                    ),
                )
            else:
                item_id = matching.pop()

            for scheme, value in identifiers:
                self.connection.execute(
                    "INSERT OR IGNORE INTO identifiers (scheme, value, item_id) VALUES (?, ?, ?)",
                    (scheme, value, item_id),
                )
                owner = self.connection.execute(
                    "SELECT item_id FROM identifiers WHERE scheme = ? AND value = ?",
                    (scheme, value),
                ).fetchone()[0]
                if owner != item_id:
                    raise IdentityConflictError(
                        f"identifier {scheme}:{value} belongs to another item"
                    )
            self._add_tags(item_id, tag_names)
            self._refresh_search(item_id)
            self.connection.commit()
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise
        return self._get_id(item_id), created

    def _add_tags(self, item_id: str, tags: list[str]) -> None:
        for tag in tags:
            self.connection.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            self.connection.execute(
                "INSERT OR IGNORE INTO item_tags (item_id, tag) VALUES (?, ?)", (item_id, tag)
            )

    def _refresh_search(self, item_id: str) -> None:
        row = self.connection.execute(
            "SELECT title, authors, venue FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        tags = " ".join(
            r[0]
            for r in self.connection.execute(
                "SELECT tag FROM item_tags WHERE item_id = ? ORDER BY tag", (item_id,)
            )
        )
        authors = " ".join(json.loads(row["authors"]))
        self.connection.execute("DELETE FROM item_search WHERE item_id = ?", (item_id,))
        self.connection.execute(
            "INSERT INTO item_search (item_id, title, authors, venue, tags) VALUES (?, ?, ?, ?, ?)",
            (item_id, row["title"], authors, row["venue"] or "", tags),
        )

    def find_identifiers(self, identifiers: list[tuple[str, str]]) -> dict[str, Any] | None:
        normalized = _identifiers(identifiers)
        matching = self._matching_ids(normalized)
        if len(matching) > 1:
            raise IdentityConflictError("identifiers belong to different existing items")
        return self._get_id(matching.pop()) if matching else None

    def _item_query(self) -> str:
        return """
            SELECT i.*,
                   COALESCE((SELECT json_group_array(tag) FROM (
                       SELECT tag FROM item_tags WHERE item_id = i.id ORDER BY tag
                   )), '[]') AS tags_json,
                   COALESCE((SELECT json_group_array(json_object('scheme', scheme, 'value', value))
                       FROM (SELECT scheme, value FROM identifiers
                             WHERE item_id = i.id ORDER BY scheme, value)), '[]') AS identifiers_json
            FROM items AS i
        """

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
        value = {
            key: row[key]
            for key in (
                "id",
                "title",
                "url",
                "kind",
                "venue",
                "published_at",
                "source",
                "status",
                "added_at",
                "read_at",
                "note_path",
            )
        }
        value["authors"] = json.loads(row["authors"])
        value["metadata"] = json.loads(row["metadata"])
        value["tags"] = json.loads(row["tags_json"])
        value["identifiers"] = [
            (entry["scheme"], entry["value"]) for entry in json.loads(row["identifiers_json"])
        ]
        return value

    def _get_id(self, item_id: str) -> dict[str, Any]:
        row = self.connection.execute(self._item_query() + " WHERE i.id = ?", (item_id,)).fetchone()
        if row is None:
            raise ItemNotFoundError(f"item not found: {item_id}")
        return self._row_to_item(row)

    def list_items(
        self,
        status: str | None = "unread",
        tag: str | None = None,
        kind: str | None = None,
        no_note: bool = False,
    ) -> list[dict[str, Any]]:
        if status is not None and status not in VALID_STATUSES:
            raise DatabaseError(f"invalid status: {status}")
        clauses: list[str] = []
        parameters: list[Any] = []
        if status is not None:
            clauses.append("i.status = ?")
            parameters.append(status)
        if kind is not None:
            clauses.append("i.kind = ?")
            parameters.append(kind)
        if tag is not None:
            clauses.append("EXISTS (SELECT 1 FROM item_tags f WHERE f.item_id=i.id AND f.tag=?)")
            parameters.append(tag)
        if no_note:
            clauses.append("i.note_path IS NULL")
        sql = self._item_query()
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY i.added_at DESC, i.id DESC"
        return [self._row_to_item(row) for row in self.connection.execute(sql, parameters)]

    def search(self, query: str, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in VALID_STATUSES:
            raise DatabaseError(f"invalid status: {status}")
        expression = query.strip()
        if not expression:
            return []
        sql = self._item_query() + (
            " JOIN item_search AS s ON s.item_id = i.id WHERE item_search MATCH ?"
        )
        parameters: list[Any] = [expression]
        if status is not None:
            sql += " AND i.status = ?"
            parameters.append(status)
        sql += " ORDER BY bm25(item_search), i.added_at DESC, i.id DESC"
        try:
            rows = self.connection.execute(sql, parameters)
            return [self._row_to_item(row) for row in rows]
        except sqlite3.OperationalError as error:
            raise DatabaseError(f"invalid FTS5 search query: {error}") from None

    @staticmethod
    def _ambiguity(label: str, query: str, rows: list[sqlite3.Row]) -> AmbiguousItemError:
        candidates = ", ".join(f"{row['id']} ({row['title']})" for row in rows[:5])
        extra = f", and {len(rows) - 5} more" if len(rows) > 5 else ""
        return AmbiguousItemError(f"ambiguous {label} {query!r}: {candidates}{extra}")

    def get(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise DatabaseError("item query must not be empty")
        row = self.connection.execute(self._item_query() + " WHERE i.id = ?", (query,)).fetchone()
        if row is not None:
            return self._row_to_item(row)
        rows = self.connection.execute(
            self._item_query() + " WHERE substr(i.id, 1, length(?)) = ? ORDER BY i.id",
            (query, query),
        ).fetchall()
        if len(rows) == 1:
            return self._row_to_item(rows[0])
        if len(rows) > 1:
            raise self._ambiguity("item ID prefix", query, rows)
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.connection.execute(
            self._item_query()
            + " WHERE i.title LIKE ? ESCAPE '\\' COLLATE NOCASE ORDER BY i.added_at DESC, i.id",
            (f"%{escaped}%",),
        ).fetchall()
        if not rows:
            raise ItemNotFoundError(f"item not found: {query}")
        if len(rows) > 1:
            raise self._ambiguity("item title", query, rows)
        return self._row_to_item(rows[0])

    def set_status(self, item_id: str, status: str) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise DatabaseError(f"invalid status: {status}")
        if status == "read":
            cursor = self.connection.execute(
                "UPDATE items SET status = 'read', read_at = COALESCE(read_at, ?) WHERE id = ?",
                (_now(), item_id),
            )
        else:
            cursor = self.connection.execute(
                "UPDATE items SET status = 'unread', read_at = NULL WHERE id = ?", (item_id,)
            )
        if cursor.rowcount == 0:
            raise ItemNotFoundError(f"item not found: {item_id}")
        return self._get_id(item_id)

    def set_note_path(self, item_id: str, path: str | Path) -> dict[str, Any]:
        self._get_id(item_id)
        value = str(path)
        if not value:
            raise DatabaseError("note path must not be empty")
        self.connection.execute("UPDATE items SET note_path = ? WHERE id = ?", (value, item_id))
        return self._get_id(item_id)
