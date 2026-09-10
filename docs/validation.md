# v0 validation — 2026-09-09 (America/Los_Angeles)

## Automated checks

- `uv run pytest -q`: **57 passed** on macOS, Python 3.11.6.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `git diff --check`: passed.
- `uv build`: built a source distribution and wheel successfully.
- An isolated `uv run --no-project --with <wheel> lit --help` launched all six
  commands successfully outside the project environment.
- CI configuration runs the offline suite/lint/format checks on Linux with
  Python 3.11 and 3.13. Remote CI has not run; this repository is local.

Coverage includes input normalization, Zotero child records and malformed or
multiple results, identifier conflict rollback, duplicate state preservation,
FTS queries, ambiguous item selection, read timestamps, configuration precedence,
per-input batch errors, Markdown preservation, missing vault/note handling,
symlink containment, and the six-command CLI workflow.

## Live integration

Started Docker Desktop and the Compose translation server, bound only to
`127.0.0.1:1969`. The tested image digest is pinned in `compose.yaml`.

`uv run python scripts/smoke_live.py` passed using temporary database/vault paths:

- 11 inputs: six arXiv papers, two webpages, three version-URL duplicates.
- 8 new items, 3 existing items, 0 failures.
- The first batch took **1.21 seconds**; the final repeat took **0.70 seconds**.
- Verified queue/search, read with note, append preserving earlier text, note
  path retrieval, DOI re-add preserving read status, and return to unread.

These are single warm-server smoke measurements, not a general throughput
benchmark; Docker startup/image download are excluded and caches/network/site
behavior affect timing. Test data was temporary and removed by the script.
No existing Zotero library or Obsidian vault was modified. The translation
server remains running for trying v0; stop it with `docker compose down`.

## Review and limits

Scoped implementation subagents used `gpt-5.6-sol`; the independent read-only
review used `gpt-5.6-luna`, following the supplied global policy. Review fixes
included existing-vault validation, omission of stale status from note
frontmatter, literal ID-prefix matching, FTS phrase semantics, and canonical
arXiv host / URL-port handling.

v0 intentionally has no separate tracker HTTP API, browser capture, direct
metadata-provider or HTML fallback, automatic note-move detection, existing
library migration, or automatic record merging. Notes are append-only through
the CLI; filesystem changes and SQLite updates are not one atomic transaction.
Configure your actual vault before using notes. Stop here for user review.

## Post-release increment: paper groups — 0.2.0.dev0

The original code and skill were pushed to
`colechristini/tiny-paper-tracker` and published as `v0.1.0`, with wheel and source
archives. Both Linux Python 3.11/3.13 CI jobs passed for that release commit.

The subsequent group feature was validated with **69 passing tests**, including
an existing schema-v1 fixture, concurrent migration, Unicode group-name
uniqueness, ID/name collision protection, atomic memberships, deduplication,
filters, and preservation of items/notes after group removal and deletion.
Ruff lint/format checks, package build, and the updated agent-skill validator pass.

The expanded live smoke test passed: 11 inputs, 8 new items, 3 duplicates, with
the original reading/note workflow plus group creation, membership, filtering,
rename, removal, and deletion. The ingestion portion took 1.02 seconds on the
running server. Data and notes were temporary. Groups are flat local collections;
the v0.1.0 release remains unchanged. Schema-v2 databases require the new CLI.
