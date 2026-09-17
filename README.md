# mcp-toolcall-lab

Reproducible experiments for reliable LLM-to-MCP tool discovery, initialization, and calls using FastMCP and mock APIs.

This repository deliberately does **not** call the Ministry of Land, Infrastructure, Transport and Tourism (MLIT) Real Estate Information Library API. It is a safe mock target for checking whether a model calls only tools actually advertised by an MCP server.

## What this first PR provides

- A FastMCP Streamable HTTP server with three deterministic real-estate-style mock tools.
- A strict system-prompt example that requires `initialize` and `tools/list` before `tools/call`.
- Unit tests for mock data behavior, with no API key or network access.

## Run locally

`openwebui_mcp_mock.py` is intentionally self-contained: copy the whole file
to the machine that runs the MCP process. It serves MCP over Streamable HTTP at
`/mcp`, which is the transport used by Open WebUI's server-side MCP client.
Do not copy only one function.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python openwebui_mcp_mock.py
```

If your shell does not support the activation command, invoke `.venv/bin/python` directly.

## Test

```bash
pip install -e '.[test]'
PYTHONPATH=src pytest -q
```

## Open WebUI experiment record

Use `system_prompts/strict_tool_selection.md` as the starting system prompt, then record each run in this table.

| Run | Model / settings | initialize | tools/list | selected tool | result | notes |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | GPT-OSS 20B / baseline | | | | | |

Success means the model chooses an exact name from `tools/list` and sends schema-valid arguments. A fictional tool name is a failure even if the intended action sounds correct.

Set `MCP_TOOLCALL_LOG=toolcalls.jsonl` before launching the standalone file to
retain each successful mock tool call as JSON Lines. Set `MCP_HOST=0.0.0.0`
when the server must be reached from another container or VM.

## Next increments

1. Expand the protocol test into error, timeout, and invalid-tool cases.
2. Add a versioned mock catalogue modeled on public REINFOLIB documentation, without API keys.
3. Compare system prompts and model runtime settings in a recorded experiment matrix.
