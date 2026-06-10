"""
Fast page fetch for the MCP gateway — DARE's own web reader.

The agent runtime's built-in page extraction (Playwright) proved slow (minutes
on some pages), so DARE offers its own: a plain HTTP fetch with lightweight
text extraction (fast path), falling back to Gemini's url_context reader for
pages that block plain clients or need JS rendering. Exposed to agents through
the gateway as the builtin `fetch_page` tool.
"""

import logging
import re
from html.parser import HTMLParser

import httpx

from config import env

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 15.0
# Generous: a full paper should come through whole — truncating mid-paper
# degrades staging quality. Runaway cost is contained by the per-run budget
# (tool-call + wall-clock caps), not by chopping sources.
MAX_CHARS = 40_000
# Below this, the plain fetch likely hit a block/consent/JS shell — try the
# LLM-backed reader instead.
_MIN_USEFUL_CHARS = 500

GEMINI_FETCH_MODEL = "gemini-2.5-flash"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
}


class _TextExtractor(HTMLParser):
    """Strip tags, dropping script/style/chrome so only readable text remains."""

    _SKIP = {"script", "style", "noscript", "svg", "header", "footer", "nav"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def _extract_text(html):
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML; keep what was parsed
        pass
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts)).strip()


def _fetch_plain(url):
    with httpx.Client(
        timeout=FETCH_TIMEOUT, follow_redirects=True, headers=_HEADERS
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "html" in content_type:
            return _extract_text(resp.text)
        if "text" in content_type or "json" in content_type:
            return resp.text.strip()
        # Binary (PDF, …) — the plain path can't read it; the fallback can.
        return ""


def _fetch_via_gemini(url):
    """Read the page through Gemini's url_context (handles JS/blocked pages)."""
    api_key = env.GEMINI_API_KEY
    if not api_key:
        return ""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_FETCH_MODEL,
        contents=(
            "Return the full readable text content of this page, verbatim "
            f"where possible, with no commentary of your own: {url}"
        ),
        config=types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext())]
        ),
    )
    return (response.text or "").strip()


def fetch_page(url):
    """
    Given a URL, return clean readable text. Never raises — errors come back
    as a text message so the calling agent can react and move on.
    """
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return "Error: 'url' must be an http(s) URL."

    text = ""
    try:
        text = _fetch_plain(url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.info("fetch_page plain fetch failed for %s: %s", url, exc)

    if len(text) < _MIN_USEFUL_CHARS:
        try:
            fallback = _fetch_via_gemini(url)
            if len(fallback) > len(text):
                text = fallback
        except Exception as exc:  # noqa: BLE001 - best-effort fallback
            logger.warning("fetch_page Gemini fallback failed for %s: %s", url, exc)

    if not text:
        return f"Could not retrieve readable content from {url}."
    return text[:MAX_CHARS]
