"""Observed URL shapes using synthetic IDs only, with all sockets blocked."""
import json
import socket

import pytest

from mcp_toolcall_lab.conversation_url import main, parse_reference
from mcp_toolcall_lab.trace_probe import extract_ids_from_url

UUID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("URL parsing must never access the network")
    monkeypatch.setattr(socket, "socket", forbidden)


@pytest.mark.parametrize("host,path,provider,kind,token", [
    ("chatgpt.com", "c", "chatgpt", "conversation_id", UUID),
    ("gemini.google.com", "app", "gemini", "conversation_id", "0123456789abcdef"),
    ("claude.ai", "chat", "claude", "conversation_id", UUID),
    ("m365.cloud.microsoft", "chat/conversation", "m365_copilot", "conversation_id", UUID),
    ("cursor.com", "agents", "cursor", "agent_id", "bc-" + UUID),
    ("github.com", "copilot/c", "github_copilot", "conversation_id", UUID),
])
def test_service_routes_and_trace_integration(host, path, provider, kind, token):
    base = f"https://{host}/{path}/{token}"
    for suffix in ("", "/", "?hl=ja", "?chat_id=unrelated#fragment"):
        result = parse_reference(base + suffix)
        assert result == {"provider": provider, "kind": kind, "id": token, "url": base}
        found = extract_ids_from_url(base + suffix, source="observe_url")
        assert [(v.kind, v.value, v.source) for v in found] == [(kind, token, "observe_url")]


def test_pr_identity_is_scoped_and_separate_from_copilot():
    result = parse_reference("https://github.com/example/lab/pull/110?x=1#discussion")
    assert result == {"provider": "github", "kind": "pull_request_id",
                      "id": "example/lab#110", "repository": "example/lab", "number": "110",
                      "url": "https://github.com/example/lab/pull/110"}
    assert parse_reference("https://github.com/example/other/pull/110")["id"] != result["id"]
    assert extract_ids_from_url(result["url"])[0].kind == "pull_request_id"


@pytest.mark.parametrize("url", [
    "", "https://[broken", "https://chatgpt.com:bad/c/abc", "http://chatgpt.com/c/abc",
    "https://chatgpt.com:444/c/abc", "https://user@chatgpt.com/c/abc",
    "https://chatgpt.com.evil.test/c/abc", "https://evil.test/app/abc",
    "https://chatgpt.com//c/abc", "https://chatgpt.com/c/abc/extra",
    "https://chatgpt.com/c/a%2Fb", "https://chatgpt.com/c/a%5Cb",
    "https://chatgpt.com/c/ab\nc", " https://chatgpt.com/c/abc",
    "https://chatgpt.com/c/new?chat_id=ignored", "https://gemini.google.com/app/",
    "https://gemini.google.com/share/abc", "https://claude.ai/new",
    "https://cursor.com/agents/new", "https://cursor.com/agents/other",
    "https://cursor.com/agents/bc-", "https://cursor.com/agents/bc-?chat_id=ignored",
    "https://github.com/copilot", "https://github.com/example/lab/pull/0",
    "https://github.com/example/lab/pull/110/files", "https://github.com/example/../pull/1",
    "https://m365.cloud.microsoft/chat/conversation/",
    "https://chatgpt.com/c/" + "a" * 257,
])
def test_unsupported_or_malformed_urls(url):
    assert parse_reference(url) is None


@pytest.mark.parametrize("host,path", [
    ("chatgpt.com", "c/abc"), ("gemini.google.com", "app/abc"),
    ("claude.ai", "chat/abc"), ("m365.cloud.microsoft", "chat/conversation/abc"),
    ("cursor.com", "agents/bc-abc"), ("github.com", "copilot/c/abc"),
    ("github.com", "example/lab/pull/1"),
])
@pytest.mark.parametrize("template", [
    "http://{host}/{path}", "https://user@{host}/{path}",
    "https://{host}:444/{path}", "https://{host}/{path}/extra",
    "https://{host}/{path}%2Fextra", "https://{host}/{path}\n",
])
def test_invalid_hosted_urls_do_not_use_legacy_query_fallback(host, path, template):
    url = template.format(host=host, path=path) + "?chat_id=unrelated"
    assert parse_reference(url) is None
    assert extract_ids_from_url(url) == []


def test_known_host_does_not_fall_back_to_query_ids():
    assert extract_ids_from_url("https://github.com/copilot?chat_id=misleading") == []
    assert extract_ids_from_url("https://chatgpt.com/c/new?chat_id=misleading") == []
    assert extract_ids_from_url("https://[broken") == []


def test_cli_distinguishes_identity_from_content(capsys):
    assert main([f"https://claude.ai/chat/{UUID}"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reference"]["provider"] == "claude"
    assert result["content_fetched"] is False
    assert main(["https://claude.ai/new"]) == 1
    assert json.loads(capsys.readouterr().out)["reference"] is None
