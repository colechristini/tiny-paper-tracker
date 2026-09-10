"""Normalization helpers for Zotero Translation Server results."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from .models import ResolvedItem

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV_RE = re.compile(r"^(?:[a-z][a-z.\-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)
_PMID_RE = re.compile(r"^\d{1,9}$")
_ISBN_RE = re.compile(r"^(?:97[89])?\d{9}[\dXx]$")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m",
    "%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
)


def _doi(value: str) -> str | None:
    candidate = value.strip()
    candidate = re.sub(r"^doi:\s*", "", candidate, flags=re.IGNORECASE)
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() in {"http", "https"} and parsed.hostname in {
        "doi.org",
        "dx.doi.org",
    }:
        candidate = parsed.path.lstrip("/")
    candidate = unquote(candidate).strip()
    return candidate.lower() if _DOI_RE.fullmatch(candidate) else None


def _arxiv(value: str) -> str | None:
    candidate = value.strip()
    candidate = re.sub(r"^arxiv:\s*", "", candidate, flags=re.IGNORECASE)
    match = re.match(
        r"^https?://(?:(?:www|export)\.)?arxiv\.org/(?:abs|pdf)/([^?#]+)", candidate, re.IGNORECASE
    )
    if match:
        candidate = unquote(match.group(1))
    candidate = re.sub(r"\.pdf$", "", candidate, flags=re.IGNORECASE).strip("/")
    if not _ARXIV_RE.fullmatch(candidate):
        return None
    return re.sub(r"v\d+$", "", candidate, flags=re.IGNORECASE).lower()


def _pmid(value: str, *, explicit_only: bool = True) -> str | None:
    candidate = value.strip()
    explicit = bool(re.match(r"^pmid:\s*", candidate, re.IGNORECASE))
    candidate = re.sub(r"^pmid:\s*", "", candidate, flags=re.IGNORECASE)
    match = re.match(
        r"^https?://(?:www\.)?pubmed\.ncbi\.nlm\.nih\.gov/(\d+)/?", candidate, re.IGNORECASE
    )
    if match:
        return match.group(1)
    if (explicit or not explicit_only) and _PMID_RE.fullmatch(candidate):
        return candidate
    return None


def _isbn(value: str, *, explicit_only: bool = True) -> str | None:
    candidate = value.strip()
    explicit = bool(re.match(r"^isbn(?:-1[03])?:\s*", candidate, re.IGNORECASE))
    candidate = re.sub(r"^isbn(?:-1[03])?:\s*", "", candidate, flags=re.IGNORECASE)
    compact = re.sub(r"[\s-]", "", candidate)
    if (explicit or not explicit_only) and _ISBN_RE.fullmatch(compact):
        compact = compact.upper()
        if len(compact) == 10:
            digits = [10 if char == "X" else int(char) for char in compact]
            valid = sum((10 - index) * digit for index, digit in enumerate(digits)) % 11 == 0
        else:
            digits = [int(char) for char in compact]
            valid = (
                sum(digit * (1 if index % 2 == 0 else 3) for index, digit in enumerate(digits)) % 10
                == 0
            )
        if valid:
            return compact
    return None


def normalize_url(value: str) -> str:
    """Return a stable HTTP(S) URL, dropping fragments and normalizing its host."""

    raw = value.strip()
    if any(character.isspace() or ord(character) < 32 for character in raw):
        raise ValueError(f"Unsupported URL: {value!r}")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Unsupported URL: {value!r}")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not supported")
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def input_identifiers(value: str) -> list[tuple[str, str]]:
    """Validate an input and return canonical aliases suitable for a DB prelookup.

    Bare all-numeric identifiers are intentionally rejected because a number alone
    is ambiguous between PMID and ISBN. Prefix either form explicitly.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a non-empty URL, DOI, arXiv ID, PMID, or ISBN")
    raw = value.strip()

    doi = _doi(raw)
    if doi:
        return [("doi", doi), ("url", f"https://doi.org/{doi}")]

    arxiv = _arxiv(raw)
    if arxiv:
        return [("arxiv", arxiv), ("url", f"https://arxiv.org/abs/{arxiv}")]

    pmid = _pmid(raw)
    if pmid:
        return [("pmid", pmid), ("url", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")]

    isbn = _isbn(raw)
    if isbn:
        return [("isbn", isbn)]

    if re.match(r"^https?://", raw, re.IGNORECASE):
        url = normalize_url(raw)
        # Recognize resolver URLs even when their exact spelling varies.
        for extractor, kind, canonical in (
            (_doi, "doi", lambda x: f"https://doi.org/{x}"),
            (_arxiv, "arxiv", lambda x: f"https://arxiv.org/abs/{x}"),
            (_pmid, "pmid", lambda x: f"https://pubmed.ncbi.nlm.nih.gov/{x}/"),
        ):
            identifier = extractor(raw)  # type: ignore[call-arg]
            if identifier:
                return [(kind, identifier), ("url", canonical(identifier))]
        return [("url", url)]

    raise ValueError(
        "Unsupported input. Use an http(s) URL or prefix an identifier with "
        "doi:, arxiv:, pmid:, or isbn:."
    )


def _unique(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in values:
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def normalize_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    date = value.strip()
    # Zotero sometimes includes a timestamp or a range. Normalize only the
    # unambiguous date portion and preserve less structured values verbatim.
    iso = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T ]|$)", date)
    if iso:
        try:
            return datetime.strptime(iso.group(1), "%Y-%m-%d").date().isoformat()  # noqa: DTZ007
        except ValueError:
            return date
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(date, fmt)  # noqa: DTZ007
        except ValueError:
            continue
        if fmt == "%Y":
            return f"{parsed.year:04d}"
        if fmt == "%Y-%m":
            return f"{parsed.year:04d}-{parsed.month:02d}"
        return parsed.date().isoformat()
    return date


_KINDS = {
    "journalArticle": "paper",
    "conferencePaper": "paper",
    "preprint": "paper",
    "magazineArticle": "article",
    "newspaperArticle": "article",
    "book": "book",
    "bookSection": "book",
    "blogPost": "blog",
    "forumPost": "forum-post",
    "videoRecording": "video",
    "audioRecording": "audio",
    "webpage": "webpage",
}


def normalize_zotero_item(item: dict[str, Any], *, input_value: str | None = None) -> ResolvedItem:
    """Convert one Zotero API JSON item to the application's ingestion contract."""

    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Translation Server item is missing a title")

    aliases: list[tuple[str, str]] = []
    if input_value is not None:
        aliases.extend(input_identifiers(input_value))

    doi = _doi(str(item.get("DOI", "")))
    if doi:
        aliases.extend((("doi", doi), ("url", f"https://doi.org/{doi}")))
        arxiv_doi = re.fullmatch(r"10\.48550/arxiv\.(.+)", doi, re.IGNORECASE)
        if arxiv_doi and (arxiv := _arxiv(arxiv_doi.group(1))):
            aliases.extend((("arxiv", arxiv), ("url", f"https://arxiv.org/abs/{arxiv}")))

    for key in ("arXiv", "arxiv", "archiveID", "archiveLocation"):
        arxiv = _arxiv(str(item.get(key, "")))
        if arxiv:
            aliases.extend((("arxiv", arxiv), ("url", f"https://arxiv.org/abs/{arxiv}")))
    for key in ("PMID", "pmid"):
        pmid = _pmid(f"pmid:{item.get(key, '')}")
        if pmid:
            aliases.extend((("pmid", pmid), ("url", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")))

    for raw_isbn in re.split(r"[,;]", str(item.get("ISBN", ""))):
        isbn = _isbn(raw_isbn, explicit_only=False)
        if isbn:
            aliases.append(("isbn", isbn))

    extra = str(item.get("extra", ""))
    for line in extra.splitlines():
        arxiv = _arxiv(line)
        if arxiv:
            aliases.extend((("arxiv", arxiv), ("url", f"https://arxiv.org/abs/{arxiv}")))
        pmid = _pmid(line)
        if pmid:
            aliases.extend((("pmid", pmid), ("url", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")))
        extra_doi = _doi(line)
        if extra_doi:
            aliases.extend((("doi", extra_doi), ("url", f"https://doi.org/{extra_doi}")))

    raw_url = item.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        try:
            aliases.extend(input_identifiers(raw_url))
        except ValueError:
            pass

    aliases = _unique(aliases)
    url_alias = next((v for k, v in aliases if k == "url"), None)
    if not url_alias:
        isbn = next((v for k, v in aliases if k == "isbn"), None)
        if isbn:
            url_alias = f"https://www.worldcat.org/isbn/{isbn}"
            aliases.append(("url", url_alias))
    if not url_alias:
        raise ValueError("Translation Server item is missing a usable URL or identifier")

    authors: list[str] = []
    creators = item.get("creators", [])
    if isinstance(creators, list):
        for creator in creators:
            if not isinstance(creator, dict) or creator.get("creatorType") not in {
                "author",
                "bookAuthor",
            }:
                continue
            name = creator.get("name")
            if not isinstance(name, str) or not name.strip():
                parts = (creator.get("firstName"), creator.get("lastName"))
                name = " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
            if name:
                authors.append(name.strip())

    venue = item.get("publicationTitle") or item.get("conferenceName") or item.get("publisher")
    venue = venue.strip() if isinstance(venue, str) and venue.strip() else None
    raw_kind = item.get("itemType", "webpage")
    kind = _KINDS.get(raw_kind, raw_kind) if isinstance(raw_kind, str) else "webpage"

    return ResolvedItem(
        title=title.strip(),
        url=url_alias,
        kind=kind,
        authors=authors,
        venue=venue,
        published_at=normalize_date(item.get("date")),
        metadata=dict(item),
        identifiers=aliases,
    )
