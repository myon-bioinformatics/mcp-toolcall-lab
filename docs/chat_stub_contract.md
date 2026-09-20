# Deterministic Deno chat stub

deploy/chat-stub/main.ts is a browser-facing reference, not a production chat
server. It loads the existing offline fixtures in
fixtures/prompt_experiments/ and exposes three small surfaces:

- GET / — a minimal form that chooses a fixture by prompt text.
- POST /api/chat — returns the official OpenAI tools / tool_calls /
  role: "tool" messages plus the correlation IDs used by the fixture.
- POST /mcp — replays the official Streamable HTTP handshake:
  initialize -> notifications/initialized -> tools/list -> tools/call.
  Responses are JSON-RPC 2.0 over SSE and include Mcp-Session-Id.

The only normal success fixture is available_tool_success. A prompt containing
query_reinfoldib selects fictional_tool_reject; it never creates a
fictional OpenAI tool call. Both paths are deterministic and offline.

    deno run --allow-env --allow-net --allow-read deploy/chat-stub/main.ts
    # open http://127.0.0.1:8787/

The Python test tests/test_chat_contract.py and the Deno tests consume the
same checked-in JSON. They are contract checks, not a second MCP implementation.
No live model, API key, MLIT request, Deno Deploy project, or Docker production
dependency is required. Deno is kept as a small reference/demo runtime so the
same wire can later be reproduced by the Python markdown.py/FastMCP/FastAPI
path.

