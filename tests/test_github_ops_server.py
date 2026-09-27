"""GitHub-ops FastMCP tools, called through FastMCP's in-memory Client (no network).

Mirrors browser-test-kit's tests/python/test_gh_ops.py stub shape (Stub /
client_for / reply) so the transport-stubbing style stays recognizable across
both repos, but drives calls through ``fastmcp.Client(transport=mcp)`` --
the same in-memory pattern ``tests/test_schema.py`` uses via
``mcp.list_tools()`` -- instead of importing gh_ops functions directly.
"""

from __future__ import annotations

import urllib.parse

import pytest
from fastmcp import Client as FastMCPClient

from mcp_toolcall_lab.github_ops_server import create_mcp, gh_ops

REPO = "octo/demo"
HEAD = "ecfd0ba1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7"


def reply(data=None, status=200, link=None):
    return gh_ops.Response(status, data, {"link": link} if link else {})


class Stub:
    """Transport stub: routes (METHOD, path) to a reply, a reply list, or a callable."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, body, headers):
        parts = urllib.parse.urlsplit(url)
        self.calls.append(
            {
                "method": method,
                "path": parts.path,
                "query": urllib.parse.parse_qs(parts.query),
                "body": body,
                "headers": headers,
            }
        )
        route = self.routes[(method, parts.path)]
        if callable(route):
            return route(parts, body)
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route

    @property
    def methods(self):
        return [call["method"] for call in self.calls]


def client_for(routes, token="t0ken"):
    stub = Stub(routes)
    return gh_ops.Client(token=token, transport=stub), stub


def pr_payload(**overrides):
    data = {
        "state": "open",
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "draft": False,
        "head": {"sha": HEAD, "ref": "feature"},
        "base": {"ref": "main"},
        "commits": 2,
        "changed_files": 3,
        "additions": 10,
        "deletions": 2,
        "html_url": "https://github.com/octo/demo/pull/11",
        "body": "",
    }
    data.update(overrides)
    return data


def comment(index, author="alice", body=None):
    return {
        "id": 100 + index,
        "created_at": f"2026-09-2{index % 10}T00:00:00Z",
        "user": {"login": author},
        "body": body if body is not None else f"comment {index}",
        "html_url": f"https://x/{index}",
    }


@pytest.fixture
def wired(monkeypatch):
    """Build a fresh server; return (mcp, patch_client) where patch_client(routes) makes
    every tool call in this test use a stubbed gh_ops.Client with the given routes."""
    import mcp_toolcall_lab.github_ops_server as gos

    mcp = create_mcp()

    def patch_client(routes, token="t0ken"):
        client, stub = client_for(routes, token=token)
        monkeypatch.setattr(gos, "_client", lambda: client)
        return client, stub

    return mcp, patch_client


async def _call(mcp, name, arguments):
    async with FastMCPClient(transport=mcp) as client:
        result = await client.call_tool(name, arguments, raise_on_error=False)
    return result


def _data(result):
    return result.structured_content["result"] if "result" in (result.structured_content or {}) else result.structured_content


# --- pr_status --------------------------------------------------------------------

async def test_pr_status_tool(wired):
    mcp, patch_client = wired
    patch_client({("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload())})
    result = await _call(mcp, "pr_status", {"repo": REPO, "number": 11})
    data = _data(result)
    assert data["ok"] is True
    assert data["state"] == "open" and data["mergeable_state"] == "clean"
    assert data["head_sha"] == HEAD
    assert data["url"] == "https://github.com/octo/demo/pull/11"
    assert result.is_error is False


# --- pr_for_branch ------------------------------------------------------------------

async def test_pr_for_branch_tool_found(wired):
    mcp, patch_client = wired
    patch_client(
        {
            ("GET", "/repos/octo/demo/pulls"): reply(
                [
                    {
                        "number": 11,
                        "state": "open",
                        "merged_at": None,
                        "draft": False,
                        "head": {"sha": HEAD},
                        "base": {"ref": "main"},
                        "title": "feature",
                        "html_url": "https://github.com/octo/demo/pull/11",
                    }
                ]
            )
        }
    )
    result = await _call(mcp, "pr_for_branch", {"repo": REPO, "branch": "feature"})
    data = _data(result)
    assert data["ok"] is True
    assert data["pulls"][0]["number"] == 11
    assert data["head"] == "octo:feature"


async def test_pr_for_branch_tool_none_found(wired):
    mcp, patch_client = wired
    patch_client({("GET", "/repos/octo/demo/pulls"): reply([])})
    result = await _call(mcp, "pr_for_branch", {"repo": REPO, "branch": "gone"})
    data = _data(result)
    assert data["ok"] is False
    assert data["pulls"] == []


# --- open_prs -------------------------------------------------------------------------

async def test_open_prs_tool_via_search(wired):
    mcp, patch_client = wired
    patch_client(
        {
            ("GET", "/search/issues"): reply(
                {
                    "total_count": 1,
                    "items": [
                        {
                            "repository_url": "https://api.github.com/repos/octo/demo",
                            "number": 5,
                            "draft": False,
                            "updated_at": "2026-09-20T00:00:00Z",
                            "user": {"login": "alice"},
                            "title": "fix thing",
                            "html_url": "https://github.com/octo/demo/pull/5",
                        }
                    ],
                }
            )
        }
    )
    result = await _call(mcp, "open_prs", {"owner": "octo"})
    data = _data(result)
    assert data["ok"] is True
    assert data["pulls"][0]["repo"] == "octo/demo"


async def test_open_prs_tool_repo_scoped(wired):
    mcp, patch_client = wired
    patch_client(
        {
            ("GET", "/repos/octo/demo/pulls"): reply(
                [
                    {
                        "number": 7,
                        "draft": True,
                        "updated_at": "2026-09-21T00:00:00Z",
                        "user": {"login": "bob"},
                        "title": "wip",
                        "html_url": "https://github.com/octo/demo/pull/7",
                    }
                ]
            )
        }
    )
    result = await _call(mcp, "open_prs", {"owner": "octo", "repos": ["demo"]})
    data = _data(result)
    assert data["ok"] is True
    assert data["pulls"] == [
        {
            "repo": "octo/demo",
            "number": 7,
            "draft": True,
            "updated_at": "2026-09-21T00:00:00Z",
            "author": "bob",
            "title": "wip",
            "url": "https://github.com/octo/demo/pull/7",
        }
    ]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("open_prs", {"owner": "octo", "limit": 0}),
        ("open_prs", {"owner": "octo", "limit": 101}),
        ("issue_comments_digest", {"repo": REPO, "number": 24, "last": 0}),
        ("issue_comments_digest", {"repo": REPO, "number": 24, "last": 101}),
        ("issue_comments_digest", {"repo": REPO, "number": 24, "preview": 0}),
        ("issue_comments_digest", {"repo": REPO, "number": 24, "preview": 1001}),
        ("check_runs", {"repo": REPO, "sha": HEAD, "min_checks": 0}),
        ("check_runs", {"repo": REPO, "sha": HEAD, "min_checks": 101}),
        ("workflow_runs", {"repo": REPO, "sha": HEAD, "limit": 0}),
        ("workflow_runs", {"repo": REPO, "sha": HEAD, "limit": 101}),
    ],
)
async def test_numeric_tool_arguments_are_bounded_before_github_call(wired, name, arguments):
    mcp, patch_client = wired
    _, stub = patch_client({})

    result = await _call(mcp, name, arguments)

    assert result.is_error is True
    assert stub.calls == []


async def test_numeric_tool_schemas_publish_bounds(wired):
    mcp, _ = wired
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    expected = [
        ("open_prs", "limit", 1, 100),
        ("issue_comments_digest", "last", 1, 100),
        ("issue_comments_digest", "preview", 1, 1000),
        ("check_runs", "min_checks", 1, 100),
        ("workflow_runs", "limit", 1, 100),
    ]
    for tool_name, field_name, minimum, maximum in expected:
        schema = tools[tool_name].parameters
        field = schema["properties"][field_name]
        # Optional fields are represented as anyOf by JSON Schema; required fields
        # carry the constraints directly. Check both shapes without depending on
        # the order of anyOf branches.
        candidates = [field, *field.get("anyOf", [])]
        assert any(candidate.get("minimum") == minimum for candidate in candidates)
        assert any(candidate.get("maximum") == maximum for candidate in candidates)


async def test_unexpected_pr_status_response_is_tool_error_and_server_recovers(wired):
    mcp, patch_client = wired
    secret = "unexpected-response-token"
    patch_client(
        {
            ("GET", "/repos/octo/demo/pulls/11"): [
                reply(["not", "an", "object"]),
                reply(pr_payload()),
            ]
        },
        token=secret,
    )

    malformed = await _call(mcp, "pr_status", {"repo": REPO, "number": 11})
    assert malformed.is_error is True
    assert secret not in str(malformed)

    valid = await _call(mcp, "pr_status", {"repo": REPO, "number": 11})
    assert valid.is_error is False
    assert _data(valid)["ok"] is True


# --- issue_comments_digest ----------------------------------------------------------

async def test_issue_comments_digest_tool_previews_only_and_bounded(wired):
    mcp, patch_client = wired
    items = [comment(i) for i in range(5)]
    items[0]["body"] = "line one\n\nline   two " + "x" * 300
    patch_client(
        {
            ("GET", "/repos/octo/demo/issues/24"): reply({"title": "Roadmap", "state": "open", "body": "b" * 42}),
            ("GET", "/repos/octo/demo/issues/24/comments"): reply(items),
        }
    )
    result = await _call(mcp, "issue_comments_digest", {"repo": REPO, "number": 24, "preview": 20})
    data = _data(result)
    assert data["ok"] is True
    assert data["total_comments"] == 5
    assert len(data["comments"]) == 5
    # Preview rows only: no full body anywhere except explicitly `show`n indexes.
    assert data["shown"] == {}
    for row in data["comments"]:
        assert "body" not in row
        assert len(row["preview"]) <= 21  # 20 + ellipsis
    assert data["comments"][0]["preview"] == "line one line two x…"


async def test_issue_comments_digest_tool_full_body_only_for_requested_indexes(wired):
    mcp, patch_client = wired
    items = [comment(i) for i in range(5)]
    patch_client(
        {
            ("GET", "/repos/octo/demo/issues/24"): reply({"title": "Roadmap", "state": "open", "body": ""}),
            ("GET", "/repos/octo/demo/issues/24/comments"): reply(items),
        }
    )
    result = await _call(mcp, "issue_comments_digest", {"repo": REPO, "number": 24, "show": [2]})
    data = _data(result)
    assert data["shown"] == {"2": "comment 2"} or data["shown"] == {2: "comment 2"}


# --- check_runs -----------------------------------------------------------------------

def check_run(name, conclusion="success", status="completed", annotations=0, run_id=1):
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": HEAD,
        "output": {"annotations_count": annotations},
    }


async def test_check_runs_tool_summarizes_without_waiting(wired):
    mcp, patch_client = wired
    patch_client(
        {
            ("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs"): reply(
                {"check_runs": [check_run("unit"), check_run("lint", conclusion="failure", run_id=2)]}
            )
        }
    )
    result = await _call(mcp, "check_runs", {"repo": REPO, "sha": HEAD, "min_checks": 2})
    data = _data(result)
    assert data["total"] == 2
    assert data["failed"] == 1
    assert data["succeeded"] == 1
    assert data["ok"] is False
    assert "failed" in data["reason"]
    assert data["sha"] == HEAD


# --- workflow_runs ----------------------------------------------------------------------

async def test_workflow_runs_tool_by_sha(wired):
    mcp, patch_client = wired
    data_payload = {
        "total_count": 1,
        "workflow_runs": [
            {
                "id": 9,
                "name": "CI",
                "event": "pull_request",
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "html_url": "u",
            }
        ],
    }
    patch_client({("GET", "/repos/octo/demo/actions/runs"): reply(data_payload)})
    result = await _call(mcp, "workflow_runs", {"repo": REPO, "sha": HEAD})
    data = _data(result)
    assert data["ok"] is True
    assert data["runs"][0]["id"] == 9


# --- url_* builders (pure, no network) -----------------------------------------------

async def test_url_pr_tool_default_and_tab(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_pr", {"repo": REPO, "number": 11})
    data = _data(result)
    assert data == {
        "ok": True,
        "web": "https://github.com/octo/demo/pull/11",
        "api": "https://api.github.com/repos/octo/demo/pulls/11",
    }
    result = await _call(mcp, "url_pr", {"repo": REPO, "number": 11, "tab": "checks"})
    data = _data(result)
    assert data == {"ok": True, "web": "https://github.com/octo/demo/pull/11/checks", "api": None}


async def test_url_compare_tool(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_compare", {"repo": REPO, "base": "main", "head": "feature"})
    data = _data(result)
    assert data == {
        "ok": True,
        "web": "https://github.com/octo/demo/compare/main...feature",
        "api": "https://api.github.com/repos/octo/demo/compare/main...feature",
    }


async def test_url_blame_tool_with_line(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_blame", {"repo": REPO, "ref": "main", "path": "a/b.py", "line": 42})
    data = _data(result)
    assert data == {"ok": True, "web": "https://github.com/octo/demo/blame/main/a/b.py#L42", "api": None}


async def test_url_history_tool(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_history", {"repo": REPO, "ref": "main", "path": "a/b.py"})
    data = _data(result)
    assert data == {
        "ok": True,
        "web": "https://github.com/octo/demo/commits/main/a/b.py",
        "api": "https://api.github.com/repos/octo/demo/commits?sha=main&path=a%2Fb.py",
    }


async def test_url_runs_tool_with_filters(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_runs", {"repo": REPO, "branch": "main", "status": "failure"})
    data = _data(result)
    assert data["ok"] is True
    assert data["web"] == "https://github.com/octo/demo/actions?query=branch%3Amain+is%3Afailure"
    assert data["api"] == "https://api.github.com/repos/octo/demo/actions/runs?branch=main&status=failure"


async def test_url_search_tool_pullrequests(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_search", {"query": "fix bug", "kind": "pullrequests"})
    data = _data(result)
    assert data["web"] == "https://github.com/search?q=is%3Apr+fix+bug&type=issues"
    assert data["api"] == "https://api.github.com/search/issues?q=is%3Apr+fix+bug"


async def test_url_raw_tool(wired):
    mcp, _ = wired
    result = await _call(mcp, "url_raw", {"repo": REPO, "ref": "main", "path": "a/b.py"})
    data = _data(result)
    assert data == {
        "ok": True,
        "web": "https://raw.githubusercontent.com/octo/demo/main/a/b.py",
        "api": "https://api.github.com/repos/octo/demo/contents/a/b.py?ref=main",
    }


# --- token handling and error surfacing -----------------------------------------------

async def test_403_is_ok_false_not_a_crash_and_token_is_scrubbed(wired, monkeypatch):
    mcp, patch_client = wired
    secret = "sekrit-token-value"
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    patch_client(
        {
            ("GET", "/repos/octo/demo/pulls/11"): reply(
                {"message": f"forbidden for token {secret}"}, status=403
            )
        },
        token=secret,
    )
    result = await _call(mcp, "pr_status", {"repo": REPO, "number": 11})
    assert result.is_error is False  # a 403 surfaces as ok: False, not a protocol-level crash
    data = _data(result)
    assert data["ok"] is False
    assert "error" in data
    assert secret not in str(data)
    assert secret not in str(result)


async def test_no_tool_accepts_a_token_argument(wired):
    mcp, _ = wired
    tools = await mcp.list_tools()
    for tool in tools:
        schema = getattr(tool, "parameters", None) or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        assert "token" not in properties, f"{tool.name} must not accept a token argument"


async def test_gh_ops_client_reads_token_only_from_environment(monkeypatch):
    """No tool wiring involved: gh_ops.Client() itself must fall back to the env, never a param."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    client = gh_ops.Client()
    assert client._token == "env-token"
