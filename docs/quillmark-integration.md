# Quillmark Integration — CMU Document Generation

DARE chats can generate brand-compliant, typeset PDF documents (CMU letters,
memos, reports, one-pagers) through [quillmark-mcp](https://github.com/tonguetoquill/quillmark-mcp),
a Typst-based document renderer that speaks the Model Context Protocol. This
page explains the architecture, the seams it touches, and how to extend it.

## Architecture

```
Browser ── Socket.IO ──> DARE web ── streamable-HTTP MCP ──> quillmark-mcp
   ▲                        │                                    │
   │  artifact_created      │  fetches rendered PDF bytes        │  Typst render
   └── (base64 data URI) ◄──┘  over the compose network          ▼
                                                          cmu-quiver (templates)
```

- **quillmark-mcp** runs as a compose service (`docker-compose.yml`), built
  from the `quillmark-mcp/` git submodule. It loads document templates
  ("quills") from the in-repo `cmu-quiver/` directory, mounted read-only at
  `/quiver`. The browser never talks to it.
- The server is registered in DARE's MCP catalog by migration
  `mcp/0012_seed_quillmark_server` (slug `quillmark`, transport
  `streamable_http`, auth `none`, URL `http://quillmark-mcp:8080/mcp`). Users
  click **Connect** once on `/mcp` (no credentials) and select the server in
  the chat composer.
- The LLM drives three MCP tools: `list_quills` → `get_spec` (returns an
  instruction + field blueprint per template) → `create_document` (a
  `~~~card-yaml` block opening with `$quill: <name>@<version>` and
  `$kind: main`, then the markdown body).

## The result → artifact bridge

`mcp/services/artifact_bridge.py` — quillmark returns `{url, mimeType}` for a
rendered document, but the URL is compose-internal and served with
`Content-Disposition: attachment`, so it is useless to the browser. The bridge:

1. Detects a PDF result **generically** (any MCP tool whose
   `structuredContent` or `resource_link` carries `application/pdf` + a URL —
   future PDF-producing servers ride for free).
2. Fetches the bytes server-side (30 s timeout, 15 MB cap).
3. Stores them as a base64 data URI in `Artifact.content`
   (`ArtifactType.PDF`), so the artifact survives quillmark restarts, needs no
   auth to preview, and round-trips through the existing artifact APIs.
4. Versions per quill+conversation: regenerating the same template in the
   same chat produces a new version in the same `ArtifactGroup` (version
   dropdown in the sidecar).
5. Emits the standard `artifact_created` / `artifact_updated` websocket
   events — the frontend needs no new socket wiring.

The hook lives in `mcp/services/mcp_tool_handler.py::handle_tool_calls`; on a
bridged result the websocket payload and the LLM-facing result text are
rewritten so no dead internal URL ever reaches the user or the model. Bridge
failures degrade silently to the old text-only behavior.

## The agentic tool loop

`mcp_tool_handler.stream_tool_result_response` was a single follow-up call
that stripped all tools — the model could never chain `get_spec` →
`create_document`. It is now an agentic loop (same shape as the Claude Code
agent loop): each turn the model sees all tool results so far and either
requests more tool calls (executed and fed back) or produces the final text.

- Natural termination: a response with no tool calls ends the loop.
- Safety cap: `MAX_TOOL_ROUNDS = 6` tool-use turns; the final allowed turn
  strips `mcp_server_ids`, forcing synthesis.
- Self-correction: Typst render errors return as tool results with
  diagnostics, so the model fixes its frontmatter and retries.
- Regression-safe: with zero follow-up tool calls the behavior is identical
  to the previous implementation.

## Other seams

| Piece | Where |
|---|---|
| PDF artifact type | `conversations/constants.py` (`ArtifactType.PDF`), migration `0079` |
| PDF download | `conversations/api/views.py::_build_artifact_download_response` decodes the stored data URI (`?format=pdf`) |
| Quill catalog API | `GET /mcp/api/quillmark/quills/` (`mcp/api/views.py::QuillmarkQuillsView`), 10-min cache — feeds the composer's Documents picker |
| System prompt rules | `core/prompts/system_prompt.py` — document-flow guidance is injected when MCP tools are active |

## Adding a document template

Templates live in the `cmu-quiver/` directory (see its README for authoring). To
add one: drop a `quills/<name>/<x.y.z>/` directory (Quill.yaml + plate.typ +
example.md + vendored packages) and `docker compose restart quillmark-mcp`.
No DARE code changes. Two caches to know about:

- Quillmark discovers quills at startup → restart the service after edits.
- DARE caches MCP tool discovery in Redis (1 h) and the quill catalog
  (10 min) → for instant pickup: `docker compose exec redis redis-cli flushdb`
  (dev only — the DB is shared with channels/cache) or wait out the TTL.

## Verifying

```bash
# Standalone render of every template (bypasses DARE):
cmu-quiver/scripts/render-examples.sh             # uses the 127.0.0.1:8090 debug bind

# Network path from the web container:
docker compose exec web curl -s http://quillmark-mcp:8080/mcp -X POST \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_quills","arguments":{}}}'
```

End-to-end: connect the server on `/mcp`, select it in the composer, and ask
for "a CMU memo to the dean summarizing this conversation" — the PDF should
render inline in the artifact sidecar with a working download button.
