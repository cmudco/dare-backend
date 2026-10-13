"""
Checking a Scout's source before it reaches the Review Inbox.

A finding is refused when it can't be opened or its DOI belongs to a different
paper; those would waste the user's review. Anything softer (a paywall, a quote
that isn't on the page, a retraction, an old date) is staged with a flag the
user sees, because the Scout may still be right.
"""

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from django.utils import timezone

from mcp.models import GatewayFetch
from mcp.services.scholarly import ScholarlyError, doi_in, work_by_doi
from mcp.services.web_fetch import FetchError, PageUnreadable, fetch_page
from research.constants import ProjectKind

logger = logging.getLogger(__name__)

_TITLE_MATCH = 0.6
_QUOTE_SPAN = 6
_QUOTE_MATCH = 0.5


@dataclass
class SourceCheck:
    url: str = ""
    refused: str = ""
    flags: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def quote_on_page(quote, page):
    """Whether most of ``quote`` appears on ``page``.

    Page text breaks lines, drops symbols and inlines citations, so an exact
    match flags real quotes. Counting how many of the quote's six-word spans
    occur tolerates that; an invented quote shares almost none.
    """
    words, text = _norm(quote).split(), _norm(page)
    if len(words) <= _QUOTE_SPAN:
        return " ".join(words) in text
    spans = [
        " ".join(words[i : i + _QUOTE_SPAN])
        for i in range(len(words) - _QUOTE_SPAN + 1)
    ]
    return sum(span in text for span in spans) / len(spans) >= _QUOTE_MATCH


def _page_text(run, url, check):
    read = (
        GatewayFetch.all_objects.filter(user=run.user, url=url, error="")
        .exclude(content="")
        .order_by("-id")
        .first()
    )
    if read:
        check.checks["read"] = True
        return read.content
    try:
        text = fetch_page(url)
    except PageUnreadable as exc:
        message = str(exc)
        if "not found (HTTP 404" in message or "(HTTP 410" in message:
            check.refused = "the link doesn't work (page not found)"
        else:
            check.flags.append("Couldn't open the page to check it")
        return ""
    except FetchError:
        logger.warning("Source check could not fetch %s", url, exc_info=True)
        check.flags.append("Page not checked")
        return ""
    check.checks["read"] = True
    return text


def _check_doi(doi, title, check):
    try:
        work = work_by_doi(doi)
    except ScholarlyError:
        check.flags.append("DOI not checked")
        return None
    if work is None:
        check.flags.append("DOI not found")
        return None
    known = work.get("display_name") or ""
    if (
        title
        and known
        and SequenceMatcher(None, _norm(title), _norm(known)).ratio() < _TITLE_MATCH
    ):
        check.refused = f"that DOI belongs to a different paper (“{known[:120]}”)"
        return work
    check.checks["doiMatches"] = True
    if work.get("is_retracted"):
        check.flags.append("Retracted")
    return work


def check_source(run, *, title, url, doi, year, quote):
    """Check one candidate; ``check.refused`` set means don't stage it."""
    check = SourceCheck(url=url)
    doi = doi_in(doi) or doi_in(url)
    if not check.url and doi:
        check.url = f"https://doi.org/{doi}"
    if not check.url:
        check.refused = "it has no link the user can open"
        return check

    work = _check_doi(doi, title, check) if doi else None
    if check.refused:
        return check
    if work and not year:
        year = work.get("publication_year")
    check.checks["year"] = year

    text = _page_text(run, check.url, check)
    if check.refused:
        return check
    if quote and text:
        found = quote_on_page(quote, text)
        check.checks["quoteFound"] = found
        if not found:
            check.flags.append("Quoted line isn't on the page")

    months = ProjectKind.config(run.project.kind)["fresh_months"]
    if months and isinstance(year, int):
        age = (timezone.now().year - year) * 12
        if age > months:
            check.flags.append(f"Older source ({year}): check it's still current")
    return check
