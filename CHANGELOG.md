# Changelog

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
