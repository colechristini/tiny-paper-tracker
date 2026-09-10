"""Shared ingestion contract; note contents never enter SQLite."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResolvedItem:
    title: str
    url: str
    kind: str = "webpage"
    authors: list[str] = field(default_factory=list)
    venue: str | None = None
    published_at: str | None = None
    source: str = "zotero-translator"
    metadata: dict[str, Any] = field(default_factory=dict)
    identifiers: list[tuple[str, str]] = field(default_factory=list)
