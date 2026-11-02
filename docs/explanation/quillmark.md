# Quillmark integration

This page shows how chat generates CMU documents, which code it touches, and how to add a template.
DARE chats produce brand-compliant, typeset PDFs (CMU letters, memos, reports, one-pagers) through
[quillmark-mcp](https://github.com/tonguetoquill/quillmark-mcp), a Typst-based document renderer
that speaks the Model Context Protocol.

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
- Migration `mcp/0012_seed_quillmark_server` registers the server in DARE's MCP catalog (slug
  `quillmark`, transport `streamable_http`, auth `none`, URL `http://quillmark-mcp:8080/mcp`).
  Set the deployment-specific URL with `manage.py configure_quillmark`. Users then click
  **Connect** once on `/mcp` (no credentials) and select the server in the chat composer.
- The LLM drives three MCP tools: `list_quills` → `get_spec` (returns an
  instruction + field blueprint per template) → `create_document` (a
  `~~~card-yaml` block opening with `$quill: <name>@<version>` and
  `$kind: main`, then the markdown body).

## The result → artifact bridge

Quillmark returns `{url, mimeType}` for a rendered document, but the URL is compose-internal and
served with `Content-Disposition: attachment`, so the browser cannot use it.
`mcp/services/artifact_bridge.py` fixes this:

1. Detects a PDF result **generically**: any MCP tool whose `structuredContent` or
   `resource_link` carries `application/pdf` plus a URL. Other PDF-producing servers work
   without extra code.
2. Fetches the bytes server-side only from the configured MCP server's origin,
   without redirects, using a streamed 30 s request and a 15 MB cap. It checks
   both the response MIME type and PDF file signature.
3. Stores them as a base64 data URI in `Artifact.content`
   (`ArtifactType.PDF`), so the artifact survives quillmark restarts, needs no
   auth to preview, and round-trips through the existing artifact APIs.
4. Versions per quill+conversation: regenerating the same template in the
   same chat produces a new version in the same `ArtifactGroup` (version
   dropdown in the sidecar).
5. Emits the standard `artifact_created` / `artifact_updated` websocket events, so the frontend
   needs no new socket wiring.

The hook lives in `conversations/services/tool_execution_service.py`. On a bridged result it
rewrites the websocket payload and the LLM-facing result text, so no dead internal URL reaches the
user or the model. Import failures become explicit tool errors, so the model and UI report them
instead of claiming a document is ready.

## The tool loop

Chat runs a bounded multi-round tool loop (`conversations/services/tool_loop_service.py`, see
[Tool loop](tool-loop.md)), so the model can chain `list_quills` → `get_spec` →
`create_document`. Each round the model sees all tool results so far and either requests more
tool calls or writes the final text.

- **Natural end.** A response with no tool calls ends the loop.
- **Safety cap.** After `MAX_TOOL_ROUNDS` (5, in `conversations/constants.py`) rounds, the next
  call runs with tools stripped, forcing a text answer.
- **Self-correction.** Typst render errors come back as tool results with diagnostics, so the
  model fixes its frontmatter and retries.

## Other seams

| Piece | Where |
|---|---|
| PDF artifact type | `conversations/constants.py` (`ArtifactType.PDF`), migration `0088` |
| PDF download | `conversations/api/views.py::_build_artifact_download_response` decodes the stored data URI (`?format=pdf`) |
| Quill catalog API | `GET /mcp/api/quillmark/quills/` (`mcp/api/views.py::QuillmarkQuillsView`) calls `list_quills` through the authenticated MCP connection and feeds the optional Documents picker |
| Agent instructions | Advertised by Quillmark's server instructions and tool descriptions; `get_spec` returns the template-specific contract |

## Adding a document template

Templates live in the `cmu-quiver/` directory (its README covers authoring). To add one, drop a
`quills/<name>/<x.y.z>/` directory (Quill.yaml + plate.typ + example.md + vendored packages) and
run `docker compose restart quillmark-mcp`. No DARE code changes. Two caches to know about:

- Quillmark discovers quills at startup, so restart the service after edits.
- DARE may cache MCP tool discovery in Redis. If a changed tool schema is not picked up,
  reconnect the server or clear the development cache.

## Verify

```bash
# Standalone render of every template (bypasses DARE):
cmu-quiver/scripts/render-examples.sh             # uses the 127.0.0.1:8090 debug bind

# Network path from the web container:
docker compose exec web curl -s http://quillmark-mcp:8080/mcp -X POST \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_quills","arguments":{}}}'
```

End to end: connect the server on `/mcp`, select it in the composer, and ask for "a CMU memo to
the dean summarizing this conversation". The PDF renders inline in the artifact sidecar with a
working download button.
