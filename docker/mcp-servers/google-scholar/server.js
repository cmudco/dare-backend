import express from "express";
import axios from "axios";
import * as cheerio from "cheerio";
import { randomUUID } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  CallToolRequestSchema,
  InitializeRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const PORT = Number(process.env.PORT || 3015);
const MCP_ENDPOINT = "/mcp";
const SESSION_HEADER = "mcp-session-id";
const MIN_REQUEST_INTERVAL_MS = Number(process.env.MIN_REQUEST_INTERVAL_MS || 1500);
const SCHOLAR_TIMEOUT_MS = Number(process.env.SCHOLAR_TIMEOUT_MS || 15000);
const USER_AGENT = process.env.SCHOLAR_USER_AGENT ||
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36";

let nextRequestAt = 0;

const googleScholarTools = [
  {
    name: "search_google_scholar",
    description: "Search Google Scholar for academic papers. Experimental: this uses low-volume Scholar HTML search and may be rate-limited or blocked by Google.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Search query, using Google Scholar's normal query syntax.",
        },
        max_results: {
          type: "integer",
          minimum: 1,
          maximum: 10,
          default: 5,
          description: "Maximum number of results to return. Keep low to reduce blocking risk.",
        },
        year_from: {
          type: "integer",
          minimum: 1900,
          maximum: 2100,
          description: "Only include results from this year onward.",
        },
        year_to: {
          type: "integer",
          minimum: 1900,
          maximum: 2100,
          description: "Only include results through this year.",
        },
        author: {
          type: "string",
          description: "Optional author filter.",
        },
      },
      required: ["query"],
    },
  },
];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForRateLimitSlot() {
  const now = Date.now();
  if (now < nextRequestAt) {
    await sleep(nextRequestAt - now);
  }
  nextRequestAt = Date.now() + MIN_REQUEST_INTERVAL_MS;
}

function normalizeWhitespace(value) {
  return (value || "").replace(/\s+/g, " ").trim();
}

function parseYear(text) {
  const matches = normalizeWhitespace(text).match(/\b(19|20)\d{2}\b/g);
  if (!matches || matches.length === 0) {
    return null;
  }
  return Number(matches[matches.length - 1]);
}

function parseCitationCount($result) {
  const links = $result.find(".gs_fl a").toArray();
  for (const link of links) {
    const text = normalizeWhitespace($result.find(link).text());
    const match = text.match(/^Cited by\s+(\d+)/i);
    if (match) {
      return Number(match[1]);
    }
  }
  return null;
}

function parseAuthors(authorsRaw) {
  const beforeDash = authorsRaw.split(" - ")[0] || authorsRaw;
  return beforeDash
    .split(",")
    .map((author) => normalizeWhitespace(author))
    .filter(Boolean);
}

function buildScholarUrl({ query, maxResults, yearFrom, yearTo, author }) {
  const searchQuery = author ? `${query} author:"${author}"` : query;
  const params = new URLSearchParams({
    q: searchQuery,
    num: String(maxResults),
  });
  if (yearFrom) {
    params.set("as_ylo", String(yearFrom));
  }
  if (yearTo) {
    params.set("as_yhi", String(yearTo));
  }
  return `https://scholar.google.com/scholar?${params.toString()}`;
}

async function fetchScholarHtml(url) {
  await waitForRateLimitSlot();

  const response = await axios.get(url, {
    timeout: SCHOLAR_TIMEOUT_MS,
    validateStatus: () => true,
    headers: {
      "User-Agent": USER_AGENT,
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
      "Accept-Encoding": "gzip, deflate",
      "Connection": "keep-alive",
    },
  });

  if (response.status === 429 || response.status === 503) {
    throw new Error(`Google Scholar rate-limited the request with HTTP ${response.status}`);
  }
  if (response.status === 403) {
    throw new Error("Google Scholar blocked the request with HTTP 403");
  }
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`Google Scholar request failed with HTTP ${response.status}`);
  }

  return response.data;
}

function parseScholarResults(html, maxResults) {
  const $ = cheerio.load(html);
  const results = [];

  $(".gs_r.gs_or.gs_scl").each((_, element) => {
    if (results.length >= maxResults) {
      return false;
    }

    const $result = $(element);
    const $titleLink = $result.find(".gs_rt a").first();
    const title = normalizeWhitespace($titleLink.text() || $result.find(".gs_rt").text());
    if (!title) {
      return undefined;
    }

    const authorsRaw = normalizeWhitespace($result.find(".gs_a").text());
    const snippet = normalizeWhitespace($result.find(".gs_rs").text());

    results.push({
      title,
      authors_raw: authorsRaw || null,
      authors: parseAuthors(authorsRaw),
      year: parseYear(authorsRaw),
      citation_count: parseCitationCount($result),
      snippet: snippet || null,
      url: $titleLink.attr("href") || null,
      source: "google_scholar",
      fetched_at: new Date().toISOString(),
    });

    return undefined;
  });

  return results;
}

