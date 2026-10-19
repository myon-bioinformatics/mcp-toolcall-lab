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
    ("gemini.google.com", "share", "gemini", "conversation_id", "0123456789abcdef"),
    ("claude.ai", "chat", "claude", "conversation_id", UUID),
    ("m365.cloud.microsoft", "chat/conversation", "m365_copilot", "conversation_id", UUID),
    ("cursor.com", "agents", "cursor", "agent_id", "bc-" + UUID),
    ("github.com", "copilot/c", "github_copilot", "conversation_id", UUID),
    ("chat.deepseek.com", "a/chat/s", "deepseek", "conversation_id", UUID),
    ("perplexity.ai", "search", "perplexity", "conversation_id", UUID),
    ("www.perplexity.ai", "search", "perplexity", "conversation_id", UUID),
    ("jules.google.com", "session", "jules", "session_id", "10839938662816797913"),
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


def test_issues_url_is_scoped_and_does_not_claim_issue():
    result = parse_reference("https://github.com/example/lab/issues/42?x=1#issuecomment-123456")
    assert result == {"provider": "github", "kind": "issue_or_pull_request_id",
                      "id": "example/lab#42", "repository": "example/lab", "number": "42",
                      "url": "https://github.com/example/lab/issues/42"}
    assert parse_reference("https://github.com/example/other/issues/42")["id"] != result["id"]
    assert extract_ids_from_url(result["url"])[0].kind == "issue_or_pull_request_id"


@pytest.mark.parametrize("leaf,kind", [
    ("pull", "pull_request_id"),
    ("issues", "issue_or_pull_request_id"),
])
def test_dotted_repo_slug_parses(leaf, kind):
    result = parse_reference(f"https://github.com/owner/my.repo/{leaf}/12?x=1#diff")
    assert result == {"provider": "github", "kind": kind,
                      "id": "owner/my.repo#12", "repository": "owner/my.repo", "number": "12",
                      "url": f"https://github.com/owner/my.repo/{leaf}/12"}
    assert extract_ids_from_url(result["url"])[0].kind == kind
    assert parse_reference(f"https://github.com/owner/./{leaf}/12") is None
    assert parse_reference(f"https://github.com/owner/../{leaf}/12") is None
    assert parse_reference(f"https://github.com/my.owner/repo/{leaf}/12") is None


@pytest.mark.parametrize("url", [
    "", "https://[broken", "https://chatgpt.com:bad/c/abc", "http://chatgpt.com/c/abc",
    "https://chatgpt.com:444/c/abc", "https://user@chatgpt.com/c/abc",
    "https://chatgpt.com.evil.test/c/abc", "https://evil.test/app/abc",
    "https://chatgpt.com//c/abc", "https://chatgpt.com/c/abc/extra",
    "https://chatgpt.com/c/a%2Fb", "https://chatgpt.com/c/a%5Cb",
    "https://chatgpt.com/c/ab\nc", " https://chatgpt.com/c/abc",
    "https://chatgpt.com/c/new?chat_id=ignored", "https://gemini.google.com/app/",
    "https://gemini.google.com/share/", "https://jules.google.com/session/",
    "https://jules.google.com/session/new", "https://claude.ai/new",
    "https://cursor.com/agents/new", "https://cursor.com/agents/other",
    "https://cursor.com/agents/bc-", "https://cursor.com/agents/bc-?chat_id=ignored",
    "https://github.com/copilot", "https://github.com/example/lab/pull/0",
    "https://github.com/example/lab/pull/110/files", "https://github.com/example/../pull/1",
    "https://github.com/example/lab/issues/0", "https://github.com/example/lab/issues/42/comments",
    "https://chat.deepseek.com/a/chat/s/new", "https://perplexity.ai/search/new",
    "https://m365.cloud.microsoft/chat/conversation/",
    "https://chatgpt.com/c/" + "a" * 257,
])
def test_unsupported_or_malformed_urls(url):
    assert parse_reference(url) is None


def test_jules_session_subpaths_are_normalized():
    url_with_file = "https://jules.google.com/session/10839938662816797913/code/README.md?query=1#frag"
    expected = {
        "provider": "jules",
        "kind": "session_id",
        "id": "10839938662816797913",
        "url": "https://jules.google.com/session/10839938662816797913",
    }
    assert parse_reference(url_with_file) == expected
    found = extract_ids_from_url(url_with_file, source="observe_url")
    assert [(v.kind, v.value, v.source) for v in found] == [("session_id", "10839938662816797913", "observe_url")]


@pytest.mark.parametrize("host,path", [
    ("chatgpt.com", "c/abc"), ("gemini.google.com", "app/abc"), ("gemini.google.com", "share/abc"),
    ("claude.ai", "chat/abc"), ("m365.cloud.microsoft", "chat/conversation/abc"),
    ("cursor.com", "agents/bc-abc"), ("github.com", "copilot/c/abc"),
    ("chat.deepseek.com", "a/chat/s/abc"), ("perplexity.ai", "search/abc"), ("www.perplexity.ai", "search/abc"),
    ("github.com", "example/lab/pull/1"), ("github.com", "example/lab/issues/1"),
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


@pytest.mark.parametrize("template", [
    "http://jules.google.com/session/10839938662816797913",
    "https://user@jules.google.com/session/10839938662816797913",
    "https://jules.google.com:444/session/10839938662816797913",
    "https://jules.google.com/session/10839938662816797913%2Fextra",
    "https://jules.google.com/session/10839938662816797913\n",
])
def test_invalid_jules_urls_do_not_use_legacy_query_fallback(template):
    url = template + "?chat_id=unrelated"
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
