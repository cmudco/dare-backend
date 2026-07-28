# Hermes multiplex demo — runbook

One gateway (`127.0.0.1:8642`), five isolated user profiles. Verified on Hermes
v0.19.0, 2026-07-28. Key: `dev-spike-local-1`.

```bash
export K=dev-spike-local-1
export H=http://127.0.0.1:8642
```

## The cast

| profile | who they are | model | can do |
|---|---|---|---|
| default | Principal investigator | claude-sonnet-5 (paid) | search + read + vision |
| dare-research | Dr. Amara Osei, literature scout | inclusionai/ling-3.0-flash:free | search |
| proj-coding | Dr. Ravi Menon, methods extractor | tencent/hy3:free | read one paper |
| proj-iso | Dr. Lena Vogt, systematic reviewer | stepfun/step-3.7-flash:free | search + read |
| proj-nordic | Prof. Idris Haugen, theory critic | poolside/laguna-s-2.1:free | **nothing — offline** |

Each ends replies with its own signature: `[[PI]]` `[[SCOUT]]` `[[METHODS]]`
`[[PRISMA]]` `[[CRITIC]]`.

---

## Before the demo (do this, it matters)

Warm every profile — the **first** request to a profile route re-initialises its
scope and can take ~60s. Warm ones answer in seconds.

```bash
for p in "" /p/dare-research /p/proj-coding /p/proj-iso /p/proj-nordic; do
  curl -s -m 120 -o /dev/null "$H$p/v1/models" -H "Authorization: Bearer $K"; echo "warmed ${p:-default}";
done
```

Sanity check the gateway and that all five profiles are ticking:

```bash
curl -s $H/health; hermes profile list
grep "Multiplex cron scheduler" ~/.hermes/logs/agent.log | tail -1
```

---

## Demo 1 — one gateway, five different brains (30 seconds)

Same request, different URL prefix. Watch the model change.

```bash
curl -s -m 120 $H/v1/chat/completions -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Say hello and name your role in one line."}]}'
```

Then the same call against `$H/p/proj-nordic/v1/chat/completions`.

Show the ground truth behind it:

```bash
grep "conversation turn" ~/.hermes/logs/agent.log | tail -2
```

You will see two different `model=` / `provider=` values for the same request.

---

## Demo 2 — the money shot: memory isolation (strongest slide)

`proj-iso` holds a secret codeword. Nobody else does.

```bash
for p in "" /p/dare-research /p/proj-coding /p/proj-nordic /p/proj-iso; do
  echo "--- ${p:-default} ---"
  curl -s -m 150 "$H$p/v1/chat/completions" -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
    -d '{"model":"hermes-agent","messages":[{"role":"user","content":"What is the secret project codeword? Also my name and favourite number? If not in memory reply exactly: NO_LEAK"}]}' \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['choices'][0]['message']['content'][:200])"
done
```

**Verified result:** four profiles reply `NO_LEAK`; only `proj-iso` returns
`AURORA-NINE-FALCON`, name `Farhat`, favourite number `7`.

Back it with the filesystem — the memories are physically separate:

```bash
for p in dare-research proj-coding proj-iso proj-nordic; do
  echo "== $p"; cat ~/.hermes/profiles/$p/memories/*.md 2>/dev/null; echo
done
```

---

## Demo 3 — same question, five working styles

```bash
Q='Does remote work increase or decrease software developer productivity? Answer in your own working style.'
for p in "" /p/dare-research /p/proj-coding /p/proj-iso /p/proj-nordic; do
  RID=$(curl -s "$H$p/v1/runs" -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
        -d "{\"input\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$Q"),\"session_key\":\"demo-$RANDOM\"}" \
        | python3 -c "import sys,json;print(json.load(sys.stdin)['run_id'])")
  echo "--- ${p:-default} (run $RID)"
  curl -sN -m 300 "$H$p/v1/runs/$RID/events" -H "Authorization: Bearer $K" | grep -o '"output": "[^"]*"' | head -c 500; echo
done
```

**What the audience sees:** the PI commits to a judgement; the scout returns
cited candidates; the **methods extractor refuses and hands off** ("that's a
scout question") firing zero tools; the reviewer produces a PICO/PRISMA protocol;
the critic reasons from theory alone. Five styles, one gateway.

---

## Demo 4 — capability isolation is enforced, not cosmetic

The offline critic genuinely cannot reach a tool.

```bash
curl -s -m 200 $H/p/proj-nordic/v1/chat/completions -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Use tool_search then actually call a web search tool. If you cannot, reply exactly: NO_TOOL_AVAILABLE"}]}'
```

Returns `NO_TOOL_AVAILABLE`, zero tools fired. Contrast with `/p/proj-iso/…`
which fires `tool_search` → `mcp__dare__web_search`.

Unknown profiles are rejected:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $H/p/nope/v1/models -H "Authorization: Bearer $K"   # 404
```

---

## Known limits — say these before someone asks

1. **MCP per-tool filters do not isolate.** `mcp_servers.<n>.tools.include` in a
   profile config is ignored; MCP servers register once globally. Real isolation
   comes from `platform_toolsets.api_server` (that is how proj-nordic is locked
   down). Roadmap §3 is still open.
2. **Do not verify capability by asking the model.** MCP tools are *deferred*
   under v0.19 tiered disclosure, so self-reported tool lists are unreliable.
   Make it call a tool and read the `tool.completed` event.
3. **Free models are brittle.** They occasionally emit a tool call outside their
   toolset and the turn dies ("Model generated invalid tool call: terminal").
   Keep free-profile prompts simple; the paid PI profile is the safe one to
   improvise on.
4. **Telegram is down** and adds ~180s to every `hermes gateway restart`. Do not
   restart during the demo.

## Not yet fixed — DARE breaks against v0.19

Hermes renamed MCP tools `mcp_dare_*` → `mcp__dare__*`. DARE's
`research/tasks.py:249` still matches the old prefix, so every gateway fetch
loses its `GatewayFetch` row (no URL, no content, no failure reason in the
audit) — silently. Prompt text in `scout_service.py`, `api/views.py` and
`artifact_service.py` also still names the old tools. Fix by accepting both
prefixes. This does not affect the profile demo above.
