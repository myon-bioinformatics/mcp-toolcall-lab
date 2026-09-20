"""Wikipedia-only Deno Streamable HTTP MCP: catalog + wire contract.

The Deno server is not FastMCP. Its ``tools/list`` inputSchema must still
come from ``create_mcp().list_tools()`` (written to
``deploy/wikipedia-mcp/catalog.generated.json``). Handshake/SSE/session
headers are checked against PR #23's recorded ``POST /mcp`` hops so this
endpoint stays official JSON-RPC 2.0 Streamable HTTP, not a lab event list.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from mcp_toolcall_lab.mcp_http import ACCEPT, extract_sse_data
from mcp_toolcall_lab.prompt_experiment import (
    MCP_INITIALIZE,
    MCP_INITIALIZED,
    MCP_TOOLS_CALL,
    MCP_TOOLS_LIST,
    hop_request,
    hop_response,
    iter_cases,
    iter_mcp_hops,
)
from mcp_toolcall_lab.wikipedia_mcp_catalog import (
    CATALOG_PATH,
    WIKIPEDIA_TOOL_NAMES,
    catalog_document,
    load_wikipedia_specs,
    write_catalog,
)
from mcp_toolcall_lab.wikipedia_tool import FIXTURE_ENV
from tests.test_schema import EXPECTED_TOOL_SPECS
from tests.test_streamable_http_protocol import running_mcp_server

ROOT = Path(__file__).resolve().parents[1]
DENO_DIR = ROOT / "deploy" / "wikipedia-mcp"
WIKI_FIXTURE = ROOT / "fixtures" / "wikipedia" / "yokohama_extract.json"
DENO = shutil.which("deno") or str(Path.home() / ".deno" / "bin" / "deno")

pytestmark = pytest.mark.integration


def _advertised(spec: dict) -> dict:
    return {
        "name": spec["name"],
        "description": spec["description"],
        "inputSchema": spec["inputSchema"],
    }


def _wikipedia_expected_specs() -> list[dict]:
    return [_advertised(spec) for spec in EXPECTED_TOOL_SPECS if spec["name"] in WIKIPEDIA_TOOL_NAMES]


def test_generated_catalog_matches_live_fastmcp_wikipedia_specs() -> None:
    recorded = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    live = catalog_document(asyncio.run(load_wikipedia_specs()))
    assert recorded == live
    advertised = [_advertised(spec) for spec in recorded["tools"]]
    assert advertised == _wikipedia_expected_specs()
    assert [spec["name"] for spec in recorded["tools"]] == list(sorted(WIKIPEDIA_TOOL_NAMES))
    assert "find_municipalities" not in [spec["name"] for spec in recorded["tools"]]


def test_catalog_export_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "catalog.generated.json"
    write_catalog(path)
    first = path.read_text(encoding="utf-8")
    write_catalog(path)
    assert path.read_text(encoding="utf-8") == first
    assert '"fetch_wikipedia_article"' in first
    assert '"fetch_wikipedia_section"' in first


def test_pr23_wire_fixtures_are_jsonrpc_sse_not_lab_events() -> None:
    """Same contract as tests/test_prompt_experiment.py, kept here so Wikipedia MCP CI
    fails if the recorded FastMCP hops stop being JSON-RPC 2.0 Streamable HTTP."""
    found_initialize = False
    for case in iter_cases():
        hops = iter_mcp_hops(case)
        for hop in hops:
            assert "event" not in hop
            http = hop["http"]
            assert http["method"] == "POST"
            assert http["path"] == "/mcp"
            request = hop_request(hop)
            assert request["jsonrpc"] == "2.0"
            method = request["method"]
            if method == MCP_INITIALIZED:
                assert http["status"] == 202
                assert http["response"] is None
                assert (http.get("response_sse") or "") == ""
                continue
            assert http["status"] == 200
            assert http["response_headers"]["Content-Type"] == "text/event-stream"
            assert str(http["response_sse"]).startswith("event: message")
            parsed = extract_sse_data(http["response_sse"])
            assert parsed == hop_response(hop)
            assert parsed["jsonrpc"] == "2.0"
            assert "result" in parsed
            if method == MCP_INITIALIZE:
                found_initialize = True
                assert request["params"]["protocolVersion"] == "2025-06-18"
                assert hop_response(hop)["result"]["protocolVersion"] == "2025-06-18"
                assert http["response_headers"]["Mcp-Session-Id"]
            if method == MCP_TOOLS_LIST:
                tools = hop_response(hop)["result"]["tools"]
                specs = [
                    _advertised(tool)
                    for tool in sorted(tools, key=lambda item: item["name"])
                ]
                assert specs == EXPECTED_TOOL_SPECS
            if method == MCP_TOOLS_CALL:
                result = hop_response(hop)["result"]
                assert "isError" in result
                assert result["content"][0]["type"] == "text"
    assert found_initialize


def test_python_fastmcp_wikipedia_subset_matches_generated_catalog() -> None:
    with running_mcp_server() as server:
        session_headers = {"Content-Type": "application/json", "Accept": ACCEPT}
        init = httpx.post(
            server.url,
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "wikipedia-mcp-catalog", "version": "0.0.1"},
                },
            },
            timeout=10.0,
        )
        session_id = init.headers.get("mcp-session-id")
        assert session_id
        httpx.post(
            server.url,
            headers={**session_headers, "Mcp-Session-Id": session_id},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=10.0,
        )
        listed = httpx.post(
            server.url,
            headers={**session_headers, "Mcp-Session-Id": session_id},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout=10.0,
        )
        tools = extract_sse_data(listed.text)["result"]["tools"]
        wiki = [
            _advertised(tool)
            for tool in tools
            if tool["name"] in WIKIPEDIA_TOOL_NAMES
        ]
        wiki.sort(key=lambda item: item["name"])
        recorded = [_advertised(spec) for spec in json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["tools"]]
        assert wiki == recorded


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_tail(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text[-limit:]


@dataclass
class RunningDeno:
    url: str
    process: subprocess.Popen[str]
    kv_path: Path


@contextmanager
def running_wikipedia_mcp(
    extra_env: dict[str, str] | None = None,
    *,
    kv_path: Path | None = None,
) -> Iterator[RunningDeno]:
    if not Path(DENO).is_file():
        pytest.skip("deno is not installed")
    port = _free_port()
    kv_dir: Path | None = None
    if kv_path is None:
        kv_dir = Path(tempfile.mkdtemp(prefix="wiki-mcp-kv-"))
        kv_path = kv_dir / "sessions.kv"
    stderr_path = Path(tempfile.mkstemp(prefix="wiki-mcp-stderr-", suffix=".log")[1])
    env = {
        **os.environ,
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
        FIXTURE_ENV: str(WIKI_FIXTURE),
        "MCP_KV_PATH": str(kv_path),
    }
    if extra_env:
        env.update(extra_env)
    with stderr_path.open("w", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            [
                DENO,
                "run",
                "--unstable-kv",
                "--allow-net",
                "--allow-env",
                "--allow-read",
                "--allow-write",
                "main.ts",
            ],
            cwd=DENO_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            text=True,
        )
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Deno Wikipedia MCP exited {process.returncode}: {_read_tail(stderr_path)}"
                    )
                try:
                    httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.2)
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise RuntimeError(f"Deno Wikipedia MCP did not start: {_read_tail(stderr_path)}")
            yield RunningDeno(url=url, process=process, kv_path=kv_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    stderr_path.unlink(missing_ok=True)
    if kv_dir is not None:
        shutil.rmtree(kv_dir, ignore_errors=True)


class DenoMcpSession:
    def __init__(self, url: str) -> None:
        self.url = url
        self.client = httpx.Client(timeout=15.0)
        self.session_id: str | None = None
        self._next_id = 1

    def close(self) -> None:
        self.client.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": ACCEPT}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def post(self, payload: dict) -> httpx.Response:
        return self.client.post(self.url, headers=self._headers(), json=payload)

    def initialize(self) -> dict:
        response = self.post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "wikipedia-mcp-test", "version": "0.0.1"},
                },
            }
        )
        self._next_id += 1
        self.session_id = response.headers.get("mcp-session-id")
        assert self.session_id, response.headers
        notify = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert notify.status_code == 202
        assert notify.content == b"" or notify.text.strip() == ""
        body = extract_sse_data(response.text)
        return body["result"]


@pytest.fixture()
def deno_session():
    with running_wikipedia_mcp() as server:
        session = DenoMcpSession(server.url)
        session.initialize()
        try:
            yield session
        finally:
            session.close()


def test_deno_handshake_matches_pr23_wire_envelope(deno_session: DenoMcpSession) -> None:
    fixture_init = None
    fixture_initialized = None
    for hop in iter_mcp_hops(iter_cases()[0]):
        method = hop_request(hop)["method"]
        if method == MCP_INITIALIZE:
            fixture_init = hop
        elif method == MCP_INITIALIZED:
            fixture_initialized = hop
    assert fixture_init is not None and fixture_initialized is not None
    assert hop_request(fixture_init)["params"]["protocolVersion"] == "2025-06-18"

    response = deno_session.post(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/list",
            "params": {},
        }
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get("mcp-session-id") == deno_session.session_id
    assert response.text.startswith("event: message")
    parsed = extract_sse_data(response.text)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["id"] == 99
    assert "result" in parsed
    assert "event" not in parsed
    names = [tool["name"] for tool in parsed["result"]["tools"]]
    assert names == list(sorted(WIKIPEDIA_TOOL_NAMES))
    specs = [_advertised(tool) for tool in parsed["result"]["tools"]]
    assert specs == _wikipedia_expected_specs()
    recorded = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["tools"]
    assert parsed["result"]["tools"] == recorded


def test_deno_cors_preflight_allows_browser_mcp_headers() -> None:
    with running_wikipedia_mcp() as server:
        response = httpx.options(
            server.url,
            headers={
                "Origin": "https://myon-bioinformatics.github.io",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,accept,mcp-session-id",
            },
            timeout=10.0,
        )
        assert response.status_code == 204
        assert response.headers.get("access-control-allow-origin") == "*"
        allow = response.headers.get("access-control-allow-headers", "").lower()
        assert "mcp-session-id" in allow
        assert "content-type" in allow
        expose = response.headers.get("access-control-expose-headers", "").lower()
        assert "mcp-session-id" in expose


def test_deno_tools_call_article_and_section_use_wikipedia_fixture(deno_session: DenoMcpSession) -> None:
    article = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "fetch_wikipedia_article", "arguments": {"title": "Yokohama"}},
            }
        ).text
    )["result"]
    assert article["isError"] is False
    payload = article["structuredContent"]
    if isinstance(payload, dict) and "result" in payload and "canonical_title" not in payload:
        payload = payload["result"]
    assert payload["canonical_title"] == "Yokohama"
    assert "14109" in payload["extract"]
    assert any(row["heading"] == "Geography" for row in payload["headings"])
    assert json.loads(article["content"][0]["text"]) == payload

    listed = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "fetch_wikipedia_section", "arguments": {"title": "Yokohama"}},
            }
        ).text
    )["result"]
    assert listed["isError"] is False
    rows = listed["structuredContent"]["result"]
    assert {"heading": "Yokohama"} in rows
    assert {"heading": "Geography"} in rows
    assert all("body" not in row for row in rows)

    geography = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_section",
                    "arguments": {"title": "Yokohama", "heading": "Geography"},
                },
            }
        ).text
    )["result"]
    assert geography["isError"] is False
    assert geography["structuredContent"]["result"] == [
        {
            "heading": "Geography",
            "heading_markdown": "## Geography",
            "body": "Kanagawa Prefecture, south of Tokyo. The mock station is JR Yokohama.",
        }
    ]

    climate = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_section",
                    "arguments": {"title": "Yokohama", "heading": "Climate"},
                },
            }
        ).text
    )["result"]
    assert climate["structuredContent"]["result"][0]["heading_markdown"] == "### Climate"

    empty = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_section",
                    "arguments": {"title": "Yokohama", "heading": "no-such-heading"},
                },
            }
        ).text
    )["result"]
    assert empty["isError"] is False
    assert empty["structuredContent"]["result"] == []

    redirected = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 15,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_article",
                    "arguments": {"title": "Yokohama, Japan"},
                },
            }
        ).text
    )["result"]
    assert redirected["structuredContent"]["canonical_title"] == "Yokohama"


def test_deno_unknown_tool_and_missing_title_are_iserror(deno_session: DenoMcpSession) -> None:
    unknown = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {"name": "find_municipalities", "arguments": {"query": "Yokohama"}},
            }
        ).text
    )["result"]
    assert unknown["isError"] is True
    assert "find_municipalities" in unknown["content"][0]["text"]

    missing = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {"name": "fetch_wikipedia_article", "arguments": {}},
            }
        ).text
    )["result"]
    assert missing["isError"] is True
    assert "title" in missing["content"][0]["text"].lower()

    absent = extract_sse_data(
        deno_session.post(
            {
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_article",
                    "arguments": {"title": "This Title Is Not In The Fixture"},
                },
            }
        ).text
    )["result"]
    assert absent["isError"] is True
    assert "no en.wikipedia.org article" in absent["content"][0]["text"]


def test_deno_requires_session_and_does_not_invent_event_rows() -> None:
    with running_wikipedia_mcp() as server:
        listed = httpx.post(
            server.url,
            headers={"Content-Type": "application/json", "Accept": ACCEPT},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout=10.0,
        )
        assert listed.status_code == 400
        health = httpx.get(server.url.replace("/mcp", "/health"), timeout=10.0)
        assert health.status_code == 200
        assert health.json() == {"ok": True, "server": "mcp-toolcall-lab-wikipedia"}
        session = DenoMcpSession(server.url)
        try:
            session.initialize()
            response = session.post(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
            )
            parsed = extract_sse_data(response.text)
            assert set(parsed) <= {"jsonrpc", "id", "result", "error"}
            assert parsed["jsonrpc"] == "2.0"
        finally:
            session.close()


def test_deno_rejects_injected_lang_as_tool_error(deno_session: DenoMcpSession) -> None:
    for lang in ("evil.com/", "evil.com", "#", "@"):
        result = extract_sse_data(
            deno_session.post(
                {
                    "jsonrpc": "2.0",
                    "id": 30,
                    "method": "tools/call",
                    "params": {
                        "name": "fetch_wikipedia_article",
                        "arguments": {"title": "Yokohama", "lang": lang},
                    },
                }
            ).text
        )["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "invalid wikipedia language code" in text
        assert "14109" not in text


def test_deno_session_expires_after_ttl() -> None:
    with running_wikipedia_mcp(extra_env={"MCP_SESSION_TTL_SECONDS": "1"}) as server:
        session = DenoMcpSession(server.url)
        try:
            session.initialize()
            listed = session.post(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )
            assert listed.status_code == 200
            time.sleep(1.5)
            expired = session.post(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
            )
            assert expired.status_code == 400
            body = expired.json()
            assert "session" in body["error"]["message"].lower()
        finally:
            session.close()


def test_deno_session_survives_process_restart_via_shared_kv(tmp_path: Path) -> None:
    kv_path = tmp_path / "sessions.kv"
    with running_wikipedia_mcp(kv_path=kv_path) as server:
        session = DenoMcpSession(server.url)
        try:
            session.initialize()
            session_id = session.session_id
            assert session_id
        finally:
            session.close()
    with running_wikipedia_mcp(kv_path=kv_path) as server:
        session = DenoMcpSession(server.url)
        session.session_id = session_id
        try:
            response = session.post(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )
            assert response.status_code == 200
            parsed = extract_sse_data(response.text)
            names = [tool["name"] for tool in parsed["result"]["tools"]]
            assert names == list(sorted(WIKIPEDIA_TOOL_NAMES))
        finally:
            session.close()


def test_deno_unknown_session_id_is_rejected() -> None:
    with running_wikipedia_mcp() as server:
        listed = httpx.post(
            server.url,
            headers={
                "Content-Type": "application/json",
                "Accept": ACCEPT,
                "Mcp-Session-Id": "sess_not-a-real-session",
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout=10.0,
        )
        assert listed.status_code == 400
        body = listed.json()
        assert body["error"]["code"] == -32000
        assert "Mcp-Session-Id" in body["error"]["message"]


def test_deno_wikipedia_fetch_rate_limit() -> None:
    with running_wikipedia_mcp(extra_env={"MCP_WIKI_FETCH_LIMIT_PER_MINUTE": "1"}) as server:
        session = DenoMcpSession(server.url)
        try:
            session.initialize()
            payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "fetch_wikipedia_article",
                    "arguments": {"title": "Yokohama"},
                },
            }
            first = extract_sse_data(session.post({**payload, "id": 40}).text)["result"]
            assert first["isError"] is False
            second = extract_sse_data(session.post({**payload, "id": 41}).text)["result"]
            assert second["isError"] is True
            assert "rate limit" in second["content"][0]["text"].lower()
        finally:
            session.close()
