"""
Paper lookup through OpenAlex: search, DOI resolution and open-access copies.

OpenAlex is free, needs no key, and indexes journals, arXiv and repositories
together, so one client covers paper search, "does this DOI exist and what is
it", retraction status, and "where is a free copy of this paywalled paper".
"""

import logging
import re

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_API = "https://api.openalex.org"
_TIMEOUT = 10
_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
MAX_RESULTS = 8
_ABSTRACT_CHARS = 400


class ScholarlyError(Exception):
    """OpenAlex could not answer (network, rate limit, bad input)."""


def doi_in(text):
    """The DOI inside a URL or string, lower-cased, or ""."""
    match = _DOI_RE.search(text or "")
    return match.group(0).rstrip(".,;)").lower() if match else ""


def _get(path, params=None):
    params = dict(params or {})
    if settings.OPENALEX_MAILTO:
        params["mailto"] = settings.OPENALEX_MAILTO
    try:
        resp = httpx.get(f"{_API}{path}", params=params, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ScholarlyError(f"OpenAlex request failed: {exc}") from exc
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ScholarlyError(f"OpenAlex returned HTTP {resp.status_code}.")
    return resp.json()


def _abstract(work):
    index = work.get("abstract_inverted_index") or {}
    if not index:
        return ""
    words = sorted((pos, word) for word, spots in index.items() for pos in spots)
    return " ".join(word for _, word in words)


def open_access_url(work):
    """The best free full-text link OpenAlex knows for ``work``, or ""."""
    for location in (
        work.get("best_oa_location") or {},
        work.get("primary_location") or {},
    ):
        if location.get("is_oa"):
            url = location.get("pdf_url") or location.get("landing_page_url")
            if url:
                return url
    return (work.get("open_access") or {}).get("oa_url") or ""


def work_by_doi(doi):
    """OpenAlex's record for ``doi``, or None when it has no such work."""
    doi = doi_in(doi)
    if not doi:
        return None
    return _get(f"/works/doi:{doi}")


def _line(work):
    venue = ((work.get("primary_location") or {}).get("source") or {}).get(
        "display_name"
    ) or "?"
    authors = "; ".join(
        (a.get("author") or {}).get("display_name", "")
        for a in (work.get("authorships") or [])[:3]
    )
    doi = doi_in(work.get("doi") or "")
    free = open_access_url(work)
    parts = [
        f"- {work.get('display_name') or '?'} ({authors or '?'}, "
        f"{work.get('publication_year') or '?'}) | {venue} | "
        f"cited {work.get('cited_by_count') or 0}x"
    ]
    if work.get("is_retracted"):
        parts.append("  RETRACTED")
    if doi:
        parts.append(f"  DOI: {doi}  https://doi.org/{doi}")
    if free:
        parts.append(f"  free copy: {free}")
    abstract = _abstract(work)
    if abstract:
        parts.append(f"  abstract: {abstract[:_ABSTRACT_CHARS]}")
    return "\n".join(parts)


def search_papers(query, max_results=MAX_RESULTS):
    """Top papers for ``query`` as compact lines, most relevant first."""
    if not isinstance(query, str) or not query.strip():
        raise ScholarlyError("'query' must be a non-empty string.")
    data = _get(
        "/works",
        {
            "search": query.strip(),
            "per-page": max_results,
            "filter": "is_paratext:false",
        },
    )
    works = (data or {}).get("results") or []
    if not works:
        raise ScholarlyError(
            f"No papers found for '{query.strip()}' (try other terms)."
        )
    return "\n".join(_line(work) for work in works)
