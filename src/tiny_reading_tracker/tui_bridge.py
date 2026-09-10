"""Versioned JSON-lines backend for the Rust terminal interface."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .db import Database, DatabaseError

PROTOCOL_VERSION = 1
_STATUSES = {"all", "unread", "reading", "read"}


def _response(**payload: Any) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "ok": True, **payload}


def _error(message: str, kind: str = "error") -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "ok": False, "error": {"kind": kind, "message": message}}


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
    return _response(items=items, groups=library.list_groups())


def _handle(request: dict[str, Any], library: Database) -> dict[str, Any]:
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
    return _error(f"unknown operation: {operation}", "request")


def main() -> int:
    db = os.environ.get("LIT_TUI_DB")
    if not db:
        print(json.dumps(_error("LIT_TUI_DB is not set", "configuration")), flush=True)
        return 2
    try:
        with Database(Path(db)) as library:
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("request must be a JSON object")
                    result = _handle(request, library)
                except (ValueError, TypeError, DatabaseError, OSError, sqlite3.Error) as exc:
                    result = _error(str(exc), "backend")
                print(json.dumps(result, ensure_ascii=False), flush=True)
    except (ValueError, OSError) as exc:
        print(json.dumps(_error(str(exc), "configuration")), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
