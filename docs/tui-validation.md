# TUI validation — 2026-09-10

This record covers the staged terminal interface work. The historical Python
and group validation remains in [validation.md](validation.md).

## Use

Build the terminal binary from this checkout, then launch it through the Python
CLI so it uses the configured database and notes directory:

```sh
cargo install --path tui --locked
uv run lit tui
```

Set `--notes-dir`, `LIT_NOTES_DIR`, or `notes_dir` in TOML for a different
portable Markdown directory. If no notes directory is supplied, configured
vaults use `Reading/`; otherwise notes use the XDG data directory. Existing
registered absolute paths and legacy relative vault paths remain supported.

## Controls

In the reading list, use Up/Down or `j`/`k` to move, `u`/`c`/`r` to set unread,
currently reading, or read, `1`–`4` to select a status filter, `g` to cycle
groups, `/` to search titles, `f` to refresh, Enter to edit the selected note,
PageUp/PageDown to scroll metadata, `?` for help, and `q` or Escape to quit.

In the editor, use Ctrl-S to save, Ctrl-V to toggle Markdown preview, Ctrl-E to
write a recovery copy, Ctrl-Q to save and quit, and Escape to save and return
to the list. Arrow keys, Home/End, PageUp/PageDown, and bracketed paste work in
edit mode. Preview mode supports the same scrolling keys and Ctrl-V returns to
editing.

The preview renders headings, emphasis, lists, blockquotes, code, links, line
breaks, rules, and readable tables. Math remains visible as raw TeX; the
terminal renderer does not typeset formulas or fetch remote images.

## Results

- Groups release: 69 Python tests passed, with the existing migration and group
  behavior covered.
- Stage 1 viewer: 73 Python tests passed; Rust format/tests/Clippy checks and
  a real PTY status interaction check passed.
- Stage 2 notes: 91 Python tests passed; six Rust tests passed; real PTY checks
  covered Unicode paste, autosave, external-edit conflict, recovery, and
  terminal restoration.
- Stage 3 Markdown preview: 10 Rust library tests and 1 binary test passed;
  formatting and Clippy passed. A real PTY check passed a long paragraph to
  the editor, used End to reach its end, toggled preview, verified typing and
  paste input were ignored in preview while the original text stayed intact,
  and exited cleanly with terminal restoration.
- Stage 4 optional links: the Python bridge protocol and backend tests are
  complete; integrated Rust completion and PTY validation remain pending.

The staged work uses temporary libraries and note directories for validation.
Markdown remains portable and independent of Obsidian. SQLite remains the
authoritative metadata store; note contents stay in files. Schema 1 and schema
2 databases migrate transactionally to schema 3, preserving existing metadata,
groups, identifiers, tags, reading state, and registered note paths. Older
release CLIs cannot open a schema 3 database after migration, so keep a backup
when upgrading an existing library.
