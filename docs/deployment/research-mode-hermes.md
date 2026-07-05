# Deploying Research Mode — the Hermes agent runtime

Research Mode delegates long-running research work (Scout / Critic / Presenter)
to **Hermes Agent** (Nous Research) over its REST API. This guide takes a server
from zero to a working Research Mode backend.

**The architecture invariant:** Hermes drives; DARE writes. Hermes never gets
database access — it returns structured results over its API, DARE persists
them, and the scholar gates everything durable.

```
DARE backend ── POST /v1/runs (bearer) ──▶ Hermes gateway (:8642)
     ▲                                          │
     │  persists (sole writer)                  │ MCP reads (tools)
     ▼                                          ▼
  Postgres ◀── staging → approved      DARE MCP gateway (/mcp/api/gateway/)
                                        Scite · Consensus · fetch_page
```

---

## 1. Install Hermes on the server

```bash
# Per Hermes docs (hermes-agent.nousresearch.com/docs) — uv-managed Python pkg
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | sh
hermes setup            # non-interactive servers: see config.yaml below
```

Hermes home is `~/.hermes/`. The always-on piece is the **gateway service**
(`hermes gateway install && hermes gateway start`) which serves the REST API.

## 2. Configure the brain (the LLM)

Edit `~/.hermes/config.yaml`:

```yaml
model:
  default: <model-id>          # e.g. claude-sonnet-5
  provider: anthropic          # or another supported provider
agent:
  max_turns: 40                # loop cap — part of the cost containment
  reasoning_effort: ''         # see note below (required for Sonnet 5 and newer)
```

> ⚠️ **Sonnet 5 / newer models + extended thinking.** With `reasoning_effort`
> set (its default is `medium`), this Hermes build sends the legacy
> `thinking.type.enabled` request field, which Sonnet 5 rejects with
> `HTTP 400 "thinking.type.enabled is not supported for this model"` — the run
> fails before its first turn. Set `agent.reasoning_effort: ''` to disable
> extended thinking until Hermes adopts the adaptive-thinking API
> (`thinking.type.adaptive` + `output_config.effort`).

Add the credential (client-paid API key — **not** a consumer subscription;
Anthropic blocks subscription OAuth outside official clients):

```bash
hermes auth add anthropic --type api-key --api-key "$ANTHROPIC_API_KEY"
```

## 3. Enable the API server

In `~/.hermes/.env`:

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=<strong-random-key>        # DARE authenticates with this
MCP_DARE_API_KEY=<dare-service-token>     # Hermes→DARE gateway auth (step 5)
```

The API listens on `127.0.0.1:8642`. Keep it loopback-only (DARE and Hermes
co-located) or front it with TLS + network policy — the bearer key grants the
agent's full toolset.

## 4. Scope the toolset (security — do not skip)

The API platform must not expose host execution to a research agent:

```bash
hermes tools disable --platform api_server \
  terminal code_execution file browser delegation cronjob image_gen
```

Expected remaining set: `web`, `vision`, `skills`, `todo`, `memory`,
`session_search`.

## 5. Connect Hermes to DARE's MCP gateway

The live gateway exposes only DARE-owned, credential-free builtins —
`web_search` and `fetch_page`. The scholar's **credentialed** tools (Scite,
Consensus) are deliberately **not** exposed here: Hermes forwards no per-user
identity, so DARE runs those server-side under the project owner and injects
their results into the run input. Credentials and audit stay in DARE.

```bash
hermes mcp add dare --url https://<dare-host>/mcp/api/gateway/ \
  --header "Authorization: Bearer ${MCP_DARE_API_KEY}"
```

Then pin the tool allowlist in `~/.hermes/config.yaml` so the agent is offered
exactly the two working builtins:

```yaml
mcp_servers:
  dare:
    url: https://<dare-host>/mcp/api/gateway/
    headers:
      Authorization: Bearer ${MCP_DARE_API_KEY}
    tools:
      include:
        - web_search
        - fetch_page
    enabled: true
