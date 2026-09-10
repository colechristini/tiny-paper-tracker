"""Safe, durable Obsidian note creation."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


class NoteError(ValueError):
    """A note could not be created without risking an overwrite or escape."""


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _component(value: str) -> str:
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "Untitled"


def _truncate_utf8(value: str, maximum: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    return encoded[:maximum].decode("utf-8", errors="ignore").rstrip(" .")


def _yaml(value: Any) -> str:
    # JSON is a YAML 1.2 subset and gives every untrusted string explicit quoting.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _new_contents(item: dict[str, Any]) -> str:
    identifiers: dict[str, str] = {}
    for entry in item.get("identifiers", []):
        if isinstance(entry, dict):
            scheme, value = entry.get("scheme"), entry.get("value")
        else:
            scheme, value = entry
        if scheme and value:
            identifiers[str(scheme).lower()] = str(value)
    lines = ["---", f"lit_id: {_yaml(item['id'])}"]
    for scheme, key in (("doi", "doi"), ("arxiv", "arxiv"), ("pmid", "PMID")):
        if scheme in identifiers:
            lines.append(f"{key}: {_yaml(identifiers[scheme])}")
    lines.extend(
        (
            f"url: {_yaml(item['url'])}",
            "---",
            "",
            f"# {item['title']}",
            "",
            "## Summary",
            "",
            "## Takeaways",
            "",
            "## Thoughts",
            "",
            "## Connections",
            "",
        )
    )
    return "\n".join(lines)


def write_note(item: dict[str, Any], vault: Path, text: str | None = None) -> Path:
    """Create or append to an item's note without replacing existing content."""

    vault = Path(vault).expanduser()
    if not vault.exists() or not vault.is_dir():
        raise NoteError(f"vault directory does not exist: {vault}")
    vault_root = vault.resolve(strict=True)
    reading = vault / "Reading"
    reading.mkdir(parents=True, exist_ok=True)
    reading_root = reading.resolve(strict=True)
    if not _inside(reading_root, vault_root):
        raise NoteError("the Reading directory resolves outside the vault")

    recorded = item.get("note_path")
    if recorded:
        candidate = Path(str(recorded)).expanduser()
        if not candidate.is_absolute():
            candidate = vault / candidate
        if not candidate.exists():
            raise NoteError(f"recorded note is missing: {candidate}")
        resolved = candidate.resolve(strict=True)
        if not _inside(resolved, vault_root) or not _inside(resolved, reading_root):
            raise NoteError("recorded note resolves outside the vault Reading directory")
        if not resolved.is_file():
            raise NoteError(f"recorded note is not a file: {candidate}")
        path = resolved
        exists = True
    else:
        item_id = _component(str(item.get("id", "")))
        if not item_id.startswith("lit_"):
            raise NoteError("invalid item ID")
        suffix = f"-{item_id}.md"
        title_budget = max(1, 240 - len(suffix.encode("utf-8")))
        title = _truncate_utf8(_component(str(item.get("title", ""))), title_budget)
        path = reading / f"{title}{suffix}"
        parent = path.parent.resolve(strict=True)
        if not _inside(parent, reading_root):
            raise NoteError("note path resolves outside the vault Reading directory")
        exists = path.exists()
        if exists:
            resolved = path.resolve(strict=True)
            if not _inside(resolved, reading_root) or not resolved.is_file():
                raise NoteError("existing note is unsafe") from None

    if not exists:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            resolved = path.resolve(strict=True)
            if not _inside(resolved, reading_root) or not resolved.is_file():
                raise NoteError("existing note is unsafe") from None
            path = resolved
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_new_contents(item))
            path = path.resolve(strict=True)

    if text is not None and text != "":
        # Append-only mode protects user edits made in Obsidian between calls.
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n" if str(text).startswith("\n") else "\n\n")
            handle.write(str(text))
            if not str(text).endswith("\n"):
                handle.write("\n")
    return path.resolve(strict=True)
