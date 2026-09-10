"""Bounded metadata ingestion through Zotero Translation Server."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from .models import ResolvedItem
from .normalize import input_identifiers, normalize_url, normalize_zotero_item


class ResolutionError(ValueError):
    """An expected failure while resolving user-supplied metadata."""


def resolve(value: str, translator_url: str, timeout: float = 20.0) -> ResolvedItem:
    """Resolve one URL or scholarly identifier using Translation Server.

    v0 deliberately has no HTML scraping or direct metadata-provider fallback.
    """

    try:
        input_identifiers(value)
    except ValueError as exc:
        raise ResolutionError(str(exc)) from exc
    if not isinstance(translator_url, str) or not translator_url.strip():
        raise ResolutionError("Translation Server URL is required")
    try:
        normalize_url(translator_url)
    except (ValueError, TypeError) as exc:
        raise ResolutionError("Translation Server URL must be an http(s) URL") from exc
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ResolutionError("Translation Server timeout must be greater than zero")

    # Route by input syntax, rather than canonical aliases: DOI and arXiv URLs
    # are still URLs as far as Translation Server's API is concerned.
    endpoint = "web" if urlsplit(value.strip()).scheme.lower() in {"http", "https"} else "search"
    url = f"{translator_url.rstrip('/')}/{endpoint}"
    try:
        response = httpx.post(
            url,
            content=value.strip(),
            headers={"Content-Type": "text/plain"},
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        raise ResolutionError(f"Translation Server is unavailable: {exc}") from exc

    if response.status_code == 300:
        raise ResolutionError(
            "Translation Server returned multiple choices; use a more specific input"
        )
    if response.is_error:
        detail = response.text.strip()
        suffix = f": {detail[:200]}" if detail else ""
        raise ResolutionError(
            f"Translation Server request failed with HTTP {response.status_code}{suffix}"
        )

    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise ResolutionError("Translation Server returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise ResolutionError("Translation Server returned malformed data (expected an array)")
    if any(not isinstance(item, dict) for item in payload):
        raise ResolutionError("Translation Server returned malformed item data")
    items = [
        item
        for item in payload
        if item.get("itemType") not in {"annotation", "attachment", "note"}
        and not item.get("parentItem")
    ]
    if len(items) != 1:
        if not items:
            raise ResolutionError("Translation Server found no matching bibliographic item")
        raise ResolutionError(
            f"Translation Server returned {len(items)} bibliographic items; "
            "use a more specific input"
        )

    try:
        return normalize_zotero_item(items[0], input_value=value)
    except ValueError as exc:
        raise ResolutionError(str(exc)) from exc
