# mcp-toolcall-lab

Reproducible experiments for reliable LLM-to-MCP tool discovery, initialization, and calls using FastMCP and mock APIs.

This repository deliberately does **not** call the Ministry of Land, Infrastructure, Transport and Tourism (MLIT) Real Estate Information Library API. It is a safe mock target for checking whether a model calls only tools actually advertised by an MCP server.

## What this first PR provides

- A FastMCP Streamable HTTP server with three deterministic real-estate-style mock tools.
- A copyable standalone file generated from the package so tool names, schemas, and docstrings cannot drift.
- A strict system-prompt example that limits the model to Open WebUI's advertised tool specs.
- Protocol tests for the Open WebUI client flow plus unknown-tool, missing-argument, type-mismatch, empty-result, and timeout cases.

## Requirements

- Python 3.11 or newer (`datetime.UTC` and `X | Y` type hints).
- `fastmcp==3.4.7` (pinned). FastMCP 4.x requires `mcp>=2` and cannot share a venv with Open WebUI's `mcp==1.27.2`.

## Run locally

`openwebui_mcp_mock.py` is generated from `src/mcp_toolcall_lab/` and is the file you copy onto another machine. Do not copy only one function. After changing tools, regenerate with `python -m mcp_toolcall_lab.export`.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python openwebui_mcp_mock.py
```

If your shell does not support the activation command, invoke `.venv/bin/python` directly.

On a VM or container that should only run the mock, copy the standalone file and install FastMCP:

```bash
pip install "fastmcp==3.4.7"
MCP_HOST=0.0.0.0 python openwebui_mcp_mock.py
```

The MCP endpoint is Streamable HTTP at `/mcp`. In Open WebUI (v0.6.31+), add an **MCP (Streamable HTTP)** connection, not OpenAPI:

| Where Open WebUI runs | Server URL |
| --- | --- |
| Same host as the mock | `http://127.0.0.1:8000/mcp` |
| Docker, mock on the host | `http://host.docker.internal:8000/mcp` |
| Another VM / compose service | `http://<mock-hostname>:8000/mcp` |

The mock process must bind `MCP_HOST=0.0.0.0` whenever the client is not on the same network namespace. Set auth to **None** unless you add a token yourself.

## Test

```bash
pip install -e '.[test]'
pytest -q
```

`pip install -e` already puts `src` on the import path, so `PYTHONPATH=src` is not required. Pull requests run the same commands on GitHub Actions with Python 3.11.

## Talking to the mock with nothing but curl

Streamable HTTP is plain JSON-RPC over HTTP — no chat UI, browser, or even the `mcp`/`fastmcp`
Python SDKs are required to drive it. `scripts/mcp_curl_smoke.sh` performs the same handshake
Open WebUI does (`initialize` → `notifications/initialized` → `tools/list` → `tools/call`), using
only `curl` and `python3 -m json.tool` for pretty-printing:

```bash
python openwebui_mcp_mock.py &
scripts/mcp_curl_smoke.sh                      # defaults to http://127.0.0.1:8000/mcp
scripts/mcp_curl_smoke.sh http://host:port/mcp # or point it at another running instance
```

## Tracing a call: chat-simulated vs. direct

A request can reach this mock two ways: **through a chat UI** (the model decides to call a tool,
the UI executes it) or **straight to MCP** (curl, a script, anything speaking Streamable HTTP
directly). Both are traceable through the same `MCP_TOOLCALL_LOG` JSONL file, correlated by
whatever the caller puts in the request's `_meta` field — MCP reserves that field for exactly this,
no protocol extension needed.

`mcp_toolcall_lab.chat_sim` mocks the "chat経由" path generically: the OpenAI-compatible
`tool_calls`/`tool`-role message shapes that Open WebUI, LibreChat, LobeChat, and most other chat
UIs share (rather than reimplementing any one product's own internal ids like Open WebUI's
`chat_id`/`function_id`), wired to a real MCP call tagged with `call_id`/`chat_id`/`source: "chat"`.
`send_direct` is the other path — no chat layer, tagged `source: "direct"`.

```python
import asyncio
from mcp_toolcall_lab.chat_sim import send_via_chat, send_direct

async def main():
    trace = await send_via_chat(
        "http://127.0.0.1:8000/mcp",
        user_text="Where is Yokohama?",
        tool_name="find_municipalities",
        arguments={"query": "Yokohama"},
    )
    print(trace.assistant_tool_call_message)  # {"role": "assistant", "tool_calls": [...]}
    print(trace.tool_result_message)          # {"role": "tool", "tool_call_id": ..., "content": ...}

    await send_direct("http://127.0.0.1:8000/mcp", tool_name="find_stations",
                       arguments={"municipality_code": "14109"}, trace_id="probe-1")

asyncio.run(main())
```

With `MCP_TOOLCALL_LOG` set, both calls above land in the same JSONL file with the same shape,
distinguished only by `meta.source` — see `tests/test_trace.py`.

`tests/test_curl_protocol.py` gives the same raw-HTTP handshake pytest coverage (success, empty
result, unknown tool, missing argument), alongside `tests/test_streamable_http_protocol.py`'s
`mcp`-SDK-based client flow.

`chat_sim.py` mocks the generic OpenAI-compatible tool-calling wire shape, not any one chat UI's
own database. See [`docs/openwebui_schema_notes.md`](docs/openwebui_schema_notes.md) for Open
WebUI's actual `chat`/`file`/`function`/`tool` table schemas (verified against its source), kept
as a reference for a dedicated Open WebUI-specific mock later.

## Empty vs error

- **Empty** is a successful `tools/call` whose result is `[]` (unknown municipality, blank query, or a Japanese name that is not in this tiny English mock). Open WebUI forwards `content`, so the model sees an empty list, not a protocol error.
- **Error** is an unknown tool name, a missing/invalid argument, or a client timeout. Those calls set `isError` or raise at the client. They are not empty results.

Set `MCP_TOOLCALL_LOG=toolcalls.jsonl` before launch to record every `tools/call` as JSON Lines, including empty results and errors. The same logger is used by `python -m mcp_toolcall_lab` and by the standalone file. `arguments` in the log are the values received on the wire, before pydantic coercion.

## Open WebUI experiment record

Use `system_prompts/strict_tool_selection.md` as the starting system prompt. Open WebUI performs `initialize` and `tools/list`; the model only chooses among the resulting tool specs.

| Run | Model / settings | selected tool | raw schema valid | server accepted | outcome | notes |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | GPT-OSS 20B / baseline | | | | | |

Success means the model copies an exact name from the advertised specs. A fictional tool name is a failure even if the intended action sounds correct. Record **raw schema valid** (arguments match `inputSchema` before coercion) separately from **server accepted** (the mock did not return `isError`). Pydantic may coerce `year: "2025"` and accept the call even when the raw JSON is not schema-valid. `outcome` is `success`, `empty`, or `error`.

## Next increments

1. Add a versioned mock catalogue modeled on public REINFOLIB documentation, without API keys.
2. Compare schema strictness (raw-valid vs server-accepted) and system prompts in a recorded experiment matrix.
