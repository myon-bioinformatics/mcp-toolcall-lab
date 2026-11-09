from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from mcp_toolcall_lab.github_public_resolver import candidate_urls, diagnostics_payload, probe_url, resolve_public_github


class Response(BytesIO):
    def __init__(self, status=200, url="https://example.test/final", headers=None):
        super().__init__(b"")
        self.status = status
        self._url = url
        self.headers = headers or {}
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


def test_anonymous_github_403_with_exhausted_rate_limit_is_rate_limited():
    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            403,
            "rate limit exceeded",
            {"X-RateLimit-Remaining": "0"},
            None,
        )
    result = probe_url("https://api.github.com/repos/octo/demo", opener=opener)
    assert result["status"] == "rate_limited"
    assert result["http_status"] == 403


def test_plain_403_remains_auth_required():
    def opener(request, timeout):
        raise HTTPError(request.full_url, 403, "forbidden", {}, None)
    assert probe_url("https://example.test/private", opener=opener)["status"] == "auth_required"


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


@pytest.mark.parametrize("repo", ["demo", "../demo", "octo/..", "octo/.", "octo/demo/extra"])
def test_repo_validation(repo):
    with pytest.raises(ValueError):
        candidate_urls(repo)


def test_path_traversal_is_rejected():
    with pytest.raises(ValueError):
        candidate_urls("octo/demo", path="../secret")


def test_synthetic_http_error_without_fp_keeps_requested_url():
    def opener(request, timeout):
        raise HTTPError(request.full_url, 404, "missing", {}, None)
    result = probe_url("https://example.test/missing", opener=opener)
    assert result["status"] == "not_found"
    assert result["resolved_url"] == "https://example.test/missing"


def _record(branch="main", full_name="octo/demo"):
    return {"repository": {"full_name": full_name}, "head": {"branch": branch}}


def test_diagnostics_payload_is_metadata_plus_the_resolver():
    """The shape ascii_artist and markdown write (no extra keys), probed through the given opener."""
    seen = []
    def opener(request, timeout):
        seen.append(request.full_url)
        return Response(200, request.full_url)
    record = _record()
    payload = diagnostics_payload(record, opener=opener)
    assert set(payload) == {"metadata", "resolver"} and payload["metadata"] is record
    assert payload["resolver"]["status"] == "observed" and payload["resolver"]["path"] == "README.md"
    assert seen == [item["url"] for item in candidate_urls("octo/demo", ref="main", path="README.md")]
    assert diagnostics_payload(record, probe=False)["resolver"] == resolve_public_github("octo/demo", path="README.md", probe=False)


@pytest.mark.parametrize("branch", ["feat+x", "renovate/@types-node", "fix/日本語", None, ""])
def test_diagnostics_payload_reports_an_unsupported_branch_without_the_network(branch):
    def opener(request, timeout):
        raise AssertionError("network used for an unsupported ref")
    resolver = diagnostics_payload(_record(branch), opener=opener)["resolver"]
    assert resolver == {"repository": "octo/demo", "ref": branch, "path": "README.md", "auth": "anonymous",
                        "candidates": [], "observations": [], "status": "not_checked", "reason": "unsupported_ref"}


def test_diagnostics_payload_still_rejects_a_bad_repository_name():
    with pytest.raises(ValueError, match="repo"):
        diagnostics_payload(_record("feat+x", "../demo"), probe=False)
