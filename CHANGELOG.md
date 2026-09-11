# Changelog

## Unreleased

- Add portable Ctrl shortcuts for formatting and word/line navigation;
  move note recovery to Ctrl-G so Ctrl-E can move to the line end.
- Wrap long lines visually in the note editor and add Command-B/Command-I
  shortcuts for bold and italic Markdown.
- Add Option-Left/Right word navigation and Command-Left/Right line navigation
  in notes and text fields.
- Continue bullet, numbered, and lettered lists when pressing Return in notes;
  preserve indentation and end the list on an empty item.
- Show checked memberships as solid white squares.

## [0.4.0] — 2026-09-10

- Add URLs and identifiers from the list with `a`; metadata resolution runs in a
  responsive background worker and uses the active root group as its destination.
- Filter the current list to papers with or without registered notes using `5`
  and `6`, while retaining status, group, and title filters.
- List refreshes preserve the same paper and section when possible, then choose
  the nearest surviving paper when a mutation or filter removes it.
- Empty subgroup headings are hidden from filtered reading lists while all
  groups remain available in the membership picker.
- Membership editing keeps parent and child checks consistent in the draft:
  selecting a child checks its parent, and clearing a parent clears its children.
- Document line-clear and backward-word shortcuts, including terminal fallbacks.

## [0.3.0] — 2026-09-10

The Rust terminal interface provides a reading list, portable Markdown note
editing, autosave and recovery copies, Markdown preview, and paper links.

- `lit tui` uses the same Python interpreter, resolved database, and notes
  directory as the CLI.
- Note files remain portable and support existing absolute paths and legacy
  relative vault paths; Obsidian is optional.
- The `@title` and Tab completion workflow searches all saved titles and inserts relative
  encoded Markdown file links after a target is selected. It creates target
  notes on selection and keeps one shared note per item across groups. It does
  not provide backlinks or a graph.
- Groups support one child level with `group create NAME --parent PARENT`;
  parent filters include child memberships and the schema migrates to version 4.
- Subgroups appear as stacked sections in the reading panel, with loose papers
  at the top without a heading. `g` and `h` cycle top-level groups in both directions.
- Filter by reading status or title, inspect metadata, and change status from
  the list. `Delete` removes a library record while preserving its note file.
- The TUI supports renaming the selected paper title with `F2` or uppercase `R`;
  titles are edited inline with Unicode-safe cursor and deletion controls.
- The list view supports editing direct paper memberships with `m`, including
  root and child group checkboxes.
- The tracker skill documents subgroup creation, qualified selectors, parent
  membership behavior, and TUI controls.

Existing libraries migrate transactionally to schema 4. Older releases cannot
open the upgraded database.

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
[0.3.0]: https://github.com/colechristini/tiny-paper-tracker/releases/tag/v0.3.0
[0.4.0]: https://github.com/colechristini/tiny-paper-tracker/releases/tag/v0.4.0
