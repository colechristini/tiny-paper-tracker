# Changelog

## Unreleased

The staged terminal interface now provides a Ratatui reading list, portable
Markdown note editing, autosave and recovery copies, Markdown preview, and
optional note-link backend support.

- `lit tui` uses the same Python interpreter, resolved database, and notes
  directory as the CLI.
- Note files remain portable and support existing absolute paths and legacy
  relative vault paths; Obsidian is optional.
- The optional links workflow searches all saved titles and inserts relative
  encoded Markdown file links after a target is selected. It creates target
  notes on selection and keeps one shared note per item across groups. It does
  not provide backlinks or a graph.
- The staged preview validation passed the Rust suite and a PTY check covering
  long paragraphs, End scrolling, preview input isolation, and clean exit.
- Integrated Rust link completion and PTY validation remain pending.

## [0.2.0] — 2026-09-10

Paper groups are now available for organizing saved resources into flat,
named collections.

- Create, list, rename, and delete groups, including empty groups.
- Add and remove saved items from groups with safe, repeatable membership
  operations.
- Filter `lit ls` and `lit search` by group, while preserving shared reading
  status, notes, tags, and item identity.
- Attach groups during `lit add`; re-adding a saved item can add memberships
  without resetting its existing state.
- Migrate existing schema 1 databases automatically to schema 2.

[0.2.0]: https://github.com/colechristini/tiny-paper-tracker/releases/tag/v0.2.0
