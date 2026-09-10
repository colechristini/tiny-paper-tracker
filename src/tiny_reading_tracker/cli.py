"""Six commands for humans and agents; network concurrency, serialized local writes."""

import functools
import json
import sqlite3
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
from .obsidian import write_note

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Save resources, track reading, and keep notes in Markdown.",
)


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
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
):
    ctx.obj = {"json": json_output}
    ctx.obj["config"] = load_config(config, db, vault)


@app.command()
@handled
def add(
    ctx: typer.Context,
    values: list[str] = typer.Argument(None, help="URLs or identifiers; no arguments reads stdin."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Repeat for multiple tags."),
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
                    item, created = library.add(resolved, tags=tag)
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
    all_items: bool = typer.Option(False, "--all", help="Show all statuses."),
    tag: str | None = typer.Option(None),
    kind: str | None = typer.Option(None, "--type"),
    no_note: bool = typer.Option(False, "--no-note"),
):
    """List the unread queue by default."""
    if read and all_items:
        raise ValueError("Use only one of --read and --all")
    with Database(ctx.obj["config"].db) as library:
        show_items(
            ctx,
            library.list_items(
                None if all_items else "read" if read else "unread",
                tag=tag,
                kind=kind,
                no_note=no_note,
            ),
        )


@app.command()
@handled
def search(
    ctx: typer.Context,
    query: str,
    read: bool = typer.Option(False, "--read"),
):
    """Search titles, authors, venues and tags using SQLite FTS5 syntax."""
    with Database(ctx.obj["config"].db) as library:
        show_items(ctx, library.search(query, status="read" if read else None))


def make_note(library, item, cfg, text):
    if cfg.vault is None:
        raise ValueError("Set your Obsidian vault with --vault, LIT_VAULT, or vault in config.toml")
    path = write_note(item, cfg.vault, text)
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
