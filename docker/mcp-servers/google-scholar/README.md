# DARE Google Scholar MCP

Self-hosted Streamable HTTP MCP server for experimental Google Scholar discovery.

This is intentionally low-volume and scraping-based. Google Scholar may block or
rate-limit requests. Keep the service marked experimental in product surfaces.

## Endpoint

`/mcp`

## Tools

- `search_google_scholar`

Returns normalized Scholar search result snippets:

- `title`
- `authors_raw`
- `authors`
- `year`
- `citation_count`
- `snippet`
- `url`
- `source`
- `fetched_at`

## Environment

- `PORT`: HTTP port, default `3015`
- `MIN_REQUEST_INTERVAL_MS`: per-process request spacing, default `1500`
- `SCHOLAR_TIMEOUT_MS`: outbound Scholar request timeout, default `15000`
- `SCHOLAR_USER_AGENT`: optional custom User-Agent
