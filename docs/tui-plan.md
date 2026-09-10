# Terminal interface implementation plan

## Architecture and scope

Keep Python `lit` and its SQLite repository as the authoritative backend. Add a
Rust Ratatui binary launched by `lit tui`; communicate through a versioned JSON
stdio bridge using the same Python interpreter and resolved configuration. No
second database implementation, web server, GPU, or Obsidian dependency.

Implement and review each stage on a fresh `codex/` branch, then merge to main.
The initial groups release is v0.2.0 and precedes all TUI work.

1. `codex/tui-viewer`: schema-safe currently-reading status, CLI support,
   Rust list on left and metadata on right, alphabetical ordering, status/group
   filters, title substring search, up/down navigation, U/C/R status hotkeys,
   empty/error states, terminal restoration, installation and CI checks.
2. `codex/tui-notes`: configurable notes directory (`--notes-dir`, LIT_NOTES_DIR,
   TOML notes_dir), default local data notes folder; preserve existing vault and
   registered note paths. Flat Markdown filenames with stable IDs, Summary and
   Notes template; Enter opens editor. Autosave after idle and before changing
   note/quitting. Atomic writes, external-change detection, failure keeps dirty
   buffer, created/modified timestamps, useful Unicode editing and scrolling.
3. `codex/tui-markdown`: edit/view toggle and scrolling Markdown preview. Render
   headings, lists, emphasis, code and links; keep math legible as literal TeX
   when terminal rendering cannot typeset it. No image/network rendering.
4. `codex/tui-links`: optional @title completion with Tab, insert ordinary
   portable Markdown file links to notes, one note per paper regardless of group.
   No backlinks or graph. Finish docs and integrated verification.

## Acceptance and review

Use temporary libraries and notes for tests, never mutate the user's library.
Python regression tests, Ruff, Rust formatting/tests/clippy, integration checks
of bridge and `lit tui`, and a PTY smoke check must pass. Reviewer checks migration
preserves all metadata/groups/identifiers/tags/notes; dirty notes survive errors;
external edits are never silently overwritten; terminal state restores on exit.
No expensive calibration. Record validation and limitations in docs.

Implementation delegated to Luna agents; root coordinates and reviews results.
