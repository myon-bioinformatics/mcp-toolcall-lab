# Serverless stub try (GitHub Actions + Pages)

GitHub Pages hosts a **static** report. It cannot keep Docker running.
GitHub Actions is the compute: it starts one compose network, sends a
turn through the stub, appends anti-patterns as JSONL, then publishes
`_site/` to https://myon-bioinformatics.github.io/mcp-toolcall-lab/.

`usable_session_id()` stays on the MCP mock: a missing session is
missing, never the string `"None"`.

## Same Docker network

| Service | DNS | Role |
| --- | --- | --- |
| `stub-front` | `:8765` | `markdown.py` + stdlib chat stub |
| `mcp-mock` | `http://mcp-mock:8000/mcp` | generated FastMCP mock |
| `cpu-llm` | `http://cpu-llm:8080/v1` | CPU-class model (lite stand-in today) |

```bash
docker compose -f docker/stub-pages/docker-compose.yml up --build
python scripts/stub_pages_smoke.py
python -m mcp_toolcall_lab.stub_front pages --out _site --last-run test-results/last-run.json
```

Actions: workflow `stub-pages` (`workflow_dispatch`). Default `pytest -q`
does not start this stack.

Ledger: `test-results/antipatterns.jsonl` (dictionary:
`fixtures/antipatterns/catalog.yaml`). A miss does not pretend Send failed.
Raw MCP / cpu-llm / last-run files stay Actions artifacts. Pages only gets
`index.html` + allowlisted `summary.json`.

## Role split

**Cursor (this slice):** compose network, stdlib stub + vendored
`markdown.py`, Actions → Pages, anti-pattern JSONL, `usable_session_id`
kept, lite `cpu-llm` so the stack always starts.

**GPT:** drop a real tiny GGUF behind the same DNS using
`docker/stub-pages/docker-compose.gguf.yml` + `scripts/fetch_tiny_cpu_gguf.sh`.
Pin image + checksum. Accuracy still does not matter. Do not add a
second log schema.
