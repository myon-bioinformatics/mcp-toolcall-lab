#!/usr/bin/env bash
# Speak to the mock through FastMCP's own bundled CLI (`fastmcp list` / `fastmcp
# call`) instead of curl, a browser, or hand-written client code. `fastmcp` is
# already a pinned dependency of this project (see pyproject.toml), so this
# needs no extra install at all — it's arguably the most "native" way to
# smoke-test this server, the same way `python -m playwright screenshot`
# reaches for Playwright's own CLI instead of a custom script.
#
# Unlike scripts/mcp_curl_smoke.sh, the CLI handles the initialize /
# notifications/initialized / Mcp-Session-Id handshake itself — there is
# nothing to wire up by hand.
#
# Usage:
#   python openwebui_mcp_mock.py &                  # start the mock (default :8000)
#   scripts/mcp_fastmcp_cli_smoke.sh                 # defaults to http://127.0.0.1:8000/mcp
#   scripts/mcp_fastmcp_cli_smoke.sh http://host:port/mcp
#
# Requires: fastmcp (already installed via `pip install -e .`).
set -euo pipefail

URL="${1:-http://127.0.0.1:8000/mcp}"

echo "== fastmcp list ==" >&2
fastmcp list "$URL" --json

echo "== fastmcp call find_municipalities query=Yokohama ==" >&2
fastmcp call "$URL" find_municipalities query=Yokohama --json

echo "== fastmcp call find_stations (unknown municipality -> empty result) ==" >&2
fastmcp call "$URL" find_stations municipality_code=00000 --json

echo "== fastmcp call find_transaction_price (unknown tool -> non-zero exit) ==" >&2
if fastmcp call "$URL" find_transaction_price query=Yokohama --json; then
    echo "expected a non-zero exit for an unknown tool" >&2
    exit 1
fi
echo "(non-zero exit as expected — the CLI resolves tool names client-side, so" >&2
echo " this never reaches the server as an isError:true tools/call the way curl's does)" >&2
