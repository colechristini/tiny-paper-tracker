---
name: tiny-reading-tracker
description: Save papers, blogs, and other resources to the Tiny Reading Tracker SQLite library using lit; search its queue, mark items read or unread, and create linked Obsidian notes. Use for Tiny Reading Tracker or lit requests and follow-ups in an established tracker workflow. Explicit Zotero-library requests use the separate reading-library workflow.
---

# Tiny Reading Tracker

SQLite owns metadata and read status; Markdown in an Obsidian vault owns notes.
Use the CLI for library changes. The Zotero Translation Server only resolves
metadata; this workflow does not write to the Zotero desktop library.
If a generic reading-library request has no established backend, clarify which
library the user means before saving it in either system.

## Invoke from any working directory

The local checkout is `/Users/colechristini/Documents/ChatGPT/tiny-reading-tracker`.
In the examples below, replace `lit` with this command prefix and retain `--json`:

```sh
uv run --project /Users/colechristini/Documents/ChatGPT/tiny-reading-tracker lit
```

Use an explicitly supplied checkout instead if the project has moved. Global
flags (`--json`, `--config`, `--db`, `--vault`) go before the command. Honor the
existing configuration and `LIT_CONFIG`, `LIT_DB`, `LIT_VAULT`, and
`LIT_TRANSLATOR_URL` environment overrides; do not create a separate database
per task. The default database is under
`${XDG_DATA_HOME:-~/.local/share}/tiny-reading-tracker/library.db` and the default
config is `${XDG_CONFIG_HOME:-~/.config}/tiny-reading-tracker/config.toml`.

For notes, use the configured existing vault. If no vault is configured or
established by the user, ask for its path only when a note is needed. Saving and
read-status changes do not require a vault. Configuration details and setup are
in the checkout's `README.md`; use `lit --help` for command discovery.

## Save and inspect

```sh
lit --json add https://arxiv.org/abs/1706.03762 https://example.com --tag attention
lit --json add doi:10.1038/nature14539 arxiv:1706.03762 pmid:12345678 isbn:9780306406157
lit --json ls
lit --json ls --all --tag attention
lit --json ls --read --no-note
lit --json search '"linear attention"'
lit --json search attention --read
```

Pass a batch in one `add` call; stdin accepts one URL or identifier per line.
Use argument arrays or proper shell quoting for URLs and user text. Prefix
numeric PMID/ISBN identifiers explicitly. Apply user-requested tags with
repeated `--tag`; imported keywords are not personal tags.

`add` returns `{results, counts}`; each result has `input`, `outcome`
(`added`, `exists`, or `error`), and an `item` or `error`. Exit code 1 can mean
partial success: inspect every result and retry only failed inputs after fixing
the cause. Re-adding a known alias preserves metadata, notes, and read status.
An identity conflict or multiple bibliographic results needs a specific resource
or user decision; do not force a merge or silently substitute a different item.

`ls` defaults to unread; `--all` includes read items. `--no-note` filters to items
without a registered note path; it is not an option for hiding note contents.
`search` spans all statuses
unless `--read` is supplied and returns an array of items. Search uses FTS5
syntax; quote phrases and punctuation-bearing terms inside the query string.
It indexes titles, authors, venues, and tags, not note contents. Search a returned
note path separately when the request concerns the user's writing.

## Status and notes

Use a returned full item ID for mutations after a search. The CLI also accepts
unique ID prefixes and title substrings; ambiguous queries fail with candidates.
Resolve the ambiguity before changing state.

```sh
lit --json read FULL_ITEM_ID
lit --json unread FULL_ITEM_ID
lit --json note FULL_ITEM_ID --text 'The user’s takeaway.'
lit --json note FULL_ITEM_ID --append 'Another thought.'
lit --json read FULL_ITEM_ID --note 'The user’s takeaway.'
```

Only mark read when the user's request establishes that they read it. Saving,
summarizing, or writing a note does not imply reading. To save something the user
has already read, add it first and mark the returned ID read. Repeated `read`
preserves its timestamp; `unread` clears it. V0 has no `reading` state command.

Notes are optional. `note` without text creates or returns a Markdown path;
`--text` and `--append` both append and never replace. `read --note` accepts one
item only. Note creation does not change status. Avoid `--open` unless opening
Obsidian helps the user's requested workflow.

Preserve the user's writing and distinguish generated summaries from their own
thoughts. A missing registered note is an error, not permission to recreate it.
If a note command has an uncertain outcome, inspect the returned/registered file
before retrying: appending text is not idempotent. Notes and database changes
are not one transaction, so check both parts of an uncertain `read --note`.

## Completion and failures

For mutations, inspect the returned item/status or note path; verify note text
in the file after writing. Report titles and outcomes concisely, including
individual failures and note paths when relevant.

If the translator is unavailable, inspect the project's Compose service; when
needed for an authorized save, start it with `docker compose up -d` from the
checkout. It is bound to localhost by default. Retry a failed lookup once after
restoring service; report persistent failures rather than changing resolvers,
creating placeholder records, or repeatedly retrying blocked sites. Already
known aliases and local queue/status/note operations do not need the server.
