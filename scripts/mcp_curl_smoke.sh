#!/usr/bin/env bash
# Speak the MCP Streamable HTTP protocol with nothing but curl — the same
# handshake Open WebUI performs (initialize -> notifications/initialized ->
# tools/list -> tools/call), so you can sanity-check a running mock server
# from any Ubuntu box without a chat UI, a browser, or the mcp/fastmcp
# Python SDKs.
#
# Usage:
#   python openwebui_mcp_mock.py &            # start the mock (default :8000)
#   scripts/mcp_curl_smoke.sh                 # defaults to http://127.0.0.1:8000/mcp
#   scripts/mcp_curl_smoke.sh http://host:port/mcp
#
# Requires: curl, python3 (stdlib json only, for parsing/pretty-printing).
set -euo pipefail

URL="${1:-http://127.0.0.1:8000/mcp}"
ACCEPT="application/json, text/event-stream"

# Each MCP response over Streamable HTTP is an SSE frame; the payload we
# want is the JSON after the first "data:" line.
extract_data() {
    grep '^data:' | head -n1 | sed 's/^data: *//'
}

echo "== initialize ==" >&2
init_headers="$(mktemp)"
init_body="$(
    curl -sS -D "$init_headers" -X POST "$URL" \
        -H "Content-Type: application/json" \
        -H "Accept: $ACCEPT" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl-smoke","version":"0.0.1"}}}' \
        | extract_data
)"
session_id="$(grep -i '^mcp-session-id:' "$init_headers" | tr -d '\r' | cut -d' ' -f2)"
rm -f "$init_headers"
echo "$init_body" | python3 -m json.tool
echo "session id: $session_id" >&2

echo "== notifications/initialized ==" >&2
curl -sS -o /dev/null -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "Accept: $ACCEPT" \
    -H "Mcp-Session-Id: $session_id" \
    -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

echo "== tools/list ==" >&2
curl -sS -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "Accept: $ACCEPT" \
    -H "Mcp-Session-Id: $session_id" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    | extract_data | python3 -m json.tool

echo "== tools/call find_municipalities (query=Yokohama) ==" >&2
curl -sS -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "Accept: $ACCEPT" \
    -H "Mcp-Session-Id: $session_id" \
    -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"find_municipalities","arguments":{"query":"Yokohama"}}}' \
    | extract_data | python3 -m json.tool
