"""Offline PTY smoke test for the terminal interface.

Run from the repository with ``uv run python scripts/smoke_tui.py``. The test
seeds a temporary SQLite library, launches the real ``lit tui`` command, and
never reads or writes the user's configured library or notes directory.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

import pyte

from tiny_reading_tracker.db import Database
from tiny_reading_tracker.models import ResolvedItem


class SmokeFailure(RuntimeError):
    """A terminal smoke assertion failed."""


_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def visible_output(value: str) -> str:
    """Remove terminal CSI sequences for assertions and concise errors."""

    return _ANSI_CSI.sub("", value)


class PtySession:
    def __init__(self, command: list[str], env: dict[str, str]) -> None:
        self.master, self.slave = pty.openpty()
        self.slave_settings = termios.tcgetattr(self.slave)
        self._set_size(100, 32)
        self.process = subprocess.Popen(
            command,
            env=env,
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            close_fds=True,
        )
        self.output = ""
        self.screen = pyte.Screen(100, 32)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def _set_size(self, columns: int, rows: int) -> None:
        size = struct.pack("HHHH", rows, columns, 0, 0)
        import fcntl

        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, size)

    def send(self, value: str | bytes) -> None:
        payload = value.encode() if isinstance(value, str) else value
        os.write(self.master, payload)

    def reset_output(self) -> None:
        self.output = ""

    def paste(self, value: str) -> None:
        self.send(b"\x1b[200~" + value.encode() + b"\x1b[201~")

    def _read(self) -> None:
        try:
            chunk = os.read(self.master, 65536)
        except OSError:
            return
        if chunk:
            decoded = self.decoder.decode(chunk)
            self.output = (self.output + decoded)[-200_000:]
            self.stream.feed(decoded)

    def screen_text(self) -> str:
        """Return the current terminal contents after applying all redraws."""

        # Build rows directly instead of ``Screen.display``: pyte 0.8 can
        # encounter empty wide-character continuation cells while ratatui is
        # redrawing a frame, and its renderer assumes every cell has data.
        rows: list[str] = []
        for row in self.screen.buffer.values():
            rows.append(
                "".join((getattr(row.get(column), "data", "") or " ") for column in range(100))
            )
        return "\n".join(rows)

    def wait_until(self, condition, description: str, timeout: float = 8.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition(self.screen_text()):
                return
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([self.master], [], [], min(0.2, remaining))
            if readable:
                self._read()
        if condition(self.screen_text()):
            return
        raise SmokeFailure(
            f"timed out waiting for {description}; screen tail:\n{self.screen_text()[-2000:]}"
        )

    def wait_for_file(self, condition, description: str, timeout: float = 8.0) -> None:
        self.wait_until(lambda _: condition(), description, timeout)

    def terminal_restored(self) -> bool:
        try:
            return termios.tcgetattr(self.slave) == self.slave_settings
        except OSError:
            return False

    def _drain_until_exit(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([self.master], [], [], min(0.2, remaining))
            if readable:
                self._read()
        return self.process.poll() is not None

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.send(b"\x11")  # Ctrl-Q saves and quits while editing.
                exited = self._drain_until_exit(2)
            except OSError:
                exited = False
            if not exited:
                try:
                    self.send("q")
                    exited = self._drain_until_exit(2)
                except OSError:
                    exited = False
            if not exited:
                try:
                    self.process.send_signal(signal.SIGTERM)
                    exited = self._drain_until_exit(3)
                except OSError:
                    exited = False
            if not exited:
                try:
                    self.process.send_signal(signal.SIGINT)
                    self._drain_until_exit(3)
                except OSError:
                    pass
        try:
            termios.tcsetattr(self.slave, termios.TCSANOW, self.slave_settings)
        except OSError:
            pass
        for descriptor in (self.master, self.slave):
            try:
                os.close(descriptor)
            except OSError:
                pass


def seed_library(path: Path) -> None:
    with Database(path) as library:
        for title, slug in (("Alpha", "alpha"), ("Beta", "beta")):
            library.add(
                ResolvedItem(
                    title=title,
                    url=f"https://example.test/{slug}",
                    source="smoke-test",
                )
            )


def item_status(path: Path, title: str) -> str:
    with Database(path) as library:
        return library.get(title)["status"]


def note_files(notes_dir: Path) -> list[Path]:
    return sorted(path for path in notes_dir.glob("*.md") if not path.name.startswith("recovery-"))


def recovery_files(notes_dir: Path) -> list[Path]:
    return sorted(notes_dir.glob("recovery-*.md"))


def run(binary: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="lit-tui-smoke-") as directory:
        root = Path(directory)
        db_path = root / "library.db"
        notes_dir = root / "notes"
        seed_library(db_path)
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
        env["LIT_TUI_BINARY"] = str(binary)
        env["TERM"] = "xterm-256color"
        env["XDG_CONFIG_HOME"] = str(root / "config")
        env["XDG_DATA_HOME"] = str(root / "data")
        command = [
            "uv",
            "run",
            "lit",
            "--db",
            str(db_path),
            "--notes-dir",
            str(notes_dir),
            "tui",
        ]
        session = PtySession(command, env)
        try:
            session.wait_until(
                lambda output: (
                    ("Reading" in output and "(2)" in output) or "No items match" in output
                ),
                "initial reading list",
            )

            session.reset_output()
            session.send("c")
            session.wait_for_file(
                lambda: item_status(db_path, "Alpha") == "reading",
                "currently-reading status to persist",
            )

            session.reset_output()
            session.send("\r")
            session.wait_for_file(lambda: len(note_files(notes_dir)) == 1, "note editor")
            local_text = "🧪 local paste"
            session.paste(local_text + "\n")
            session.wait_for_file(
                lambda: (
                    len(note_files(notes_dir)) == 1
                    and local_text in note_files(notes_dir)[0].read_text(encoding="utf-8")
                ),
                "autosaved Unicode paste",
            )
            note_path = note_files(notes_dir)[0]

            # Exercise the legacy control bytes that work without enhanced
            # keyboard reporting. They must arrive through the real PTY, not
            # only as synthetic crossterm KeyEvents in Rust unit tests.
            session.send(b"\x02")  # Ctrl-B: open bold markers.
            session.send("bold")
            session.send(b"\x02")  # Ctrl-B: skip the matching closer.
            session.send(" ")
            session.send(b"\x14")  # Ctrl-T: open italic markers.
            session.send("italic")
            session.send(b"\x14")  # Ctrl-T: skip the matching closer.
            session.send(b"\x01")  # Ctrl-A: logical line start.
            session.send("START ")
            session.send(b"\x05")  # Ctrl-E: logical line end.
            session.send(" END")
            session.send(b"\x10")  # Ctrl-P: previous word.
            session.send("PRE-")
            session.send(b"\x0e")  # Ctrl-N: next word.
            session.send("!")
            session.send("\r")
            session.send(b"\x09")  # Tab/Ctrl-I: italic without completion.
            session.send("tab")
            session.send(b"\x09")
            portable_text = "START **bold** *italic*--- PRE-END!\n*tab*"
            try:
                session.wait_for_file(
                    lambda: portable_text in note_path.read_text(encoding="utf-8"),
                    "portable control-key editing",
                )
            except SmokeFailure as error:
                raise SmokeFailure(
                    f"{error}\nnote contents: {note_path.read_text(encoding='utf-8')!r}"
                ) from error

            note_path.write_text(
                note_path.read_text(encoding="utf-8") + "\nEXTERNAL EDIT\n",
                encoding="utf-8",
            )
            conflict_text = "LOCAL CONFLICT BUFFER"
            session.reset_output()
            session.send(conflict_text)
            session.send(b"\x13")  # Ctrl-S
            session.wait_until(lambda output: "Save failed:" in output, "external-edit conflict")
            session.reset_output()
            session.send(b"\x07")  # Ctrl-G
            session.wait_for_file(
                lambda: (
                    recovery_files(notes_dir)
                    and conflict_text in recovery_files(notes_dir)[-1].read_text(encoding="utf-8")
                ),
                "recovery copy",
            )
            preserved = note_path.read_text(encoding="utf-8")
            if "EXTERNAL EDIT" not in preserved or conflict_text in preserved:
                raise SmokeFailure("external edit was not preserved during recovery")

            session.reset_output()
            session.send("\x1b[B")  # select Beta
            session.send("\r")
            session.wait_for_file(lambda: len(note_files(notes_dir)) == 2, "second note editor")
            session.send("@Alp")
            session.wait_until(lambda output: "Links" in output, "link completion")
            session.send(b"\x09")  # Tab accepts completion instead of formatting.
            beta_path = note_files(notes_dir)[-1]
            session.wait_for_file(
                lambda: "[Alpha](<" in beta_path.read_text(encoding="utf-8"),
                "Tab link completion",
            )
            long_text = "# Preview\n\n" + ("terminal preview word " * 40) + "PREVIEW-END-MARKER"
            session.paste(long_text)
            session.wait_for_file(
                lambda: (
                    len(note_files(notes_dir)) == 2
                    and "PREVIEW-END-MARKER"
                    in note_files(notes_dir)[-1].read_text(encoding="utf-8")
                ),
                "autosaved preview content",
            )
            before_preview = beta_path.read_text(encoding="utf-8")
            session.reset_output()
            session.send(b"\x16")  # Ctrl-V
            session.send("IGNORED")
            session.paste("IGNORED-PASTE")
            session.send(b"\x1b[F")  # End
            session.wait_until(lambda output: "PREVIEW-END-MARKER" in output, "preview end marker")
            if beta_path.read_text(encoding="utf-8") != before_preview:
                raise SmokeFailure("preview mode accepted editing input")

            # Ctrl-Q exercises the editor's save-on-quit path and terminal
            # restoration while the preview is still active.
            session.send(b"\x11")  # Ctrl-Q saves and quits from the editor.
            session.wait_until(
                lambda _: session.process.poll() is not None, "clean quit", timeout=6
            )
            if session.process.returncode != 0:
                raise SmokeFailure(f"TUI exited with status {session.process.returncode}")
            if beta_path.read_text(encoding="utf-8") != before_preview:
                raise SmokeFailure("preview input changed the note before clean quit")
            if not session.terminal_restored():
                raise SmokeFailure("terminal settings were not restored on clean quit")
            return {
                "status": "passed",
                "items": 2,
                "unicode_autosave": True,
                "portable_control_shortcuts": True,
                "external_conflict_recovery": True,
                "preview_end_marker": True,
            }
        finally:
            session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path("tui/target/debug/lit-tui"),
        help="path to the lit-tui binary (default: tui/target/debug/lit-tui)",
    )
    args = parser.parse_args()
    binary = args.binary.expanduser().resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error(f"lit-tui binary is not executable: {binary}")
    try:
        result = run(binary)
    except (OSError, subprocess.SubprocessError, SmokeFailure) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
