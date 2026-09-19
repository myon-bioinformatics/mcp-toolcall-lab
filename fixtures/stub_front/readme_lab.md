# mcp-toolcall-lab

A tiny README-shaped corpus for the stdlib stub front. Headings here are the
deterministic "model": send the title, get this section's body.

## Find municipalities

`find_municipalities` looks up mock cities by name or prefecture.

Known rows: Chiyoda (13101, Tokyo), Yokohama (14109, Kanagawa),
Matsudo (12207, Chiba). A blank query is empty, not an error.

## Find stations

`find_stations` takes a `municipality_code`. Yokohama → JR Yokohama;
unknown codes return `[]`.

## Tracing

Lab `chat_id` is the pin. The stub puts it on MCP `_meta` and `X-Chat-Id`.
The mock server also mints one per MCP session when a real UI forgets.

## Next

CPU LLM in Docker is a later backend. This file stays the offline reply.