```

> ⚠️ **Both entries matter.** A missing `web_search` silently forces the agent
> to *guess* article URLs instead of searching (→ hallucinated DOIs, mass fetch
> failures). And do **not** list credentialed tools like `consensus__search` —
> the live gateway refuses them, so every such call just wastes a turn.

Mint the service token (a long-lived JWT for the service user; a dedicated
service-key auth class is the planned replacement):

```bash
python manage.py shell -c "
from datetime import timedelta
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken
t = AccessToken.for_user(get_user_model().objects.get(email='<service-user>'))
t.set_exp(lifetime=timedelta(days=365))
print(t)"
```

> ⚠️ **After adding or changing gateway tools, run `hermes gateway restart`** —
> Hermes caches the MCP tool list per connection.

### 5.1 Deterministic audit attribution — Hermes runtime patches

DARE talks to Hermes over **two channels**: the per-run SSE **control stream**
(`tool.completed` events — names + timing, no result body) and the shared **MCP
gateway** (where DARE runs `fetch_page`/`web_search` and stores the full result
in `GatewayFetch` — but with no run id). To show *which* result belongs to
*which* streamed call, the audit needs a shared key on both sides.

DARE (this repo) already carries its half: it reads a per-call id from MCP
`_meta` and stores it as `GatewayFetch.call_id`, and joins the stream event to
the corpus row on that id (falling back to an in-order/time-window match when
absent — so **these patches are optional but recommended**; without them the
audit still works, just fuzzily, and can blank or mis-attribute rows for
re-fetched URLs or concurrent runs).

To make it exact, patch the **Hermes clone** to forward each call's `tool_use`
id on both channels (kept minimal; ideally upstreamed — a package reinstall
overwrites them):

1. **`tools/mcp_tool.py`** — send the id on the MCP call (in the `_call()`
   coroutine that wraps `session.call_tool`):
   ```python
   from tools.approval import _approval_tool_call_id
   _cid = _approval_tool_call_id.get("")
   result = await server.session.call_tool(
       tool_name, arguments=args, meta=({"dareCall": _cid} if _cid else None)
   )
   ```
2. **`agent/tool_executor.py`** — pass the id on **both** `tool.completed`
   progress-callback sites (the sequential path and the parallel path — the
   parallel one is the MCP dispatch and is easy to miss):
   ```python
   agent.tool_progress_callback(
       "tool.completed", function_name, None, None,
       duration=..., is_error=..., result=...,
       tool_call_id=getattr(tc, "id", "") or "",          # sequential path
       # tool_call_id=getattr(tool_call, "id", "") or "",  # parallel path var
   )
   ```
3. **`gateway/platforms/api_server.py`** — put it on the streamed event:
   ```python
   elif event_type == "tool.completed":
       _push({..., "error": kwargs.get("is_error", False),
              "toolCallId": kwargs.get("tool_call_id", "")})
   ```

Then `hermes gateway restart`. Verify by confirming a Scout's
`GatewayFetch.call_id` equals the `toolCallId` on its stream event — the audit
should then show each call's own result/reason with no blank rows.

## 6. DARE backend settings

In the DARE environment:

```bash
HERMES_GATEWAY_URL=http://127.0.0.1:8642
HERMES_API_KEY=<API_SERVER_KEY from step 3>
HERMES_SYNC_SOUL=true                       # provision SOUL.md per run
HERMES_SOUL_PATH=/home/<user>/.hermes/SOUL.md
GEMINI_API_KEY=...                          # fetch_page fallback reader (optional)
```

Apply migrations first — the audit-attribution work adds
`GatewayFetch.run_key` and `.call_id` (see §5.1):

```bash
python manage.py migrate
```

Run the stack: ASGI server (`uvicorn dare.asgi:application --workers N`) + Redis
+ **django-rq workers** (delegated runs execute on the `default` queue):

```bash
python manage.py rqworker default            # Linux
# macOS dev only: add --worker-class rq.SimpleWorker
```

> ⚠️ **Redis is required as the shared cache, not just for RQ/Channels.** The
> ASGI server runs multiple worker processes; the Django default `LocMemCache`
> is per-process, so anything cached on one request (MCP OAuth PKCE state,
> session data) is invisible to the next request on another worker. `CACHES` in
> `config/settings/common.py` is a `RedisCache` reusing the same `REDIS_*` env
> as Channels/RQ — point them all at one Redis instance. (Because that Redis DB
> is shared, never call `cache.clear()` — it FLUSHDBs the whole DB.)

## 7. Smoke test

```bash
# 1. Hermes up?
curl -s -H "Authorization: Bearer $API_SERVER_KEY" http://127.0.0.1:8642/v1/models

# 2. Gateway reachable from Hermes? (fetch_page round-trip through the agent)
curl -s -X POST http://127.0.0.1:8642/v1/runs \
  -H "Authorization: Bearer $API_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"input":"Call the mcp_dare_fetch_page tool on https://example.com and reply with the page title only.","session_id":"deploy-smoke"}'
# poll: curl .../v1/runs/<run_id>  → expect output "Example Domain"

# 3. End to end: POST /api/research/projects/<id>/scout/ with a JWT, poll
#    /api/research/agent-runs/<id>/, expect staged findings in the Review Inbox.
```

## 8. Cost containment (already enforced in code — knobs for reference)

| Layer | Knob | Default |
|---|---|---|
| Hermes loop | `agent.max_turns` | 40 |
| DARE per run | `MAX_RUN_TOOL_CALLS` / `MAX_RUN_SECONDS` (`research/tasks.py`) | 18 / 480s |
| Scout depth | quick = 2 searches/3 reads · deep = 5 searches/10 reads | per request |
| Page reads | `MAX_CHARS` (`mcp/services/web_fetch.py`) | 40k chars |

Budget-exceeded runs are stopped via the Hermes stop endpoint, then a final
synthesis turn writes findings from the pages already fetched this run — DARE
injects those page excerpts into the finalize prompt (the fresh turn has no
session memory of them), so a capped run salvages a real result instead of
returning empty. Every run records token usage.

Page failures are honest, not fatal: a paywalled / blocked / 404 page is
returned to the agent as a normal "couldn't read this one" result (its real
HTTP reason — 403, 404, 429 — probed and reported, not guessed), **without** an
`isError` flag, so a run of dead links no longer trips Hermes's per-server
circuit breaker and kills the run.

## 9. Multi-project memory (current state)

One Hermes instance serves all projects. Isolation today: per-project session
keys (`X-Hermes-Session-Key: dare-proj<id>`, Hermes's official scoping handle)
+ per-run sessions for delegated work. The agent's `MEMORY.md`/`USER.md` files
are instance-global (user-level in practice; bounded to ~2k chars by Hermes).
Planned upgrades, in order: memory-provider scoping (Honcho) keyed by session
key — per-project memory on one gateway; per-project gateway credentials for
hard tool scoping. **Per-project Hermes profiles are deliberately not used**
(one gateway process per project does not scale operationally).

## Known limitations (tracked for v1.1)

- Gateway exposes all of the service user's connected MCP servers; per-run
  scoping is prompt-level today, credential-level later.
- Structured output is prompt-contract + tolerant parsing + repair re-ask
  (Hermes's API has no native schema forcing yet; tracked upstream).
- `SOUL.md` file sync assumes runs from different projects don't overlap
  in the same instant; per-run `instructions` always carry the soul as fallback.
