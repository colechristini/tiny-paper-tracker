# Tiny Reading Tracker

A local reading library for papers, blogs, and other web resources. SQLite owns
metadata and read status; your Obsidian vault owns your writing. Zotero's
Translation Server resolves metadata without the Zotero desktop app.

This is **v0**: six CLI commands, no tracker web API or browser extension.

## Quick start

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and a running Docker engine.

```sh
uv sync --locked
docker compose up -d
uv run lit add https://arxiv.org/abs/1706.03762 https://example.com
uv run lit ls
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
For notes, point to an **existing** Obsidian vault:

```sh
export LIT_VAULT="$HOME/Documents/My Vault"
lit note "Attention Is All You Need" --text "My takeaway…"
lit note "Attention Is All You Need" --open
```

Alternatively copy `config.example.toml` to
`~/.config/tiny-reading-tracker/config.toml` (respects `XDG_CONFIG_HOME`) and edit
the paths. No real vault path is checked into this repository.

Precedence: global CLI options > environment variables > TOML > defaults.
Global options (`--config`, `--db`, `--vault`, `--json`) go **before** the command.
Environment overrides: `LIT_CONFIG`, `LIT_DB`, `LIT_VAULT`, `LIT_TRANSLATOR_URL`.
Relative TOML paths resolve against the config directory; CLI and environment
paths resolve against the working directory. `~` is expanded.

```sh
lit --config ./my-settings.toml ls
lit --db /tmp/try-lit.db --json ls --all
```

## The six commands

```sh
# Add URLs or DOI/arXiv/PMID/ISBN identifiers. Quote identifiers with spaces.
lit add https://arxiv.org/abs/1706.03762 10.1038/nature14539 --tag ml
lit add pmid:12345678 isbn:9780306406157  # numeric IDs require an explicit prefix
pbpaste | lit add                     # one URL/identifier per line
cat urls.txt | lit add

lit ls                               # unread queue
lit ls --read
lit ls --all --tag ml --type paper
lit ls --read --no-note

lit read "Attention Is All You Need"  # full ID, unique prefix, or title fragment
lit read lit_ID1 lit_ID2
lit unread "Attention Is All You Need"
lit read "Attention Is All You Need" --note "My main takeaway…"

lit note "Attention Is All You Need"   # create or return note path
lit note "Attention Is All You Need" --append "Another thought…"
lit note "Attention Is All You Need" --open

lit search attention                 # searches all statuses
lit search '"linear attention"' --read
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

`lit note` creates a Markdown file inside `Reading/` with a sanitized title and
stable item ID in the filename. Frontmatter links the item to its identifiers.
Existing writing is preserved; `--text` and `--append` append, never replace.
SQLite stores only the note path. A missing registered note is an error so the
tool does not silently recreate a file you moved. Automatic rename detection
and cross-device synchronization are outside v0.

The local SQLite database uses transactions, foreign keys, an explicit schema
version, and WAL. For a consistent backup while other commands might run, use
SQLite's backup API or the shell's `.backup` command:

```sh
sqlite3 ~/.local/share/tiny-reading-tracker/library.db '.backup /absolute/path/library-backup.db'
```

Back up the Obsidian vault separately. Filesystem note writes and SQLite changes
are not a single cross-filesystem transaction. Keep the database on a local
filesystem, not a live shared/network database mount.

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
dependencies. See `docs/v0-plan.md` for the agreed boundary and
`docs/validation.md` for the actual validation results.

For an opt-in end-to-end check against the running server, run
`uv run python scripts/smoke_live.py`. This performs 11 live lookups and the full
six-command workflow in a temporary database/vault. Compose pins the tested
image digest; review and update that digest deliberately when updating Zotero.
