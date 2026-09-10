from __future__ import annotations

from pathlib import Path

from tiny_reading_tracker.db import Database
from tiny_reading_tracker.models import ResolvedItem
from tiny_reading_tracker.tui_bridge import _handle


def test_bridge_lists_title_sorted_and_mutates_status(tmp_path: Path) -> None:
    with Database(tmp_path / "library.db") as library:
        second, _ = library.add(
            ResolvedItem(title="Zeta", url="https://z", source="test", identifiers=[("url", "z")])
        )
        first, _ = library.add(
            ResolvedItem(title="Alpha", url="https://a", source="test", identifiers=[("url", "a")])
        )
        response = _handle({"version": 1, "op": "list", "status": "all"}, library)
        assert [item["id"] for item in response["items"]] == [first["id"], second["id"]]
        changed = _handle(
            {"version": 1, "op": "set_status", "id": first["id"], "status": "reading"}, library
        )
        assert changed["ok"] and changed["item"]["status"] == "reading"


def test_bridge_rejects_unknown_protocol_operation(tmp_path: Path) -> None:
    with Database(tmp_path / "library.db") as library:
        response = _handle({"version": 99, "op": "list"}, library)
        assert response["ok"] is False
        assert response["error"]["kind"] == "protocol"
