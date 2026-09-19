"""Stdlib stub front: heading → body, MCP cases, dual locators, chat_id."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from mcp_toolcall_lab.stub_front import (
    CASE_HEADING_HIT,
    CASE_HEADING_MISS,
    CASE_MCP_EMPTY,
    CASE_MCP_SUCCESS,
    DEFAULT_CORPUS,
    LIBRECHAT_INPUT,
    LIBRECHAT_SEND,
    OWUI_INPUT,
    OWUI_RESPONSE,
    OWUI_SEND,
    StubState,
    classify_prompt,
    load_corpus,
    lookup_heading,
    main,
    make_handler,
    parse_sections,
    reply,
)

ROOT = Path(__file__).resolve().parents[1]


def test_corpus_has_readme_and_wiki_headings() -> None:
    sections = load_corpus(DEFAULT_CORPUS)
    titles = {section.title for section in sections}
    assert "Find municipalities" in titles
    assert "Yokohama" in titles
    assert "Geography" in titles
    yokohama = lookup_heading("Yokohama", sections, fuzzy=False)
    assert yokohama is not None
    assert "14109" in yokohama.body


def test_exact_heading_wins_over_mcp_keyword() -> None:
    sections = parse_sections("# Yokohama\n\nFrom the corpus.\n")
    classified = classify_prompt("Yokohama", sections)
    assert classified["kind"] == "heading"
    assert classified["section"].body == "From the corpus."


def test_sentence_with_yokohama_takes_mcp_path() -> None:
    sections = load_corpus(DEFAULT_CORPUS)
    classified = classify_prompt("Find municipalities named Yokohama", sections)
    assert classified["kind"] == "mcp"
    assert classified["tool"] == "find_municipalities"
    assert classified["arguments"]["query"] == "Yokohama"


def test_reply_heading_and_mcp_inprocess(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "mcp.jsonl"
    monkeypatch.setenv("MCP_TOOLCALL_LOG", str(log))
    heading = reply("# Find municipalities", chat_id="chat_" + "a" * 24)
    assert heading.case == CASE_HEADING_HIT
    assert heading.chat_id.startswith("chat_")
    assert "find_municipalities" in heading.assistant

    mcp_ok = reply("Where is Yokohama?", chat_id=heading.chat_id)
    assert mcp_ok.case == CASE_MCP_SUCCESS
    assert mcp_ok.tool == "find_municipalities"
    assert "14109" in mcp_ok.assistant
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["meta"]["chat_id"] == heading.chat_id
    assert row["meta"]["source"] == "stub-front"

    miss = reply("this heading does not exist xyz")
    assert miss.case == CASE_HEADING_MISS


def test_mcp_empty_case() -> None:
    sections = parse_sections("# Other\n\nbody\n")
    classified = classify_prompt("stations unknown code 00000", sections)
    assert classified["kind"] == "mcp"
    assert classified["arguments"]["municipality_code"] == "00000"
    turn = reply("stations unknown code 00000", sections=sections)
    assert turn.case == CASE_MCP_EMPTY
    assert turn.mcp_outcome == "empty"


def test_cli_turn_one_liner() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolcall_lab.stub_front", "turn", "--heading", "Yokohama", "--chat-id", "chat_" + "b" * 24],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["case"] == CASE_HEADING_HIT
    assert payload["chat_id"] == "chat_" + "b" * 24
    assert "14109" in payload["assistant"]


def test_html_exposes_librechat_and_owui_locators() -> None:
    state = StubState(sections=load_corpus(DEFAULT_CORPUS), mcp_url=None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        base = f"http://{host}:{port}"
        with urlopen(base + "/", timeout=5) as response:
            assert response.status == 200
            url = response.geturl()
            html = response.read().decode("utf-8")
        assert "/c/chat_" in url
        assert f'data-testid="{LIBRECHAT_INPUT}"' in html
        assert f'data-testid="{LIBRECHAT_SEND}"' in html
        assert f'id="{OWUI_INPUT}"' in html
        assert f'id="{OWUI_SEND}"' in html
        chat_id = url.rstrip("/").rsplit("/", 1)[-1]
        body = f"prompt=Yokohama".encode("utf-8")
        request = Request(
            f"{base}/c/{chat_id}",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            page = response.read().decode("utf-8")
        assert f'id="{OWUI_RESPONSE}"' in page
        assert "14109" in page
        api = Request(
            f"{base}/api/turn",
            data=json.dumps({"prompt": "Find municipalities named Yokohama", "chat_id": chat_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(api, timeout=5) as response:
            turn = json.loads(response.read().decode("utf-8"))
        assert turn["case"] == CASE_MCP_SUCCESS
        assert turn["chat_id"] == chat_id
    finally:
        server.shutdown()
        server.server_close()


def test_main_turn_exit(capsys) -> None:
    code = main(["turn", "--heading", "Yokohama", "--chat-id", "chat_cli"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["case"] == CASE_HEADING_HIT
