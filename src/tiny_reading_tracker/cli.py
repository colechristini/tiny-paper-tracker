"""Reading and group commands; network concurrency, serialized local writes."""

import functools
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

import typer

from .config import Config, load_config
from .db import Database
from .ingest import resolve
from .normalize import input_identifiers
from .notes import append_note, load_note

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Save resources, track reading, and keep notes in Markdown.",
)
group_app = typer.Typer(no_args_is_help=True, help="Organize resources into named collections.")
app.add_typer(group_app, name="group")


def clean(value: object) -> str:
    return "".join(c if not unicodedata.category(c).startswith("C") else " " for c in str(value))


def emit(ctx: typer.Context, value: object, lines: list[str]) -> None:
    if ctx.obj["json"]:
        typer.echo(json.dumps(value, ensure_ascii=False))
    else:
        for line in lines:
            typer.echo(clean(line))


def handled(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, OSError, sqlite3.Error) as exc:
            ctx = kwargs.get("ctx")
            if ctx is not None and ctx.obj and ctx.obj.get("json"):
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.echo(f"Error: {clean(exc)}", err=True)
            raise typer.Exit(1) from exc

    return wrapped


@app.callback()
@handled
def configure(
    ctx: typer.Context,
    config: Path | None = typer.Option(None, help="TOML config file (or LIT_CONFIG)."),
    db: Path | None = typer.Option(None, help="SQLite database path (or LIT_DB)."),
    vault: Path | None = typer.Option(None, help="Obsidian vault path (or LIT_VAULT)."),
    notes_dir: Path | None = typer.Option(
        None, "--notes-dir", help="Portable Markdown notes directory (or LIT_NOTES_DIR)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
):
    ctx.obj = {"json": json_output}
    ctx.obj["config"] = load_config(config, db, vault, notes_dir)


@app.command()
@handled
def add(
    ctx: typer.Context,
    values: list[str] = typer.Argument(None, help="URLs or identifiers; no arguments reads stdin."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Repeat for multiple tags."),
    group: list[str] | None = typer.Option(
        None, "--group", help="Existing group name or ID; repeatable."
    ),
):
    """Save a batch; failures do not discard successful inputs. Re-adding preserves status."""
    cfg: Config = ctx.obj["config"]
    if not values:
        if sys.stdin.isatty():
            raise ValueError("Provide URLs/identifiers, or pipe one per line to lit add")
        values = [line.strip() for line in sys.stdin if line.strip()]
    if not values:
        raise ValueError("No URLs or identifiers supplied")

    with Database(cfg.db) as library:
        # Fail on an unknown group before starting lookups or saving any items.
        group_ids = [library.get_group(query)["id"] for query in group or []]

        def prepare(value):
            # Worker threads do network work only. SQLite writes stay on the parent thread.
            try:
                return resolve(value, cfg.translator_url, cfg.timeout)
            except (ValueError, OSError) as exc:
                return exc

        results = []
        pending = []
        for value in values:
            try:
                existing = library.find_identifiers(input_identifiers(value))
                if existing:
                    # Let add union tags while preserving existing metadata/state.
                    from .models import ResolvedItem

                    item, _ = library.add(
                        ResolvedItem(
                            title=existing["title"],
                            url=existing["url"],
                            identifiers=input_identifiers(value),
                        ),
                        tags=tag,
                        groups=group_ids,
                    )
                    results.append({"input": value, "outcome": "exists", "item": item})
                else:
                    results.append(None)
                    pending.append((len(results) - 1, value))
            except ValueError as exc:
                results.append({"input": value, "outcome": "error", "error": str(exc)})
        with ThreadPoolExecutor(max_workers=cfg.workers) as executor:
            for (index, value), resolved in zip(
                pending, executor.map(prepare, [value for _, value in pending]), strict=True
            ):
                try:
                    if isinstance(resolved, Exception):
                        raise resolved
                    item, created = library.add(resolved, tags=tag, groups=group_ids)
                    results[index] = {
                        "input": value,
                        "outcome": "added" if created else "exists",
                        "item": item,
                    }
                except (ValueError, OSError, sqlite3.Error) as exc:
                    results[index] = {"input": value, "outcome": "error", "error": str(exc)}
    counts = {
        key: sum(r["outcome"] == key for r in results) for key in ("added", "exists", "error")
    }
    lines = [
        f"! {r['input']}: {r['error']}"
        if r["outcome"] == "error"
        else f"{'+' if r['outcome'] == 'added' else '='} {r['item']['id']}  {r['item']['title']}"
        for r in results
    ]
    lines.append(f"{counts['added']} added, {counts['exists']} existing, {counts['error']} failed")
    emit(ctx, {"results": results, "counts": counts}, lines)
    if counts["error"]:
        raise typer.Exit(1)


def show_items(ctx, items):
    emit(
        ctx,
        items,
        [f"{i['id']}  {i['status']:6}  {i['kind']:8}  {i['title']}" for i in items]
        or ["No items found."],
    )


@app.command("ls")
@handled
def list_items(
    ctx: typer.Context,
    read: bool = typer.Option(False, "--read", help="Show read items."),
    reading: bool = typer.Option(False, "--reading", help="Show currently-reading items."),
    all_items: bool = typer.Option(False, "--all", help="Show all statuses."),
    tag: str | None = typer.Option(None),
    kind: str | None = typer.Option(None, "--type"),
    no_note: bool = typer.Option(False, "--no-note"),
    group: str | None = typer.Option(None, "--group", help="Filter by group name or ID."),
):
    """List the unread queue by default."""
    selected = [read, reading, all_items]
    if sum(selected) > 1:
        raise ValueError("Use only one of --read, --reading, and --all")
    with Database(ctx.obj["config"].db) as library:
        show_items(
            ctx,
            library.list_items(
                None if all_items else "read" if read else "reading" if reading else "unread",
                tag=tag,
                kind=kind,
                no_note=no_note,
                group=group,
            ),
        )


@app.command()
@handled
def search(
    ctx: typer.Context,
    query: str,
    read: bool = typer.Option(False, "--read"),
    reading: bool = typer.Option(False, "--reading"),
    group: str | None = typer.Option(None, "--group", help="Search within a group."),
):
    """Search titles, authors, venues and tags using SQLite FTS5 syntax."""
    if read and reading:
        raise ValueError("Use only one of --read and --reading")
    with Database(ctx.obj["config"].db) as library:
        show_items(
            ctx,
            library.search(
                query, status="read" if read else "reading" if reading else None, group=group
            ),
        )


@app.command()
@handled
def reading(
    ctx: typer.Context,
    queries: list[str] = typer.Argument(
        ..., help="IDs, unique ID prefixes, or quoted title fragments."
    ),
):
    """Mark saved items as currently being read."""
    with Database(ctx.obj["config"].db) as library:
        items = [library.get(query) for query in queries]
        show_items(ctx, [library.set_status(item["id"], "reading") for item in items])


@app.command()
@handled
def tui(ctx: typer.Context):
    """Open the terminal interface using this installation's Python config."""
    if ctx.obj["json"]:
        raise ValueError("tui does not support --json")
    binary = os.environ.get("LIT_TUI_BINARY") or shutil.which("lit-tui")
    if binary is None:
        for candidate in (
            Path.cwd() / "tui" / "target" / "debug" / "lit-tui",
            Path.cwd() / "tui" / "target" / "release" / "lit-tui",
        ):
            if candidate.is_file():
                binary = str(candidate)
                break
    if binary is None:
        raise ValueError("lit-tui is not installed; run cargo install --path tui --locked")
    env = dict(os.environ)
    env["LIT_TUI_PYTHON"] = sys.executable
    env["LIT_TUI_DB"] = str(ctx.obj["config"].db)
    env["LIT_TUI_NOTES_DIR"] = str(resolved_notes_dir(ctx.obj["config"]))
    if ctx.obj["config"].vault is not None:
        env["LIT_TUI_VAULT"] = str(ctx.obj["config"].vault)
    result = subprocess.run([binary], env=env, check=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


def resolved_notes_dir(cfg: Config) -> Path:
    if cfg.notes_dir is not None:
        return cfg.notes_dir
    if cfg.vault is not None:
        return (cfg.vault / "Reading").resolve()
    return (
        (Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")) / "tiny-reading-tracker/notes")
        .expanduser()
        .resolve()
    )


def make_note(library, item, cfg, text):
    notes_dir = resolved_notes_dir(cfg)
    if text is None:
        note = load_note(item, notes_dir, cfg.vault)
    else:
        note = append_note(item, notes_dir, text, cfg.vault)
    path = note.path
    if not item.get("note_path"):
        library.set_note_path(item["id"], str(path))
    return path


@app.command()
@handled
def read(
    ctx: typer.Context,
    queries: list[str] = typer.Argument(
        ..., help="IDs, unique ID prefixes, or quoted title fragments."
    ),
    note_text: str | None = typer.Option(None, "--note", help="Append a note (one item only)."),
):
    """Mark saved items read and record a timestamp."""
    if note_text is not None and len(queries) != 1:
        raise ValueError("--note requires exactly one item")
    cfg = ctx.obj["config"]
    with Database(cfg.db) as library:
        items = [library.get(query) for query in queries]
        if note_text is not None:
            make_note(library, items[0], cfg, note_text)
        updated = [library.set_status(item["id"], "read") for item in items]
        show_items(ctx, updated)


@app.command()
@handled
def unread(
    ctx: typer.Context,
    queries: list[str] = typer.Argument(
        ..., help="IDs, unique ID prefixes, or quoted title fragments."
    ),
):
    """Return items to the unread queue and clear their read timestamp."""
    with Database(ctx.obj["config"].db) as library:
        items = [library.get(query) for query in queries]
        show_items(ctx, [library.set_status(item["id"], "unread") for item in items])


@app.command()
@handled
def note(
    ctx: typer.Context,
    query: str,
    text: str | None = typer.Option(None, "--text", "--append", help="Append text to the note."),
    open_note: bool = typer.Option(False, "--open", help="Open the note in Obsidian."),
):
    """Create or append an optional Markdown note, preserving existing writing."""
    cfg = ctx.obj["config"]
    with Database(cfg.db) as library:
        item = library.get(query)
        path = make_note(library, item, cfg, text)
    if open_note:
        if not webbrowser.open("obsidian://open?" + urlencode({"path": str(path)})):
            typer.echo("Note saved; could not launch Obsidian. Open the path manually.", err=True)
    emit(ctx, {"id": item["id"], "note_path": str(path)}, [str(path)])


def show_group(ctx, group):
    emit(ctx, group, [f"{group['id']}  {group['name']}  ({group['item_count']} items)"])


@group_app.command("create")
@handled
def group_create(
    ctx: typer.Context,
    name: str,
    parent: str | None = typer.Option(None, "--parent", help="Create as a child of this root group."),
):
    """Create an empty group. Names are case-insensitively unique."""
    with Database(ctx.obj["config"].db) as library:
        show_group(ctx, library.create_group(name, parent=parent))


@group_app.command("ls")
@handled
def group_list(ctx: typer.Context):
    """List groups, including empty ones, with total membership counts."""
    with Database(ctx.obj["config"].db) as library:
        groups = library.list_groups()
        emit(
            ctx,
            groups,
            [f"{g['id']}  {g['name']}  ({g['item_count']} items)" for g in groups]
            or ["No groups found."],
        )


@group_app.command("rename")
@handled
def group_rename(ctx: typer.Context, group: str, name: str):
    """Rename a group, preserving its ID and membership."""
    with Database(ctx.obj["config"].db) as library:
        show_group(ctx, library.rename_group(group, name))


@group_app.command("delete")
@handled
def group_delete(ctx: typer.Context, group: str):
    """Delete the group and its memberships; retain every library item and note."""
    with Database(ctx.obj["config"].db) as library:
        deleted = library.delete_group(group)
        emit(ctx, deleted, [f"Deleted group {deleted['name']}; library items retained."])


@group_app.command("add")
@handled
def group_add(
    ctx: typer.Context,
    group: str,
    queries: list[str] = typer.Argument(
        ..., help="Saved item IDs, unique prefixes, or title fragments."
    ),
):
    """Add saved items to a group; repeated membership is a no-op."""
    with Database(ctx.obj["config"].db) as library:
        item_ids = [library.get(query)["id"] for query in queries]
        show_group(ctx, library.add_to_group(group, item_ids))


@group_app.command("remove")
@handled
def group_remove(
    ctx: typer.Context,
    group: str,
    queries: list[str] = typer.Argument(
        ..., help="Saved item IDs, unique prefixes, or title fragments."
    ),
):
    """Remove membership only, retaining items in the library and other groups."""
    with Database(ctx.obj["config"].db) as library:
        item_ids = [library.get(query)["id"] for query in queries]
        show_group(ctx, library.remove_from_group(group, item_ids))
