# v0 boundary

Based on the final message in **Paper Tracking Systems** (conversation
`6aa1e5d4-3528-832f-8c92-2884bd65837f`).

Deliver six commands: `lit add`, `lit ls`, `lit read`, `lit unread`, `lit note`,
and `lit search`. Python/Typer CLI, SQLite with identifier aliases and FTS5,
Zotero Translation Server for URL/identifier metadata, Obsidian Markdown for
optional notes. Batch inputs and JSON output make the CLI usable by agents.
Configure local data and vault paths; never assume an existing vault location.

Translation Server is the only server in v0. Defer a separate tracker HTTP API,
browser extension, Zotero migration, agent skill, direct metadata APIs, PDF
management, and richer workflows until the user reviews v0.

Implementation roles: parent owns CLI/configuration/integration/documentation;
one implementation agent owns ingestion/normalization; another owns database
and Markdown note handling. Parent serializes commits after reviewing each unit.
An independent review follows integration.

Acceptance: reproducible install; six-command workflow; deduplication across
known aliases without overwriting status/notes; safe ambiguous-query behavior;
durable Markdown without overwrites; per-input batch failures; meaningful
offline tests and a live translator smoke test when Docker is available.
Stop at v0 and request feedback before further work.
