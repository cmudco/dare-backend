#!/usr/bin/env bash
# Renders every quill's example.md through a running quillmark-mcp server and
# saves the PDFs to ./out/. Usage: scripts/render-examples.sh [mcp-base-url]
# Default URL matches the dare-backend compose debug bind.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_URL="${1:-http://127.0.0.1:8090}"
MCP="$BASE_URL/mcp"
mkdir -p out

rpc() {
  curl -sf -X POST "$MCP" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d "$1" | sed -n 's/^data: //p; /^{/p' | head -1
}

echo "== list_quills =="
rpc '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_quills","arguments":{}}}' \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); print(json.dumps(r.get("result",{}).get("structuredContent",r), indent=2))'

for example in quills/*/*/example.md; do
  name=$(basename "$(dirname "$(dirname "$example")")")
  echo "== create_document: $name =="
  payload=$(python3 - "$example" <<'PY'
import json, sys
content = open(sys.argv[1]).read()
print(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "create_document", "arguments": {"content": content}}}))
PY
)
  response=$(rpc "$payload")
  url=$(printf '%s' "$response" | python3 -c 'import json,sys; r=json.load(sys.stdin).get("result",{}); print((r.get("structuredContent") or {}).get("url",""))')
  if [ -z "$url" ]; then
    echo "RENDER FAILED for $name:"
    printf '%s' "$response" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(json.dumps(r, indent=2)[:3000])'
    exit 1
  fi
  # Artifact URLs use the compose-internal hostname; rewrite for the host.
  host_url=$(printf '%s' "$url" | sed "s|http://quillmark-mcp:8080|$BASE_URL|")
  curl -sf -o "out/$name.pdf" "$host_url"
  echo "   -> out/$name.pdf ($(wc -c < "out/$name.pdf" | tr -d ' ') bytes)"
done
echo "All quills rendered."
