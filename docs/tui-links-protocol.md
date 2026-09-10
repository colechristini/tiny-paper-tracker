# TUI links protocol

The stage 4 link operations use the existing version 1 JSON-lines bridge. Each
request and response includes `version: 1`. Link search considers every saved
item, regardless of the current status or group filter, and never creates a
note.

`link_search` accepts a title substring:

```json
{"version":1,"op":"link_search","query":"attention"}
```

The successful response contains at most 20 alphabetically ordered title and
ID pairs:

```json
{"version":1,"ok":true,"items":[{"id":"lit_…","title":"Attention"}]}
```

`note_link` requires the full current and target IDs, and the current note must
already be open in the bridge session:

```json
{"version":1,"op":"note_link","id":"lit_current","target_id":"lit_target"}
```

The bridge loads or creates the target note through the portable notes backend,
registering its path only when the target had no registered note. The active
note baseline remains unchanged. The response contains a Markdown link and the
target's absolute path:

```json
{"version":1,"ok":true,"markdown":"[Target](<relative/path.md>)","target_path":"/…/Target-lit_target.md"}
```

The link destination is relative to the current note directory and URL-encoded
with `/` preserved. Titles escape backslashes and square brackets; newlines are
rendered as spaces. Spaces, `#`, parentheses, Unicode, and other path
characters are percent-encoded. Errors use the bridge's standard structured
`error` object and leave the active note and database unchanged, except for a
new target note path registration after a successful target load.
