# DARE Google Scholar MCP

Self-hosted Streamable HTTP MCP server for experimental Google Scholar discovery.

This is intentionally low-volume and scraping-based. Google Scholar may block or
rate-limit requests. Keep the service marked experimental in product surfaces.
When Google Scholar is blocked, the server can return an explicit OpenAlex
fallback response so research prompts still receive usable academic metadata.

## Endpoint

`/mcp`

## Tools

- `search_google_scholar`

Returns normalized search result snippets:

- `title`
- `authors_raw`
- `authors`
- `year`
- `citation_count`
- `abstract` (OpenAlex fallback only, when available)
- `snippet`
- `url`
- `doi` (OpenAlex fallback only, when available)
- `pdf_url` (OpenAlex fallback only, when available)
- `venue` (OpenAlex fallback only, when available)
- `source`
- `fetched_at`

Top-level response fields include `source`. When this is `openalex_fallback`,
the response also includes `google_scholar_error` and warnings that citation
counts are from OpenAlex rather than Google Scholar.

## Environment

- `PORT`: HTTP port, default `3015`
- `MIN_REQUEST_INTERVAL_MS`: per-process request spacing, default `1500`
- `SCHOLAR_TIMEOUT_MS`: outbound Scholar request timeout, default `15000`
- `SCHOLAR_USER_AGENT`: optional custom User-Agent
- `OPENALEX_FALLBACK_ENABLED`: return OpenAlex results after Scholar failures,
  default `true`
- `OPENALEX_TIMEOUT_MS`: outbound OpenAlex request timeout, default `15000`
- `OPENALEX_MAILTO`: optional email passed to OpenAlex polite-pool requests