function validateSearchArgs(args) {
  const query = normalizeWhitespace(args?.query);
  if (!query) {
    throw new Error("query is required");
  }

  const maxResults = Math.min(Math.max(Number(args.max_results || 5), 1), 10);
  const yearFrom = args.year_from === undefined || args.year_from === null || args.year_from === ""
    ? null
    : Number(args.year_from);
  const yearTo = args.year_to === undefined || args.year_to === null || args.year_to === ""
    ? null
    : Number(args.year_to);

  if (yearFrom && yearTo && yearFrom > yearTo) {
    throw new Error("year_from cannot be greater than year_to");
  }

  return {
    query,
    maxResults,
    yearFrom,
    yearTo,
    author: normalizeWhitespace(args.author || "") || null,
  };
}

async function searchGoogleScholar(args) {
  const normalized = validateSearchArgs(args);
  const url = buildScholarUrl(normalized);
  const html = await fetchScholarHtml(url);
  const results = parseScholarResults(html, normalized.maxResults);

  return {
    query: normalized.query,
    filters: {
      author: normalized.author,
      year_from: normalized.yearFrom,
      year_to: normalized.yearTo,
    },
    total_results: results.length,
    source: "google_scholar",
    warnings: [
      "Experimental Google Scholar HTML search. Results may be incomplete, rate-limited, or blocked.",
      "Snippet is not guaranteed to be the full abstract.",
      "DOI and PDF URLs are not reliably available from Scholar search results.",
    ],
    results,
  };
}

class GoogleScholarMCPServer {
  constructor() {
    this.server = new Server(
      {
        name: "dare-google-scholar-mcp",
        version: "0.1.0",
      },
      {
        capabilities: {
          tools: {},
        },
      },
    );
    this.transports = {};
    this.setupHandlers();
  }

  setupHandlers() {
    this.server.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: googleScholarTools,
    }));

    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      const { name, arguments: args } = request.params;
      if (name !== "search_google_scholar") {
        throw new Error(`Unknown tool: ${name}`);
      }

      try {
        const response = await searchGoogleScholar(args || {});
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(response, null, 2),
            },
          ],
          structuredContent: response,
          isError: false,
        };
      } catch (error) {
        const response = {
          error: error instanceof Error ? error.message : String(error),
          source: "google_scholar",
          retryable: true,
        };
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(response, null, 2),
            },
          ],
          structuredContent: response,
          isError: true,
        };
      }
    });
  }

  async handlePostRequest(req, res) {
    const sessionId = req.headers[SESSION_HEADER];
    let transport;

    if (sessionId && this.transports[sessionId]) {
      transport = this.transports[sessionId];
      await transport.handleRequest(req, res, req.body);
      return;
    }

    if (!sessionId && this.isInitializeRequest(req.body)) {
      transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
      });
      await this.server.connect(transport);
      await transport.handleRequest(req, res, req.body);

      if (transport.sessionId) {
        this.transports[transport.sessionId] = transport;
      }
      return;
    }

    res.status(400).json({
      jsonrpc: "2.0",
      id: req.body?.id || randomUUID(),
      error: {
        code: -32000,
        message: "Bad Request: invalid session ID or method.",
      },
    });
  }

  async handleGetRequest(req, res) {
    const sessionId = req.headers[SESSION_HEADER];
    if (!sessionId || !this.transports[sessionId]) {
      res.status(400).json({
        jsonrpc: "2.0",
        id: randomUUID(),
        error: {
          code: -32000,
          message: "Bad Request: invalid session ID or method.",
        },
      });
      return;
    }

    await this.transports[sessionId].handleRequest(req, res);
  }

  isInitializeRequest(body) {
    if (Array.isArray(body)) {
      return body.some((item) => InitializeRequestSchema.safeParse(item).success);
    }
    return InitializeRequestSchema.safeParse(body).success;
  }

  async close() {
    await this.server.close();
  }
}

const app = express();
app.use(express.json({ limit: "1mb" }));

const mcpServer = new GoogleScholarMCPServer();

app.post(MCP_ENDPOINT, async (req, res) => {
  try {
    await mcpServer.handlePostRequest(req, res);
  } catch (error) {
    console.error("MCP POST failed", error);
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: "2.0",
        id: req.body?.id || randomUUID(),
        error: {
          code: -32603,
          message: "Internal server error",
        },
      });
    }
  }
});

app.get(MCP_ENDPOINT, async (req, res) => {
  try {
    await mcpServer.handleGetRequest(req, res);
  } catch (error) {
    console.error("MCP GET failed", error);
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: "2.0",
        id: randomUUID(),
        error: {
          code: -32603,
          message: "Internal server error",
        },
      });
    }
  }
});

app.get("/health", (_req, res) => {
  res.json({ ok: true, service: "dare-google-scholar-mcp" });
});

const httpServer = app.listen(PORT, () => {
  console.log(`DARE Google Scholar MCP listening on port ${PORT}`);
});

const keepAlive = setInterval(() => {}, 60_000);

process.on("SIGTERM", async () => {
  clearInterval(keepAlive);
  httpServer.close(async () => {
    await mcpServer.close();
    process.exit(0);
  });
});

process.on("SIGINT", async () => {
  clearInterval(keepAlive);
  httpServer.close(async () => {
    await mcpServer.close();
    process.exit(0);
  });
});
