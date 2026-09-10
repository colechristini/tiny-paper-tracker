"""PTY smoke test for two-level subgroup sections."""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path

from smoke_tui import PtySession, SmokeFailure

from tiny_reading_tracker.db import Database, ItemNotFoundError
from tiny_reading_tracker.models import ResolvedItem


def seed(path: Path, notes: Path) -> dict[str, str]:
    with Database(path) as library:
        root = library.create_group("Research")
        first = library.create_group("Alpha", parent=root["id"])
        second = library.create_group("Beta", parent=root["id"])
        library.create_group("Empty", parent=root["id"])
        ids: dict[str, str] = {}
        for title in ("Loose", "Duplicate"):
            item, _ = library.add(
                ResolvedItem(title=title, url=f"https://example.test/{title}", source="smoke")
            )
            ids[title] = item["id"]
        item, _ = library.add(
            ResolvedItem(title="Alpha only", url="https://example.test/alpha", source="smoke")
        )
        ids["Alpha only"] = item["id"]
        library.connection.executemany(
            "INSERT INTO item_groups (group_id, item_id) VALUES (?, ?)",
            [
                (root["id"], ids["Loose"]),
                (first["id"], ids["Duplicate"]),
                (second["id"], ids["Duplicate"]),
                (first["id"], ids["Alpha only"]),
            ],
        )
        notes.mkdir()
        note = notes / "duplicate.md"
        note.write_text("EXISTING DUPLICATE NOTE\n", encoding="utf-8")
        library.set_note_path(ids["Duplicate"], note)
        library.connection.commit()
        return ids


def run(binary: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="lit-tui-subgroups-") as directory:
        root = Path(directory)
        db = root / "library.db"
        notes = root / "notes"
        ids = seed(db, notes)
        env = os.environ.copy()
        for key in (
            "LIT_CONFIG",
            "LIT_DB",
            "LIT_VAULT",
            "LIT_NOTES_DIR",
            "LIT_TUI_DB",
            "LIT_TUI_NOTES_DIR",
            "LIT_TUI_VAULT",
        ):
            env.pop(key, None)
        env.update(
            {
                "LIT_TUI_BINARY": str(binary),
                "TERM": "xterm-256color",
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"),
            }
        )
        session = PtySession(
            ["uv", "run", "lit", "--db", str(db), "--notes-dir", str(notes), "tui"], env
        )
        try:
            session.wait_until(
                lambda text: "Research / Alpha" in text and "Research / Beta" in text,
                "subgroup headers",
            )
            screen = session.screen_text()
            if (
                "General" in screen
                or "Research / Empty" in screen
                or "(empty)" in screen
                or "Loose" not in screen
            ):
                raise SmokeFailure(f"unexpected subgroup layout:\n{screen}")
            session.send("c")
            session.wait_for_file(lambda: _status(db, ids["Loose"]) == "reading", "loose status")
            session.wait_until(lambda text: "Status: reading" in text, "loose status redraw")
            # Loose row, Alpha header, Duplicate row: move twice and mutate the duplicate.
            session.send("j")
            time.sleep(0.5)
            session.send("j")
            time.sleep(0.5)
            session.send("c")
            session.wait_until(lambda text: "Status: reading" in text, "duplicate status redraw")
            with Database(db) as library:
                if library.get(ids["Duplicate"])["status"] != "reading":
                    raise SmokeFailure(
                        f"status action targeted the wrong subgroup occurrence: {session.screen_text()}"
                    )
            session.send("j")
            session.wait_until(
                lambda text: "Status: reading" in text, "duplicate second occurrence"
            )
            before = sorted(notes.glob("*.md"))
            session.send("\r")
            session.wait_until(
                lambda text: "EXISTING DUPLICATE NOTE" in text, "existing duplicate note"
            )
            session.send(b"\x1b[27u")
            session.wait_until(lambda text: "Ctrl-V preview" not in text, "return from note")
            session.send("\x7f")
            session.wait_for_file(lambda: _missing(db, ids["Duplicate"]), "duplicate deletion")
            if not (notes / "duplicate.md").exists() or sorted(notes.glob("*.md")) != before:
                raise SmokeFailure("deleting an occurrence removed or replaced its note")
            session.send("g")
            session.wait_until(lambda text: "· Research ·" in text, "root group footer")
            root_view = session.screen_text()
            if (
                "Alpha" not in root_view
                or "Beta" in root_view
                or "Empty" in root_view
                or "Loose" not in root_view
            ):
                raise SmokeFailure("root group view showed an empty or omitted a populated section")
            session.send("h")
            session.wait_until(lambda text: "all groups" in text, "return to all groups")
            session.send("q")
            session.wait_until(
                lambda _: session.process.poll() is not None, "clean quit", timeout=6
            )
            if session.process.returncode != 0 or not session.terminal_restored():
                raise SmokeFailure("subgroup smoke did not restore terminal cleanly")
            return {"status": "passed", "subgroups": True, "duplicate_deleted": True}
        finally:
            session.close()


def _missing(path: Path, item_id: str) -> bool:
    with Database(path) as library:
        try:
            library.get(item_id)
        except ItemNotFoundError:
            return True
        return False


def _status(path: Path, item_id: str) -> str:
    with Database(path) as library:
        return library.get(item_id)["status"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=Path("tui/target/debug/lit-tui"))
    args = parser.parse_args()
    result = run(args.binary.expanduser().resolve())
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
