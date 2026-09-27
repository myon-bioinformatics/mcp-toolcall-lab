from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from mcp_toolcall_lab.github_public_resolver import candidate_urls, probe_url, resolve_public_github


class Response(BytesIO):
    def __init__(self, status=200, url="https://example.test/final"):
        super().__init__(b"")
        self.status = status
        self._url = url
    def getcode(self):
        return self.status
    def geturl(self):
        return self._url


def test_candidates_cover_ui_api_pages_and_raw():
    rows = candidate_urls("octo/demo", ref="main", path="docs/a b.md")
    kinds = {row["kind"] for row in rows}
    assert kinds == {"repository", "api", "pages", "raw", "contents_api"}
    assert any("a%20b.md" in row["url"] for row in rows if row["kind"] == "raw")


@pytest.mark.parametrize(("code", "status"), [(200, "reachable"), (302, "reachable"), (401, "auth_required"), (403, "auth_required"), (404, "not_found"), (429, "rate_limited"), (500, "http_error")])
def test_probe_classifies_observed_http_status(code, status):
    def opener(request, timeout):
        if code >= 400:
            raise HTTPError(request.full_url, code, "x", None, None)
        return Response(code, request.full_url)
    result = probe_url("https://example.test/x", opener=opener)
    assert result["status"] == status
    assert result["http_status"] == code
    assert result["evidence"] == "http_response"


def test_network_failure_is_unverified_not_inaccessible():
    def opener(request, timeout):
        raise URLError("offline")
    result = probe_url("https://example.test/x", opener=opener)
    assert result["status"] == "unverified"
    assert result["http_status"] is None
    assert result["evidence"] == "network_error"


def test_not_checked_is_explicit_and_makes_no_observation():
    result = resolve_public_github("octo/demo", probe=False)
    assert result["status"] == "not_checked"
    assert result["observations"] == []
    assert result["auth"] == "anonymous"


@pytest.mark.parametrize("repo", ["demo", "../demo", "octo/demo/extra"])
def test_repo_validation(repo):
    with pytest.raises(ValueError):
        candidate_urls(repo)


def test_path_traversal_is_rejected():
    with pytest.raises(ValueError):
        candidate_urls("octo/demo", path="../secret")
