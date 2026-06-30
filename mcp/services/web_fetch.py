"""
DARE's own web tools for the MCP gateway — search and read.

The agent runtime's built-in web tools route through a credit-gated runtime
gateway (and its page extraction proved slow — minutes on some pages). DARE's
chat already has fast, reliable equivalents on the Anthropic API: the native
``web_search`` and ``web_fetch`` server tools (the same ones the "Web Search" /
"Web Fetch" toggles drive in a normal conversation). The gateway exposes exactly
those, as the builtins ``web_search`` and ``fetch_page``, so a delegated agent
searches and reads the same way the chat does — DARE-owned, audited, and with no
dependency on the agent runtime's web tooling or its billing.

Failure is honest: when a page genuinely can't be retrieved (paywall, block,
robots, a fetch error), Anthropic returns a ``web_fetch_tool_error`` with an
``error_code`` — we raise on it rather than passing the model's polite refusal
back as if it were page content. The gateway turns the raised error into a tool
error, so the run's audit shows the call failed instead of a false success.
"""

import logging

from conversations.constants import Provider
from core.services.api_key_service import get_provider_api_key_sync

logger = logging.getLogger(__name__)

# A full paper should come through whole — truncating mid-paper degrades staging
# quality. Runaway cost is contained by the per-run budget, not by chopping.
MAX_CHARS = 40_000
# Cheap model to drive the server-side web tools; the results/text come from the
# server tool result, not the model's own knowledge.
WEB_FETCH_MODEL = "claude-haiku-4-5-20251001"
WEB_SEARCH_MODEL = "claude-haiku-4-5-20251001"
# How many results to hand back per search — enough to triage, not a flood.
MAX_SEARCH_RESULTS = 8
_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 1,
}
_BETA_HEADER = "web-fetch-2025-09-10"
_TOOL = {
    "type": "web_fetch_20250910",
    "name": "web_fetch",
    "max_uses": 1,
    "citations": {"enabled": False},
    "max_content_tokens": 50_000,
}


class FetchError(Exception):
    """A page genuinely could not be fetched (paywall, block, fetch error)."""


# Anthropic web_fetch error codes -> plain, honest reasons. The wording makes
# clear the failure is THIS page (paywall / block / 404), not a DARE or tool
# outage, so the agent reports it accurately instead of "the tool is down".
_FETCH_REASONS = {
    "url_not_accessible": "the page is blocked, paywalled, or refused the reader",
    "unavailable": "the page is unavailable (paywall, removed, or 404)",
    "too_many_requests": "the site rate-limited the reader",
    "max_uses_exceeded": "the page-fetch budget for this run was reached",
    "unsupported_content_type": "the page is not a readable document",
    "url_not_allowed": "the URL is not on the allowed list",
    "url_too_long": "the URL is too long to fetch",
    "invalid_input": "the URL was rejected as invalid",
}


def _fetch_reason(code):
    """A plain-language reason for an Anthropic web_fetch error code."""
    return _FETCH_REASONS.get(code, f"the reader could not retrieve it ({code})")


def _fetch_result(message):
    """The web_fetch tool-result block as a plain dict, or None. Dicts (via
    model_dump) navigate reliably across SDK versions; nested SDK attribute
    access does not."""
    for block in message.content:
        data = block.model_dump() if hasattr(block, "model_dump") else block
        if isinstance(data, dict) and data.get("type") == "web_fetch_tool_result":
            return data
    return None


def _pdf_text(b64_data):
    """Extract text from a base64 PDF the fetch tool returned (no model needed)."""
    import base64
    import io

    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(base64.b64decode(b64_data)))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def _document_text(result):
    """
    Readable text straight from the web_fetch_result document — the fast path.
    The server tool already fetched and parsed the page, so we read its text
    here instead of waiting for the model to re-emit it (which is slow and
    costly). Handles text pages directly and PDFs by local extraction.
    """
    source = ((result.get("content") or {}).get("content") or {}).get("source") or {}
    data = source.get("data")
    if not isinstance(data, str) or not data:
        return ""
    if source.get("type") == "text":
        return data.strip()
    if source.get("type") == "base64" and "pdf" in (source.get("media_type") or ""):
        try:
            return _pdf_text(data)
        except Exception as exc:  # noqa: BLE001 - unreadable PDF; report as a miss
            logger.info("fetch_page PDF extraction failed: %s", exc)
    return ""


