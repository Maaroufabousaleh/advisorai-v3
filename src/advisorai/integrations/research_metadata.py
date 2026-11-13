"""Keyless citation discovery adapters for OpenAlex and Crossref.

These adapters retrieve bibliographic metadata only. They intentionally do not
fetch abstracts, full text, or make a paper's claims execution evidence.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, Request, build_opener

from advisorai.research.cards import PaperCard

OPENALEX_ENDPOINT = "https://api.openalex.org/works"
CROSSREF_ENDPOINT = "https://api.crossref.org/works"


@dataclass(frozen=True, slots=True)
class CitationSearchResult:
    provider: str
    query: str
    retrieved_at: datetime
    cards: tuple[PaperCard, ...]


def _public_json(url: str, timeout: float = 15.0) -> Mapping[str, object]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "AdvisorAI-V3 research (citation metadata only)"})
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read()
    except (HTTPError, OSError, TimeoutError) as exc:
        raise RuntimeError("public citation provider request failed") from None
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("public citation provider response was invalid") from None
    if not isinstance(data, Mapping):
        raise RuntimeError("public citation provider response was invalid")
    return data


class OpenAlexWorksClient:
    """Search OpenAlex works without an API key; persist citation-only cards."""

    provider = "openalex"

    def __init__(self, requester: Callable[[str], Mapping[str, object]] | None = None) -> None:
        self._requester = requester or _public_json

    def search(self, query: str, *, limit: int = 10) -> CitationSearchResult:
        _validate_search(query, limit)
        url = f"{OPENALEX_ENDPOINT}?{urlencode({'search': query, 'per-page': limit, 'select': 'id,doi,title,publication_year,authorships'})}"
        data = self._requester(url)
        rows = data.get("results")
        if not isinstance(rows, list):
            raise RuntimeError("OpenAlex response schema was invalid")
        cards = tuple(card for row in rows if isinstance(row, Mapping) if (card := _openalex_card(row)))
        return CitationSearchResult(self.provider, query, datetime.now(UTC), cards)


class CrossrefWorksClient:
    """Search Crossref works and retain DOI/source provenance only."""

    provider = "crossref"

    def __init__(self, requester: Callable[[str], Mapping[str, object]] | None = None) -> None:
        self._requester = requester or _public_json

    def search(self, query: str, *, limit: int = 10) -> CitationSearchResult:
        _validate_search(query, limit)
        url = f"{CROSSREF_ENDPOINT}?{urlencode({'query.bibliographic': query, 'rows': limit, 'select': 'DOI,title,author,published-print,published-online'})}"
        data = self._requester(url)
        message = data.get("message")
        rows = message.get("items") if isinstance(message, Mapping) else None
        if not isinstance(rows, list):
            raise RuntimeError("Crossref response schema was invalid")
        cards = tuple(card for row in rows if isinstance(row, Mapping) if (card := _crossref_card(row)))
        return CitationSearchResult(self.provider, query, datetime.now(UTC), cards)


def _validate_search(query: str, limit: int) -> None:
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("citation search query must contain 1 to 500 characters")
    if isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("citation result limit must be between 1 and 50")


def _authors(items: object, *, openalex: bool = False) -> tuple[str, ...]:
    if not isinstance(items, list):
        return ()
    result: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        author = item.get("author") if openalex else item
        name = author.get("display_name") if openalex and isinstance(author, Mapping) else (
            author.get("family") if isinstance(author, Mapping) else None
        )
        if isinstance(name, str) and name.strip():
            result.append(name.strip())
    return tuple(result[:30])


def _openalex_card(row: Mapping[str, object]) -> PaperCard | None:
    title, identifier = row.get("title"), row.get("id")
    if not isinstance(title, str) or not title.strip() or not isinstance(identifier, str) or not identifier.startswith("https://openalex.org/W"):
        return None
    year = row.get("publication_year")
    return PaperCard(
        source_url=identifier,
        authors=_authors(row.get("authorships"), openalex=True),
        publication_date=str(year) if year else "unknown",
        claimed_result=title.strip(),
        limitations=("Citation metadata only; verify primary paper before relying on claims.",),
        relevance="OpenAlex works search result",
    )


def _crossref_card(row: Mapping[str, object]) -> PaperCard | None:
    doi = row.get("DOI")
    titles = row.get("title")
    if not isinstance(doi, str) or not doi.strip() or not isinstance(titles, list) or not titles or not isinstance(titles[0], str):
        return None
    date = row.get("published-print") or row.get("published-online")
    parts = date.get("date-parts") if isinstance(date, Mapping) else None
    first = parts[0] if isinstance(parts, list) and parts else None
    year = first[0] if isinstance(first, list) and first else None
    return PaperCard(
        source_url=f"https://doi.org/{doi.strip()}",
        authors=_authors(row.get("author")),
        publication_date=str(year) if year else "unknown",
        claimed_result=titles[0].strip(),
        limitations=("Citation metadata only; verify primary paper before relying on claims.",),
        relevance="Crossref works search result",
    )


__all__ = ["CitationSearchResult", "CrossrefWorksClient", "OpenAlexWorksClient"]
