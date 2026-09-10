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

SCHEMA_VERSION = 4
VALID_STATUSES = {"unread", "reading", "read"}


class DatabaseError(ValueError):
    """Base class for errors that are safe to present to a CLI user."""


class IdentityConflictError(DatabaseError):
    """Raised when a set of aliases would join two distinct library items."""


class ItemNotFoundError(DatabaseError):
    """Raised when no item matches a lookup."""


class AmbiguousItemError(DatabaseError):
    """Raised when a lookup matches more than one item."""


class GroupNotFoundError(DatabaseError):
    """Raised when no group matches an exact ID or name lookup."""


class AmbiguousGroupError(DatabaseError):
    """Raised when a bare group name matches multiple groups."""


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


def _group_name(value: str) -> tuple[str, str]:
    name = str(value).strip()
    if not name:
        raise DatabaseError("group name must not be empty")
    return name, name.casefold()


def _group_queries(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        query = str(value).strip()
        if not query:
            raise DatabaseError("group query must not be empty")
        if query not in seen:
            result.append(query)
            seen.add(query)
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
        try:
            # The v2 -> v3 table rebuild changes a CHECK constraint. Foreign-key
            # enforcement is restored before returning and checked by SQLite on
            # every subsequent write.
            self.connection.execute("PRAGMA foreign_keys = OFF")
            self._begin()
            version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, 1, 2, 3, SCHEMA_VERSION):
                raise DatabaseError(
                    f"unsupported database schema version {version}; expected {SCHEMA_VERSION}"
                )
            if version == 4:
                self.connection.commit()
                self.connection.execute("PRAGMA foreign_keys = ON")
                return
            if version == 0:
                self.connection.execute("""
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
                status TEXT NOT NULL CHECK (status IN ('unread', 'reading', 'read')),
                added_at TEXT NOT NULL,
                read_at TEXT,
                note_path TEXT
                )
                """)
                self.connection.execute("""
                CREATE TABLE identifiers (
                scheme TEXT NOT NULL,
                value TEXT NOT NULL,
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                PRIMARY KEY (scheme, value)
                )
                """)
                self.connection.execute("""
                CREATE TABLE tags (
                name TEXT PRIMARY KEY
                )
                """)
                self.connection.execute("""
                CREATE TABLE item_tags (
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                tag TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE,
                PRIMARY KEY (item_id, tag)
                )
                """)
                self.connection.execute("""
                CREATE VIRTUAL TABLE item_search USING fts5(
                item_id UNINDEXED, title, authors, venue, tags
                )
                """)
            if version < 2:
                self.connection.execute("""
                CREATE TABLE groups (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL,
                    parent_id TEXT REFERENCES groups(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    UNIQUE(parent_id, name_key)
                )
                """)
                self.connection.execute("""
                CREATE TABLE item_groups (
                    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                    PRIMARY KEY (group_id, item_id)
                )
                """)
                self.connection.execute(
                    "CREATE INDEX item_groups_item_id_idx ON item_groups(item_id)"
                )
            if version in (1, 2):
                # SQLite cannot add a value to an existing CHECK constraint. Rebuild
                # only the small items table while preserving every row and field.
                self.connection.execute("""
                CREATE TABLE items_v3 (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                kind TEXT NOT NULL,
                authors TEXT NOT NULL,
                venue TEXT,
                published_at TEXT,
                source TEXT NOT NULL,
                metadata TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('unread', 'reading', 'read')),
                added_at TEXT NOT NULL,
                read_at TEXT,
                note_path TEXT
                )
                """)
                self.connection.execute("""
                INSERT INTO items_v3 SELECT id, title, url, kind, authors, venue,
                    published_at, source, metadata, status, added_at, read_at, note_path
                    FROM items
                """)
                self.connection.execute("DROP TABLE items")
                self.connection.execute("ALTER TABLE items_v3 RENAME TO items")
                if list(self.connection.execute("PRAGMA foreign_key_check")):
                    raise DatabaseError("database migration failed foreign-key validation")
            if version in (2, 3):
                self.connection.execute("ALTER TABLE groups RENAME TO groups_v3")
                self.connection.execute("""CREATE TABLE groups (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                    parent_id TEXT REFERENCES groups(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL, UNIQUE(parent_id, name_key)
                )""")
                self.connection.execute("""INSERT INTO groups (id,name,name_key,created_at)
                    SELECT id,name,name_key,created_at FROM groups_v3""")
                self.connection.execute("""CREATE TABLE item_groups_v4 (
                    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                    PRIMARY KEY (group_id, item_id)
                )""")
                self.connection.execute(
                    "INSERT INTO item_groups_v4 SELECT group_id,item_id FROM item_groups"
                )
                self.connection.execute("DROP TABLE item_groups")
                self.connection.execute("ALTER TABLE item_groups_v4 RENAME TO item_groups")
                self.connection.execute(
                    "CREATE INDEX item_groups_item_id_idx ON item_groups(item_id)"
                )
                self.connection.execute("DROP TABLE groups_v3")
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS groups_parent_idx ON groups(parent_id)"
            )
            self.connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS groups_root_name_idx ON groups(name_key) WHERE parent_id IS NULL"
            )
            self.connection.execute("""CREATE TRIGGER IF NOT EXISTS groups_parent_is_root_insert
                BEFORE INSERT ON groups WHEN NEW.parent_id IS NOT NULL AND
                (NEW.parent_id = NEW.id OR (SELECT parent_id FROM groups WHERE id = NEW.parent_id) IS NOT NULL)
                BEGIN SELECT RAISE(ABORT, 'groups may have at most one parent level'); END""")
            self.connection.execute("""CREATE TRIGGER IF NOT EXISTS groups_parent_is_root_update
                BEFORE UPDATE OF parent_id ON groups WHEN NEW.parent_id IS NOT NULL AND
                (NEW.parent_id = NEW.id OR (SELECT parent_id FROM groups WHERE id = NEW.parent_id) IS NOT NULL
                 OR (SELECT COUNT(*) FROM groups WHERE parent_id=NEW.id) > 0)
                BEGIN SELECT RAISE(ABORT, 'groups may have at most one parent level'); END""")
            if list(self.connection.execute("PRAGMA foreign_key_check")):
                raise DatabaseError("database migration failed foreign-key validation")
            self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.connection.commit()
            self.connection.execute("PRAGMA foreign_keys = ON")
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            self.connection.execute("PRAGMA foreign_keys = ON")
            raise

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

    def add(
        self,
        item: ResolvedItem,
        tags: list[str] | None = None,
        groups: list[str] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        title = item.title.strip()
        url = item.url.strip()
        kind = item.kind.strip()
        source = item.source.strip()
        if not title or not url or not kind or not source:
            raise DatabaseError("title, URL, kind, and source must not be empty")
        identifiers = _identifiers(item.identifiers)
        tag_names = _tags(tags or [])
        group_queries = _group_queries(groups or [])
        try:
            self._begin()
            group_ids = [self._resolve_group(query)["id"] for query in group_queries]
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
            self._add_group_memberships(item_id, group_ids)
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

    def _add_group_memberships(self, item_id: str, group_ids: Iterable[str]) -> None:
        for group_id in group_ids:
            self.connection.execute(
                "INSERT OR IGNORE INTO item_groups (group_id, item_id) VALUES (?, ?)",
                (group_id, item_id),
            )

    def _group_query(self) -> str:
        return """
            SELECT g.id, g.name, g.name_key, g.parent_id, g.created_at,
                   CASE WHEN g.parent_id IS NULL THEN g.name ELSE p.name || '/' || g.name END AS path,
                   (SELECT COUNT(DISTINCT ig2.item_id) FROM item_groups ig2
                    WHERE ig2.group_id = g.id OR ig2.group_id IN
                      (SELECT c.id FROM groups c WHERE c.parent_id = g.id)) AS item_count
            FROM groups AS g LEFT JOIN groups AS p ON p.id = g.parent_id
        """

    @staticmethod
    def _row_to_group(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "parent_id": row["parent_id"],
            "path": row["path"],
            "created_at": row["created_at"],
            "item_count": row["item_count"],
        }

    def _group_path(self, group_id: str) -> str:
        row = self.connection.execute(
            "SELECT name,parent_id FROM groups WHERE id=?", (group_id,)
        ).fetchone()
        if row is None:
            raise GroupNotFoundError(f"group not found: {group_id}")
        return (
            row["name"]
            if not row["parent_id"]
            else f"{self._group_path(row['parent_id'])}/{row['name']}"
        )

    def _resolve_group(self, query: str) -> dict[str, Any]:
        query, name_key = _group_name(query)
        row = self.connection.execute(self._group_query() + " WHERE g.id = ?", (query,)).fetchone()
        if row is not None:
            return self._row_to_group(row)
        rows = self.connection.execute(
            self._group_query() + " WHERE g.name_key = ?", (name_key,)
        ).fetchall()
        if len(rows) == 1:
            return self._row_to_group(rows[0])
        if len(rows) > 1 and "/" not in query:
            candidates = ", ".join(self._group_path(r["id"]) for r in rows)
            raise AmbiguousGroupError(f"ambiguous group {query!r}: {candidates}")
        if "/" in query:
            parts = query.split("/")
            if len(parts) == 2:
                parts = [part.strip() for part in parts]
                row = self.connection.execute(
                    self._group_query()
                    + " WHERE p.name_key=? AND p.parent_id IS NULL AND g.name_key=?",
                    (parts[0].casefold(), parts[1].casefold()),
                ).fetchone()
                if row is not None:
                    return self._row_to_group(row)
        if not rows:
            raise GroupNotFoundError(f"group not found: {query}")
        raise GroupNotFoundError(f"group not found: {query}")

    def create_group(self, name: str, parent: str | None = None) -> dict[str, Any]:
        name, name_key = _group_name(name)
        try:
            self._begin()
            parent_id = None
            if parent is not None:
                parent_group = self._resolve_group(parent)
                if parent_group["parent_id"] is not None:
                    raise DatabaseError("groups may have at most one parent level")
                parent_id = parent_group["id"]
            if self.connection.execute("SELECT 1 FROM groups WHERE id = ?", (name_key,)).fetchone():
                raise DatabaseError("group name conflicts with an existing group ID")
            while True:
                group_id = f"grp_{uuid.uuid4()}"
                if (
                    self.connection.execute(
                        "SELECT 1 FROM groups WHERE name_key = ?", (group_id.casefold(),)
                    ).fetchone()
                    is None
                ):
                    break
            self.connection.execute(
                "INSERT INTO groups (id, name, name_key, parent_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (group_id, name, name_key, parent_id, _now()),
            )
            result = self._resolve_group(group_id)
            self.connection.commit()
            return result
        except sqlite3.IntegrityError as exc:
            if self.connection.in_transaction:
                self.connection.rollback()
            if "at most one parent" in str(exc):
                raise DatabaseError(str(exc)) from None
            raise DatabaseError(f"group already exists: {name}") from None
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def get_group(self, query: str) -> dict[str, Any]:
        return self._resolve_group(query)

    def list_groups(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            self._group_query() + " GROUP BY g.id ORDER BY g.name_key, g.id"
        )
        return [self._row_to_group(row) for row in rows]

    def rename_group(self, query: str, name: str) -> dict[str, Any]:
        name, name_key = _group_name(name)
        try:
            self._begin()
            group_id = self._resolve_group(query)["id"]
            conflicting_id = self.connection.execute(
                "SELECT id FROM groups WHERE id = ? AND id <> ?", (name_key, group_id)
            ).fetchone()
            if conflicting_id is not None:
                raise DatabaseError("group name conflicts with an existing group ID")
            self.connection.execute(
                "UPDATE groups SET name = ?, name_key = ? WHERE id = ?",
                (name, name_key, group_id),
            )
            result = self._resolve_group(group_id)
            self.connection.commit()
            return result
        except sqlite3.IntegrityError:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise DatabaseError(f"group already exists: {name}") from None
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def delete_group(self, query: str) -> dict[str, Any]:
        try:
            self._begin()
            result = self._resolve_group(query)
            self.connection.execute("DELETE FROM groups WHERE id = ?", (result["id"],))
            self.connection.commit()
            return result
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def _validate_item_ids(self, item_ids: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item_id).strip() for item_id in item_ids))
        if any(not item_id for item_id in normalized):
            raise DatabaseError("item IDs must not be empty")
        if not normalized:
            return normalized
        placeholders = ", ".join("?" for _ in normalized)
        found = {
            row[0]
            for row in self.connection.execute(
                f"SELECT id FROM items WHERE id IN ({placeholders})", normalized
            )
        }
        missing = [item_id for item_id in normalized if item_id not in found]
        if missing:
            raise ItemNotFoundError(f"item not found: {', '.join(missing)}")
        return normalized

    def add_to_group(self, query: str, item_ids: list[str]) -> dict[str, Any]:
        try:
            self._begin()
            group_id = self._resolve_group(query)["id"]
            normalized = self._validate_item_ids(item_ids)
            self.connection.executemany(
                "INSERT OR IGNORE INTO item_groups (group_id, item_id) VALUES (?, ?)",
                ((group_id, item_id) for item_id in normalized),
            )
            result = self._resolve_group(group_id)
            self.connection.commit()
            return result
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def remove_from_group(self, query: str, item_ids: list[str]) -> dict[str, Any]:
        try:
            self._begin()
            group_id = self._resolve_group(query)["id"]
            normalized = self._validate_item_ids(item_ids)
            group = self._resolve_group(group_id)
            ids = (
                [group_id]
                + [
                    r[0]
                    for r in self.connection.execute(
                        "SELECT id FROM groups WHERE parent_id=?", (group_id,)
                    )
                ]
                if group["parent_id"] is None
                else [group_id]
            )
            self.connection.executemany(
                "DELETE FROM item_groups WHERE group_id IN (%s) AND item_id = ?"
                % ",".join("?" for _ in ids),
                ([*ids, item_id] for item_id in normalized),
            )
            result = self._resolve_group(group_id)
            self.connection.commit()
            return result
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

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
                             WHERE item_id = i.id ORDER BY scheme, value)), '[]') AS identifiers_json,
                   COALESCE((SELECT json_group_array(json_object('id', id, 'name', name))
                       FROM (SELECT g.id, g.name FROM groups AS g
                             JOIN item_groups AS ig ON ig.group_id = g.id
                             WHERE ig.item_id = i.id ORDER BY g.name_key, g.id)), '[]') AS groups_json
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
        value["groups"] = json.loads(row["groups_json"])
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
        group: str | None = None,
    ) -> list[dict[str, Any]]:
        if status is not None and status not in VALID_STATUSES:
            raise DatabaseError(f"invalid status: {status}")
        clauses: list[str] = []
        parameters: list[Any] = []
        group_id = self._resolve_group(group)["id"] if group is not None else None
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
        if group_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM item_groups ig WHERE ig.item_id=i.id AND (ig.group_id=? OR ig.group_id IN (SELECT id FROM groups WHERE parent_id=?)))"
            )
            parameters.extend([group_id, group_id])
        sql = self._item_query()
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY i.added_at DESC, i.id DESC"
        return [self._row_to_item(row) for row in self.connection.execute(sql, parameters)]

    def search(
        self, query: str, status: str | None = None, group: str | None = None
    ) -> list[dict[str, Any]]:
        if status is not None and status not in VALID_STATUSES:
            raise DatabaseError(f"invalid status: {status}")
        group_id = self._resolve_group(group)["id"] if group is not None else None
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
        if group_id is not None:
            sql += " AND EXISTS (SELECT 1 FROM item_groups ig WHERE ig.item_id=i.id AND (ig.group_id=? OR ig.group_id IN (SELECT id FROM groups WHERE parent_id=?)))"
            parameters.extend([group_id, group_id])
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
        elif status == "reading":
            cursor = self.connection.execute(
                "UPDATE items SET status = 'reading', read_at = NULL WHERE id = ?", (item_id,)
            )
        else:
            cursor = self.connection.execute(
                "UPDATE items SET status = 'unread', read_at = NULL WHERE id = ?", (item_id,)
            )
        if cursor.rowcount == 0:
            raise ItemNotFoundError(f"item not found: {item_id}")
        return self._get_id(item_id)

    def delete_item(self, item_id: str) -> None:
        """Delete one item by its exact full ID, retaining notes and shared records."""
        if not isinstance(item_id, str) or not item_id:
            raise DatabaseError("item ID must be a non-empty full ID")
        try:
            self._begin()
            if (
                self.connection.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone()
                is None
            ):
                raise ItemNotFoundError(f"item not found: {item_id}")
            self.connection.execute("DELETE FROM item_search WHERE item_id = ?", (item_id,))
            self.connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
            self.connection.commit()
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def set_note_path(self, item_id: str, path: str | Path) -> dict[str, Any]:
        self._get_id(item_id)
        value = str(path)
        if not value:
            raise DatabaseError("note path must not be empty")
        self.connection.execute("UPDATE items SET note_path = ? WHERE id = ?", (value, item_id))
        return self._get_id(item_id)