def fetch_page(url):
    """
    Return a page's readable text via Anthropic's native ``web_fetch`` tool —
    DARE's chat reader. Raises ``FetchError`` when the page can't be retrieved,
    so the gateway reports a tool error instead of a false success.
    """
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise FetchError("'url' must be an http(s) URL.")

    api_key = get_provider_api_key_sync(Provider.CLAUDE.value)
    if not api_key:
        raise FetchError("No Anthropic API key configured for page fetch.")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=WEB_FETCH_MODEL,
            max_tokens=128,
            tools=[_TOOL],
            extra_headers={"anthropic-beta": _BETA_HEADER},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Use the web_fetch tool to retrieve this URL. Do not "
                        "summarise or repeat the page content — reply with only "
                        f"the word DONE: {url}"
                    ),
                }
            ],
        )
    except anthropic.APIError as exc:
        raise FetchError(f"Page fetch request failed: {exc}") from exc

    result = _fetch_result(message)
    if result is None:
        raise FetchError(f"The reader did not fetch {url}.")

    content = result.get("content") or {}
    if content.get("type") == "web_fetch_tool_error":
        # Anthropic's own failure signal — surface it as a typed, honest reason so
        # the agent reports "this page is paywalled/blocked", never "tool is down".
        code = content.get("error_code", "unavailable")
        raise FetchError(
            f"Could not read {url} — {_fetch_reason(code)} (this page only, not a "
            "tool or system error; try a different source)."
        )

    text = _document_text(result)
    if not text:
        raise FetchError(
            f"Reached {url} but found no readable text — likely a login wall or a "
            "script-only page (this page only, not a tool error)."
        )
    return text[:MAX_CHARS]


class SearchError(Exception):
    """A web search could not be completed (no API key, request failed, no hits)."""


def _search_results(message):
    """The web_search_tool_result block's result list. Raises SearchError on the
    tool's own error shape; returns [] when no result block is present."""
    for block in message.content:
        data = block.model_dump() if hasattr(block, "model_dump") else block
        if not (
            isinstance(data, dict) and data.get("type") == "web_search_tool_result"
        ):
            continue
        content = data.get("content")
        if isinstance(content, list):
            return content
        if (
            isinstance(content, dict)
            and content.get("type") == "web_search_tool_result_error"
        ):
            code = content.get("error_code", "unavailable")
            raise SearchError(f"Web search failed ({code}).")
    return []


def web_search(query, max_results=MAX_SEARCH_RESULTS):
    """
    Search the web via Anthropic's native ``web_search`` tool — DARE's chat
    searcher — and return a compact list of result links (title + URL + age),
    so a delegated agent can triage sources and then read the best ones with
    ``fetch_page``. Raises ``SearchError`` when the search can't be run, so the
    gateway reports an honest tool error instead of a false empty success.
    """
    if not isinstance(query, str) or not query.strip():
        raise SearchError("'query' must be a non-empty string.")

    api_key = get_provider_api_key_sync(Provider.CLAUDE.value)
    if not api_key:
        raise SearchError("No Anthropic API key configured for web search.")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=WEB_SEARCH_MODEL,
            max_tokens=128,
            tools=[_SEARCH_TOOL],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Use the web_search tool exactly once for the query "
                        "below. Do not analyse or summarise the results — reply "
                        f"with only the word DONE.\n\nQuery: {query.strip()}"
                    ),
                }
            ],
        )
    except anthropic.APIError as exc:
        raise SearchError(f"Web search request failed: {exc}") from exc

    lines = []
    for r in _search_results(message):
        if not (isinstance(r, dict) and r.get("type") == "web_search_result"):
            continue
        url = (r.get("url") or "").strip()
        if not url:
            continue
        title = (r.get("title") or "").strip() or url
        age = (r.get("page_age") or "").strip()
        lines.append(f"- {title}\n  {url}" + (f"  ({age})" if age else ""))
        if len(lines) >= max_results:
            break

    if not lines:
        raise SearchError(
            f"No web results for '{query.strip()}' (try different terms)."
        )
    return "\n".join(lines)
