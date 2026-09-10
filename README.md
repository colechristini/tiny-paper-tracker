# Tiny Reading Tracker

A local reading library for papers, blogs, and other web resources. SQLite owns
metadata and read status; notes are portable Markdown files that can be edited
in the terminal, Obsidian, or another editor. Zotero's Translation Server
resolves metadata without the Zotero desktop app.

The current **v0.4.0** adds reading directly from the terminal, note filters,
and selection fixes to the Rust interface, portable Markdown notes, and subgroups.
There is no tracker web API or browser extension. See
[CHANGELOG.md](CHANGELOG.md) for release notes.

## Quick start

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and a running Docker engine.

```sh
uv sync --locked
cargo install --path tui --locked
docker compose up -d
uv run lit add https://arxiv.org/abs/1706.03762 https://example.com
uv run lit ls
uv run lit tui
uv run lit read "Attention Is All You Need"
```

Metadata resolution uses the [official Zotero Translation Server API](https://github.com/zotero/translation-server#endpoints).
Compose exposes the server only on `127.0.0.1:1969`. The server needs internet
access. To stop it, run `docker compose down`.

To make `lit` available outside this repository, optionally run
`uv tool install --editable .`. Otherwise prefix commands below with `uv run`.

## Configuration

No configuration is required for saving and tracking items. The default database
is `~/.local/share/tiny-reading-tracker/library.db` (respects `XDG_DATA_HOME`).
Notes use a portable local Markdown directory by default:

```sh
export LIT_VAULT="$HOME/Documents/My Vault"
lit note "Attention Is All You Need" --text "My takeaway…"
lit note "Attention Is All You Need" --open
```

Set `--notes-dir`, `LIT_NOTES_DIR`, or `notes_dir` in TOML to choose another
directory. When a vault is configured and no notes directory is supplied,
notes live under its `Reading/` directory. `--vault` remains optional and is
only needed to resolve an existing relative note path or open a note in
Obsidian.

Alternatively copy `config.example.toml` to
`~/.config/tiny-reading-tracker/config.toml` (respects `XDG_CONFIG_HOME`) and edit
the paths. No real vault path is checked into this repository.

Precedence: global CLI options > environment variables > TOML > defaults.
Global options (`--config`, `--db`, `--vault`, `--notes-dir`, `--json`) go **before** the command.
Environment overrides: `LIT_CONFIG`, `LIT_DB`, `LIT_VAULT`, `LIT_NOTES_DIR`, `LIT_TRANSLATOR_URL`.
Relative TOML paths resolve against the config directory; CLI and environment
paths resolve against the working directory. `~` is expanded.

```sh
lit --config ./my-settings.toml ls
lit --db /tmp/try-lit.db --json ls --all
```

## CLI commands

```sh
# Add URLs or DOI/arXiv/PMID/ISBN identifiers. Quote identifiers with spaces.
lit add https://arxiv.org/abs/1706.03762 10.1038/nature14539 --tag ml
lit add pmid:12345678 isbn:9780306406157  # numeric IDs require an explicit prefix
pbpaste | lit add                     # one URL/identifier per line
cat urls.txt | lit add

lit ls                               # unread queue
lit ls --read
lit ls --reading                     # currently-reading queue
lit ls --all --tag ml --type paper
lit ls --read --no-note

lit read "Attention Is All You Need"  # full ID, unique prefix, or title fragment
lit read lit_ID1 lit_ID2
lit unread "Attention Is All You Need"
lit reading "Attention Is All You Need" # currently reading
lit read "Attention Is All You Need" --note "My main takeaway…"

lit note "Attention Is All You Need"   # create or return note path
lit note "Attention Is All You Need" --append "Another thought…"
lit note "Attention Is All You Need" --open

lit search attention                 # searches all statuses
lit search '"linear attention"' --read
lit search attention --reading
lit --json search attention           # machine-readable output
```

Search uses [SQLite FTS5 query syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax)
over title, authors, venue, and tags. Quote terms containing punctuation inside
the query, e.g. `lit search '"new-tag"'`. Notes remain searchable through Obsidian
or normal filesystem tools.

Title lookups are case-insensitive substrings; ambiguous matches fail with
candidates rather than marking an arbitrary item. Repeated `read` preserves the
original read timestamp; `unread` clears it. Notes are optional and do not change
read status. `--no-note` means no registered note path, not a vault scan.

## Paper groups

Groups are named collections with roots and one optional child level. One item
can belong to multiple groups, and its read status and note remain shared across
those groups. Tags remain independent topic labels.

```sh
lit group create "Journal Club"
lit group create "Methods" --parent "Journal Club" # one child level
lit group create "Attention"
lit group ls                            # includes empty groups and total item counts
lit add https://arxiv.org/abs/1706.03762 --group "Journal Club" --group "Attention"
lit group add "Journal Club" "Diffusion Study" lit_ITEM_ID
lit ls --group "Journal Club"            # unread members
lit ls --all --group "Journal Club"      # all members
lit search attention --group "Journal Club" --read
lit group rename "Journal Club" "Friday Reading"
lit group remove "Friday Reading" lit_ITEM_ID
lit group delete "Friday Reading"
```

Create a group before passing `--group`; unknown groups fail before ingestion.
Re-adding a saved URL with `--group` attaches the existing item without resetting
its state. Group names are trimmed and case-insensitively unique among siblings;
commands accept an exact name, `Parent/Child` path, or full group ID. Parent
filters include direct members and direct children. Rename preserves the ID and
memberships.
Item selectors use the same IDs, unique prefixes, and unambiguous title fragments
as `lit read`. A multi-item membership operation fails without partial changes
if any selector is invalid or ambiguous. Repeated add/remove membership is safe.

Removing an item from a group keeps it in the library and other groups. Removing
from a parent also removes its child memberships. Deleting a group removes that
group, its children, and memberships, preserving all papers and notes. JSON item
output includes `groups: [{id, name}]`. Collections are limited to roots and one
child level.

The terminal interface uses `g` and `h` to cycle forward and backward through
the unfiltered library and groups. Press `a` to add a URL or identifier without
leaving the TUI. Adds run in the background so the interface can redraw while
metadata resolves; the popup remains open until the outcome is known and lets
you correct and retry failures. The current root-group filter is the destination,
or the item is added ungrouped from the unfiltered view.
Press `5` for papers with registered notes and `6` for papers without registered
notes; press the active note filter again to clear it. Note filters combine with
the current status, group, and title search.
After a refresh, the TUI keeps the same paper and subgroup occurrence selected
when it remains visible. If a mutation or filter removes it, selection moves to
the nearest surviving paper before it, or the next paper when none precedes it.
Subgroup headings appear only when that subgroup has papers visible under the
current filters; empty subgroups remain available in the membership picker.
In the list view, `Delete` (or macOS
`Backspace`) removes the selected library item while leaving its Markdown note
file intact. Press `F2` or uppercase `R` to rename the selected title inline;
`Enter` saves and `Esc` cancels. Text deletion in the editor and search input
keeps its normal meaning.
Press `m` in list mode to edit group memberships. Move with the arrow keys or
`j`/`k`, toggle with `Space`, apply with `Enter`, or cancel with `Esc`. Checkboxes
show direct memberships: selecting a subgroup includes the paper in its
parent's filtered view without checking the parent. Create groups with
`lit group create` before assigning them in the picker.

Existing databases automatically migrate from schema 1, 2, or 3 to schema 4
in a transaction when opened. Existing metadata, groups, tags, notes, and
reading state are preserved. The v0.1.0 and v0.2.0 CLIs cannot open the
upgraded database; retain a backup if you need to return to an older release.

## Ingestion and identity

Up to four lookups run concurrently (configurable `workers`); database writes are
serialized. Each input gets an `added`, `exists`, or `error` result. Successful
inputs persist even if another input fails. Failures yield exit code 1, so an
agent can retry just those inputs. Re-adding an already known identifier works
without the translator and preserves read status and notes.

DOI, arXiv, PMID and canonical URL aliases map to immutable local IDs. Known
aliases deduplicate; different URLs can deduplicate when the translator returns
a shared identifier. If new metadata bridges two existing records, v0 reports a
conflict instead of guessing how to merge their notes/status. There is no fuzzy
title deduplication. Imported publisher keywords are not automatically turned
into your tags; use repeated `--tag` options.

A page must resolve to one item. Search-result pages with multiple choices,
unsupported pages, invalid identifiers, malformed responses, and timeouts are
reported as errors. There is no silent URL-only fallback in v0. Translator and
publisher coverage varies; this does not download papers or snapshots.

## Notes and durability

`lit note` and the TUI create flat Markdown files with a sanitized title and
stable item ID in the filename. New files use `Summary` and `Notes` sections;
frontmatter records the item and creation time. Existing writing is preserved,
and the CLI append operation never replaces it. The TUI records created and
modified timestamps, autosaves after a short idle period, saves before leaving
the editor, and writes a recovery copy with `Ctrl-E` when a save conflict
occurs. Atomic saves preserve file permissions and detect edits or deletion
before replacement. Cooperative TUI/CLI writers are serialized; an editor
that does not use the lock can still race the final check on filesystems
without a compare-and-swap operation.

SQLite stores only the note path. Registered absolute paths and legacy relative
vault paths remain supported, while new notes can live anywhere in the flat
configured notes directory. A missing registered note is an error so the tool
does not silently recreate a file you moved. Automatic rename detection and
cross-device synchronization are outside v0.3.

## Optional note links

The optional TUI links feature searches all saved titles and uses `@title`
completion with Tab when enabled. Selecting a target inserts a portable
relative Markdown file link and creates the target note only when needed. Items
in multiple groups still share one note. Link paths are URL-encoded, while
titles escape Markdown brackets and backslashes. The feature does not add
backlinks or a graph; see [the bridge protocol](docs/tui-links-protocol.md) for
the wire contract. Integrated Rust completion and PTY validation are pending.

The local SQLite database uses transactions, foreign keys, an explicit schema
version, and WAL. For a consistent backup while other commands might run, use
SQLite's backup API or the shell's `.backup` command:

```sh
sqlite3 ~/.local/share/tiny-reading-tracker/library.db '.backup /absolute/path/library-backup.db'
```

Back up the configured notes directory separately. Filesystem note writes and
SQLite changes are not a single cross-filesystem transaction. Keep the database
on a local filesystem, not a live shared/network database mount.

## Agent skill

The repository includes `skills/tiny-reading-tracker/SKILL.md`, a personal agent
skill for this CLI. Invoke it as `$tiny-reading-tracker`, for example:
“Use $tiny-reading-tracker to save these links and tag them attention.” It keeps
this SQLite library separate from the existing Zotero `reading-library` skill.

To install on this machine, link the skill folder into your personal skill
directory (run from this checkout; an existing destination is not replaced):

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
skill_target="${CODEX_HOME:-$HOME/.codex}/skills/tiny-reading-tracker"
test ! -e "$skill_target" && test ! -L "$skill_target" && \
  ln -s "$PWD/skills/tiny-reading-tracker" "$skill_target"
```

The skill records this checkout's local path. Update that path if you move the
repository. Its instructions use `uv run --project` so no global `lit` install
is needed.

## Development

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests isolate data and notes in temporary directories and mock translation
responses; they do not touch your reading library. The lockfile pins Python
dependencies. See `docs/v0-plan.md` for the agreed boundary,
`docs/validation.md` for the historical validation results, and
`docs/tui-validation.md` for the staged terminal interface checks.

For an opt-in end-to-end check against the running server, run
`uv run python scripts/smoke_live.py`. This performs 11 live lookups and the full
full CLI workflow in a temporary database and notes directory. Compose pins the tested
image digest; review and update that digest deliberately when updating Zotero.
